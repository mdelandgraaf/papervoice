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
from dataclasses import dataclass, replace
from papervoice.room_presets import PRESET_ROOM_PREFIX, project_preset, read_presets

BOARDROOM_ROOM = "papervoice-boardroom"
DIRECT_ROOM_PREFIX = "papervoice-direct-"
# PER-314: custom rooms let a caller handpick which voice agents join a call —
# e.g. a marketing sync with just Marketing + CEO instead of the full boardroom.
# The chosen membership is encoded directly in the room name (dot-joined agent
# identities after this prefix), because the room name is the only channel that
# reaches the dispatched worker. This mirrors how DIRECT_ROOM_PREFIX encodes its
# single target identity, and keeps custom rooms fully stateless (no new storage).
CUSTOM_ROOM_PREFIX = "papervoice-room-"

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
    " If you're asked about the status or contents of any task and you're not certain, call the"
    " look_up_paperclip tool to check real Paperclip state instead of guessing — never make one up."
    " If you need the board's steering or a decision before you can continue, ask the question"
    " out loud and then call the ask_board tool with that same question to wait for their answer"
    " — don't just guess or wait for the human to bring it up on their own."
)

# Default instructions for the designated moderator and participant agents.
# These are used when no override is configured via the Papervoice plugin settings.
# See prompts.py for the canonical default strings and the load_prompt_config() loader.
from papervoice.prompts import DEFAULT_MODERATOR_INSTRUCTIONS, DEFAULT_PARTICIPANT_INSTRUCTIONS

MODERATOR_INSTRUCTIONS = DEFAULT_MODERATOR_INSTRUCTIONS
PARTICIPANT_INSTRUCTIONS = DEFAULT_PARTICIPANT_INSTRUCTIONS

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


def parse_custom_room_identities(room_name: str) -> tuple[str, ...] | None:
    """LiveKit identities a custom room (PER-314) selected, or None if not a custom room.

    Custom room names carry their hand-picked membership directly, dot-joined
    after CUSTOM_ROOM_PREFIX (e.g. ``papervoice-room-agent-ceo.agent-marketing``
    for a CEO + Marketing sync). Returns the identities in the order encoded, or
    None when `room_name` is not a custom room. An empty/degenerate encoding
    (just the bare prefix) also yields None so the caller can reject it.

    LiveKit identities must not contain '.', since that is the member separator —
    the plugin UI only ever emits the ``agent-<slug>``/configured identities,
    none of which use dots.
    """
    if not room_name.startswith(CUSTOM_ROOM_PREFIX):
        return None
    encoded = room_name[len(CUSTOM_ROOM_PREFIX):]
    identities = tuple(part for part in encoded.split(".") if part)
    return identities or None


def resolve_named_preset(room_name: str, config: dict, roster: tuple[Persona, ...]) -> tuple[Persona, ...] | None:
    if not room_name.startswith(PRESET_ROOM_PREFIX): return None
    preset=next((p for p in read_presets(config) if p["id"]==room_name[len(PRESET_ROOM_PREFIX):]),None)
    if preset is None: return ()
    projection=project_preset(preset,(p.paperclip_agent_id for p in roster if p.paperclip_agent_id))
    wanted=set(projection.valid_agent_ids)
    return tuple(p for p in roster if p.paperclip_agent_id in wanted)

def filter_roster(roster: tuple[Persona, ...], identities: tuple[str, ...]) -> tuple[Persona, ...]:
    """Keep only the personas whose identity is in `identities`, preserving roster order.

    Order follows the input `roster` (which load_roster_from_paperclip already
    sorts moderator-first), so the opener/closer stays valid for any subset that
    includes it — and if the moderator was not selected, the first remaining
    persona by roster order becomes the opener. Identities not present in the
    roster are silently ignored (an agent could have been disabled between when
    the link was minted and the call).
    """
    wanted = set(identities)
    return tuple(p for p in roster if p.identity in wanted)


_logger = logging.getLogger(__name__)


def _build_instructions(
    config: "PapervoiceAgentConfig",
    is_opener: bool,
    prompt_cfg: "PromptConfig | None" = None,
) -> str:
    """Select the configured system prompt for this persona's call role.

    Agent profile text must not leak into conversational instructions: the plugin
    settings are the single source of truth for moderator and participant prompts.
    """
    from papervoice.prompts import PromptConfig

    cfg = prompt_cfg or PromptConfig()
    if is_opener:
        return cfg.moderator_instructions
    return cfg.participant_instructions


