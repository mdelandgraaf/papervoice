"""Unit tests for the server-side moderator floor-control state machine.

No LiveKit/ElevenLabs involved — SpeakerHandle is driven by plain async
callables, exactly as boardroom.py wires it to real AgentSessions.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.moderator import AgendaItem, Moderator as _Moderator, SpeakerHandle


def Moderator(*args, **kwargs):
    """Test default: a near-zero open floor window unless a test overrides it.

    Prevents every agenda-completion test from silently waiting out the real
    ``DEFAULT_OPEN_FLOOR_SECONDS`` (20s) for a human question that never
    comes — only ModeratorOpenFloorTest cares about that window's length.
    """
    kwargs.setdefault("open_floor_seconds", 0.05)
    return _Moderator(*args, **kwargs)


def make_speaker(identity, log, raise_on_speak=False):
    async def speak(prompt):
        if raise_on_speak:
            raise RuntimeError("session dropped")
        log.append((identity, prompt))

    async def interrupt():
        log.append((identity, "INTERRUPTED"))

    return SpeakerHandle(identity, speak, interrupt)


def make_interruptible_speaker(identity, log):
    """speak() blocks (like real TTS playout) on its first call until interrupt()
    is called, simulating a human barging in mid-turn; later calls (e.g. the
    follow-up answer) return immediately, like an uninterrupted turn."""
    unblocked = asyncio.Event()
    calls = 0

    async def speak(prompt):
        nonlocal calls
        calls += 1
        log.append((identity, prompt))
        if calls == 1:
            await unblocked.wait()
            unblocked.clear()

    async def interrupt():
        unblocked.set()

    return SpeakerHandle(identity, speak, interrupt)


def make_interruptible_texting_speaker(identity, log):
    """Like make_interruptible_speaker, but speak() also reports back what it
    'said' (as a real SpeakerHandle does via boardroom.py's _spoken_text) —
    exercises Moderator._speak recording the agent's own turns, including a
    barge-in continuation, into the shared transcript."""
    unblocked = asyncio.Event()
    calls = 0

    async def speak(prompt):
        nonlocal calls
        calls += 1
        log.append((identity, prompt))
        if calls == 1:
            await unblocked.wait()
            unblocked.clear()
        return f"{identity}-said-call-{calls}"

    async def interrupt():
        unblocked.set()

    return SpeakerHandle(identity, speak, interrupt)


class ModeratorAgendaTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_agenda_in_order(self):
        log = []
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        speakers = {"ceo": make_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers)

        completed = await moderator.run_agenda()

        self.assertEqual(completed, ["ceo", "eng"])
        self.assertEqual(log, [("ceo", "open"), ("eng", "update")])
        self.assertIsNone(moderator.current_speaker)

    async def test_only_one_speaker_holds_the_floor_at_a_time(self):
        log = []
        seen_concurrent_speaker = None

        async def speak(prompt):
            nonlocal seen_concurrent_speaker
            seen_concurrent_speaker = moderator.current_speaker
            log.append(prompt)

        speakers = {"ceo": SpeakerHandle("ceo", speak, lambda: log.append("interrupt"))}
        moderator = Moderator([AgendaItem("ceo", "open")], speakers)

        await moderator.run_agenda()

        self.assertEqual(seen_concurrent_speaker, "ceo")


class ModeratorBargeInTest(unittest.IsolatedAsyncioTestCase):
    async def test_human_speech_interrupts_current_speaker_and_revokes_floor(self):
        log = []
        moderator = Moderator([], {"ceo": make_speaker("ceo", log)})
        moderator.current_speaker = "ceo"

        await moderator.on_human_speech_started()

        self.assertIsNone(moderator.current_speaker)
        self.assertEqual(log, [("ceo", "INTERRUPTED")])

    async def test_no_op_when_nobody_holds_the_floor(self):
        log = []
        moderator = Moderator([], {"ceo": make_speaker("ceo", log)})

        await moderator.on_human_speech_started()  # must not raise or interrupt anyone

        self.assertEqual(log, [])


class ModeratorBargeInResponseTest(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for the board's PER-75 feedback: barge-in correctly
    stops an agent's TTS, but nothing then answered the human's question — the
    agenda just resumed its next scripted line."""

    async def test_interrupted_agent_answers_the_question_before_the_agenda_continues(self):
        log = []
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        speakers = {"ceo": make_interruptible_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers)

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)  # let ceo's "open" turn start and block on speak()
        self.assertEqual(moderator.current_speaker, "ceo")

        await moderator.on_human_speech_started()  # barge-in: interrupts ceo, revokes the floor
        moderator.record_transcript("board-member", "what's our runway?")

        completed = await asyncio.wait_for(task, timeout=1)

        answer_prompt = 'Someone just asked: "what\'s our runway?" Answer them directly and briefly, then continue.'
        self.assertEqual(completed, ["ceo", "eng"])
        self.assertIn(("ceo", answer_prompt), log)
        self.assertLess(log.index(("ceo", "open")), log.index(("ceo", answer_prompt)))
        self.assertLess(log.index(("ceo", answer_prompt)), log.index(("eng", "update")))
        self.assertIsNone(moderator.current_speaker)

    async def test_interrupted_agent_gets_a_further_turn_to_finish_the_original_update(self):
        """PER-76 board feedback from a live test call: answering the human's
        question is a different turn from the scripted update that got cut
        off — the agenda used to move straight to the next speaker
        afterward, silently dropping whatever the interrupted agent hadn't
        said yet ("I think Eng wasn't finished with his update")."""
        log = []
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        speakers = {"ceo": make_interruptible_texting_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers)

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)  # let ceo's "open" turn start and block on speak()
        self.assertEqual(moderator.current_speaker, "ceo")

        await moderator.on_human_speech_started()  # barge-in: interrupts ceo, revokes the floor
        moderator.record_transcript("board-member", "what's our runway?")

        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo", "eng"])
        ceo_prompts = [prompt for who, prompt in log if who == "ceo"]
        # original "open" turn, the barge-in answer, and one more turn to finish "open"
        self.assertEqual(len(ceo_prompts), 3)
        self.assertEqual(ceo_prompts[0], "open")
        self.assertIn("what's our runway?", ceo_prompts[1])
        self.assertTrue(ceo_prompts[2].startswith("open\n\n"))
        self.assertIn("interrupted before finishing", ceo_prompts[2])
        # order: answer, then resume, then eng's own turn — never resumed after eng starts
        resume_index = next(i for i, (who, p) in enumerate(log) if who == "ceo" and p == ceo_prompts[2])
        self.assertLess(log.index(("ceo", ceo_prompts[1])), resume_index)
        self.assertLess(resume_index, log.index(("eng", "update")))
        self.assertIsNone(moderator.current_speaker)
        # the resumed turn's spoken text lands in the shared transcript too,
        # so a further barge-in continuation (or the post-call summary) has
        # real content to work from, not just the human's side of it.
        self.assertIn(("ceo", "ceo-said-call-3"), moderator.transcript)

    async def test_no_finished_utterance_within_timeout_resumes_agenda_without_hanging(self):
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        log = []
        speakers = {"ceo": make_interruptible_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers, barge_in_reply_timeout_seconds=0.05)

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)
        await moderator.on_human_speech_started()
        # no record_transcript() call — the human's utterance never finishes transcribing

        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo", "eng"])
        self.assertNotIn("ceo", moderator.dropped)

    async def test_agent_track_speech_never_counts_as_the_barge_in_question(self):
        """record_transcript() is also how an agent's own speech gets logged
        (via Moderator._speak, whenever a SpeakerHandle reports what it said) —
        a speaker identity, never a human one, must not resolve the pending reply."""
        agenda = [AgendaItem("ceo", "open")]
        log = []
        speakers = {"ceo": make_interruptible_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers, barge_in_reply_timeout_seconds=0.05)

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)
        await moderator.on_human_speech_started()
        moderator.record_transcript("eng", "not a human, should not satisfy the wait")

        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo"])
        # eng's transcript entry must never be mistaken for the human's finished
        # barge-in utterance — no "Someone just asked" answer turn should fire.
        answer_calls = [entry for entry in log if entry[0] == "ceo" and entry[1].startswith("Someone just asked")]
        self.assertEqual(answer_calls, [])
        # the barge-in still cut ceo off mid-"open", so it still gets a further
        # turn to finish that original content once the (unanswered) Q&A window
        # times out — this is the resume behavior, not a barge-in answer.
        resume_calls = [entry for entry in log if entry[0] == "ceo" and entry[1] != "open"]
        self.assertEqual(len(resume_calls), 1)
        self.assertIn("interrupted before finishing", resume_calls[0][1])


