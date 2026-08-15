"""Thin Paperclip control-plane adapter — the only module allowed to touch the Paperclip API.

Used at call start (load each persona's live issue context, see personas.py's
paperclip_agent_id), during the call (file a follow-up issue the board raises,
see boardroom.py's file_followup_issue tool), and after the call (post the
standup summary as a comment). See docs/ARCHITECTURE.md Milestone 3.

This module authenticates as a Paperclip agent identity, same as any other
Paperclip agent run — it is not a new vendor account. The long-lived key it
needs (PAPERCLIP_API_KEY below) is a Paperclip-internal credential, not an
ElevenLabs/LiveKit/Twilio-style paid account.
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

_OPEN_STATUSES = "todo,in_progress,in_review,blocked"
_TIMEOUT = 15.0
_DEFAULT_PENDING_POSTS_DIR = "/var/tmp/papervoice-boardroom/pending-posts"
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
    key = os.environ.get("PAPERCLIP_API_KEY", "")
    if not key:
        raise RuntimeError("PAPERCLIP_API_KEY is not set (copy .env.example to .env)")
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


def drain_pending_comments() -> int:
    """Retry every queued comment; leave failures queued and return successes."""
    directory = _pending_posts_dir()
    if not directory.exists():
        return 0
    posted = 0
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            post_comment(payload["issue_id"], payload["body"])
            path.unlink()
            posted += 1
            logger.info("posted queued Paperclip comment %s", path.name)
        except Exception:
            logger.exception("failed to drain queued Paperclip comment %s; retaining it", path)
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
    display_name, roster_order).
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
        voice_id = pv.get("voice_id")
        if not voice_id:
            logger.warning(
                "agent %s (%s) has papervoice.enabled but no voice_id — skipping",
                agent.get("name"),
                agent["id"],
            )
            continue
        slug = agent["name"].lower().replace(" ", "-")
        livekit_identity = pv.get("livekit_identity") or f"agent-{slug}"
        display_name = pv.get("display_name") or agent["name"]
        roster_order = int(pv.get("roster_order", 99))
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
            )
        )
    result.sort(key=lambda a: (a.roster_order, a.name))
    return result
