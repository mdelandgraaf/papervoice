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
# Kept as an optional test/compatibility escape hatch. Production passes None:
# the board, not silence, decides when the call is over (PER-162).
DEFAULT_OPEN_FLOOR_SECONDS: float | None = None
DEFAULT_ASK_AND_WAIT_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class AgendaItem:
    identity: str
    prompt: str
    # "turn" (default): a scripted open/status-update/close turn. "reaction":
    # an optional cross-talk turn where `identity` may briefly comment on the
    # previous speaker's update (see boardroom._standup_agenda) — purely
    # descriptive metadata for agenda construction/tests; the moderator's
    # turn-running logic (_run_turn/_speak) treats both the same way, since an
    # empty/tool-only reply already behaves as a no-op "pass" (see _speak).
    kind: str = "turn"


class SpeakerHandle:
    """What the moderator needs from one agent's live session: speak on command, stop on command."""

    def __init__(
        self,
        identity: str,
        speak: Callable[[str], Awaitable[str | None]],
        interrupt: Callable[[], Awaitable[None]],
        leave: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.identity = identity
        self._speak = speak
        self._interrupt = interrupt
        self._leave = leave

    async def speak(self, prompt: str) -> str | None:
        """Speak `prompt` and return the text actually said, if known (used to
        feed the moderator's rolling transcript). None if the caller doesn't
        track it (e.g. plain test doubles)."""
        return await self._speak(prompt)

    async def interrupt(self) -> None:
        await self._interrupt()

    async def leave(self) -> None:
        if self._leave is not None:
            await self._leave()


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
    Answering isn't finishing: answering the human's question is a different
    turn from the scripted update that got cut off, and the agenda used to
    move straight to the next agenda item afterward — silently dropping
    whatever the interrupted agent hadn't said yet (confirmed live on a PER-76
    board test call: "I think Eng wasn't finished with his update"). After
    the barge-in answer, `_run_turn` now grants the same agent one more turn
    to finish the original prompt before moving on.
    The call does not hang up when the script ends: once the agenda is exhausted,
    `run_agenda` keeps the floor open until the last human leaves. Silence is
    never a departure signal. A sentence-level imperative naming one agent may
    dismiss only that agent (PER-162).
    Steering isn't only human-initiated: everything above fires on a human's
    own initiative (interrupting mid-turn, or speaking up once the agenda
    ends). `_ask_and_wait` is the agent-initiated mirror — a persona's own
    turn can pose a question and hold the floor open for a human reply before
    continuing, the same hold-the-floor state machine generalized to fire on
    demand (see PER-83 board feedback).
    """

    def __init__(
        self,
        agenda: list[AgendaItem],
        speakers: dict[str, SpeakerHandle],
        turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
        barge_in_reply_timeout_seconds: float = DEFAULT_BARGE_IN_REPLY_TIMEOUT_SECONDS,
        open_floor_seconds: float | None = DEFAULT_OPEN_FLOOR_SECONDS,
        ask_and_wait_timeout_seconds: float = DEFAULT_ASK_AND_WAIT_TIMEOUT_SECONDS,
        addressee_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._agenda = list(agenda)
        self._speakers = dict(speakers)
        self._turn_timeout_seconds = turn_timeout_seconds
        self._barge_in_reply_timeout_seconds = barge_in_reply_timeout_seconds
        self._open_floor_seconds = open_floor_seconds
        self._ask_and_wait_timeout_seconds = ask_and_wait_timeout_seconds
        # Resolve a human utterance to the identity of the specific agent it
        # addresses by name (e.g. "Eng, what's blocking you?" -> "agent-eng"),
        # or None when it names no one. Injected by boardroom.py from the live
        # roster so the moderator stays LiveKit/persona-agnostic. Without it,
        # addressing is a no-op and answers route to the interrupted/holding
        # agent exactly as before (PER-293).
        self._addressee_resolver = addressee_resolver
        self.current_speaker: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.dropped: set[str] = set()
        self.dismissed: set[str] = set()
        self._call_ended = asyncio.Event()
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
        self._human_speech_stopped = asyncio.Event()
        self._human_speech_stopped.set()
        self._human_speaking = False
        self._pending_reply_text: str | None = None

    def add_speaker(self, identity: str, handle: SpeakerHandle) -> None:
        self._speakers[identity] = handle

    def _addressed_available_agent(self, text: str | None) -> str | None:
        """Identity of the specific agent a human utterance addresses by name,
        but only when that agent is actually present and able to answer.

        Returns None when no resolver is configured, the text names no one, or
        the named agent has been dropped/dismissed/never joined — callers then
        fall back to their default responder (the interrupted agent, or the
        floor-holder). Never redirects a question into a dead end.
        """
        if not text or self._addressee_resolver is None:
            return None
        identity = self._addressee_resolver(text)
        if identity is None or identity in self.dropped or identity not in self._speakers:
            return None
        return identity

    async def dismiss_speaker(self, identity: str) -> bool:
        """Remove only the explicitly named agent from the live conversation."""
        speaker = self._speakers.pop(identity, None)
        if speaker is None:
            return False
        self.dismissed.add(identity)
        if self.current_speaker == identity:
            self.current_speaker = None
            await speaker.interrupt()
        if self._awaiting_reply_to == identity:
            self._awaiting_reply_to = None
            self._human_reply_ready.set()
        await speaker.leave()
        return True

    def end_call(self) -> None:
        """End the call because the last human participant left."""
        self._call_ended.set()
        self._human_reply_ready.set()
        self._human_speech_stopped.set()

    def record_filed_issue(self, identifier: str, title: str) -> None:
        """Record a Paperclip issue filed live during the call, for the post-call summary."""
        self.filed_issues.append((identifier, title))

    def record_transcript(self, speaker_identity: str, text: str) -> None:
        """Append a line to the shared transcript every agent's next turn sees as context."""
        self.transcript.append((speaker_identity, text))
        if speaker_identity not in self._speakers and speaker_identity != "moderator":
            # A final human transcript is also an authoritative utterance boundary.
            self.on_human_speech_stopped()
        if self._awaiting_reply_to is not None and speaker_identity not in self._speakers and speaker_identity != "moderator":
            # This is the finished utterance from whoever just barged in —
            # wake up _respond_to_barge_in() waiting on it.
            self._pending_reply_text = text
            self._human_reply_ready.set()

    def recent_transcript_text(self, max_lines: int = 20) -> str:
        lines = self.transcript[-max_lines:]
        return "\n".join(f"{who}: {said}" for who, said in lines)

    def full_transcript_text(self) -> str:
        """Every recorded line, unlike recent_transcript_text()'s rolling window — used for
        the post-call summary so a board member can read the whole meeting, not just a tail
        (see PER-76 board feedback: "is there a way to view the transcription?")."""
        return "\n".join(f"{who}: {said}" for who, said in self.transcript)

    async def on_human_speech_started(self) -> None:
        """Barge-in: a human started talking. Revoke the floor and cancel agent TTS immediately."""
        # Keep the post-agenda inactivity deadline from hanging up on a
        # human who started talking just before it expired (PER-162).
        self._human_speaking = True
        self._human_speech_stopped.clear()
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

    def on_human_speech_stopped(self) -> None:
        """Mark the end of live human speech so quiet-time waits can resume."""
        self._human_speaking = False
        self._human_speech_stopped.set()

    async def run_agenda(self) -> list[str]:
        """Run the standup agenda in order, then hold the floor open until the
        human leaves the call. Returns the
        identities that completed their turn."""
        completed = []
        for item in self._agenda:
            if await self._run_turn(item):
                completed.append(item.identity)
        if completed:
            await self._hold_open_floor(completed[-1])
        return completed

    async def _hold_open_floor(self, responder_identity: str) -> None:
        """Keep the post-agenda discussion open until the human leaves.

        A finite ``open_floor_seconds`` remains available only for focused tests;
        production uses no inactivity deadline (PER-162).
        """
        while not self._call_ended.is_set():
            if responder_identity in self.dropped or responder_identity not in self._speakers:
                available = [identity for identity in self._speakers if identity not in self.dropped]
                if not available:
                    return
                responder_identity = available[0]
            self._awaiting_reply_to = responder_identity
            self._human_reply_ready.clear()
            answered = await self._respond_to_barge_in(
                responder_identity,
                reply_timeout_seconds=self._open_floor_seconds,
                wait_until_call_ends=self._open_floor_seconds is None,
            )
            if not answered and self._open_floor_seconds is not None:
                return
            # Deliberately do NOT pin `responder_identity` to whoever just
            # answered. A *named* question is already redirected to that agent
            # for its own answer, per-utterance, inside _respond_to_barge_in.
            # The open-floor default must stay the moderator/closer so an
            # unnamed question ("what's next?") keeps going to them — otherwise
            # naming another agent once silently hands the whole rest of the
            # open floor to that agent and the moderator never speaks again
            # (PER-293 regression: "the ceo stayed silent after referring to
            # the other agent").

    async def _ask_and_wait(self, identity: str, question: str, timeout: float | None = None) -> str | None:
        """Agent-initiated steering ask (PER-83): let `identity` pose `question`
        mid-turn and hold the floor open for a human reply before continuing,
        instead of steering only ever flowing human-in (barge-in) the way
        `_hold_open_floor`/`_respond_to_barge_in` do. This is the same
        hold-the-floor state machine those use, generalized so any turn can
        trigger it on demand rather than only after a barge-in or at meeting
        end — boardroom.py exposes it as a per-persona `ask_board` function
        tool the LLM decides to call mid-turn.

        Unlike `_respond_to_barge_in`, this doesn't itself grant the agent a
        follow-up speaking turn to voice an answer — it just records the
        question, waits, and hands the raw reply text back to the caller
        (the in-flight LLM turn, via the tool result) to react to however it
        sees fit. Records `question` on the shared transcript itself, since
        the turn asking it hasn't finished (and so hasn't recorded its own
        spoken text via `_speak`) by the time this call needs the transcript
        to reflect it.

        Returns the human's reply text, or None if `identity` was already
        dropped/never joined, or no finished utterance arrived within
        `timeout` (defaults to `ask_and_wait_timeout_seconds`) — callers must
        treat None as "no answer" and move on; this never hangs the agenda.
        """
        if identity in self.dropped or identity not in self._speakers:
            return None
        self.record_transcript(identity, question)
        wait_timeout = timeout if timeout is not None else self._ask_and_wait_timeout_seconds
        self._awaiting_reply_to = identity
        self._human_reply_ready.clear()
        try:
            await asyncio.wait_for(self._human_reply_ready.wait(), timeout=wait_timeout)
        except TimeoutError:
            logger.warning("ask_and_wait: no reply from a human within %.0fs for %s", wait_timeout, identity)
            return None
        finally:
            if self._awaiting_reply_to == identity:
                self._awaiting_reply_to = None
        return self._pending_reply_text

    async def _run_turn(self, item: AgendaItem) -> bool:
        if item.identity in self.dropped:
            logger.warning("skipping %s: already dropped this call", item.identity)
            self.record_transcript("moderator", f"{item.identity} is unavailable, moving on")
            return False
        if item.identity not in self._speakers:
            logger.warning("skipping %s: never joined", item.identity)
            self.record_transcript("moderator", f"{item.identity} never joined, moving on")
            return False

        if not await self._speak(item.identity, item.prompt):
            return False

        if self._awaiting_reply_to == item.identity:
            # This turn is the one a human just barged in on: stopping the
            # TTS isn't enough on its own — the human is left talking to a
            # wall unless someone actually answers before the script resumes.
            await self._respond_to_barge_in(item.identity)
            if item.identity not in self.dropped and item.identity in self._speakers:
                # Answering the human's question is a different turn from the
                # scripted update that got cut off — it doesn't get the rest
                # of that update said. Give the same agent one more turn to
                # finish it before the agenda moves on (see PER-76 board
                # feedback: "I think Eng wasn't finished with his update").
                await self._speak(
                    item.identity,
                    f"{item.prompt}\n\nYou were interrupted before finishing this — pick up"
                    " where you left off and finish it now. Don't repeat anything you already"
                    " said.",
                )
        return True

    async def _speak(self, identity: str, prompt: str) -> bool:
        """Grant `identity` the floor for one utterance, recording what it
        said (if the SpeakerHandle reports it) to the shared transcript.
        Returns False only when the turn had to be abandoned outright
        (timeout or a dropped session) — an ordinary barge-in still returns
        True, since the moderator handles that as a separate concern.
        """
        speaker = self._speakers[identity]
        self.current_speaker = identity
        try:
            spoken = await asyncio.wait_for(speaker.speak(prompt), timeout=self._turn_timeout_seconds)
        except TimeoutError:
            logger.error("agent %s timed out mid-turn (>%.0fs), dropping", identity, self._turn_timeout_seconds)
            self.dropped.add(identity)
            self.record_transcript("moderator", f"{identity} timed out, moving on")
            return False
        except Exception:
            logger.exception("agent %s dropped mid-turn", identity)
            self.dropped.add(identity)
            self.record_transcript("moderator", f"{identity} dropped, moving on")
            return False
        finally:
            if self.current_speaker == identity:
                self.current_speaker = None
        if spoken:
            self.record_transcript(identity, spoken)
        return True

    async def _respond_to_barge_in(
        self, responder_identity: str, *, reply_timeout_seconds: float | None = None, wait_until_call_ends: bool = False
    ) -> str | bool:
        """A barge-in just cut ``responder_identity`` off mid-turn (or the
        agenda just ended and the floor is being held open for a final
        question — see `_hold_open_floor`). Wait briefly for the human's
        finished utterance and have an agent answer it — looping if the human
        interrupts the answer too — before returning control to the caller
        (which, mid-agenda, gives the interrupted agent a further turn to
        finish whatever it was originally saying — see `_run_turn`).

        ``responder_identity`` is only the default answerer. When the finished
        human utterance addresses a specific agent by name (e.g. "Eng, what's
        blocking you?"), that agent answers instead — routing the question to
        who it was actually aimed at rather than whoever happened to hold the
        floor (PER-293). Returns the identity that answered on a clean reply
        (so `_hold_open_floor` can keep a natural back-and-forth going with the
        same agent), or False when nothing was answered.
        """
        timeout = reply_timeout_seconds if reply_timeout_seconds is not None else self._barge_in_reply_timeout_seconds
        while True:
            try:
                if wait_until_call_ends:
                    reply_wait = asyncio.create_task(self._human_reply_ready.wait())
                    end_wait = asyncio.create_task(self._call_ended.wait())
                    done, pending = await asyncio.wait(
                        {reply_wait, end_wait}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    if end_wait in done:
                        return False
                else:
                    await asyncio.wait_for(self._human_reply_ready.wait(), timeout=timeout)
            except TimeoutError:
                if reply_timeout_seconds is not None and self._human_speaking:
                    logger.info("human still speaking at inactivity deadline; keeping floor open")
                    await self._human_speech_stopped.wait()
                    continue
                if reply_timeout_seconds is None:
                    # Ordinary barge-in waits retain their existing bounded behavior.
                    self.on_human_speech_stopped()
                logger.warning(
                    "no finished human utterance within %.0fs; giving up",
                    timeout,
                )
                self._awaiting_reply_to = None
                return False

            question = self._pending_reply_text
            self._awaiting_reply_to = None
            self._human_reply_ready.clear()
            if self._call_ended.is_set():
                return False
            # Route to the agent the human named, if any is present; otherwise
            # the default responder (interrupted agent / floor-holder) answers.
            named = self._addressed_available_agent(question)
            responder_identity = named or responder_identity
            logger.info(
                "barge-in reply routing: text=%r -> %s (%s)",
                question,
                responder_identity,
                "addressed by name" if named else "default responder",
            )
            if responder_identity in self.dropped or responder_identity not in self._speakers:
                return False

            if not await self._speak(
                responder_identity,
                f'Someone just asked: "{question}" Answer them directly and briefly, then continue.',
            ):
                return False

            if self._awaiting_reply_to != responder_identity:
                return responder_identity  # answered cleanly, no further barge-in on the answer itself
