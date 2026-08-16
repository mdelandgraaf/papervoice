"""Board persona roster for the M2/M3 boardroom.

Each persona is one LiveKit room participant with its own LLM instructions
and a distinct premade ElevenLabs voice (see docs/ARCHITECTURE.md). Voice ids
are premade voices present on the account as of 2026-08-12 — re-verify with
scripts/healthcheck after any ElevenLabs account change.

`paperclip_agent_id` (Milestone 3) binds a persona to the real Paperclip agent
it speaks for, so it can load that agent's live issue state at call start and
default follow-up issues it files to that assignee. Company org as of
2026-08-13: CEO -> Aissistent, Eng -> VoiceEngineer (this agent — the M2/M3
builder). There is no dedicated "Ops" agent yet, so that persona is left
unbound and falls back to a company-wide snapshot (see boardroom.py). Re-verify
these ids with scripts/healthcheck if agents are renamed, replaced, or hired.

PER-89: dynamic roster via `load_roster_from_paperclip()`. Reads company agents
whose `metadata.papervoice.enabled` is True, builds personas from their real
Paperclip profile (name, title, capabilities), and falls back to BOARDROOM_ROSTER
when the API is unreachable or no agents are enabled. Enabling an agent for
papervoice requires writing `metadata.papervoice: {enabled: true, voice_id: "...",
livekit_identity: "...", display_name: "...", roster_order: N}` on the agent via
PATCH /api/agents/:id (requires agents:configure on that agent).
"""

import logging
from dataclasses import dataclass

BOARDROOM_ROOM = "papervoice-boardroom"

CEO_AGENT_ID = "99228e80-fdb1-4ad2-9cba-f74b891c9b8f"  # Aissistent
ENG_AGENT_ID = "5685b8be-37ef-4bfe-8c68-7dd21ef48382"  # VoiceEngineer


@dataclass(frozen=True)
class Persona:
    identity: str
    display_name: str
    voice_id: str
    instructions: str
    paperclip_agent_id: str | None = None


_COMMON_STYLE = (
    " Be concise and conversational — one or two sentences per turn, no lists, no markdown."
    " This is a live multi-party voice call: you only speak when the moderator explicitly"
    " grants you the floor. If a human starts talking while you're mid-sentence, stop immediately."
    " If you need the board's steering or a decision before you can continue, ask the question"
    " out loud and then call the ask_board tool with that same question to wait for their answer"
    " — don't just guess or wait for the human to bring it up on their own."
)

# Instructions for the designated moderator agent. Injected verbatim when
# metadata.papervoice.moderator is True; replaces the auto-generated opener
# instructions so the moderator's character and responsibilities are explicit.
MODERATOR_INSTRUCTIONS = (
    "This is a daily standup, and you are the moderator and CEO."
    " You open the standup, keep it on time, and close it."
    " Be concise and conversational — one or two sentences per turn, no lists, no markdown."
    " This is a live multi-party voice call."
    " Report what Paperclip issues on your name you have done recently, what's still pending,"
    " what needs decisions from the board, and any blockers."
    " After your update, give the floor to another agent."
    " If you have a genuinely useful reaction — advice, a question — give it."
    " If not, call the pass_on_reacting tool and don't say anything else;"
    " don't force a comment just to fill air time."
    " If a human starts talking while you're mid-sentence, stop immediately."
    " If you need the board's steering or a decision before you can continue,"
    " ask the question out loud and then call the ask_board tool with that same question"
    " to wait for their answer — don't just guess or wait for the human to bring it up on their own."
)

