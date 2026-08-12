"""Server-side moderator: floor control + standup agenda for the M2 boardroom.

Kept independent of LiveKit/ElevenLabs specifics (no room/session imports) so
the turn-taking and barge-in state machine is unit-testable without a live
room. boardroom.py wires this to real AgentSessions via SpeakerHandle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger("papervoice.moderator")

DEFAULT_TURN_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class AgendaItem:
    identity: str
    prompt: str


class SpeakerHandle:
    """What the moderator needs from one agent's live session: speak on command, stop on command."""

    def __init__(
        self,
        identity: str,
        speak: Callable[[str], Awaitable[None]],
        interrupt: Callable[[], Awaitable[None]],
    ) -> None:
        self.identity = identity
        self._speak = speak
        self._interrupt = interrupt

    async def speak(self, prompt: str) -> None:
        await self._speak(prompt)

    async def interrupt(self) -> None:
        await self._interrupt()


class Moderator:
    """Holds the speaker token, runs the agenda, and preempts on human barge-in.

    Turn-taking discipline: agents only ever speak from inside `run_agenda`,
    triggered by the moderator granting the floor — never from their own VAD.
    Graceful degradation: an agent whose turn raises OR never finishes inside
    `turn_timeout_seconds` (session dropped, or a hung TTS/network call — seen
    live during the M2 build, see docs/ARCHITECTURE.md) is marked dropped and
    skipped; the agenda keeps moving for everyone else.
    """

    def __init__(
        self,
        agenda: list[AgendaItem],
        speakers: dict[str, SpeakerHandle],
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
    ) -> None:
        self._agenda = list(agenda)
        self._speakers = dict(speakers)
        self._turn_timeout_seconds = turn_timeout_seconds
        self.current_speaker: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.dropped: set[str] = set()

    def add_speaker(self, identity: str, handle: SpeakerHandle) -> None:
        self._speakers[identity] = handle

    def record_transcript(self, speaker_identity: str, text: str) -> None:
        """Append a line to the shared transcript every agent's next turn sees as context."""
        self.transcript.append((speaker_identity, text))

    def recent_transcript_text(self, max_lines: int = 20) -> str:
        lines = self.transcript[-max_lines:]
        return "\n".join(f"{who}: {said}" for who, said in lines)

    async def on_human_speech_started(self) -> None:
        """Barge-in: a human started talking. Revoke the floor and cancel agent TTS immediately."""
        if self.current_speaker is None:
            return
        dropped_speaker = self.current_speaker
        self.current_speaker = None
        speaker = self._speakers.get(dropped_speaker)
        if speaker is not None:
            await speaker.interrupt()
        logger.info("barge-in: revoked floor from %s", dropped_speaker)

    async def run_agenda(self) -> list[str]:
        """Run the standup agenda in order. Returns the identities that completed their turn."""
        completed = []
        for item in self._agenda:
            if await self._run_turn(item):
                completed.append(item.identity)
        return completed

    async def _run_turn(self, item: AgendaItem) -> bool:
        if item.identity in self.dropped:
            logger.warning("skipping %s: already dropped this call", item.identity)
            self.record_transcript("moderator", f"{item.identity} is unavailable, moving on")
            return False
        speaker = self._speakers.get(item.identity)
        if speaker is None:
            logger.warning("skipping %s: never joined", item.identity)
            self.record_transcript("moderator", f"{item.identity} never joined, moving on")
            return False
        self.current_speaker = item.identity
        try:
            await asyncio.wait_for(speaker.speak(item.prompt), timeout=self._turn_timeout_seconds)
        except TimeoutError:
            logger.error("agent %s timed out mid-turn (>%.0fs), dropping", item.identity, self._turn_timeout_seconds)
            self.dropped.add(item.identity)
            self.record_transcript("moderator", f"{item.identity} timed out, moving on")
            return False
        except Exception:
            logger.exception("agent %s dropped mid-turn", item.identity)
            self.dropped.add(item.identity)
            self.record_transcript("moderator", f"{item.identity} dropped, moving on")
            return False
        finally:
            if self.current_speaker == item.identity:
                self.current_speaker = None
        return True
