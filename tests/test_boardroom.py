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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.boardroom import (
    _background_tasks,
    _build_summary,
    _file_issue_tool,
    _fire_and_forget,
    _load_context,
    _standup_agenda,
)
from papervoice.moderator import Moderator
from papervoice.personas import BOARDROOM_ROSTER
from papervoice.vendors import paperclip as pc_vendor


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


class StandupAgendaContextTest(unittest.TestCase):
    """Milestone 3: live Paperclip context folded into status-update prompts."""

    def test_context_line_appears_in_matching_persona_prompt(self):
        opener, first, *_ = BOARDROOM_ROSTER
        agenda = _standup_agenda(BOARDROOM_ROSTER, context={first.identity: "PER-1 (todo, high): Do the thing"})

        item = next(i for i in agenda if i.identity == first.identity)
        self.assertIn("PER-1 (todo, high): Do the thing", item.prompt)

    def test_missing_context_entry_omits_status_line_without_error(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER, context={})
        # must not raise, and every persona still gets a turn
        identities = {item.identity for item in agenda}
        self.assertEqual(identities, {p.identity for p in BOARDROOM_ROSTER})

    def test_none_context_defaults_like_empty(self):
        with_none = _standup_agenda(BOARDROOM_ROSTER, context=None)
        with_empty = _standup_agenda(BOARDROOM_ROSTER, context={})
        self.assertEqual([i.prompt for i in with_none], [i.prompt for i in with_empty])

    def test_status_prompts_mention_the_followup_tool(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)
        opener, *rest = BOARDROOM_ROSTER
        for item in agenda[1:-1]:
            self.assertIn("file_followup_issue", item.prompt)


class LoadContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_briefing_per_persona_using_bound_agent_id(self):
        with mock.patch.object(pc_vendor, "context_briefing", side_effect=lambda agent_id: f"briefing-for-{agent_id}") as briefing:
            context = await _load_context(BOARDROOM_ROSTER)
        self.assertEqual(len(context), len(BOARDROOM_ROSTER))
        for persona in BOARDROOM_ROSTER:
            self.assertEqual(context[persona.identity], f"briefing-for-{persona.paperclip_agent_id}")
        self.assertEqual(briefing.call_count, len(BOARDROOM_ROSTER))

    async def test_one_persona_failing_does_not_block_the_others(self):
        opener, *rest = BOARDROOM_ROSTER

        def fake_briefing(agent_id):
            if agent_id == opener.paperclip_agent_id:
                raise RuntimeError("Paperclip API down")
            return "ok"

        with mock.patch.object(pc_vendor, "context_briefing", side_effect=fake_briefing):
            context = await _load_context(BOARDROOM_ROSTER)
        self.assertNotIn(opener.identity, context)
        for persona in rest:
            self.assertEqual(context[persona.identity], "ok")


class BuildSummaryTest(unittest.TestCase):
    def test_includes_completed_turns_and_filed_issues(self):
        moderator = Moderator(agenda=[], speakers={})
        moderator.record_filed_issue("PER-42", "Investigate flaky test")
        moderator.record_transcript("agent-ceo", "Let's wrap up.")

        summary = _build_summary(moderator, ["agent-ceo", "agent-eng"])

        self.assertIn("agent-ceo, agent-eng", summary)
        self.assertIn("PER-42: Investigate flaky test", summary)
        self.assertIn("Let's wrap up.", summary)

    def test_no_filed_issues_omits_that_section(self):
        moderator = Moderator(agenda=[], speakers={})
        summary = _build_summary(moderator, ["agent-ceo"])
        self.assertNotIn("Follow-up issues filed", summary)

    def test_dropped_agents_are_listed(self):
        moderator = Moderator(agenda=[], speakers={})
        moderator.dropped.add("agent-ops")
        summary = _build_summary(moderator, [])
        self.assertIn("agent-ops", summary)
        self.assertIn("Dropped mid-call", summary)


class FileIssueToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_records_on_moderator_and_returns_identifier(self):
        persona = BOARDROOM_ROSTER[0]
        moderator = Moderator(agenda=[], speakers={})
        tool = _file_issue_tool(persona, moderator)

        with mock.patch.object(pc_vendor, "create_issue", return_value={"identifier": "PER-7", "id": "uuid-7"}) as create:
            result = await tool(title="Fix the flaky test", description="seen twice this week")

        self.assertEqual(result, "Filed PER-7: Fix the flaky test")
        self.assertEqual(moderator.filed_issues, [("PER-7", "Fix the flaky test")])
        create.assert_called_once_with(
            title="Fix the flaky test",
            description="seen twice this week",
            assignee_agent_id=persona.paperclip_agent_id,
        )

    async def test_api_failure_reports_back_without_raising(self):
        persona = BOARDROOM_ROSTER[0]
        moderator = Moderator(agenda=[], speakers={})
        tool = _file_issue_tool(persona, moderator)

        with mock.patch.object(pc_vendor, "create_issue", side_effect=RuntimeError("API down")):
            result = await tool(title="Whatever")

        self.assertIn("couldn't file", result)
        self.assertEqual(moderator.filed_issues, [])


if __name__ == "__main__":
    unittest.main()