def _configured_fallback_roster(prompt_cfg: "PromptConfig | None") -> tuple[Persona, ...]:
    """Apply settings prompts even when the live Paperclip roster is unavailable."""
    from papervoice.prompts import PromptConfig

    cfg = prompt_cfg or PromptConfig()
    return tuple(
        replace(
            persona,
            instructions=(cfg.moderator_instructions if index == 0 else cfg.participant_instructions),
        )
        for index, persona in enumerate(BOARDROOM_ROSTER)
    )


def build_persona_from_agent(
    config: "PapervoiceAgentConfig",
    is_opener: bool,
    prompt_cfg: "PromptConfig | None" = None,
) -> Persona:
    """Build a Persona from a live Paperclip agent's voice config and profile.

    `is_opener` marks the agent as the standup opener/closer — they greet the
    room and run the closing sweep instead of giving a status update.
    `prompt_cfg` overrides the built-in moderator/participant system prompts.
    """
    return Persona(
        identity=config.livekit_identity,
        display_name=config.display_name,
        voice_id=config.voice_id,
        instructions=_build_instructions(config, is_opener=is_opener, prompt_cfg=prompt_cfg),
        paperclip_agent_id=config.agent_id,
    )


def load_roster_from_paperclip(
    prompt_cfg: "PromptConfig | None" = None,
    client: "PaperclipClient | None" = None,
) -> tuple[Persona, ...]:
    """Build the boardroom roster from Paperclip agents with metadata.papervoice.enabled.

    Agents are ordered by their metadata.papervoice.roster_order value; the first
    agent in that order becomes the opener/closer. Falls back to the static
    BOARDROOM_ROSTER if the Paperclip API is unreachable or no agents are enabled —
    the call must keep working even if Paperclip is down at call start.

    `client` (PER-405): when the multi-tenant boardroom passes a per-company
    ``PaperclipClient`` (built from the LiveKit room-metadata companyId), the
    roster is loaded from that company. When absent, falls back to the
    env-configured default client so the single-tenant/dev path is unchanged.

    Enable an agent for papervoice by PATCHing its metadata (requires agents:configure):
      {"metadata": {"papervoice": {"enabled": true, "voice_id": "...",
                                   "livekit_identity": "agent-ceo",
                                   "display_name": "CEO", "roster_order": 0}}}
    """
    from papervoice.vendors import paperclip as pc_vendor  # deferred to avoid top-level env check

    try:
        # No client => module-level shim so existing tests that monkey-patch
        # pc_vendor.get_voice_enabled_agents continue to intercept.
        configs = client.get_voice_enabled_agents() if client else pc_vendor.get_voice_enabled_agents()
    except Exception:
        _logger.warning("failed to load papervoice roster from Paperclip API; using static fallback")
        return _configured_fallback_roster(prompt_cfg)
    if not configs:
        _logger.warning("no papervoice-enabled agents found in Paperclip; using static fallback")
        return _configured_fallback_roster(prompt_cfg)
    # Put the agent designated as moderator first (opener/closer); fall back to
    # roster_order position when no moderator is explicitly selected.
    moderator_cfg = next((c for c in configs if c.moderator), None)
    if moderator_cfg:
        ordered = [moderator_cfg] + [c for c in configs if c is not moderator_cfg]
    else:
        ordered = list(configs)
    dynamic = {cfg.agent_id: cfg for cfg in ordered}
    static_ids = {p.paperclip_agent_id for p in BOARDROOM_ROSTER}
    if not static_ids.intersection(dynamic):
        return tuple(build_persona_from_agent(cfg, is_opener=(i == 0), prompt_cfg=prompt_cfg) for i, cfg in enumerate(ordered))
    merged: list[Persona] = []
    for fallback in _configured_fallback_roster(prompt_cfg):
        cfg = dynamic.pop(fallback.paperclip_agent_id, None)
        merged.append(build_persona_from_agent(cfg, is_opener=(len(merged) == 0), prompt_cfg=prompt_cfg) if cfg else fallback)
    merged.extend(build_persona_from_agent(cfg, is_opener=False, prompt_cfg=prompt_cfg) for cfg in dynamic.values())
    return tuple(merged)


# Forward-reference type alias (resolved at call time, not import time — avoids a hard
# dependency on vendors at module-load for the type hints above)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from papervoice.vendors.paperclip import PapervoiceAgentConfig, PaperclipClient
    from papervoice.prompts import PromptConfig
