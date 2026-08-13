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
"""

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
    Persona(
        identity="agent-ops",
        display_name="Ops",
        voice_id="XrExE9yKIg1WjnnlVkGX",  # Matilda — knowledgeable, professional
        instructions="You are the Ops lead. Report on infra health, costs, and anything needing board attention."
        + _COMMON_STYLE,
        # No dedicated Ops agent yet — context_briefing() falls back to a
        # company-wide open-issue snapshot when paperclip_agent_id is None.
    ),
)


def persona_by_identity(identity: str) -> Persona | None:
    return next((p for p in BOARDROOM_ROSTER if p.identity == identity), None)
