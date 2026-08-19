"""Unit tests for the server-side moderator floor-control state machine.

No LiveKit/ElevenLabs involved — SpeakerHandle is driven by plain async
callables, exactly as boardroom.py wires it to real AgentSessions.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import asyncio
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.moderator import AgendaItem, Moderator as _Moderator, SpeakerHandle


def Moderator(*args, **kwargs):
    """Test defaults: near-zero open floor window and no speech-end grace period.

    Prevents every agenda-completion test from silently waiting out the real
    ``DEFAULT_OPEN_FLOOR_SECONDS`` (20s) and ``DEFAULT_SPEECH_END_GRACE_SECONDS``
    (1s) — only ModeratorOpenFloorTest and ModeratorSpeechEndGraceTest exercise
    those explicitly.
    """
    kwargs.setdefault("open_floor_seconds", 0.05)
    kwargs.setdefault("speech_end_grace_seconds", 0.0)
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


def _name_resolver(mapping):
    """A tiny stand-in for boardroom._addressed_target: map any whole-word name
    in the utterance to its agent identity. Keeps these moderator tests free of
    the real roster/regex, which test_boardroom.AddressedTargetTest covers."""

    def resolve(text):
        low = text.lower()
        for name, identity in mapping.items():
            if re.search(rf"\b{re.escape(name)}\b", low):
                return identity
        return None

    return resolve


def _answer_signaling_speaker(identity, log, answered):
    """Non-blocking speaker that flags an asyncio.Event whenever it's asked to
    answer a barge-in question, so a test can drive successive open-floor
    exchanges deterministically."""

    async def speak(prompt):
        log.append((identity, prompt))
        if prompt.startswith("Someone just asked"):
            answered.set()

    async def interrupt():
        return None

    return SpeakerHandle(identity, speak, interrupt)


class ModeratorAddressingTest(unittest.IsolatedAsyncioTestCase):
    """PER-293: a human naming a specific agent ("Eng, ...") must be answered by
    that agent, not by whoever happened to hold the floor when they spoke."""

    async def test_barge_in_naming_another_agent_routes_the_answer_to_that_agent(self):
        log = []
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        speakers = {"ceo": make_interruptible_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers, addressee_resolver=_name_resolver({"eng": "eng"}))

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)  # let ceo's "open" turn start and block
        await moderator.on_human_speech_started()  # barge-in cuts ceo off
        moderator.record_transcript("board-member", "Eng, what's blocking you?")

        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo", "eng"])
        # Eng — not the interrupted ceo — fields the addressed question.
        eng_answer = next(p for who, p in log if who == "eng" and p.startswith("Someone just asked"))
        self.assertIn("what's blocking you?", eng_answer)
        self.assertFalse(any(who == "ceo" and p.startswith("Someone just asked") for who, p in log))
        # The interrupted ceo still gets to finish its own cut-off update.
        self.assertTrue(any(who == "ceo" and "interrupted before finishing" in p for who, p in log))

    async def test_open_floor_question_naming_an_agent_is_answered_by_that_agent(self):
        log = []
        speakers = {"ceo": make_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(
            [AgendaItem("ceo", "close")],
            speakers,
            open_floor_seconds=0.2,
            addressee_resolver=_name_resolver({"eng": "eng"}),
        )

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)  # let ceo close and the open floor arm
        moderator.record_transcript("board-member", "Eng, one more thing before we wrap")

        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo"])
        self.assertTrue(any(who == "eng" and "one more thing" in p for who, p in log))
        self.assertFalse(any(who == "ceo" and p.startswith("Someone just asked") for who, p in log))

    async def test_unnamed_open_floor_question_returns_to_the_moderator(self):
        # PER-293 regression fix: naming Eng once must NOT hand the whole rest
        # of the open floor to Eng. The named question goes to Eng; the next
        # *unnamed* question falls back to the moderator/closer (ceo), so the
        # moderator never goes permanently silent after one addressed question.
        log = []
        answered = asyncio.Event()
        speakers = {
            "ceo": _answer_signaling_speaker("ceo", log, answered),
            "eng": _answer_signaling_speaker("eng", log, answered),
        }
        moderator = Moderator(
            [AgendaItem("ceo", "close")],
            speakers,
            open_floor_seconds=0.2,
            addressee_resolver=_name_resolver({"eng": "eng"}),
        )

        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0)
        moderator.record_transcript("board-member", "Eng, status?")
        await asyncio.wait_for(answered.wait(), timeout=1)

        answered.clear()
        await asyncio.sleep(0.02)  # let the open floor re-arm with ceo (the default) as responder
        moderator.record_transcript("board-member", "and anything else")  # no name
        await asyncio.wait_for(answered.wait(), timeout=1)

        completed = await asyncio.wait_for(task, timeout=1)
        self.assertEqual(completed, ["ceo"])
        eng_answers = [p for who, p in log if who == "eng" and p.startswith("Someone just asked")]
        ceo_answers = [p for who, p in log if who == "ceo" and p.startswith("Someone just asked")]
        self.assertEqual(len(eng_answers), 1)  # only the question that named Eng
        self.assertEqual(len(ceo_answers), 1)  # the unnamed follow-up returns to the moderator
        self.assertIn("and anything else", ceo_answers[0])

    async def test_naming_an_unavailable_agent_falls_back_to_the_default_responder(self):
        log = []
        agenda = [AgendaItem("ceo", "open"), AgendaItem("eng", "update")]
        speakers = {"ceo": make_interruptible_speaker("ceo", log), "eng": make_speaker("eng", log)}
        moderator = Moderator(agenda, speakers, addressee_resolver=_name_resolver({"eng": "eng"}))
        moderator.dropped.add("eng")  # eng dropped this call — can't take the question

        task = asyncio.ensure_future(moderator.run_agenda())
        await asyncio.sleep(0)
        await moderator.on_human_speech_started()
        moderator.record_transcript("board-member", "Eng, can you cover that?")

        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo"])  # eng was dropped, so its agenda turn is skipped
        # With eng unavailable the question falls back to the interrupted ceo,
        # never silently dropped for want of the named agent.
        self.assertTrue(any(who == "ceo" and p.startswith("Someone just asked") for who, p in log))


class ModeratorAskAndWaitTest(unittest.IsolatedAsyncioTestCase):
    """PER-83: agent-initiated steering asks — a persona's own turn can pose a question and
    hold the floor open for a human reply, generalizing the hold-the-floor machinery
    `_hold_open_floor`/`_respond_to_barge_in` only ever fire on the human's own initiative.
    """

    async def test_question_is_recorded_and_reply_is_returned(self):
        log = []
        moderator = Moderator([], {"ceo": make_speaker("ceo", log)}, ask_and_wait_timeout_seconds=1)

        task = asyncio.ensure_future(moderator._ask_and_wait("ceo", "should we ship today?"))
        await asyncio.sleep(0)
        self.assertIn(("ceo", "should we ship today?"), moderator.transcript)

        moderator.record_transcript("board-member", "yes, ship it")
        answer = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(answer, "yes, ship it")
        self.assertIsNone(moderator._awaiting_reply_to)

    async def test_no_reply_within_timeout_returns_none_without_hanging(self):
        moderator = Moderator([], {"ceo": make_speaker("ceo", [])}, ask_and_wait_timeout_seconds=0.05)

        answer = await asyncio.wait_for(moderator._ask_and_wait("ceo", "any objections?"), timeout=1)

        self.assertIsNone(answer)
        self.assertIsNone(moderator._awaiting_reply_to)

    async def test_explicit_timeout_argument_overrides_the_default(self):
        moderator = Moderator([], {"ceo": make_speaker("ceo", [])}, ask_and_wait_timeout_seconds=1000)

        answer = await asyncio.wait_for(
            moderator._ask_and_wait("ceo", "any objections?", timeout=0.05), timeout=1
        )

        self.assertIsNone(answer)

    async def test_dropped_identity_returns_none_immediately_without_recording(self):
        moderator = Moderator([], {"ceo": make_speaker("ceo", [])})
        moderator.dropped.add("ceo")

        answer = await moderator._ask_and_wait("ceo", "any objections?")

        self.assertIsNone(answer)
        self.assertEqual(moderator.transcript, [])

    async def test_identity_that_never_joined_returns_none_immediately(self):
        moderator = Moderator([], {})

        answer = await moderator._ask_and_wait("ceo", "any objections?")

        self.assertIsNone(answer)
        self.assertEqual(moderator.transcript, [])

    async def test_agent_track_speech_does_not_resolve_the_wait(self):
        moderator = Moderator(
            [],
            {"ceo": make_speaker("ceo", []), "eng": make_speaker("eng", [])},
            ask_and_wait_timeout_seconds=0.05,
        )

        task = asyncio.ensure_future(moderator._ask_and_wait("ceo", "any objections?"))
        await asyncio.sleep(0)
        moderator.record_transcript("eng", "not a human, should not satisfy the wait")

        answer = await asyncio.wait_for(task, timeout=1)

        self.assertIsNone(answer)


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

    async def test_each_exchange_resets_the_open_floor_inactivity_window(self):
        log = []
        answered = asyncio.Event()
        answer_count = 0

        async def speak(prompt):
            nonlocal answer_count
            log.append(("ceo", prompt))
            if prompt.startswith("Someone just asked"):
                answer_count += 1
                answered.set()

        async def interrupt():
            return None

        moderator = Moderator(
            [AgendaItem("ceo", "close")],
            {"ceo": SpeakerHandle("ceo", speak, interrupt)},
            open_floor_seconds=0.1,
        )

        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0)
        moderator.record_transcript("board-member", "first question")
        await asyncio.wait_for(answered.wait(), timeout=1)

        answered.clear()
        await asyncio.sleep(0)  # let the moderator re-arm after the first answer
        self.assertFalse(task.done())
        moderator.record_transcript("board-member", "follow-up question")
        await asyncio.wait_for(answered.wait(), timeout=1)

        completed = await asyncio.wait_for(task, timeout=1)
        self.assertEqual(completed, ["ceo"])
        self.assertEqual(answer_count, 2)
        self.assertTrue(any(chr(34) + "first question" + chr(34) in prompt for _, prompt in log))
        self.assertTrue(any(chr(34) + "follow-up question" + chr(34) in prompt for _, prompt in log))

    async def test_speech_started_before_deadline_can_finish_after_deadline(self):
        """PER-162: do not tear down while a human is still elaborating."""
        log = []
        moderator = Moderator(
            [AgendaItem("ceo", "close")],
            {"ceo": make_speaker("ceo", log)},
            open_floor_seconds=0.05,
        )

        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0.03)
        await moderator.on_human_speech_started()
        await asyncio.sleep(0.04)
        self.assertFalse(task.done())

        moderator.on_human_speech_stopped()
        moderator.record_transcript("board-member", "a longer request that crossed the deadline")
        completed = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(completed, ["ceo"])
        self.assertTrue(any("longer request" in prompt for _, prompt in log))

    async def test_silence_after_agenda_ends_closes_the_call_without_hanging(self):
        agenda = [AgendaItem("ceo", "close")]
        speakers = {"ceo": make_speaker("ceo", [])}
        moderator = Moderator(agenda, speakers, open_floor_seconds=0.05)

        completed = await asyncio.wait_for(moderator.run_agenda(), timeout=1)

        self.assertEqual(completed, ["ceo"])
        self.assertIsNone(moderator.current_speaker)

    async def test_production_open_floor_does_not_end_due_to_silence(self):
        moderator = _Moderator([AgendaItem("ceo", "close")], {"ceo": make_speaker("ceo", [])})
        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0.08)
        self.assertFalse(task.done())
        moderator.end_call()
        self.assertEqual(await asyncio.wait_for(task, timeout=1), ["ceo"])

    async def test_explicit_dismissal_removes_only_named_speaker(self):
        moderator = Moderator([], {"ceo": make_speaker("ceo", []), "eng": make_speaker("eng", [])})
        self.assertTrue(await moderator.dismiss_speaker("eng"))
        self.assertIn("ceo", moderator._speakers)
        self.assertNotIn("eng", moderator._speakers)
        self.assertEqual(moderator.dismissed, {"eng"})

    async def test_dismissing_responder_keeps_call_open_with_remaining_agent(self):
        moderator = _Moderator(
            [AgendaItem("ceo", "close")],
            {"ceo": make_speaker("ceo", []), "eng": make_speaker("eng", [])},
        )
        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0)
        await moderator.dismiss_speaker("ceo")
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        moderator.end_call()
        self.assertEqual(await asyncio.wait_for(task, timeout=1), ["ceo"])

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


class ModeratorSpeechEndGraceTest(unittest.IsolatedAsyncioTestCase):
    """PER-392: agents must not respond to a mid-sentence VAD pause.

    Silero VAD fires quickly on a brief intra-sentence pause (~300–700 ms);
    without a grace window the first STT final triggers an immediate reply
    before the human has finished their thought. The grace period debounce
    accumulates contiguous segments and fires only after sustained silence.
    """

    async def test_two_segments_assembled_into_one_reply(self):
        """Human pauses mid-sentence: two STT finals produce one reply with the full text."""
        log = []
        moderator = _Moderator(
            [AgendaItem("ceo", "close")],
            {"ceo": make_speaker("ceo", log)},
            open_floor_seconds=0.5,
            speech_end_grace_seconds=0.08,
        )

        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0)  # let ceo close and open floor arm

        # Segment 1 arrives; grace period starts
        moderator.record_transcript("board-member", "What about the")
        # Human resumes speaking before grace expires — debounce must be cancelled
        await moderator.on_human_speech_started()
        # Segment 2 arrives; new grace period starts
        moderator.record_transcript("board-member", "revenue numbers?")

        completed = await asyncio.wait_for(task, timeout=2.0)

        self.assertEqual(completed, ["ceo"])
        answer_calls = [p for _, p in log if p.startswith("Someone just asked")]
        # Exactly one reply despite two STT segments
        self.assertEqual(len(answer_calls), 1)
        # Full accumulated text is what the agent sees
        self.assertIn("What about the revenue numbers?", answer_calls[0])

    async def test_single_segment_answered_after_grace_period_expires(self):
        """Human speaks one sentence cleanly; reply fires after grace period, not before."""
        log = []
        fired_times = []

        async def speak(prompt):
            fired_times.append(asyncio.get_event_loop().time())
            log.append(("ceo", prompt))

        moderator = _Moderator(
            [AgendaItem("ceo", "close")],
            {"ceo": SpeakerHandle("ceo", speak, lambda: None)},
            open_floor_seconds=0.5,
            speech_end_grace_seconds=0.08,
        )

        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0)

        t0 = asyncio.get_event_loop().time()
        moderator.record_transcript("board-member", "any final thoughts?")

        completed = await asyncio.wait_for(task, timeout=2.0)

        self.assertEqual(completed, ["ceo"])
        answer_calls = [p for _, p in log if p.startswith("Someone just asked")]
        self.assertEqual(len(answer_calls), 1)
        # The answer fires no earlier than the grace period after the transcript
        self.assertGreater(fired_times[-1] - t0, 0.07)

    async def test_grace_period_zero_fires_immediately(self):
        """speech_end_grace_seconds=0 preserves the pre-PER-392 immediate-reply behaviour."""
        log = []
        moderator = _Moderator(
            [AgendaItem("ceo", "close")],
            {"ceo": make_speaker("ceo", log)},
            open_floor_seconds=0.2,
            speech_end_grace_seconds=0.0,
        )

        task = asyncio.create_task(moderator.run_agenda())
        await asyncio.sleep(0)
        moderator.record_transcript("board-member", "wrap up please")

        completed = await asyncio.wait_for(task, timeout=1.0)

        self.assertEqual(completed, ["ceo"])
        self.assertTrue(any("wrap up please" in p for _, p in log))


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