class ModeratorOpenFloorTest(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for the board's PER-75 feedback ("suddenly they
    all left the chat"): the call shouldn't hang up the instant the closing
    line finishes — a human should get a short window to ask one more thing.
    """

    async def test_human_question_after_agenda_ends_gets_answered(self):
        log = []
        agenda = [AgendaItem("ceo", "close")]
        speakers = {"ceo": make_speaker("ceo", log)}
        moderator = Moderator(agenda, speakers, open_floor_seconds=0.2)

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)  # let ceo's closing line run and the open-floor window arm
        moderator.record_transcript("board-member", "one more thing before you go")

        completed = await asyncio.wait_for(task, timeout=1)

        answer_prompt = 'Someone just asked: "one more thing before you go" Answer them directly and briefly, then continue.'
        self.assertEqual(completed, ["ceo"])
        self.assertIn(("ceo", answer_prompt), log)
        self.assertIsNone(moderator.current_speaker)

    async def test_silence_after_agenda_ends_closes_the_call_without_hanging(self):
        agenda = [AgendaItem("ceo", "close")]
        speakers = {"ceo": make_speaker("ceo", [])}
        moderator = Moderator(agenda, speakers, open_floor_seconds=0.05)

        completed = await asyncio.wait_for(moderator.run_agenda(), timeout=1)

        self.assertEqual(completed, ["ceo"])
        self.assertIsNone(moderator.current_speaker)

    async def test_no_open_floor_when_the_closer_never_joined(self):
        agenda = [AgendaItem("missing", "close")]
        moderator = Moderator(agenda, {}, open_floor_seconds=0.05)

        completed = await asyncio.wait_for(moderator.run_agenda(), timeout=1)

        self.assertEqual(completed, [])


class ModeratorGracefulDegradationTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_dropping_mid_turn_does_not_kill_the_agenda(self):
        log = []
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update"), AgendaItem("ops", "close")]
        speakers = {
            "ceo": make_speaker("ceo", log),
            "eng": make_speaker("eng", log, raise_on_speak=True),
            "ops": make_speaker("ops", log),
        }
        moderator = Moderator(agenda, speakers)

        completed = await moderator.run_agenda()

        self.assertEqual(completed, ["ceo", "ops"])
        self.assertIn("eng", moderator.dropped)
        self.assertIsNone(moderator.current_speaker)

    async def test_agent_never_joined_is_skipped_not_fatal(self):
        agenda = [AgendaItem("ceo", "open"), AgendaItem("missing", "update")]
        moderator = Moderator(agenda, {"ceo": make_speaker("ceo", [])})

        completed = await moderator.run_agenda()

        self.assertEqual(completed, ["ceo"])

    async def test_a_turn_that_hangs_past_the_timeout_is_dropped_not_stuck_forever(self):
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        log = []

        async def hangs_forever(prompt):
            await asyncio.sleep(3600)

        speakers = {
            "ceo": SpeakerHandle("ceo", hangs_forever, lambda: log.append("interrupt")),
            "eng": make_speaker("eng", log),
        }
        moderator = Moderator(agenda, speakers, turn_timeout_seconds=0.05)

        completed = await asyncio.wait_for(moderator.run_agenda(), timeout=5)

        self.assertEqual(completed, ["eng"])
        self.assertIn("ceo", moderator.dropped)

    async def test_previously_dropped_agent_is_skipped_on_later_agenda_items(self):
        log = []
        agenda = [AgendaItem("eng", "first"), AgendaItem("eng", "second")]
        speakers = {"eng": make_speaker("eng", log, raise_on_speak=True)}
        moderator = Moderator(agenda, speakers)

        completed = await moderator.run_agenda()

        self.assertEqual(completed, [])
        self.assertEqual(log, [])  # second turn never even calls speak()


class ModeratorTranscriptTest(unittest.TestCase):
    def test_records_and_renders_rolling_transcript(self):
        moderator = Moderator([], {})
        moderator.record_transcript("board-member", "what's the status?")
        moderator.record_transcript("agent-eng", "shipped the healthcheck fix")

        text = moderator.recent_transcript_text()

        self.assertEqual(text, "board-member: what's the status?\nagent-eng: shipped the healthcheck fix")

    def test_recent_transcript_text_caps_line_count(self):
        moderator = Moderator([], {})
        for i in range(30):
            moderator.record_transcript("human", f"line {i}")

        text = moderator.recent_transcript_text(max_lines=5)

        self.assertEqual(len(text.splitlines()), 5)
        self.assertIn("line 29", text)
        self.assertNotIn("line 24", text)

    def test_full_transcript_text_includes_every_line(self):
        moderator = Moderator([], {})
        for i in range(30):
            moderator.record_transcript("human", f"line {i}")

        text = moderator.full_transcript_text()

        self.assertEqual(len(text.splitlines()), 30)
        self.assertIn("line 0", text)
        self.assertIn("line 29", text)

    def test_full_transcript_text_empty_when_nothing_recorded(self):
        moderator = Moderator([], {})
        self.assertEqual(moderator.full_transcript_text(), "")


class ModeratorFiledIssuesTest(unittest.TestCase):
    """Milestone 3: issues filed live during the call, for the post-call summary."""

    def test_starts_empty(self):
        moderator = Moderator([], {})
        self.assertEqual(moderator.filed_issues, [])

    def test_record_filed_issue_appends_in_order(self):
        moderator = Moderator([], {})
        moderator.record_filed_issue("PER-1", "First follow-up")
        moderator.record_filed_issue("PER-2", "Second follow-up")

        self.assertEqual(
            moderator.filed_issues,
            [("PER-1", "First follow-up"), ("PER-2", "Second follow-up")],
        )


if __name__ == "__main__":
    unittest.main()
