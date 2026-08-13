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

import os

import httpx

_OPEN_STATUSES = "todo,in_progress,in_review,blocked"
_TIMEOUT = 15.0


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
