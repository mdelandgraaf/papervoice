"""Thin Paperclip control-plane adapter — the only module allowed to touch the Paperclip API.

Used at call start (load each persona's live issue context, see personas.py's
paperclip_agent_id), during the call (file a follow-up issue the board raises,
see boardroom.py's file_followup_issue tool), and after the call (post the
standup summary as a comment). See docs/ARCHITECTURE.md Milestone 3.

This module authenticates as a Paperclip agent identity, same as any other
Paperclip agent run — it is not a new vendor account. The key it needs is a
Paperclip-internal credential, not an ElevenLabs/LiveKit/Twilio-style paid
account. It prefers the durable board-minted PAPERCLIP_BOARDROOM_API_KEY
(PER-388) and falls back to the run-scoped PAPERCLIP_API_KEY only when that
is absent.

PER-405: multi-tenant. The primary API is now a ``PaperclipClient`` holding an
explicit ``company_id``/``api_key`` pair, so one long-running boardroom worker
can serve multiple Paperclip companies (one client per per-job companyId read
from LiveKit room metadata). The module-level helpers below (``agent_context``,
``create_issue`` …) are preserved shims that call ``default_client()`` and
continue to serve the single-tenant, healthcheck, and drain-queue paths that
have no per-job company context.
"""

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
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


def _default_env_api_key() -> str:
    """Return the Paperclip credential this long-running worker authenticates with.

    Prefer the durable, non-run-scoped board-minted key (``PAPERCLIP_BOARDROOM_API_KEY``,
    a ``pcp_*`` agent API key delivered on PER-388) because this process outlives any
    single heartbeat and must not depend on a ~1h run JWT. Fall back to the run-scoped
    ``PAPERCLIP_API_KEY`` only when the durable key is absent (e.g. a dev shell that never
    had PER-388's secret injected), preserving the old behaviour.
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


def _default_env_company_id() -> str:
    company_id = os.environ.get("PAPERCLIP_COMPANY_ID", "")
    if not company_id:
        raise RuntimeError("PAPERCLIP_COMPANY_ID is not set (copy .env.example to .env)")
    return company_id


# Legacy internal aliases retained for the existing unit-test surface — the
# PER-405 refactor moved these to _default_env_* to make the source of the
# credential/company explicit in every call site. New code should build a
# PaperclipClient directly or call client_for_company(); do NOT reintroduce a
# module-level singleton keyed off these helpers.
_api_key = _default_env_api_key
_company_id = _default_env_company_id


def _compact(issue: dict) -> dict:
    return {
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "status": issue.get("status"),
        "priority": issue.get("priority"),
    }


def _detail(issue: dict) -> dict:
    """Like _compact but also carries a bounded description excerpt, so the in-call
    live-lookup tool can answer "what is that issue actually about" questions."""
    description = (issue.get("description") or "").strip()
    if len(description) > _LOOKUP_DESC_CHARS:
        description = description[:_LOOKUP_DESC_CHARS].rstrip() + "…"
    return {**_compact(issue), "description": description}


@dataclass(frozen=True)
class PapervoiceAgentConfig:
    """A Paperclip agent whose metadata.papervoice.enabled is True, enriched with voice config.

    Built by ``PaperclipClient.get_voice_enabled_agents`` and consumed by
    personas.build_persona_from_agent. Fields come from two places: the agent record
    itself (agent_id, name, role, title, capabilities) and the agent's
    metadata.papervoice object (voice_id, livekit_identity, display_name,
    roster_order, moderator).
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


@dataclass(frozen=True)
class LiveKitCreds:
    """LiveKit URL/key/secret returned by the plugin's /boardroom-config route (PER-411)."""

    url: str
    api_key: str
    api_secret: str


@dataclass(frozen=True)
class PluginConfigResult:
    """Plugin-served per-company config: a ready PaperclipClient + LiveKit creds.

    Returned by :meth:`PaperclipClient.load_from_plugin` so the boardroom
    entrypoint can override the process-wide LiveKit env before dispatching
    the call without threading three separate values around.
    """

    client: "PaperclipClient"
    livekit: LiveKitCreds