BOARDROOM_ROSTER = (
    Persona(
        identity="agent-ceo",
        display_name="CEO",
        voice_id="EXAVITQu4vr4xnSDxMaL",  # Sarah — mature, reassuring, confident
        instructions="You are Aissistent, the CEO. You open the standup, keep it on time, and close it."
        + _COMMON_STYLE,
        paperclip_agent_id=CEO_AGENT_ID,
    ),
    Persona(
        identity="agent-eng",
        display_name="Eng",
        voice_id="JBFqnCBsd6RMkjVDRZzb",  # George — warm, captivating storyteller
        instructions="You are the Engineering lead. Report what shipped, what's in flight, and any blockers."
        + _COMMON_STYLE,
        paperclip_agent_id=ENG_AGENT_ID,
    ),
    # Ops persona removed from static fallback: only CEO (Aissistent) and Eng
    # (VoiceEngineer) are configured as papervoice agents in Paperclip. Adding
    # Ops here caused it to appear in calls whenever the API key expired and the
    # fallback was used, even though no Ops agent exists in the company (PER-93).
)


def persona_by_identity(identity: str) -> Persona | None:
    return next((p for p in BOARDROOM_ROSTER if p.identity == identity), None)


_logger = logging.getLogger(__name__)


def _build_instructions(config: "PapervoiceAgentConfig", is_opener: bool) -> str:
    """Construct persona instructions from a Paperclip agent's live profile fields."""
    if is_opener and config.moderator:
        return MODERATOR_INSTRUCTIONS
    intro = f"You are {config.name}"
    if config.title:
        intro += f", {config.title}"
    intro += "."
    body_parts: list[str] = []
    if config.capabilities:
        body_parts.append(config.capabilities)
    if is_opener:
        body_parts.append("You open the standup, keep it on time, and close it.")
    else:
        body_parts.append("Give your status update: what shipped, what's in flight, and any blockers.")
    body = " ".join(body_parts)
    return f"{intro} {body}" + _COMMON_STYLE


def build_persona_from_agent(config: "PapervoiceAgentConfig", is_opener: bool) -> Persona:
    """Build a Persona from a live Paperclip agent's voice config and profile.

    `is_opener` marks the agent as the standup opener/closer — they greet the
    room and run the closing sweep instead of giving a status update.
    """
    return Persona(
        identity=config.livekit_identity,
        display_name=config.display_name,
        voice_id=config.voice_id,
        instructions=_build_instructions(config, is_opener=is_opener),
        paperclip_agent_id=config.agent_id,
    )


def load_roster_from_paperclip() -> tuple[Persona, ...]:
    """Build the boardroom roster from Paperclip agents with metadata.papervoice.enabled.

    Agents are ordered by their metadata.papervoice.roster_order value; the first
    agent in that order becomes the opener/closer. Falls back to the static
    BOARDROOM_ROSTER if the Paperclip API is unreachable or no agents are enabled —
    the call must keep working even if Paperclip is down at call start.

    Enable an agent for papervoice by PATCHing its metadata (requires agents:configure):
      {"metadata": {"papervoice": {"enabled": true, "voice_id": "...",
                                   "livekit_identity": "agent-ceo",
                                   "display_name": "CEO", "roster_order": 0}}}
    """
    from papervoice.vendors import paperclip as pc_vendor  # deferred to avoid top-level env check

    try:
        configs = pc_vendor.get_voice_enabled_agents()
    except Exception:
        _logger.warning("failed to load papervoice roster from Paperclip API; using static fallback")
        return BOARDROOM_ROSTER
    if not configs:
        _logger.warning("no papervoice-enabled agents found in Paperclip; using static fallback")
        return BOARDROOM_ROSTER
    # Put the agent designated as moderator first (opener/closer); fall back to
    # roster_order position when no moderator is explicitly selected.
    moderator_cfg = next((c for c in configs if c.moderator), None)
    if moderator_cfg:
        ordered = [moderator_cfg] + [c for c in configs if c is not moderator_cfg]
    else:
        ordered = list(configs)
    return tuple(build_persona_from_agent(cfg, is_opener=(i == 0)) for i, cfg in enumerate(ordered))


# Forward-reference type alias (resolved at call time, not import time — avoids a hard
# dependency on vendors at module-load for the type hints above)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from papervoice.vendors.paperclip import PapervoiceAgentConfig
