"""Unit tests for the pure agenda-building logic in boardroom.py.

Room/session wiring (_connect_agent, _connect_transcriber) needs a live
LiveKit room and is exercised by the manual smoke test instead, per
docs/ARCHITECTURE.md's "Definition of done" for call-flow changes.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import asyncio
import gc
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.boardroom import _background_tasks, _fire_and_forget, _standup_agenda
from papervoice.personas import BOARDROOM_ROSTER


class StandupAgendaTest(unittest.TestCase):
    def test_every_persona_gets_at_least_one_turn(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        identities = {item.identity for item in agenda}
        self.assertEqual(identities, {p.identity for p in BOARDROOM_ROSTER})

    def test_opener_speaks_twice_everyone_else_once(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        identities = [item.identity for item in agenda]
        opener, *rest = BOARDROOM_ROSTER
        self.assertEqual(identities.count(opener.identity), 2)
        for persona in rest:
            self.assertEqual(identities.count(persona.identity), 1, persona.identity)

    def test_opener_also_closes(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        self.assertEqual(agenda[0].identity, BOARDROOM_ROSTER[0].identity)
        self.assertEqual(agenda[-1].identity, BOARDROOM_ROSTER[0].identity)
        self.assertIn("close", agenda[-1].prompt.lower())

    def test_middle_items_are_the_non_opener_personas_in_order(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        middle_identities = [item.identity for item in agenda[1:-1]]
        expected = [p.identity for p in BOARDROOM_ROSTER[1:]]
        self.assertEqual(middle_identities, expected)


class FireAndForgetTest(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for PER-75's barge-in bug: on_user_state_changed
    called asyncio.create_task() without keeping a reference, so nothing kept
    the interrupt task alive — asyncio is free to garbage-collect an
    unreferenced task mid-run. _fire_and_forget must hold a strong reference
    until the task completes, and must not let a failure vanish silently.
    """

    async def test_task_survives_gc_with_no_external_reference(self):
        ran = asyncio.Event()

        async def slow_interrupt():
            await asyncio.sleep(0)  # yield once, like a real await session.interrupt(force=True)
            ran.set()

        _fire_and_forget(slow_interrupt(), name="test-barge-in")
        gc.collect()  # nothing but _background_tasks references the task now

        await asyncio.wait_for(ran.wait(), timeout=1)

    async def test_task_is_discarded_from_the_background_set_once_done(self):
        before = len(_background_tasks)

        async def noop():
            return None

        task = _fire_and_forget(noop(), name="test-noop")
        await task

        self.assertEqual(len(_background_tasks), before)

    async def test_exception_is_logged_not_raised(self):
        async def boom():
            raise RuntimeError("session dropped mid-interrupt")

        task = _fire_and_forget(boom(), name="test-boom")

        await task  # must not raise — a failed barge-in must not crash the transcriber's event loop


if __name__ == "__main__":
    unittest.main()
