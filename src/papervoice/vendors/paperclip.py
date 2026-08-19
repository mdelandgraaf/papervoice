"""Thin Paperclip control-plane adapter — the only module allowed to touch the Paperclip API.

Used at call start (load each persona's live issue context, see personas.py's
paperclip_agent_id), during the call (file a follow-up issue the board raises,
see boardroom.py's file_followup_issue tool), and after the call (post the
standup summary as a comment). See docs/ARCHITECTURE.md Milestone 3.

This module authenticates as a Paperclip agent identity, same as any other
Paperclip agent run — it is not a new vendor account. The key it needs (see
_api_key below) is a Paperclip-internal credential, not an
ElevenLabs/LiveKit/Twilio-style paid account. It prefers the durable board-minted
PAPERCLIP_BOARDROOM_API_KEY (PER-388) and falls back to the run-scoped
PAPERCLIP_API_KEY only when that is absent.
"""

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

_OPEN_STATUSES = "todo,in_progress,in_review,blocked"
# Longest description excerpt returned by search_issues (the in-call live-lookup
# tool). Bounded so a big issue body can't blow the LLM turn's token budget or
# latency; the agent gets enough to answer a question without reading a novel.
_LOOKUP_DESC_CHARS = 500
_TIMEOUT = 15.0
_DEFAULT_PENDING_POSTS_DIR = "/var/tmp/papervoice-boardroom/pending-posts"
# A drain claim (`*.draining-<pid>`) held longer than this by a still-running pid
# is treated as stale, guarding against pid reuse handing a crashed drainer's pid
# to an unrelated live process. Set well above _TIMEOUT so a healthy in-flight
# post — which cannot outlast the HTTP timeout — is never reclaimed out from under
# a live drainer. Override with PAPERCLIP_CLAIM_STALE_SECONDS.
_CLAIM_STALE_SECONDS = 15 * 60
logger = logging.getLogger(__name__)


def _pending_posts_dir() -> Path:
    return Path(os.environ.get("PAPERCLIP_PENDING_POSTS_DIR", _DEFAULT_PENDING_POSTS_DIR))


def _api_base() -> str:
    """Normalize PAPERCLIP_API_URL the same way the Paperclip skill's curl examples do:
    strip a trailing slash, then a trailing /api, so both forms work in .env."""
    base = os.environ.get("PAPERCLIP_API_URL", "")
    if not base:
        raise RuntimeError("PAPERCLIP_API_URL is not set (copy .env.example to .env)")
    return base.rstrip("/").removesuffix("/api")


def _api_key() -> str:
    """Return the Paperclip credential this long-running worker authenticates with.

    Prefer the durable, non-run-scoped board-minted key (``PAPERCLIP_BOARDROOM_API_KEY``,
    a ``pcp_*`` agent API key delivered on PER-388) because this process outlives any
    single heartbeat and must not depend on a ~1h run JWT. Fall back to the run-scoped
    ``PAPERCLIP_API_KEY`` only when the durable key is absent (e.g. a dev shell that never
    had PER-388's secret injected), preserving the old behaviour. This preference is what
    lets PER-386 retire the every-25-min token-refresh routine: with the durable key in
    ``.env`` there is nothing to refresh, so the worker never needs a wake to stay authed.
    """
    key = os.environ.get("PAPERCLIP_BOARDROOM_API_KEY", "") or os.environ.get(
        "PAPERCLIP_API_KEY", ""
    )
    if not key:
        raise RuntimeError(
            "no Paperclip credential set: expected PAPERCLIP_BOARDROOM_API_KEY "
            "(durable key) or PAPERCLIP_API_KEY (run JWT fallback) in .env"
        )
    return key


def _company_id() -> str:
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if not company_id:
        raise RuntimeError("PAPERCLIP_COMPANY_ID is not set (copy .env.example to .env)")
    return company_id


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


def _compact(issue: dict) -> dict:
    return {
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "status": issue.get("status"),
        "priority": issue.get("priority"),
    }


