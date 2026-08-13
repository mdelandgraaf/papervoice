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
DEFAULT_BARGE_IN_REPLY_TIMEOUT_SECONDS = 8.0
DEFAULT_OPEN_FLOOR_SECONDS = 20.0


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
    Barge-in answers, not just barge-in silence: stopping TTS on human speech
    is not enough on its own — a barge-in that only revokes the floor leaves
    the human talking to a wall once their question is done. `_run_turn`
    detects a turn a human just cut off and routes to `_respond_to_barge_in`,
    which grants the floor back to that same agent to actually answer before
    the scripted agenda resumes (see PER-75 board feedback).
    The call doesn't hang up the instant the script ends, either: once the
    agenda is exhausted, `run_agenda` holds the floor open for
    `open_floor_seconds` so a human who wants to speak up after the closing
    line finishes — not mid-sentence, i.e. not a barge-in — still gets heard
    before every session gets torn down (see PER-75 board feedback: "suddenly
    they all left the chat").
    """

    def __init__(
        self,
        agenda: list[AgendaItem],
        speakers: dict[str, SpeakerHandle],
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
        barge_in_reply_timeout_seconds: float = DEFAULT_BARGE_IN_REPLY_TIMEOUT_SECONDS,
        open_floor_seconds: float = DEFAULT_OPEN_FLOOR_SECONDS,
    ) -> None:
        self._agenda = list(agenda)
        self._speakers = dict(speakers)
        self._turn_timeout_seconds = turn_timeout_seconds
        self._barge_in_reply_timeout_seconds = barge_in_reply_timeout_seconds
        self._open_floor_seconds = open_floor_seconds
        self.current_speaker: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.dropped: set[str] = set()
        # Paperclip issues filed live during the call (see boardroom.py's
        # file_followup_issue tool), for the post-call summary comment.
        self.filed_issues: list[tuple[str, str]] = []
        # Set the instant a barge-in cuts an agent off; cleared once that
        # agent has answered whatever the human said (or the wait times out).
        # Lets record_transcript() and _run_turn() recognize "this final
        # human line is the question that just interrupted someone" without
        # the two ever being causally ordered by a single await chain.
        self._awaiting_reply_to: str | None = None
        self._human_reply_ready = asyncio.Event()
        self._pending_reply_text: str | None = None

    def add_speaker(self, identity: str, handle: SpeakerHandle) -> None:
        self._speakers[identity] = handle

    def record_filed_issue(self, identifier: str, title: str) -> None:
        """Record a Paperclip issue filed live during the call, for the post-call summary."""
        self.filed_issues.append((identifier, title))

    def record_transcript(self, speaker_identity: str, text: str) -> None:
        """Append a line to the shared transcript every agent's next turn sees as context."""
        self.transcript.append((speaker_identity, text))
        if self._awaiting_reply_to is not None and speaker_identity not in self._speakers and speaker_identity != "moderator":
            # This is the finished utterance from whoever just barged in —
            # wake up _respond_to_barge_in() waiting on it.
            self._pending_reply_text = text
            self._human_reply_ready.set()

    def recent_transcript_text(self, max_lines: int = 20) -> str:
        lines = self.transcript[-max_lines:]
        return "\n".join(f"{who}: {said}" for who, said in lines)

    async def on_human_speech_started(self) -> None:
        """Barge-in: a human started talking. Revoke the floor and cancel agent TTS immediately."""
        if self.current_speaker is None:
            return
        dropped_speaker = self.current_speaker
        self.current_speaker = None
        self._awaiting_reply_to = dropped_speaker
        self._human_reply_ready.clear()
        speaker = self._speakers.get(dropped_speaker)
        if speaker is not None:
            await speaker.interrupt()
        logger.info("barge-in: revoked floor from %s", dropped_speaker)

    async def run_agenda(self) -> list[str]:
        """Run the standup agenda in order, then hold the floor open briefly
        for a final human question before the call ends. Returns the
        identities that completed their turn."""
        completed = []
        for item in self._agenda:
            if await self._run_turn(item):
                completed.append(item.identity)
        if completed:
            await self._hold_open_floor(completed[-1])
        return completed

    async def _hold_open_floor(self, responder_identity: str) -> None:
        """Give a human `open_floor_seconds` to ask something after the
        scripted agenda ends, instead of tearing every session down the
        instant the closing line finishes (see PER-75 board feedback:
        "suddenly they all left the chat"). Arms the same
        `_awaiting_reply_to`/`_respond_to_barge_in` machinery a mid-agenda
        barge-in uses, so any human utterance that lands in this window gets
        a real answer from `responder_identity` before the call actually ends.
        """
        if responder_identity in self.dropped or responder_identity not in self._speakers:
            return
        self._awaiting_reply_to = responder_identity
        self._human_reply_ready.clear()
        await self._respond_to_barge_in(responder_identity, reply_timeout_seconds=self._open_floor_seconds)

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
        if self._awaiting_reply_to == item.identity:
            # This turn is the one a human just barged in on: stopping the
            # TTS isn't enough on its own — the human is left talking to a
            # wall unless someone actually answers before the script resumes.
            await self._respond_to_barge_in(item.identity)
        return True

    async def _respond_to_barge_in(
        self, responder_identity: str, *, reply_timeout_seconds: float | None = None
    ) -> None:
        """A barge-in just cut ``responder_identity`` off mid-turn (or the
        agenda just ended and the floor is being held open for a final
        question — see `_hold_open_floor`). Wait briefly for the human's
        finished utterance and have that same agent answer it — looping if
        the human interrupts the answer too — before the agenda resumes its
        scripted line (or the call ends).
        """
        timeout = reply_timeout_seconds if reply_timeout_seconds is not None else self._barge_in_reply_timeout_seconds
        while True:
            try:
                await asyncio.wait_for(self._human_reply_ready.wait(), timeout=timeout)
            except TimeoutError:
                logger.warning(
                    "no finished human utterance within %.0fs; giving up",
                    timeout,
                )
                self._awaiting_reply_to = None
                return

            question = self._pending_reply_text
            self._awaiting_reply_to = None
            self._human_reply_ready.clear()
            if responder_identity in self.dropped:
                return
            speaker = self._speakers.get(responder_identity)
            if speaker is None:
                return

            self.current_speaker = responder_identity
            try:
                await asyncio.wait_for(
                    speaker.speak(f'Someone just asked: "{question}" Answer them directly and briefly, then continue.'),
                    timeout=self._turn_timeout_seconds,
                )
            except TimeoutError:
                logger.error("agent %s timed out answering a human question, dropping", responder_identity)
                self.dropped.add(responder_identity)
                self.record_transcript("moderator", f"{responder_identity} timed out, moving on")
                return
            except Exception:
                logger.exception("agent %s dropped answering a human question", responder_identity)
                self.dropped.add(responder_identity)
                self.record_transcript("moderator", f"{responder_identity} dropped, moving on")
                return
            finally:
                if self.current_speaker == responder_identity:
                    self.current_speaker = None

            if self._awaiting_reply_to != responder_identity:
                return  # answered cleanly, no further barge-in on the answer itself
