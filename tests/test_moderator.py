"""Unit tests for the server-side moderator floor-control state machine.

No LiveKit/ElevenLabs involved — SpeakerHandle is driven by plain async
callables, exactly as boardroom.py wires it to real AgentSessions.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.moderator import AgendaItem, Moderator, SpeakerHandle


def make_speaker(identity, log, raise_on_speak=False):
    async def speak(prompt):
        if raise_on_speak:
            raise RuntimeError("session dropped")
        log.append((identity, prompt))

    async def interrupt():
        log.append((identity, "INTERRUPTED"))

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


if __name__ == "__main__":
    unittest.main()