def agent_context(agent_id: str, limit: int = 5) -> list[dict]:
    """Compact open-issue list (identifier/title/status/priority) assigned to `agent_id`."""
    resp = httpx.get(
        f"{_api_base()}/api/companies/{_company_id()}/issues",
        headers=_headers(),
        params={"assigneeAgentId": agent_id, "status": _OPEN_STATUSES},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return [_compact(issue) for issue in resp.json()[:limit]]


def company_snapshot(limit: int = 8) -> list[dict]:
    """Compact list of the company's highest-priority open issues (server sorts by priority).

    Used for personas with no bound Paperclip agent (see personas.py) so they still
    speak from real state instead of nothing.
    """
    resp = httpx.get(
        f"{_api_base()}/api/companies/{_company_id()}/issues",
        headers=_headers(),
        params={"status": _OPEN_STATUSES},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return [_compact(issue) for issue in resp.json()[:limit]]


def _detail(issue: dict) -> dict:
    """Like _compact but also carries a bounded description excerpt, so the in-call
    live-lookup tool can answer "what is that issue actually about" questions."""
    description = (issue.get("description") or "").strip()
    if len(description) > _LOOKUP_DESC_CHARS:
        description = description[:_LOOKUP_DESC_CHARS].rstrip() + "…"
    return {**_compact(issue), "description": description}


def search_issues(query: str, limit: int = 5) -> list[dict]:
    """Live issue lookup by identifier, title, or keyword — backs the in-call
    look_up_paperclip tool (PER-401) so an agent can read real issue state mid-call
    instead of guessing or hallucinating when asked about a task.

    Unlike agent_context/company_snapshot this does NOT filter by status: a caller
    on the call may well ask "did PER-390 ever ship?", so a closed (done/cancelled)
    match must still come back. Results carry a bounded description excerpt via
    _detail. The server ranks matches (title > identifier > description > comments).
    """
    resp = httpx.get(
        f"{_api_base()}/api/companies/{_company_id()}/issues",
        headers=_headers(),
        params={"q": query},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return [_detail(issue) for issue in resp.json()[:limit]]


def context_briefing(agent_id: str | None, limit: int = 5) -> str:
    """One-line-per-issue briefing text ready to drop into an agent's turn instructions."""
    issues = agent_context(agent_id, limit=limit) if agent_id else company_snapshot(limit=limit)
    if not issues:
        return "no open issues."
    return "; ".join(f"{i['identifier']} ({i['status']}, {i['priority']}): {i['title']}" for i in issues)


def create_issue(
    title: str,
    description: str = "",
    assignee_agent_id: str | None = None,
    priority: str = "medium",
) -> dict:
    """File a follow-up issue raised live during a call. Returns {"identifier", "id"}."""
    payload: dict[str, str] = {"title": title, "description": description, "priority": priority}
    if assignee_agent_id:
        payload["assigneeAgentId"] = assignee_agent_id
    resp = httpx.post(
        f"{_api_base()}/api/companies/{_company_id()}/issues",
        headers=_headers(),
        json=payload,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    return {"identifier": body.get("identifier"), "id": body.get("id")}


def post_comment(issue_id: str, body: str) -> dict:
    """Post a markdown comment (e.g. the post-call summary) to an issue."""
    resp = httpx.post(
        f"{_api_base()}/api/issues/{issue_id}/comments",
        headers=_headers(),
        json={"body": body},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def queue_comment(issue_id: str, body: str) -> Path:
    """Persist a comment for a later wake when Paperclip is unavailable."""
    directory = _pending_posts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=directory)
    path = Path(temporary).with_suffix(".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"issue_id": issue_id, "body": body}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    logger.error("queued failed Paperclip comment for retry: %s", path)
    return path


def post_comment_or_queue(issue_id: str, body: str) -> dict | None:
    """Post a comment, durably queueing it if the API call fails."""
    try:
        return post_comment(issue_id, body)
    except Exception:
        queue_comment(issue_id, body)
        logger.exception("failed to post Paperclip comment to %s; queued for retry", issue_id)
        return None


def _claim_stale_seconds() -> float:
    raw = os.environ.get("PAPERCLIP_CLAIM_STALE_SECONDS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _CLAIM_STALE_SECONDS


def _pid_alive(pid: int) -> bool:
    """True if a process with `pid` currently exists (regardless of its owner)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user — still alive
    return True


def _reclaim_orphaned_claims(directory: Path) -> int:
    """Requeue drain claims abandoned by a drainer that crashed mid-post.

    drain_pending_comments claims a queued file with an atomic rename to
    `<name>.draining-<pid>` before posting it. A hard crash (SIGKILL) in the
    narrow window between that claim and the unlink/requeue orphans the file: it
    no longer matches the `*.json` glob, so no later wake would ever retry it and
    the queued board comment (a transcript, never a secret) would be silently
    lost. At the top of every drain we rename such orphans back to `*.json` so
    the normal loop picks them up again — trading that silent loss for an
    at-most-once-more repost (a duplicate) if the crash landed after the post
    itself succeeded. A claim counts as orphaned when its owning pid is gone, or
    — guarding against pid reuse — when it has sat untouched far longer than any
    healthy post could take. Live, recently-touched claims are left alone. See
    PER-259; load-tested in tests/load/test_token_handling_load.py scenario D.
    """
    stale_seconds = _claim_stale_seconds()
    reclaimed = 0
    for claim in directory.glob("*.draining-*"):
        original, sep, pid_text = claim.name.rpartition(".draining-")
        if not sep:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue  # unrecognized suffix — leave it untouched
        if _pid_alive(pid):
            try:
                age = time.time() - claim.stat().st_mtime
            except FileNotFoundError:
                continue  # already reclaimed/unlinked by a concurrent drainer
            if age < stale_seconds:
                continue  # a live drainer is still posting this one
        try:
            os.rename(claim, directory / original)  # atomic; concurrent-reclaim loser gets ENOENT
        except FileNotFoundError:
            continue  # another drainer reclaimed it first
        except OSError:
            logger.exception("failed to reclaim orphaned drain claim %s", claim.name)
            continue
        reclaimed += 1
        logger.warning("reclaimed orphaned drain claim %s (owner pid %s gone/stale)", claim.name, pid)
    return reclaimed


def drain_pending_comments() -> int:
    """Retry every queued comment; leave failures queued and return successes.

    Safe under concurrent drains (overlapping wakes call this on every wake — see
    PER-86 load test tests/load/test_token_handling_load.py, scenario B2). Each
    file is *claimed* by an atomic rename to a drainer-private name before the
    network post: os.rename can hand the source to exactly one caller, so the
    loser gets FileNotFoundError and skips it. Without this claim, two drainers
    both read a *.json in the window before either unlinks it and post it twice —
    load testing measured ~7x duplicate posts with 8 drainers at 20 ms API
    latency. On post failure the claim is renamed back to *.json for a later retry.

    Before the loop we reclaim claims orphaned by a drainer that was hard-killed
    mid-post (PER-259), so a crash in that window costs an at-most-once repost
    instead of silently dropping the queued comment.
    """
    directory = _pending_posts_dir()
    if not directory.exists():
        return 0
    _reclaim_orphaned_claims(directory)
    posted = 0
    for path in sorted(directory.glob("*.json")):
        # Claim name must NOT end in .json — queued files are dotfiles and
        # pathlib's "*.json" glob matches them, so a .json claim would be
        # re-claimed by a concurrent drainer. Suffix keeps it out of the glob.
        claim = directory / f"{path.name}.draining-{os.getpid()}"
        try:
            os.rename(path, claim)  # atomic claim; loser of the race gets ENOENT
        except FileNotFoundError:
            continue  # another drainer already claimed this comment
        try:
            payload = json.loads(claim.read_text(encoding="utf-8"))
            post_comment(payload["issue_id"], payload["body"])
            claim.unlink()
            posted += 1
            logger.info("posted queued Paperclip comment %s", path.name)
        except Exception:
            logger.exception("failed to drain queued Paperclip comment %s; retaining it", path.name)
            try:
                os.replace(claim, path)  # un-claim so a later wake retries it
            except OSError:
                logger.exception("failed to requeue claimed comment %s", claim.name)
    return posted


def is_auth_error(exc: Exception) -> bool:
    """True if `exc` is an HTTP 401/403 from the Paperclip API.

    Used by boardroom.py to tell "board tools offline" (expired/invalid
    PAPERCLIP_API_KEY — see the short-lived-token fallback in
    docs/ARCHITECTURE.md's M3 notes, PER-76) apart from any other failure, so
    the call can log and speak a clear, specific notice instead of a generic
    "something went wrong."
    """
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)


def get_plugin_config() -> dict:
    """Fetch the Papervoice plugin configuration from the Paperclip API.

    Returns the raw configJson dict on success, or an empty dict on 404
    (unconfigured) or 403 (caller is an agent token without board access —
    the boardroom worker authenticates as an agent and cannot reach the
    plugin-config endpoint, which requires a board session).
    Raises on any other HTTP error.
    """
    resp = httpx.get(
        f"{_api_base()}/api/plugins/papervoice/config",
        headers=_headers(),
        params={"companyId": _company_id()},
        timeout=_TIMEOUT,
    )
    if resp.status_code in (404, 403):
        return {}
    resp.raise_for_status()
    body = resp.json()
    return body.get("configJson") or {}


def list_agent_ids() -> set[str]:
    """All agent ids in the company — used by scripts/healthcheck to catch the persona
    roster's paperclip_agent_id values drifting off the company (agent renamed/removed)."""
    resp = httpx.get(
        f"{_api_base()}/api/companies/{_company_id()}/agents",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return {agent["id"] for agent in resp.json()}


def get_all_agents() -> list[dict]:
    """All company agents with full records — used by the dashboard to render the roster.

    Each dict includes at minimum: id, name, role, title, capabilities, metadata.
    """
    resp = httpx.get(
        f"{_api_base()}/api/companies/{_company_id()}/agents",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def update_agent_voice_config(agent_id: str, config: dict | None) -> None:
    """PATCH an agent's metadata.papervoice settings via the Paperclip API.

    Pass a dict (with at minimum `enabled`, and `voice_id` when enabling) to
    set the voice config, or None to clear it. Requires agents:configure on the
    target agent — will 403 if the caller lacks that grant.
    """
    resp = httpx.patch(
        f"{_api_base()}/api/agents/{agent_id}",
        headers=_headers(),
        json={"metadata": {"papervoice": config}},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


@dataclass(frozen=True)
class PapervoiceAgentConfig:
    """A Paperclip agent whose metadata.papervoice.enabled is True, enriched with voice config.

    Built by get_voice_enabled_agents() and consumed by personas.build_persona_from_agent().
    Fields come from two places: the agent record itself (agent_id, name, role, title,
    capabilities) and the agent's metadata.papervoice object (voice_id, livekit_identity,
    display_name, roster_order, moderator).
    """

    agent_id: str
    name: str
    role: str
    title: str | None
    capabilities: str | None
    voice_id: str
    livekit_identity: str
    display_name: str
    roster_order: int
    moderator: bool = False


def get_voice_enabled_agents() -> list[PapervoiceAgentConfig]:
    """Company agents with metadata.papervoice.enabled == True, sorted by roster_order.

    Each agent in the result has a valid voice_id. Agents missing voice_id are skipped with
    a warning — they are not ready for boardroom participation yet. Agents without
    livekit_identity or display_name get sensible defaults derived from their name.

    Called by personas.load_roster_from_paperclip() to build a live roster from real
    Paperclip agents instead of relying on the hardcoded BOARDROOM_ROSTER. See PER-89.
    """
    resp = httpx.get(
        f"{_api_base()}/api/companies/{_company_id()}/agents",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    result: list[PapervoiceAgentConfig] = []
    for agent in resp.json():
        pv = (agent.get("metadata") or {}).get("papervoice") or {}
        if not pv.get("enabled"):
            continue
        # Paperclip's metadata serializer currently exposes nested keys in
        # camelCase even when callers PATCH snake_case. Accept both forms so
        # persisted dashboard configuration remains readable.
        voice_id = pv.get("voice_id") or pv.get("voiceId")
        if not voice_id:
            logger.warning(
                "agent %s (%s) has papervoice.enabled but no voice_id — skipping",
                agent.get("name"),
                agent["id"],
            )
            continue
        slug = agent["name"].lower().replace(" ", "-")
        livekit_identity = pv.get("livekit_identity") or pv.get("livekitIdentity") or pv.get("identity") or f"agent-{slug}"
        display_name = pv.get("display_name") or pv.get("displayName") or agent["name"]
        roster_order = int(pv.get("roster_order", pv.get("rosterOrder", pv.get("order", 99))))
        moderator = bool(pv.get("moderator", False))
        result.append(
            PapervoiceAgentConfig(
                agent_id=agent["id"],
                name=agent["name"],
                role=agent.get("role") or "general",
                title=agent.get("title"),
                capabilities=agent.get("capabilities"),
                voice_id=voice_id,
                livekit_identity=livekit_identity,
                display_name=display_name,
                roster_order=roster_order,
                moderator=moderator,
            )
        )
    result.sort(key=lambda a: (a.roster_order, a.name))
    return result
