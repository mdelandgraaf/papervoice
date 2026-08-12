"""Papervoice M2: multi-agent boardroom with moderator turn-taking.

One LiveKit room, N AI agent participants (each its own persona LLM +
distinct ElevenLabs voice, see personas.py) plus humans. A single connection
runs shared STT across all human tracks and feeds the moderator; the
moderator (moderator.py) grants each agent the floor in turn and revokes it
the instant a human starts talking (barge-in). Agent sessions carry no STT/VAD
of their own — they only speak when told to, per docs/ARCHITECTURE.md
"Moderator floor control, not VAD politeness."

If one agent's turn raises (session/participant dropped), the moderator logs
it, marks that agent unavailable, and keeps the agenda moving — the call
does not go dead.

Run (from repo root, with .venv active):
    PYTHONPATH=src python -m papervoice.boardroom connect --room papervoice-boardroom
Then have humans join with:
    scripts/join-link your-name papervoice-boardroom
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from livekit import rtc
from livekit.agents import Agent, AgentSession, WorkerOptions, cli
from livekit.agents.voice import room_io
from livekit.agents.voice.events import UserInputTranscribedEvent, UserStateChangedEvent
from livekit.plugins import anthropic, silero

from papervoice.moderator import AgendaItem, Moderator, SpeakerHandle
from papervoice.personas import BOARDROOM_ROOM, BOARDROOM_ROSTER, Persona
from papervoice.vendors import elevenlabs as el_vendor
from papervoice.vendors import livekit as lk_vendor

logger = logging.getLogger("papervoice.boardroom")

TRANSCRIBER_IDENTITY = "papervoice-moderator"
AGENT_LLM_MODEL = "claude-haiku-4-5"


def _standup_agenda(roster: tuple[Persona, ...]) -> list[AgendaItem]:
    opener, *rest = roster
    items = [
        AgendaItem(
            opener.identity,
            "Open the standup: greet everyone, say this is the Papervoice milestone-two"
            f" test call, and hand it to {rest[0].display_name} for their update.",
        )
    ]
    for i, persona in enumerate(rest):
        nxt = rest[i + 1].display_name if i + 1 < len(rest) else None
        handoff = f" Then hand off to {nxt}." if nxt else f" Then hand back to {opener.display_name} to close."
        items.append(AgendaItem(persona.identity, f"Give a brief status update.{handoff}"))
    items.append(AgendaItem(opener.identity, "Close the standup — thank everyone and wrap up."))
    return items


async def _connect_agent(room_name: str, persona: Persona) -> tuple[AgentSession, rtc.Room]:
    token = lk_vendor.mint_join_token(persona.identity, room_name, ttl_hours=1, agent=True)
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    session = AgentSession(
        llm=anthropic.LLM(model=AGENT_LLM_MODEL),
        tts=el_vendor.plugin_tts(voice_id_override=persona.voice_id),
    )
    await session.start(
        agent=Agent(instructions=persona.instructions),
        room=room,
        # No STT/VAD: this agent never listens for itself. The shared
        # transcriber below is the room's only ears; the moderator feeds
        # each agent the rolling transcript as text when granting the floor.
        room_options=room_io.RoomOptions(audio_input=False, text_input=False),
    )
    return session, room


async def _connect_transcriber(room_name: str, moderator: Moderator) -> tuple[AgentSession, rtc.Room]:
    token = lk_vendor.mint_join_token(TRANSCRIBER_IDENTITY, room_name, ttl_hours=1, agent=True)
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    # ElevenLabs Scribe doesn't support streaming STT; VAD segments the human
    # audio into utterances so each one can be sent as a single batch call.
    session = AgentSession(stt=el_vendor.plugin_stt(), vad=silero.VAD.load())

    def on_transcribed(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final and ev.transcript.strip():
            moderator.record_transcript(ev.speaker_id or "human", ev.transcript.strip())

    def on_user_state_changed(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            asyncio.create_task(moderator.on_human_speech_started())

    session.on("user_input_transcribed", on_transcribed)
    session.on("user_state_changed", on_user_state_changed)

    await session.start(
        agent=Agent(instructions="You silently transcribe the room; you never speak."),
        room=room,
        room_options=room_io.RoomOptions(
            audio_input=True,
            text_input=False,
            audio_output=False,
            text_output=False,
            # Human tracks only (browser + phone dial-in) — never our own
            # agent participants. Subscribing to agent audio here would let
            # an agent's own TTS trigger barge-in against itself, exactly
            # the anti-pattern docs/ARCHITECTURE.md warns against.
            participant_kinds=[rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, rtc.ParticipantKind.PARTICIPANT_KIND_SIP],
        ),
    )
    return session, room


def _speaker_handle(identity: str, session: AgentSession, moderator: Moderator) -> SpeakerHandle:
    async def speak(prompt: str) -> None:
        context = moderator.recent_transcript_text()
        instructions = prompt if not context else f"Recent conversation:\n{context}\n\n{prompt}"
        handle = session.generate_reply(instructions=instructions, allow_interruptions=False)
        await handle.wait_for_playout()

    async def interrupt() -> None:
        # force=True: agent turns run with allow_interruptions=False (they
        # only ever speak on the moderator's command, never react to their
        # own VAD) — but a human barge-in must still cut them off immediately.
        session.interrupt(force=True)

    return SpeakerHandle(identity, speak, interrupt)


async def run_standup(room_name: str = BOARDROOM_ROOM) -> list[str]:
    """Connect every persona + the shared transcriber, then run the agenda. Returns completed identities."""
    moderator = Moderator(agenda=_standup_agenda(BOARDROOM_ROSTER), speakers={})
    sessions: list[AgentSession] = []
    rooms: list[rtc.Room] = []
    try:
        transcriber_session, transcriber_room = await _connect_transcriber(room_name, moderator)
        sessions.append(transcriber_session)
        rooms.append(transcriber_room)

        for persona in BOARDROOM_ROSTER:
            try:
                session, room = await _connect_agent(room_name, persona)
            except Exception:
                logger.exception("agent %s failed to join, continuing without it", persona.identity)
                moderator.dropped.add(persona.identity)
                continue
            sessions.append(session)
            rooms.append(room)
            moderator.add_speaker(persona.identity, _speaker_handle(persona.identity, session, moderator))

        return await moderator.run_agenda()
    finally:
        for session in sessions:
            await session.aclose()
        for room in rooms:
            await room.disconnect()


async def entrypoint(ctx) -> None:
    await ctx.connect()
    await run_standup(ctx.room.name)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