@dataclass(frozen=True)
class PaperclipClient:
    """Company-scoped Paperclip API client.

    Holds one ``(company_id, api_key)`` pair so a long-running worker can serve
    multiple Paperclip companies from a single process — a fresh client is
    built per LiveKit job from the room-metadata companyId (PER-405).
    ``default_client()`` reads env vars for single-tenant/healthcheck/dev paths.
    """

    company_id: str
    api_key: str

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    @classmethod
    def load_from_plugin(
        cls,
        company_id: str,
        room_metadata: dict | None,
    ) -> PluginConfigResult | None:
        """Fetch per-company boardroom config from the Papervoice plugin (PER-411).

        The plugin's ``mint-join-link`` (PER-410) stamps a short-lived HMAC
        bearer into LiveKit room metadata as ``boardroomConfigToken``. With
        that token in hand the worker calls
        ``GET /api/plugins/papervoice/api/boardroom-config?companyId=…`` and gets
        back ``{boardroomApiKey, liveKitUrl, liveKitApiKey, liveKitApiSecret}``
        resolved from the company's Paperclip secrets. This lets one worker
        serve any number of companies with zero per-company ``.env`` entries
        and no restart when a company's config changes.

        Returns ``None`` when the room metadata carries no token (old link /
        HMAC secret not provisioned), the token is expired per the stamped
        expiry, or the plugin route returns any non-2xx — the caller then
        falls back to the env-var based key lookup path so single-tenant and
        pre-PER-410 deployments keep working. All failures are logged so
        drift is visible.
        """
        if not company_id or not isinstance(room_metadata, dict):
            return None
        token = room_metadata.get("boardroomConfigToken")
        if not isinstance(token, str) or not token:
            return None
        # Server verifies exp itself; still short-circuit on a token that's
        # already past its stamped expiry so we never burn a plugin call on a
        # guaranteed 403. `boardroomConfigTokenExpiresAt` is an ISO-8601
        # timestamp; treat any parse failure as "unknown, let the server decide."
        expires_at = room_metadata.get("boardroomConfigTokenExpiresAt")
        if isinstance(expires_at, str) and expires_at:
            try:
                from datetime import datetime, timezone

                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if exp <= datetime.now(timezone.utc):
                    logger.warning(
                        "boardroom-config token in room metadata is expired (exp=%s); "
                        "falling back to env-configured credentials",
                        expires_at,
                    )
                    return None
            except ValueError:
                pass
        try:
            resp = httpx.get(
                # apiRoutes are mounted at /api/plugins/:pluginId/api/<path>
                # per the plugin SDK; do NOT confuse this with the
                # ctx.data.register("config", ...) endpoint at
                # /api/plugins/papervoice/config used by the Settings page.
                f"{_api_base()}/api/plugins/papervoice/api/boardroom-config",
                headers={"Authorization": f"Bearer {token}"},
                params={"companyId": company_id},
                timeout=_TIMEOUT,
            )
        except Exception:
            logger.exception(
                "boardroom-config plugin fetch failed for companyId=%s; falling back to env",
                company_id,
            )
            return None
        if resp.status_code >= 300:
            logger.warning(
                "boardroom-config plugin fetch returned status=%s for companyId=%s; "
                "falling back to env-configured credentials",
                resp.status_code,
                company_id,
            )
            return None
        try:
            body = resp.json()
        except ValueError:
            logger.error(
                "boardroom-config plugin fetch returned non-JSON body for companyId=%s; "
                "falling back to env-configured credentials",
                company_id,
            )
            return None
        api_key = body.get("boardroomApiKey")
        livekit_url = body.get("liveKitUrl")
        livekit_api_key = body.get("liveKitApiKey")
        livekit_api_secret = body.get("liveKitApiSecret")
        if not all(isinstance(v, str) and v for v in (api_key, livekit_url, livekit_api_key, livekit_api_secret)):
            logger.error(
                "boardroom-config plugin response for companyId=%s is missing fields; "
                "falling back to env-configured credentials",
                company_id,
            )
            return None
        return PluginConfigResult(
            client=cls(company_id=company_id, api_key=api_key),
            livekit=LiveKitCreds(
                url=livekit_url,
                api_key=livekit_api_key,
                api_secret=livekit_api_secret,
            ),
        )

    def agent_context(self, agent_id: str, limit: int = 5) -> list[dict]:
        """Compact open-issue list (identifier/title/status/priority) assigned to `agent_id`."""
        resp = httpx.get(
            f"{_api_base()}/api/companies/{self.company_id}/issues",
            headers=self._headers(),
            params={"assigneeAgentId": agent_id, "status": _OPEN_STATUSES},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [_compact(issue) for issue in resp.json()[:limit]]

    def company_snapshot(self, limit: int = 8) -> list[dict]:
        """Compact list of the company's highest-priority open issues (server sorts by priority).

        Used for personas with no bound Paperclip agent (see personas.py) so they still
        speak from real state instead of nothing.
        """
        resp = httpx.get(
            f"{_api_base()}/api/companies/{self.company_id}/issues",
            headers=self._headers(),
            params={"status": _OPEN_STATUSES},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [_compact(issue) for issue in resp.json()[:limit]]

    def search_issues(self, query: str, limit: int = 5) -> list[dict]:
        """Live issue lookup by identifier, title, or keyword — backs the in-call
        look_up_paperclip tool (PER-401) so an agent can read real issue state mid-call
        instead of guessing or hallucinating when asked about a task.

        Unlike agent_context/company_snapshot this does NOT filter by status: a caller
        on the call may well ask "did PER-390 ever ship?", so a closed (done/cancelled)
        match must still come back. Results carry a bounded description excerpt via
        _detail. The server ranks matches (title > identifier > description > comments).
        """
        resp = httpx.get(
            f"{_api_base()}/api/companies/{self.company_id}/issues",
            headers=self._headers(),
            params={"q": query},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [_detail(issue) for issue in resp.json()[:limit]]

    def context_briefing(self, agent_id: str | None, limit: int = 5) -> str:
        """One-line-per-issue briefing text ready to drop into an agent's turn instructions."""
        issues = self.agent_context(agent_id, limit=limit) if agent_id else self.company_snapshot(limit=limit)
        if not issues:
            return "no open issues."
        return "; ".join(f"{i['identifier']} ({i['status']}, {i['priority']}): {i['title']}" for i in issues)

    def create_issue(
        self,
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
            f"{_api_base()}/api/companies/{self.company_id}/issues",
            headers=self._headers(),
            json=payload,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        return {"identifier": body.get("identifier"), "id": body.get("id")}

    def post_comment(self, issue_id: str, body: str) -> dict:
        """Post a markdown comment (e.g. the post-call summary) to an issue.

        The comments endpoint is issue-scoped (no company in the URL), so any client
        whose api_key has access to the issue can post here — but a queued comment
        (see queue_comment/drain_pending_comments) records its owning company so a
        drain in a multi-tenant worker uses the right per-company client.
        """
        resp = httpx.post(
            f"{_api_base()}/api/issues/{issue_id}/comments",
            headers=self._headers(),
            json={"body": body},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def post_comment_or_queue(self, issue_id: str, body: str) -> dict | None:
        """Post a comment, durably queueing it if the API call fails."""
        try:
            return self.post_comment(issue_id, body)
        except Exception:
            queue_comment(issue_id, body, company_id=self.company_id)
            logger.exception("failed to post Paperclip comment to %s; queued for retry", issue_id)
            return None

    def get_plugin_config(self) -> dict:
        """Fetch the Papervoice plugin configuration from the Paperclip API.

        Returns the raw configJson dict on success, or an empty dict on 404
        (unconfigured) or 403 (caller is an agent token without board access —
        the boardroom worker authenticates as an agent and cannot reach the
        plugin-config endpoint, which requires a board session).
        Raises on any other HTTP error.
        """
        resp = httpx.get(
            f"{_api_base()}/api/plugins/papervoice/config",
            headers=self._headers(),
            params={"companyId": self.company_id},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (404, 403):
            return {}
        resp.raise_for_status()
        body = resp.json()
        return body.get("configJson") or {}

    def list_agent_ids(self) -> set[str]:
        """All agent ids in the company — used by scripts/healthcheck to catch the persona
        roster's paperclip_agent_id values drifting off the company (agent renamed/removed)."""
        resp = httpx.get(
            f"{_api_base()}/api/companies/{self.company_id}/agents",
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return {agent["id"] for agent in resp.json()}

    def get_all_agents(self) -> list[dict]:
        """All company agents with full records — used by the dashboard to render the roster.

        Each dict includes at minimum: id, name, role, title, capabilities, metadata.
        """
        resp = httpx.get(
            f"{_api_base()}/api/companies/{self.company_id}/agents",
            headers=self._headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def update_agent_voice_config(self, agent_id: str, config: dict | None) -> None:
        """PATCH an agent's metadata.papervoice settings via the Paperclip API.

        Pass a dict (with at minimum `enabled`, and `voice_id` when enabling) to
        set the voice config, or None to clear it. Requires agents:configure on the
        target agent — will 403 if the caller lacks that grant.
        """
        resp = httpx.patch(
            f"{_api_base()}/api/agents/{agent_id}",
            headers=self._headers(),
            json={"metadata": {"papervoice": config}},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

    def get_voice_enabled_agents(self) -> list[PapervoiceAgentConfig]:
        """Company agents with metadata.papervoice.enabled == True, sorted by roster_order.

        Each agent in the result has a valid voice_id. Agents missing voice_id are skipped with
        a warning — they are not ready for boardroom participation yet. Agents without
        livekit_identity or display_name get sensible defaults derived from their name.

        Called by personas.load_roster_from_paperclip() to build a live roster from real
        Paperclip agents instead of relying on the hardcoded BOARDROOM_ROSTER. See PER-89.
        """
        resp = httpx.get(
            f"{_api_base()}/api/companies/{self.company_id}/agents",
            headers=self._headers(),
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


def _boardroom_key_env_var(company_id: str) -> str:
    """Env-var name for a per-company boardroom key.

    Companyids are lowercase UUIDs; env-var names need uppercase + underscores.
    ``PAPERCLIP_BOARDROOM_API_KEY_<UUID_WITH_UNDERSCORES>`` — e.g. companyId
    ``8126b511-8dd2-4fa0-8a4f-22d630b83108`` becomes
    ``PAPERCLIP_BOARDROOM_API_KEY_8126B511_8DD2_4FA0_8A4F_22D630B83108``.
    """
    return "PAPERCLIP_BOARDROOM_API_KEY_" + company_id.upper().replace("-", "_")


@lru_cache(maxsize=64)
def _discover_company_for_key(api_key: str) -> str | None:
    """Ask Paperclip which company owns ``api_key`` by calling GET /api/agents/me.

    Powers the "just paste your pcp_ keys, we figure out the companyIds" flow
    (PAPERCLIP_BOARDROOM_API_KEYS). Returns the ``companyId`` string on success,
    or ``None`` when the key is invalid, revoked, or the API is unreachable —
    the caller skips that key so one bad entry doesn't sink the whole worker.

    Result is memoized so a worker with multiple keys resolves each companyId
    once, not on every LiveKit job dispatch. Key rotation requires a worker
    restart (the durable pcp_* key lives in the .env file).
    """
    try:
        resp = httpx.get(
            f"{_api_base()}/api/agents/me",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        logger.exception("failed to discover companyId for a PAPERCLIP_BOARDROOM_API_KEYS entry")
        return None
    company_id = payload.get("companyId") if isinstance(payload, dict) else None
    if isinstance(company_id, str) and company_id:
        return company_id
    logger.warning("GET /api/agents/me returned no companyId; skipping key")
    return None


def _parse_keys_env(raw: str) -> list[str]:
    """Split PAPERCLIP_BOARDROOM_API_KEYS into a de-duplicated ordered list.

    Accepts commas and whitespace as separators so operators can paste keys on
    one line or over several lines (env files rarely allow real newlines, but
    a shell heredoc does). Blank entries are dropped; the first occurrence of
    each key wins so log messages stay stable across restarts.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for token in raw.replace(",", " ").split():
        key = token.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def load_boardroom_key_map(
    discover_company: "callable[[str], str | None] | None" = None,
) -> dict[str, str]:
    """Build the ``companyId -> api_key`` map from env, for multi-tenant deployments (PER-405).

    Merges four sources, later ones winning on duplicates:
      1. The default single-company binding: ``PAPERCLIP_COMPANY_ID`` +
         ``PAPERCLIP_BOARDROOM_API_KEY`` (or fallback ``PAPERCLIP_API_KEY``).
         Preserved so an existing single-tenant deployment needs no config changes.
      2. Per-company env vars matching ``PAPERCLIP_BOARDROOM_API_KEY_<UUID>`` —
         convenient for adding one extra company to an existing .env.
      3. ``PAPERCLIP_BOARDROOM_API_KEYS`` — comma/whitespace-separated ``pcp_*``
         keys. The worker asks Paperclip which company each key belongs to via
         ``GET /api/agents/me`` at startup, so operators never have to look up
         (or type) a UUID. This is the recommended setup for 2+ companies.
      4. A JSON file at ``PAPERCLIP_BOARDROOM_KEYS_JSON`` shaped
         ``{"<companyId>": "<pcp_...>", ...}`` — highest priority, so it can
         rotate a specific company's key without touching the other sources.

    Returns an empty dict if no sources are configured; the caller decides whether
    that is an error (multi-tenant worker startup) or fine (dev shell that will use
    ``default_client()`` directly).

    ``discover_company`` is injectable for tests; production leaves it as the
    real HTTP call.
    """
    key_map: dict[str, str] = {}

    default_company = os.environ.get("PAPERCLIP_COMPANY_ID", "")
    default_key = os.environ.get("PAPERCLIP_BOARDROOM_API_KEY", "") or os.environ.get(
        "PAPERCLIP_API_KEY", ""
    )
    if default_company and default_key:
        key_map[default_company] = default_key

    prefix = "PAPERCLIP_BOARDROOM_API_KEY_"
    for name, value in os.environ.items():
        if not name.startswith(prefix) or not value:
            continue
        raw = name[len(prefix):]
        if not raw:
            continue
        # Reverse the encoding used by _boardroom_key_env_var: dashes were
        # turned into underscores, so turn them back. The result is
        # lowercase-with-dashes — the canonical Paperclip companyId form, whether
        # UUID or shortname. Callers must not use raw underscores in companyIds
        # (they collide with the encoding).
        company_id = raw.lower().replace("_", "-")
        key_map[company_id] = value

    keys_env = os.environ.get("PAPERCLIP_BOARDROOM_API_KEYS", "")
    if keys_env:
        discover = discover_company or _discover_company_for_key
        for api_key in _parse_keys_env(keys_env):
            company_id = discover(api_key)
            if company_id:
                key_map[company_id] = api_key
                logger.info(
                    "PAPERCLIP_BOARDROOM_API_KEYS: mapped key to companyId=%s",
                    company_id,
                )

    json_path = os.environ.get("PAPERCLIP_BOARDROOM_KEYS_JSON", "")
    if json_path:
        try:
            with open(json_path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
        except OSError:
            logger.exception("PAPERCLIP_BOARDROOM_KEYS_JSON=%s not readable", json_path)
        except ValueError:
            logger.exception("PAPERCLIP_BOARDROOM_KEYS_JSON=%s is not valid JSON", json_path)
        else:
            if isinstance(data, dict):
                for company_id, api_key in data.items():
                    if isinstance(company_id, str) and isinstance(api_key, str) and api_key:
                        key_map[company_id] = api_key

    return key_map


def default_client() -> PaperclipClient:
    """Client bound to the env-configured default company + credential.

    Used by the module-level shims (agent_context, create_issue …) and by
    single-tenant paths (healthcheck, drain-pending-posts, dev shells) that
    never see a per-job companyId.
    """
    return PaperclipClient(company_id=_default_env_company_id(), api_key=_default_env_api_key())


def client_for_company(
    company_id: str | None,
    room_metadata: dict | None = None,
) -> PaperclipClient:
    """Client for the given companyId, picking the right per-company key.

    ``company_id=None`` (or absent from the key map when it equals the default
    env company) returns ``default_client()`` — preserves the single-tenant path.
    Raises ``KeyError`` when a non-default companyId has no configured key, so
    the boardroom entrypoint fails fast at job start rather than mid-call.

    ``room_metadata`` (PER-411): when present and carrying a
    ``boardroomConfigToken``, this call tries the plugin's
    ``/boardroom-config`` route first so a company that never had a
    per-worker ``.env`` entry can still be served. Any plugin failure logs
    and falls through to the env-var lookup order below — see
    :meth:`PaperclipClient.load_from_plugin`. The plugin path also returns
    LiveKit credentials, but this shim discards them; callers that need
    those (the boardroom entrypoint) should use
    :meth:`PaperclipClient.load_from_plugin` directly.
    """
    if company_id and isinstance(room_metadata, dict):
        plugin = PaperclipClient.load_from_plugin(company_id, room_metadata)
        if plugin is not None:
            return plugin.client
        logger.info(
            "boardroom-config plugin fetch unavailable for companyId=%s; "
            "falling back to env-configured credentials",
            company_id,
        )
    if not company_id:
        return default_client()
    key_map = load_boardroom_key_map()
    api_key = key_map.get(company_id)
    if api_key:
        return PaperclipClient(company_id=company_id, api_key=api_key)
    # Nothing configured for this company but it matches the env default — fall through.
    if company_id == os.environ.get("PAPERCLIP_COMPANY_ID", ""):
        return default_client()
    raise KeyError(
        f"no boardroom API key configured for companyId={company_id!r}; "
        f"add its pcp_ key to PAPERCLIP_BOARDROOM_API_KEYS (recommended) or set "
        f"env var {_boardroom_key_env_var(company_id)} or an entry in "
        "PAPERCLIP_BOARDROOM_KEYS_JSON"
    )


# --- Module-level shims: preserve the pre-PER-405 API surface ----------------
#
# Every function below simply delegates to ``default_client()``. Callers that
# want per-job company isolation should build a PaperclipClient directly (or
# call client_for_company(company_id)) instead of using these shims. These are
# kept so scripts/healthcheck, scripts/direct-link, prompts.load_prompt_config,
# and single-tenant test suites don't need to change with the multi-tenant
# refactor.


def agent_context(agent_id: str, limit: int = 5) -> list[dict]:
    return default_client().agent_context(agent_id, limit)


def company_snapshot(limit: int = 8) -> list[dict]:
    return default_client().company_snapshot(limit)


def search_issues(query: str, limit: int = 5) -> list[dict]:
    return default_client().search_issues(query, limit)


def context_briefing(agent_id: str | None, limit: int = 5) -> str:
    # Composed from the module-level shims (not client methods) so tests that
    # monkey-patch pc.agent_context / pc.company_snapshot continue to intercept.
    issues = agent_context(agent_id, limit=limit) if agent_id else company_snapshot(limit=limit)
    if not issues:
        return "no open issues."
    return "; ".join(
        f"{i['identifier']} ({i['status']}, {i['priority']}): {i['title']}" for i in issues
    )


def create_issue(
    title: str,
    description: str = "",
    assignee_agent_id: str | None = None,
    priority: str = "medium",
) -> dict:
    return default_client().create_issue(title, description, assignee_agent_id, priority)


def post_comment(issue_id: str, body: str) -> dict:
    return default_client().post_comment(issue_id, body)


def post_comment_or_queue(issue_id: str, body: str) -> dict | None:
    # Composed from module-level post_comment/queue_comment so tests that
    # monkey-patch pc.post_comment continue to intercept.
    try:
        return post_comment(issue_id, body)
    except Exception:
        queue_comment(issue_id, body)
        logger.exception(
            "failed to post Paperclip comment to %s; queued for retry", issue_id
        )
        return None


def get_plugin_config() -> dict:
    return default_client().get_plugin_config()


def list_agent_ids() -> set[str]:
    return default_client().list_agent_ids()


def get_all_agents() -> list[dict]:
    return default_client().get_all_agents()


def update_agent_voice_config(agent_id: str, config: dict | None) -> None:
    default_client().update_agent_voice_config(agent_id, config)


def get_voice_enabled_agents() -> list[PapervoiceAgentConfig]:
    return default_client().get_voice_enabled_agents()


# --- Queued comment drain (single-tenant infra retained) ---------------------
#
# Queue files carry an optional companyId so a drainer running under a
# multi-tenant worker can post each queued comment via the correct
# per-company client. Files written before PER-405 lack that field and fall
# back to the default client — same behaviour as before.


def queue_comment(issue_id: str, body: str, company_id: str | None = None) -> Path:
    """Persist a comment for a later wake when Paperclip is unavailable."""
    directory = _pending_posts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=directory)
    path = Path(temporary).with_suffix(".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            payload: dict[str, str] = {"issue_id": issue_id, "body": body}
            if company_id:
                payload["company_id"] = company_id
            json.dump(payload, stream)
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

    Multi-tenant (PER-405): each queued file records its owning ``company_id``.
    A drainer picks the matching per-company client via ``client_for_company``,
    falling back to ``default_client`` for pre-PER-405 files without that field.
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
            queued_company = payload.get("company_id")
            if queued_company:
                try:
                    client = client_for_company(queued_company)
                except KeyError:
                    logger.exception(
                        "queued comment %s targets companyId=%s but no key is configured; "
                        "leaving it queued",
                        path.name,
                        queued_company,
                    )
                    os.replace(claim, path)
                    continue
                client.post_comment(payload["issue_id"], payload["body"])
            else:
                # Pre-PER-405 files without company_id use the module-level shim so
                # single-tenant deployments (and existing test monkey-patches of
                # pc.post_comment) continue to work unchanged.
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
