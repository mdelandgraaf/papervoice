"""Unit tests for the pure agenda-building logic in boardroom.py.

Room/session wiring (_connect_agent, _connect_transcriber) needs a live
LiveKit room and is exercised by the manual smoke test instead, per
docs/ARCHITECTURE.md's "Definition of done" for call-flow changes.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import asyncio
import gc
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import papervoice.boardroom as _boardroom_module
from papervoice.boardroom import (
    _ask_board_tool,
    _background_tasks,
    _build_summary,
    _direct_call_instructions,
    _addressed_target,
    _direct_file_issue_tool,
    _file_issue_tool,
    _fire_and_forget,
    _dismissal_target,
    _format_issues_for_speech,
    _load_context,
    _lookup_tool,
    _pass_on_reacting,
    _resolve_direct_persona,
    _standup_agenda,
    run_standup,
)
from papervoice.moderator import Moderator
from papervoice.personas import (
    BOARDROOM_ROSTER,
    Persona,
    build_persona_from_agent,
    filter_roster,
    load_roster_from_paperclip,
    parse_custom_room_identities,
)
from papervoice.prompts import DEFAULT_MODERATOR_INSTRUCTIONS, PromptConfig
from papervoice.vendors import paperclip as pc_vendor


def _auth_error(status_code: int = 401) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("unauthorized", request=request, response=response)


class ExplicitDismissalTest(unittest.TestCase):
    def test_requires_a_named_imperative(self):
        self.assertEqual(_dismissal_target("CEO, please leave.", BOARDROOM_ROSTER), "agent-ceo")
        self.assertIsNone(_dismissal_target("Should the CEO leave?", BOARDROOM_ROSTER))
        self.assertIsNone(_dismissal_target("Everyone can leave", BOARDROOM_ROSTER))

    def test_targets_only_the_named_agent(self):
        self.assertEqual(_dismissal_target("Tell Eng to drop off", BOARDROOM_ROSTER), "agent-eng")


class CustomRoomRosterTest(unittest.TestCase):
    """PER-314: custom room names select an explicit subset of enabled agents."""

    def test_parses_selected_identities(self):
        self.assertEqual(
            parse_custom_room_identities("papervoice-room-agent-ceo.agent-marketing"),
            ("agent-ceo", "agent-marketing"),
        )

    def test_non_custom_and_empty_rooms_do_not_select_agents(self):
        self.assertIsNone(parse_custom_room_identities("papervoice-boardroom"))
        self.assertIsNone(parse_custom_room_identities("papervoice-room-"))

    def test_filter_ignores_unknown_agents_and_preserves_roster_order(self):
        selected = filter_roster(BOARDROOM_ROSTER, ("agent-eng", "unknown", "agent-ceo"))
        self.assertEqual(tuple(p.identity for p in selected), ("agent-ceo", "agent-eng"))

    def test_filter_can_create_single_agent_room(self):
        selected = filter_roster(BOARDROOM_ROSTER, ("agent-eng",))
        self.assertEqual(tuple(p.identity for p in selected), ("agent-eng",))


class ModeratorPromptTest(unittest.TestCase):
    def test_moderator_does_not_answer_for_addressed_participants(self):
        self.assertIn("do not answer for them", DEFAULT_MODERATOR_INSTRUCTIONS)
        self.assertIn("let them speak for themselves", DEFAULT_MODERATOR_INSTRUCTIONS)
        self.assertIn("facilitate and keep time", DEFAULT_MODERATOR_INSTRUCTIONS)


class AddressedTargetTest(unittest.TestCase):
    """PER-293: resolve which agent a human utterance addresses by name so the
    moderator routes the question to them, not to whoever held the floor."""

    def test_leading_vocative_resolves_the_named_agent(self):
        self.assertEqual(_addressed_target("Eng, what's blocking you?", BOARDROOM_ROSTER), "agent-eng")
        # STT often drops the comma — the bare leading name must still resolve.
        self.assertEqual(_addressed_target("Eng what shipped this week", BOARDROOM_ROSTER), "agent-eng")
        self.assertEqual(_addressed_target("hey CEO can you summarize", BOARDROOM_ROSTER), "agent-ceo")

    def test_handoff_cue_resolves_the_named_agent(self):
        self.assertEqual(_addressed_target("over to Eng", BOARDROOM_ROSTER), "agent-eng")
        self.assertEqual(_addressed_target("what about Eng on the deploy", BOARDROOM_ROSTER), "agent-eng")
        # "from"/"for" cues — "let's get an update from Eng", "this one's for Eng".
        self.assertEqual(_addressed_target("let's get an update from Eng on that", BOARDROOM_ROSTER), "agent-eng")
        self.assertEqual(_addressed_target("this one's for Eng", BOARDROOM_ROSTER), "agent-eng")

    def test_trailing_vocative_question_resolves_the_named_agent(self):
        self.assertEqual(_addressed_target("what do you think, Eng?", BOARDROOM_ROSTER), "agent-eng")

    def test_trailing_vocative_without_question_mark_resolves(self):
        # PER-293 recall gap: STT drops the comma and the "?", and the name
        # lands last — these all used to miss and fall back to the floor-holder.
        self.assertEqual(_addressed_target("go ahead Eng", BOARDROOM_ROSTER), "agent-eng")
        self.assertEqual(_addressed_target("can you tell us more Eng", BOARDROOM_ROSTER), "agent-eng")
        self.assertEqual(_addressed_target("why don't you take this one Eng", BOARDROOM_ROSTER), "agent-eng")

    def test_interior_vocative_before_a_question_resolves_the_named_agent(self):
        # PER-401 board test call: a human named the agent mid-utterance, right
        # before the question ("..., <name>? are you able to list the open
        # tasks?"). leading/cued/trailing all miss this, so it used to fall
        # through to the default responder and get deflected. The '?' right after
        # the name is an unambiguous direct address. (Live calls use the
        # Paperclip-loaded display name "VoiceEngineer" → alias "voice engineer";
        # the static fallback roster names the same persona "Eng".)
        self.assertEqual(
            _addressed_target(
                "shall we test that at the moment eng? are you able to list the current open tasks?",
                BOARDROOM_ROSTER,
            ),
            "agent-eng",
        )
        self.assertEqual(_addressed_target("CEO? can you recap", BOARDROOM_ROSTER), "agent-ceo")

    def test_interior_comma_vocative_resolves_the_named_agent(self):
        # PER-402 board call: the human addressed the agent mid-utterance with
        # the name set off by commas but no '?' right after it ("so wait, voice
        # engineer, um, I ask you a question"). leading/cued/trailing/
        # interior_question all missed it, so it fell through to the CEO default
        # responder and VoiceEngineer never answered. (Static fallback roster
        # names the same persona "Eng".)
        self.assertEqual(
            _addressed_target(
                "so wait, eng, um, I ask you a question. can you tell me the status",
                BOARDROOM_ROSTER,
            ),
            "agent-eng",
        )

    def test_request_auxiliary_naming_the_agent_resolves_them(self):
        # PER-402 board call: "can voice engineer now, um, tell me the fifth
        # word..." — a request *for* the named agent to act. The auxiliary
        # ("can") sits right before the name.
        self.assertEqual(
            _addressed_target("can eng now, um, tell me the fifth word", BOARDROOM_ROSTER),
            "agent-eng",
        )
        self.assertEqual(
            _addressed_target("could CEO make this task for us", BOARDROOM_ROSTER),
            "agent-ceo",
        )

    def test_asking_about_an_agent_is_not_a_request_to_them(self):
        # The other side of PER-402: "why doesn't/didn't <name> respond" is
        # *about* the agent, not a request to them, and must stay with the
        # default responder (the CEO fielding the meta-question).
        self.assertIsNone(
            _addressed_target("so why doesn't eng now respond to me", BOARDROOM_ROSTER)
        )
        self.assertIsNone(
            _addressed_target("can you tell me why eng didn't respond to my question", BOARDROOM_ROSTER)
        )

    def test_no_name_returns_none(self):
        self.assertIsNone(_addressed_target("what's our runway?", BOARDROOM_ROSTER))

    def test_talking_about_an_agent_is_not_addressing_them(self):
        # A name buried mid-sentence in a question *about* the agent is not a
        # vocative and must not hijack routing to them.
        self.assertIsNone(_addressed_target("what's Eng been working on", BOARDROOM_ROSTER))

    def test_substring_of_another_word_does_not_match(self):
        # "engineering" must not be mistaken for the "Eng" agent.
        self.assertIsNone(_addressed_target("how's the engineering roadmap looking", BOARDROOM_ROSTER))
        self.assertIsNone(_addressed_target("the whole engineering team is heads down", BOARDROOM_ROSTER))


class StandupAgendaTest(unittest.TestCase):
    def test_every_persona_gets_at_least_one_turn(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        identities = {item.identity for item in agenda}
        self.assertEqual(identities, {p.identity for p in BOARDROOM_ROSTER})

    def test_opener_speaks_twice_first_update_once_everyone_after_reacts_too(self):
        """Opener: open + close. First `rest` persona: just their own update — there's no
        prior status update yet for it to react to. Every later `rest` persona: a reaction
        turn (PER-83) ahead of its own update, so it speaks twice."""
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        identities = [item.identity for item in agenda]
        opener, first, *later = BOARDROOM_ROSTER
        self.assertEqual(identities.count(opener.identity), 2)
        self.assertEqual(identities.count(first.identity), 1)
        for persona in later:
            self.assertEqual(identities.count(persona.identity), 2, persona.identity)

    def test_opener_also_closes(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        self.assertEqual(agenda[0].identity, BOARDROOM_ROSTER[0].identity)
        self.assertEqual(agenda[-1].identity, BOARDROOM_ROSTER[0].identity)
        self.assertIn("close", agenda[-1].prompt.lower())

    def test_closer_is_told_to_sweep_undocumented_decisions_into_tickets(self):
        """PER-76 board feedback: outcomes of the call should become Paperclip tasks, not
        rely on each agent having remembered to file its own follow-up mid-turn."""
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        closer_prompt = agenda[-1].prompt
        self.assertIn("file_followup_issue", closer_prompt)
        self.assertIn("decisions", closer_prompt.lower())

    def test_middle_items_are_the_non_opener_personas_in_order_ignoring_reactions(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)

        middle_identities = [item.identity for item in agenda[1:-1] if item.kind != "reaction"]
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
        for item in agenda[1:-1]:
            if item.kind == "reaction":
                continue
            self.assertIn("file_followup_issue", item.prompt)

    def test_offline_flag_adds_a_spoken_notice_to_the_opener_only(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER, paperclip_offline=True)
        self.assertIn("offline", agenda[0].prompt.lower())
        for item in agenda[1:]:
            self.assertNotIn("offline", item.prompt.lower())

    def test_no_offline_notice_by_default(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)
        self.assertNotIn("offline", agenda[0].prompt.lower())


class StandupAgendaReactionTurnsTest(unittest.TestCase):
    """PER-83: agent-to-agent cross-talk — the next speaker gets an optional brief chance
    to react to the previous speaker's update before giving their own.

    Reaction turns only fire for the 3rd+ person in the roster (i > 0 in the
    rest slice). These tests use a 3-person fixture so the reaction-turn branch
    is actually exercised — using BOARDROOM_ROSTER (2 people) would leave `later`
    empty and the reaction tests would vacuously pass or fail to find any reaction.
    """

    def setUp(self):
        from papervoice.personas import Persona
        self._three_persona_roster = (
            Persona("agent-ceo", "CEO", "voice-ceo", "You are the CEO."),
            Persona("agent-eng", "Eng", "voice-eng", "You are Eng."),
            Persona("agent-ops", "Ops", "voice-ops", "You are Ops."),
        )

    def test_no_reaction_before_the_first_status_update(self):
        agenda = _standup_agenda(BOARDROOM_ROSTER)
        opener, first, *_ = BOARDROOM_ROSTER
        # item 0 is the opener's greeting, item 1 must be `first`'s own update, not a reaction
        self.assertEqual(agenda[1].identity, first.identity)
        self.assertEqual(agenda[1].kind, "turn")

    def test_reaction_turn_precedes_every_later_status_update(self):
        roster = self._three_persona_roster
        agenda = _standup_agenda(roster)
        opener, first, *later = roster

        turns_by_identity = {item.identity: item for item in agenda if item.kind == "turn"}
        reactions_by_identity = {item.identity: item for item in agenda if item.kind == "reaction"}
        for persona in later:
            self.assertIn(persona.identity, reactions_by_identity)
            reaction_index = agenda.index(reactions_by_identity[persona.identity])
            update_index = agenda.index(turns_by_identity[persona.identity])
            self.assertLess(reaction_index, update_index)

    def test_reaction_prompt_names_the_previous_speaker_and_the_pass_tool(self):
        roster = self._three_persona_roster
        agenda = _standup_agenda(roster)
        reaction = next(item for item in agenda if item.kind == "reaction")

        self.assertIn(roster[1].display_name, reaction.prompt)
        self.assertIn("pass_on_reacting", reaction.prompt)

    def test_reaction_turns_are_optional_not_forced_filler(self):
        agenda = _standup_agenda(self._three_persona_roster)
        reaction = next(item for item in agenda if item.kind == "reaction")

        self.assertIn("if not, call the pass_on_reacting tool", reaction.prompt.lower())
        self.assertIn("don't force a comment", reaction.prompt.lower())


class LoadContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_briefing_per_persona_using_bound_agent_id(self):
        with mock.patch.object(pc_vendor, "context_briefing", side_effect=lambda agent_id: f"briefing-for-{agent_id}") as briefing:
            context, offline = await _load_context(BOARDROOM_ROSTER)
        self.assertEqual(len(context), len(BOARDROOM_ROSTER))
        for persona in BOARDROOM_ROSTER:
            self.assertEqual(context[persona.identity], f"briefing-for-{persona.paperclip_agent_id}")
        self.assertEqual(briefing.call_count, len(BOARDROOM_ROSTER))
        self.assertFalse(offline)

    async def test_one_persona_failing_does_not_block_the_others(self):
        opener, *rest = BOARDROOM_ROSTER

        def fake_briefing(agent_id):
            if agent_id == opener.paperclip_agent_id:
                raise RuntimeError("Paperclip API down")
            return "ok"

        with mock.patch.object(pc_vendor, "context_briefing", side_effect=fake_briefing):
            context, offline = await _load_context(BOARDROOM_ROSTER)
        self.assertNotIn(opener.identity, context)
        for persona in rest:
            self.assertEqual(context[persona.identity], "ok")
        self.assertFalse(offline)

    async def test_one_auth_failure_among_others_is_not_reported_as_fully_offline(self):
        opener, *rest = BOARDROOM_ROSTER

        def fake_briefing(agent_id):
            if agent_id == opener.paperclip_agent_id:
                raise _auth_error()
            return "ok"

        with mock.patch.object(pc_vendor, "context_briefing", side_effect=fake_briefing):
            context, offline = await _load_context(BOARDROOM_ROSTER)
        self.assertNotIn(opener.identity, context)
        self.assertFalse(offline)

    async def test_every_persona_auth_failing_is_reported_offline(self):
        with mock.patch.object(pc_vendor, "context_briefing", side_effect=_auth_error()):
            context, offline = await _load_context(BOARDROOM_ROSTER)
        self.assertEqual(context, {})
        self.assertTrue(offline)


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

    def test_transcript_is_not_truncated_to_a_tail(self):
        moderator = Moderator(agenda=[], speakers={})
        for i in range(50):
            moderator.record_transcript("agent-ceo", f"line {i}")

        summary = _build_summary(moderator, ["agent-ceo"])

        self.assertIn("line 0", summary)
        self.assertIn("line 49", summary)


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

    async def test_auth_failure_gives_a_specific_spoken_apology(self):
        persona = BOARDROOM_ROSTER[0]
        moderator = Moderator(agenda=[], speakers={})
        tool = _file_issue_tool(persona, moderator)

        with mock.patch.object(pc_vendor, "create_issue", side_effect=_auth_error()):
            result = await tool(title="Whatever")

        self.assertIn("expired", result)
        self.assertEqual(moderator.filed_issues, [])


class LookupToolTest(unittest.IsolatedAsyncioTestCase):
    """PER-401: the in-call live Paperclip lookup tool. It must read real issue state
    (search or own-issues) so an agent answers from data instead of hallucinating, and
    degrade to a clear spoken message on auth/API failure rather than raising."""

    async def test_query_searches_and_formats_results(self):
        persona = BOARDROOM_ROSTER[1]  # Eng, has a bound paperclip_agent_id
        tool = _lookup_tool(persona)
        hits = [{"identifier": "PER-42", "title": "Ship voice fix", "status": "in_progress",
                 "priority": "high", "description": "Wire the lookup tool."}]
        with mock.patch.object(pc_vendor, "search_issues", return_value=hits) as search:
            with mock.patch.object(pc_vendor, "agent_context") as agent_ctx:
                result = await tool(query="PER-42")
        search.assert_called_once_with("PER-42")
        agent_ctx.assert_not_called()
        self.assertIn("PER-42", result)
        self.assertIn("Ship voice fix", result)
        self.assertIn("Wire the lookup tool.", result)

    async def test_empty_query_lists_own_open_issues_for_bound_persona(self):
        persona = BOARDROOM_ROSTER[1]  # Eng
        tool = _lookup_tool(persona)
        mine = [{"identifier": "PER-9", "title": "My task", "status": "todo", "priority": "low"}]
        with mock.patch.object(pc_vendor, "agent_context", return_value=mine) as agent_ctx:
            with mock.patch.object(pc_vendor, "search_issues") as search:
                result = await tool(query="")
        agent_ctx.assert_called_once_with(persona.paperclip_agent_id, 12)
        search.assert_not_called()
        self.assertIn("PER-9", result)

    async def test_empty_query_unbound_persona_uses_company_snapshot(self):
        persona = Persona(identity="agent-x", display_name="X", voice_id="v", instructions="",
                          paperclip_agent_id=None)
        tool = _lookup_tool(persona)
        with mock.patch.object(pc_vendor, "company_snapshot", return_value=[]) as snapshot:
            with mock.patch.object(pc_vendor, "agent_context") as agent_ctx:
                result = await tool(query="")
        snapshot.assert_called_once_with(12)
        agent_ctx.assert_not_called()
        self.assertIn("no open", result.lower())

    async def test_no_matches_reports_clearly(self):
        persona = BOARDROOM_ROSTER[1]
        tool = _lookup_tool(persona)
        with mock.patch.object(pc_vendor, "search_issues", return_value=[]):
            result = await tool(query="nonexistent")
        self.assertIn("No matching", result)

    async def test_auth_failure_gives_specific_spoken_message(self):
        persona = BOARDROOM_ROSTER[1]
        tool = _lookup_tool(persona)
        with mock.patch.object(pc_vendor, "search_issues", side_effect=_auth_error()):
            result = await tool(query="PER-1")
        self.assertIn("expired", result)

    async def test_api_failure_reports_back_without_raising(self):
        persona = BOARDROOM_ROSTER[1]
        tool = _lookup_tool(persona)
        with mock.patch.object(pc_vendor, "search_issues", side_effect=RuntimeError("API down")):
            result = await tool(query="PER-1")
        self.assertIn("couldn't look that up", result)

    def test_format_includes_description_only_when_present(self):
        with_desc = _format_issues_for_speech(
            [{"identifier": "PER-1", "title": "A", "status": "todo", "priority": "low", "description": "hello"}]
        )
        self.assertIn("Details: hello", with_desc)
        without = _format_issues_for_speech(
            [{"identifier": "PER-2", "title": "B", "status": "done", "priority": "high"}]
        )
        self.assertNotIn("Details:", without)


class PassOnReactingToolTest(unittest.IsolatedAsyncioTestCase):
    """PER-83: the reaction-turn "pass" convention — calling this instead of speaking must
    never itself raise or require any moderator/persona state."""

    async def test_returns_without_side_effects(self):
        result = await _pass_on_reacting()
        self.assertIn("passed", result.lower())


class AskBoardToolTest(unittest.IsolatedAsyncioTestCase):
    """PER-83: agent-initiated steering — the ask_board tool is a thin wrapper around
    Moderator._ask_and_wait that turns its return value into a tool result the LLM can
    react to in the same turn."""

    async def test_answer_is_relayed_back_through_the_tool(self):
        persona = BOARDROOM_ROSTER[0]
        moderator = Moderator(agenda=[], speakers={})
        tool = _ask_board_tool(persona, moderator)

        with mock.patch.object(moderator, "_ask_and_wait", return_value="ship it") as ask_and_wait:
            result = await tool(question="Should we ship the beta today?")

        ask_and_wait.assert_called_once_with(persona.identity, "Should we ship the beta today?")
        self.assertEqual(result, 'The board answered: "ship it"')

    async def test_no_answer_within_timeout_reports_back_without_raising(self):
        persona = BOARDROOM_ROSTER[0]
        moderator = Moderator(agenda=[], speakers={})
        tool = _ask_board_tool(persona, moderator)

        with mock.patch.object(moderator, "_ask_and_wait", return_value=None):
            result = await tool(question="Should we ship the beta today?")

        self.assertIn("No answer", result)


class DynamicRosterTest(unittest.TestCase):
    """PER-89: dynamic boardroom roster built from Paperclip agents with metadata.papervoice.enabled."""

    def _make_config(self, **kwargs):
        defaults = dict(
            agent_id="agent-1",
            name="TestAgent",
            role="engineer",
            title="Test Engineer",
            capabilities="Builds stuff.",
            voice_id="voice-abc",
            livekit_identity="agent-test",
            display_name="Test",
            roster_order=0,
        )
        defaults.update(kwargs)
        return pc_vendor.PapervoiceAgentConfig(**defaults)

    def test_build_persona_from_agent_opener_instructions(self):
        config = self._make_config(roster_order=0)
        persona = build_persona_from_agent(config, is_opener=True)
        self.assertEqual(persona.identity, "agent-test")
        self.assertEqual(persona.voice_id, "voice-abc")
        self.assertEqual(persona.paperclip_agent_id, "agent-1")
        self.assertIn("open the standup", persona.instructions)

    def test_settings_are_only_system_prompt_source(self):
        config = self._make_config(
            name="PROFILE NAME MUST NOT LEAK",
            title="PROFILE TITLE MUST NOT LEAK",
            capabilities="PROFILE CAPABILITIES MUST NOT LEAK",
            moderator=False,
        )
        prompts = PromptConfig(
            moderator_instructions="CUSTOM MODERATOR ONLY",
            participant_instructions="CUSTOM PARTICIPANT ONLY",
        )

        opener = build_persona_from_agent(config, is_opener=True, prompt_cfg=prompts)
        participant = build_persona_from_agent(config, is_opener=False, prompt_cfg=prompts)

        self.assertEqual(opener.instructions, "CUSTOM MODERATOR ONLY")
        self.assertEqual(participant.instructions, "CUSTOM PARTICIPANT ONLY")

    def test_build_persona_from_agent_non_opener_instructions(self):
        config = self._make_config(roster_order=1)
        persona = build_persona_from_agent(config, is_opener=False)
        self.assertNotIn("open the standup", persona.instructions)
        self.assertIn("latest status", persona.instructions.lower())

    def test_build_persona_no_title_still_works(self):
        config = self._make_config(title=None, capabilities=None)
        persona = build_persona_from_agent(config, is_opener=True)
        self.assertEqual(persona.instructions, PromptConfig().moderator_instructions)

    def test_fallback_roster_uses_settings_prompts(self):
        prompts = PromptConfig(
            moderator_instructions="CUSTOM MODERATOR ONLY",
            participant_instructions="CUSTOM PARTICIPANT ONLY",
        )
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=[]):
            roster = load_roster_from_paperclip(prompts)

        self.assertEqual(roster[0].instructions, "CUSTOM MODERATOR ONLY")
        self.assertTrue(all(p.instructions == "CUSTOM PARTICIPANT ONLY" for p in roster[1:]))

    def test_load_roster_falls_back_when_api_returns_empty(self):
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=[]):
            roster = load_roster_from_paperclip()
        self.assertEqual(
            [(p.identity, p.voice_id, p.paperclip_agent_id) for p in roster],
            [(p.identity, p.voice_id, p.paperclip_agent_id) for p in BOARDROOM_ROSTER],
        )

    def test_load_roster_falls_back_on_api_error(self):
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", side_effect=RuntimeError("network error")):
            roster = load_roster_from_paperclip()
        self.assertEqual(
            [(p.identity, p.voice_id, p.paperclip_agent_id) for p in roster],
            [(p.identity, p.voice_id, p.paperclip_agent_id) for p in BOARDROOM_ROSTER],
        )

    def test_load_roster_builds_personas_from_enabled_agents(self):
        configs = [
            self._make_config(agent_id="ceo-id", livekit_identity="agent-ceo", display_name="CEO", roster_order=0),
            self._make_config(agent_id="eng-id", livekit_identity="agent-eng", display_name="Eng", roster_order=1),
        ]
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=configs):
            roster = load_roster_from_paperclip()
        self.assertEqual(len(roster), 2)
        self.assertEqual(roster[0].identity, "agent-ceo")
        self.assertEqual(roster[0].paperclip_agent_id, "ceo-id")
        self.assertIn("open the standup", roster[0].instructions)
        self.assertEqual(roster[1].identity, "agent-eng")
        self.assertNotIn("open the standup", roster[1].instructions)

    def test_partial_known_dynamic_roster_keeps_static_peer(self):
        configs = [self._make_config(
            agent_id=BOARDROOM_ROSTER[1].paperclip_agent_id,
            livekit_identity="agent-eng", display_name="Eng", roster_order=1,
        )]
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=configs):
            roster = load_roster_from_paperclip()
        self.assertEqual({p.paperclip_agent_id for p in roster},
                         {p.paperclip_agent_id for p in BOARDROOM_ROSTER})

    def test_dynamic_roster_is_compatible_with_standup_agenda(self):
        """A live-loaded roster (2 agents) must produce a valid agenda with no errors."""
        configs = [
            self._make_config(agent_id="ceo-id", livekit_identity="agent-ceo", display_name="CEO", roster_order=0),
            self._make_config(agent_id="eng-id", livekit_identity="agent-eng", display_name="Eng", roster_order=1),
        ]
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=configs):
            roster = load_roster_from_paperclip()
        agenda = _standup_agenda(roster)
        identities = {item.identity for item in agenda}
        self.assertEqual(identities, {"agent-ceo", "agent-eng"})
        self.assertEqual(agenda[0].identity, "agent-ceo")
        self.assertEqual(agenda[-1].identity, "agent-ceo")


class DirectCallInstructionsTest(unittest.TestCase):
    """PER-181: 1:1 direct-call instructions are distinct from standup instructions."""

    def test_includes_persona_display_name(self):
        persona = BOARDROOM_ROSTER[1]  # Eng
        instructions = _direct_call_instructions(persona)
        self.assertIn(persona.display_name, instructions)

    def test_no_standup_specific_language(self):
        persona = BOARDROOM_ROSTER[0]
        instructions = _direct_call_instructions(persona)
        self.assertNotIn("standup", instructions.lower())
        self.assertNotIn("moderator", instructions.lower())
        self.assertNotIn("agenda", instructions.lower())

    def test_briefing_is_included_when_present(self):
        persona = BOARDROOM_ROSTER[0]
        briefing = "PER-42 (in_progress, high): Implement something"
        instructions = _direct_call_instructions(persona, briefing=briefing)
        self.assertIn(briefing, instructions)

    def test_no_briefing_omits_the_context_line(self):
        persona = BOARDROOM_ROSTER[0]
        instructions = _direct_call_instructions(persona, briefing=None)
        self.assertNotIn("Paperclip issues:", instructions)


class DirectFileIssueToolTest(unittest.IsolatedAsyncioTestCase):
    """PER-181: the direct-call file_followup_issue tool records to the filed_issues list."""

    async def test_success_appends_to_filed_list_and_returns_identifier(self):
        persona = BOARDROOM_ROSTER[0]
        filed: list = []
        tool = _direct_file_issue_tool(persona, filed)

        with mock.patch.object(pc_vendor, "create_issue", return_value={"identifier": "PER-99", "id": "uuid-99"}):
            result = await tool(title="Discuss offline", description="from the 1:1 call")

        self.assertEqual(result, "Filed PER-99: Discuss offline")
        self.assertEqual(filed, [("PER-99", "Discuss offline")])

    async def test_api_failure_does_not_append_to_list(self):
        persona = BOARDROOM_ROSTER[0]
        filed: list = []
        tool = _direct_file_issue_tool(persona, filed)

        with mock.patch.object(pc_vendor, "create_issue", side_effect=RuntimeError("network down")):
            result = await tool(title="Whatever")

        self.assertIn("couldn't file", result)
        self.assertEqual(filed, [])

    async def test_auth_failure_gives_specific_apology(self):
        persona = BOARDROOM_ROSTER[0]
        filed: list = []
        tool = _direct_file_issue_tool(persona, filed)

        with mock.patch.object(pc_vendor, "create_issue", side_effect=_auth_error()):
            result = await tool(title="Whatever")

        self.assertIn("expired", result)
        self.assertEqual(filed, [])


class ResolveDirectPersonaTest(unittest.TestCase):
    """PER-181: _resolve_direct_persona finds a persona by livekit_identity."""

    def _make_config(self, **kwargs):
        defaults = dict(
            agent_id="agent-1",
            name="TestAgent",
            role="engineer",
            title=None,
            capabilities=None,
            voice_id="voice-abc",
            livekit_identity="agent-test",
            display_name="Test",
            roster_order=0,
        )
        defaults.update(kwargs)
        return pc_vendor.PapervoiceAgentConfig(**defaults)

    def test_resolves_from_paperclip_roster(self):
        cfg = self._make_config(livekit_identity="agent-eng", display_name="Eng", agent_id="eng-id", voice_id="v123")
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=[cfg]):
            persona = _resolve_direct_persona("agent-eng")
        self.assertIsNotNone(persona)
        self.assertEqual(persona.identity, "agent-eng")
        self.assertEqual(persona.display_name, "Eng")
        self.assertEqual(persona.voice_id, "v123")
        self.assertEqual(persona.paperclip_agent_id, "eng-id")

    def test_falls_back_to_static_roster_when_api_fails(self):
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", side_effect=RuntimeError("net")):
            persona = _resolve_direct_persona("agent-ceo")
        # Static fallback has agent-ceo from BOARDROOM_ROSTER
        self.assertIsNotNone(persona)
        self.assertEqual(persona.identity, "agent-ceo")

    def test_falls_back_to_static_roster_when_api_returns_nothing(self):
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=[]):
            persona = _resolve_direct_persona("agent-eng")
        self.assertIsNotNone(persona)
        self.assertEqual(persona.identity, "agent-eng")

    def test_returns_none_for_unknown_identity(self):
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=[]):
            persona = _resolve_direct_persona("agent-nobody")
        self.assertIsNone(persona)

    def test_paperclip_result_has_empty_instructions(self):
        """run_direct_call builds its own instructions; the resolved persona should not carry standup text."""
        cfg = self._make_config(livekit_identity="agent-ceo")
        with mock.patch.object(pc_vendor, "get_voice_enabled_agents", return_value=[cfg]):
            persona = _resolve_direct_persona("agent-ceo")
        self.assertEqual(persona.instructions, "")


class WebRTCTimeoutIsolationTest(unittest.IsolatedAsyncioTestCase):
    """PER-337: connection timeouts and transcriber failure isolation."""

    def _make_roster(self):
        return (
            Persona("agent-a", "A", "voice-a", "You are A."),
            Persona("agent-b", "B", "voice-b", "You are B."),
        )

    async def test_agent_connect_timeout_drops_agent_and_continues(self):
        """TimeoutError from room.connect propagates out of _connect_agent; run_standup
        catches it per-agent, marks the agent dropped, and keeps the call alive."""
        roster = self._make_roster()
        captured = {}

        orig_init = Moderator.__init__
        def capturing_init(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            captured["moderator"] = self

        async def fake_connect_agent(room_name, persona, moderator, client=None):
            if persona.identity == "agent-b":
                raise asyncio.TimeoutError("ICE stalled")
            return mock.AsyncMock(), mock.AsyncMock()

        async def fake_connect_transcriber(*a, **kw):
            return mock.AsyncMock(), mock.AsyncMock()

        with mock.patch.object(Moderator, "__init__", capturing_init), \
             mock.patch.object(Moderator, "run_agenda", new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(_boardroom_module, "_connect_agent",
                               mock.AsyncMock(side_effect=fake_connect_agent)), \
             mock.patch.object(_boardroom_module, "_connect_transcriber",
                               mock.AsyncMock(side_effect=fake_connect_transcriber)), \
             mock.patch.object(_boardroom_module, "_load_context",
                               mock.AsyncMock(return_value=({}, False))), \
             mock.patch.object(_boardroom_module, "load_prompt_config",
                               return_value=PromptConfig()):
            await run_standup("test-room", roster=roster)

        self.assertIn("agent-b", captured["moderator"].dropped)
        self.assertNotIn("agent-a", captured["moderator"].dropped)

    async def test_transcriber_failure_keeps_standup_alive(self):
        """If _connect_transcriber raises (timeout or any error), run_standup logs
        the failure and continues the call without STT/barge-in instead of crashing."""
        roster = self._make_roster()

        async def fake_connect_transcriber(*a, **kw):
            raise asyncio.TimeoutError("DTLS stalled")

        async def fake_connect_agent(room_name, persona, moderator, client=None):
            return mock.AsyncMock(), mock.AsyncMock()

        with mock.patch.object(_boardroom_module, "_connect_transcriber",
                               mock.AsyncMock(side_effect=fake_connect_transcriber)), \
             mock.patch.object(_boardroom_module, "_connect_agent",
                               mock.AsyncMock(side_effect=fake_connect_agent)), \
             mock.patch.object(_boardroom_module, "_load_context",
                               mock.AsyncMock(return_value=({}, False))), \
             mock.patch.object(Moderator, "run_agenda", new=mock.AsyncMock(return_value=[])), \
             mock.patch.object(_boardroom_module, "load_prompt_config",
                               return_value=PromptConfig()):
            completed = await run_standup("test-room", roster=roster)

        # Standup must not raise; it returns the completed identities list
        self.assertIsNotNone(completed)


class DirectCallRoomReadinessTest(unittest.IsolatedAsyncioTestCase):
    """PER-349: run_direct_call awaits room I/O readiness before greeting.

    Guards the race between session.start() returning and _init_task finishing
    audio setup (participant subscription + audio output track publication). On
    a worker restart into an existing room the window is wide enough for
    generate_reply() to fire before audio is live, silently discarding the
    greeting.
    """

    _ENV = {"LIVEKIT_URL": "ws://test-only"}

    def _make_fixtures(self, wait_for_ready_side_effect=None):
        """Build (mock_session, mock_room, registered_handlers)."""
        registered = {}
        mock_room = mock.MagicMock()
        mock_room.remote_participants = {}
        mock_room.connect = mock.AsyncMock()
        mock_room.disconnect = mock.AsyncMock()
        mock_room.on = lambda evt, h: registered.update({evt: h})

        mock_room_io = mock.MagicMock()
        mock_room_io.wait_for_ready = mock.AsyncMock(side_effect=wait_for_ready_side_effect)

        mock_session = mock.MagicMock()
        mock_session.room_io = mock_room_io
        mock_session.start = mock.AsyncMock()
        mock_session.generate_reply = mock.AsyncMock()
        mock_session.aclose = mock.AsyncMock()

        return mock_session, mock_room, registered

    async def _trigger_disconnect(self, registered, mock_room, *, delay_yields: int = 60):
        """Simulate the last human leaving after `delay_yields` event-loop turns."""
        for _ in range(delay_yields):
            await asyncio.sleep(0)
        handler = registered.get("participant_disconnected")
        if handler:
            fake_p = mock.MagicMock()
            fake_p.kind = _boardroom_module.rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
            mock_room.remote_participants = {}
            handler(fake_p)

    async def test_greets_only_after_room_io_ready(self):
        """generate_reply() fires only after wait_for_ready() completes (correct order)."""
        persona = Persona("agent-eng", "Eng", "voice-eng", "You are Eng.")
        call_order = []
        ready_evt = asyncio.Event()

        mock_session, mock_room, registered = self._make_fixtures()

        async def _track_ready():
            call_order.append("wait_for_ready")
            ready_evt.set()

        async def _track_greet(**kwargs):
            call_order.append("generate_reply")

        mock_session.room_io.wait_for_ready = mock.AsyncMock(side_effect=_track_ready)
        mock_session.generate_reply = mock.AsyncMock(side_effect=_track_greet)

        async def _fire_after_ready():
            await ready_evt.wait()
            # One more yield lets generate_reply() finish before we set done.
            await asyncio.sleep(0)
            handler = registered.get("participant_disconnected")
            if handler:
                fake_p = mock.MagicMock()
                fake_p.kind = _boardroom_module.rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
                mock_room.remote_participants = {}
                handler(fake_p)

        with mock.patch.dict(os.environ, self._ENV), \
             mock.patch.object(_boardroom_module, "AgentSession", return_value=mock_session), \
             mock.patch.object(_boardroom_module.rtc, "Room", return_value=mock_room), \
             mock.patch.object(_boardroom_module.lk_vendor, "mint_join_token", return_value="tok"), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_stt", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_tts", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.silero.VAD, "load", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.anthropic, "LLM", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module, "load_prompt_config", return_value=PromptConfig()), \
             mock.patch.object(_boardroom_module.pc_vendor, "context_briefing", return_value=None):
            await asyncio.gather(
                _boardroom_module.run_direct_call("papervoice-direct-agent-eng", persona),
                _fire_after_ready(),
            )

        self.assertEqual(call_order, ["wait_for_ready", "generate_reply"],
                         "wait_for_ready must complete before generate_reply is called")

    async def test_skips_greeting_when_room_io_times_out(self):
        """If wait_for_ready() times out, greeting is silently skipped; session stays live."""
        persona = Persona("agent-eng", "Eng", "voice-eng", "You are Eng.")

        async def _timeout():
            raise asyncio.TimeoutError("no participant found in time")

        mock_session, mock_room, registered = self._make_fixtures(wait_for_ready_side_effect=_timeout)

        with mock.patch.dict(os.environ, self._ENV), \
             mock.patch.object(_boardroom_module, "AgentSession", return_value=mock_session), \
             mock.patch.object(_boardroom_module.rtc, "Room", return_value=mock_room), \
             mock.patch.object(_boardroom_module.lk_vendor, "mint_join_token", return_value="tok"), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_stt", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_tts", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.silero.VAD, "load", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.anthropic, "LLM", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module, "load_prompt_config", return_value=PromptConfig()), \
             mock.patch.object(_boardroom_module.pc_vendor, "context_briefing", return_value=None):
            await asyncio.gather(
                _boardroom_module.run_direct_call("papervoice-direct-agent-eng", persona),
                self._trigger_disconnect(registered, mock_room),
            )

        mock_session.generate_reply.assert_not_called()

    async def test_restart_uses_recovery_message(self):
        """PER-349: on worker restart the human is already in the room — greeting must
        not re-introduce the agent; it should acknowledge the interruption instead."""
        persona = Persona("agent-eng", "Eng", "voice-eng", "You are Eng.")
        mock_session, mock_room, registered = self._make_fixtures()

        # Pre-populate participants to simulate a restart into an existing room.
        fake_human = mock.MagicMock()
        fake_human.kind = _boardroom_module.rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
        mock_room.remote_participants = {"human-1": fake_human}

        with mock.patch.dict(os.environ, self._ENV), \
             mock.patch.object(_boardroom_module, "AgentSession", return_value=mock_session), \
             mock.patch.object(_boardroom_module.rtc, "Room", return_value=mock_room), \
             mock.patch.object(_boardroom_module.lk_vendor, "mint_join_token", return_value="tok"), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_stt", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_tts", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.silero.VAD, "load", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.anthropic, "LLM", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module, "load_prompt_config", return_value=PromptConfig()), \
             mock.patch.object(_boardroom_module.pc_vendor, "context_briefing", return_value=None):
            await asyncio.gather(
                _boardroom_module.run_direct_call("papervoice-direct-agent-eng", persona),
                self._trigger_disconnect(registered, mock_room),
            )

        mock_session.generate_reply.assert_called_once()
        instructions = mock_session.generate_reply.call_args.kwargs["instructions"]
        self.assertIn("back", instructions.lower(),
                      "recovery greeting must acknowledge the agent is back")
        self.assertNotIn("Greet", instructions,
                         "recovery greeting must not use the fresh-call 'Greet' opener")

    async def test_fresh_call_uses_introduction_greeting(self):
        """PER-349: on a fresh call (no human present at join time) the agent introduces itself."""
        persona = Persona("agent-eng", "Eng", "voice-eng", "You are Eng.")
        mock_session, mock_room, registered = self._make_fixtures()
        # remote_participants is empty by default — no human was present when we joined.

        with mock.patch.dict(os.environ, self._ENV), \
             mock.patch.object(_boardroom_module, "AgentSession", return_value=mock_session), \
             mock.patch.object(_boardroom_module.rtc, "Room", return_value=mock_room), \
             mock.patch.object(_boardroom_module.lk_vendor, "mint_join_token", return_value="tok"), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_stt", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_tts", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.silero.VAD, "load", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.anthropic, "LLM", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module, "load_prompt_config", return_value=PromptConfig()), \
             mock.patch.object(_boardroom_module.pc_vendor, "context_briefing", return_value=None):
            await asyncio.gather(
                _boardroom_module.run_direct_call("papervoice-direct-agent-eng", persona),
                self._trigger_disconnect(registered, mock_room),
            )

        mock_session.generate_reply.assert_called_once()
        instructions = mock_session.generate_reply.call_args.kwargs["instructions"]
        self.assertIn("Greet", instructions,
                      "fresh-call greeting must welcome the joining participant")
        self.assertIn("introduce yourself", instructions.lower(),
                      "fresh-call greeting must introduce the agent by name")

    async def test_restart_agent_can_hear_preexisting_human(self):
        """PER-350: on a worker restart into a room where the human is already present,
        the replacement session must be wired to *hear* — not just speak.

        The 19:24 incident (job AJ_BkESbCGMqt9X) crashed mid-1:1; the replacement was
        silent because its STT/barge-in callbacks never picked up the human whose audio
        track was published before the new session started. This test pins the two
        guarantees that keep the agent's ears live on restart:

          1. The session is constructed with an STT engine and a VAD (the ears), and
          2. ``room_io.wait_for_ready()`` is awaited before greeting — that await is the
             SDK gate that runs RoomIO._init_task, which enumerates *existing* room
             participants and links the pre-existing human's audio track to STT input
             (livekit/agents/voice/room_io/room_io.py::_init_task). Without awaiting it,
             generate_reply() races ahead of audio-input subscription and the agent is
             deaf even though it appears to greet.
        """
        persona = Persona("agent-eng", "Eng", "voice-eng", "You are Eng.")
        mock_session, mock_room, registered = self._make_fixtures()

        # Restart: the human is already in the room before the replacement joins.
        fake_human = mock.MagicMock()
        fake_human.kind = _boardroom_module.rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
        mock_room.remote_participants = {"human-1": fake_human}

        captured = {}

        def _capture_agent_session(*args, **kwargs):
            captured["kwargs"] = kwargs
            return mock_session

        stt_marker = mock.MagicMock(name="stt")
        vad_marker = mock.MagicMock(name="vad")

        with mock.patch.dict(os.environ, self._ENV), \
             mock.patch.object(_boardroom_module, "AgentSession", side_effect=_capture_agent_session), \
             mock.patch.object(_boardroom_module.rtc, "Room", return_value=mock_room), \
             mock.patch.object(_boardroom_module.lk_vendor, "mint_join_token", return_value="tok"), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_stt", return_value=stt_marker), \
             mock.patch.object(_boardroom_module.el_vendor, "plugin_tts", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module.silero.VAD, "load", return_value=vad_marker), \
             mock.patch.object(_boardroom_module.anthropic, "LLM", return_value=mock.MagicMock()), \
             mock.patch.object(_boardroom_module, "load_prompt_config", return_value=PromptConfig()), \
             mock.patch.object(_boardroom_module.pc_vendor, "context_briefing", return_value=None):
            await asyncio.gather(
                _boardroom_module.run_direct_call("papervoice-direct-agent-eng", persona),
                self._trigger_disconnect(registered, mock_room),
            )

        # Ears attached: the replacement session has both an STT engine and a VAD.
        self.assertIs(captured["kwargs"].get("stt"), stt_marker,
                      "restart session must be built with an STT engine so it can hear")
        self.assertIs(captured["kwargs"].get("vad"), vad_marker,
                      "restart session must be built with a VAD so barge-in works")
        # The session actually started, and the input-linking gate was awaited.
        mock_session.start.assert_awaited_once()
        mock_session.room_io.wait_for_ready.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
