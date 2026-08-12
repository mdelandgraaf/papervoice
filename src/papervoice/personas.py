"""Board persona roster for the M2 boardroom.

Each persona is one LiveKit room participant with its own LLM instructions
and a distinct premade ElevenLabs voice (see docs/ARCHITECTURE.md). Voice ids
are premade voices present on the account as of 2026-08-12 — re-verify with
scripts/healthcheck after any ElevenLabs account change.
"""

from dataclasses import dataclass

BOARDROOM_ROOM = "papervoice-boardroom"


@dataclass(frozen=True)
class Persona:
    identity: str
    display_name: str
    voice_id: str
    instructions: str


_COMMON_STYLE = (
    " Be concise and conversational — one or two sentences per turn, no lists, no markdown."
    " This is a live multi-party voice call: you only speak when the moderator explicitly"
    " grants you the floor. If a human starts talking while you're mid-sentence, stop immediately."
)

BOARDROOM_ROSTER = (
    Persona(
        identity="agent-ceo",
        display_name="CEO",
        voice_id="EXAVITQu4vr4xnSDxMaL",  # Sarah — mature, reassuring, confident
        instructions="You are Aissistent, the CEO. You open the standup, keep it on time, and close it."
        + _COMMON_STYLE,
    ),
    Persona(
        identity="agent-eng",
        display_name="Eng",
        voice_id="JBFqnCBsd6RMkjVDRZzb",  # George — warm, captivating storyteller
        instructions="You are the Engineering lead. Report what shipped, what's in flight, and any blockers."
        + _COMMON_STYLE,
    ),
    Persona(
        identity="agent-ops",
        display_name="Ops",
        voice_id="XrExE9yKIg1WjnnlVkGX",  # Matilda — knowledgeable, professional
        instructions="You are the Ops lead. Report on infra health, costs, and anything needing board attention."
        + _COMMON_STYLE,
    ),
)


def persona_by_identity(identity: str) -> Persona | None:
    return next((p for p in BOARDROOM_ROSTER if p.identity == identity), None)
