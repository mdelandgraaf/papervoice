"""Unit tests for the Paperclip control-plane vendor adapter (Milestone 3).

No live Paperclip API calls, no credentials.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.vendors import paperclip as pc


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


class PaperclipAdapterTest(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in ("PAPERCLIP_API_URL", "PAPERCLIP_API_KEY", "PAPERCLIP_COMPANY_ID"):
            os.environ.pop(key, None)

    def tearDown(self):
        self._env.stop()

    def test_api_base_missing_raises(self):
        with self.assertRaisesRegex(RuntimeError, "PAPERCLIP_API_URL"):
            pc._api_base()

    def test_api_key_missing_raises(self):
        with self.assertRaisesRegex(RuntimeError, "PAPERCLIP_API_KEY"):
            pc._api_key()

    def test_company_id_missing_raises(self):
        with self.assertRaisesRegex(RuntimeError, "PAPERCLIP_COMPANY_ID"):
            pc._company_id()

    def test_api_base_strips_trailing_slash_and_api_suffix(self):
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com/api/"
        self.assertEqual(pc._api_base(), "https://paperclip.example.com")

    def test_api_base_passes_through_bare_host(self):
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com"
        self.assertEqual(pc._api_base(), "https://paperclip.example.com")

    def _set_env(self):
        os.environ.update(
            PAPERCLIP_API_URL="https://paperclip.example.com",
            PAPERCLIP_API_KEY="pc-test-key",
            PAPERCLIP_COMPANY_ID="company-1",
        )

    def test_agent_context_sends_auth_header_and_filters(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(
            return_value=[
                {"identifier": "PER-1", "title": "Fix thing", "status": "in_progress", "priority": "high"},
                {"identifier": "PER-2", "title": "Other thing", "status": "todo", "priority": "low"},
            ]
        )
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            issues = pc.agent_context("agent-1", limit=5)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0], {"identifier": "PER-1", "title": "Fix thing", "status": "in_progress", "priority": "high"})
        _, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pc-test-key")
        self.assertEqual(kwargs["params"]["assigneeAgentId"], "agent-1")
        self.assertIn("todo", kwargs["params"]["status"])

    def test_agent_context_respects_limit(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(
            return_value=[{"identifier": f"PER-{i}", "title": "x", "status": "todo", "priority": "low"} for i in range(10)]
        )
        with mock.patch("httpx.get", return_value=fake_resp):
            issues = pc.agent_context("agent-1", limit=3)
        self.assertEqual(len(issues), 3)

    def test_company_snapshot_omits_assignee_filter(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=[])
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            pc.company_snapshot()
        _, kwargs = get.call_args
        self.assertNotIn("assigneeAgentId", kwargs["params"])

    def test_context_briefing_uses_agent_context_when_agent_id_given(self):
        self._set_env()
        with mock.patch.object(pc, "agent_context", return_value=[
            {"identifier": "PER-1", "title": "Fix thing", "status": "in_progress", "priority": "high"}
        ]) as agent_ctx:
            with mock.patch.object(pc, "company_snapshot") as snapshot:
                text = pc.context_briefing("agent-1")
        agent_ctx.assert_called_once_with("agent-1", limit=5)
        snapshot.assert_not_called()
        self.assertIn("PER-1", text)
        self.assertIn("Fix thing", text)

    def test_context_briefing_falls_back_to_company_snapshot_when_no_agent_id(self):
        self._set_env()
        with mock.patch.object(pc, "agent_context") as agent_ctx:
            with mock.patch.object(pc, "company_snapshot", return_value=[]) as snapshot:
                text = pc.context_briefing(None)
        agent_ctx.assert_not_called()
        snapshot.assert_called_once()
        self.assertEqual(text, "no open issues.")

    def test_context_briefing_empty_issue_list(self):
        self._set_env()
        with mock.patch.object(pc, "agent_context", return_value=[]):
            text = pc.context_briefing("agent-1")
        self.assertEqual(text, "no open issues.")

    def test_create_issue_posts_expected_payload_and_returns_identifier(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"identifier": "PER-99", "id": "uuid-99"})
        with mock.patch("httpx.post", return_value=fake_resp) as post:
            result = pc.create_issue("Follow up on X", description="details", assignee_agent_id="agent-1")
        self.assertEqual(result, {"identifier": "PER-99", "id": "uuid-99"})
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["title"], "Follow up on X")
        self.assertEqual(kwargs["json"]["assigneeAgentId"], "agent-1")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pc-test-key")

    def test_create_issue_omits_assignee_when_none(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"identifier": "PER-100", "id": "uuid-100"})
        with mock.patch("httpx.post", return_value=fake_resp) as post:
            pc.create_issue("No assignee")
        _, kwargs = post.call_args
        self.assertNotIn("assigneeAgentId", kwargs["json"])

    def test_post_comment_sends_body(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"id": "comment-1"})
        with mock.patch("httpx.post", return_value=fake_resp) as post:
            result = pc.post_comment("issue-1", "summary text")
        self.assertEqual(result, {"id": "comment-1"})
        args, kwargs = post.call_args
        self.assertIn("issue-1", args[0])
        self.assertEqual(kwargs["json"]["body"], "summary text")

    def test_failed_post_is_persisted_and_drained_after_expiry(self):
        self._set_env()
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            with mock.patch.object(pc, "post_comment", side_effect=RuntimeError("expired token")):
                self.assertIsNone(pc.post_comment_or_queue("issue-1", "summary text"))
            queued = list(os.scandir(pending))
            self.assertEqual(len(queued), 1)

            with mock.patch.object(pc, "post_comment", return_value={"id": "comment-1"}) as post:
                self.assertEqual(pc.drain_pending_comments(), 1)
            post.assert_called_once_with("issue-1", "summary text")
            self.assertEqual(list(os.scandir(pending)), [])

    def test_failed_retry_remains_queued(self):
        self._set_env()
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            pc.queue_comment("issue-1", "summary text")
            with mock.patch.object(pc, "post_comment", side_effect=RuntimeError("still expired")):
                self.assertEqual(pc.drain_pending_comments(), 0)
            self.assertEqual(len(list(os.scandir(pending))), 1)

    def test_list_agent_ids_returns_id_set(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=[{"id": "a"}, {"id": "b"}])
        with mock.patch("httpx.get", return_value=fake_resp):
            self.assertEqual(pc.list_agent_ids(), {"a", "b"})

    # --- get_voice_enabled_agents (PER-89) ---

    def _fake_agents_resp(self, agents: list[dict]):
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=agents)
        return fake_resp

    def test_get_voice_enabled_agents_returns_enabled_only(self):
        self._set_env()
        agents = [
            {"id": "a1", "name": "CEO", "role": "ceo", "title": None, "capabilities": None,
             "metadata": {"papervoice": {"enabled": True, "voice_id": "v1", "livekit_identity": "agent-ceo",
                                         "display_name": "CEO", "roster_order": 0}}},
            {"id": "a2", "name": "Bot", "role": "general", "title": None, "capabilities": None,
             "metadata": {"papervoice": {"enabled": False}}},
            {"id": "a3", "name": "Eng", "role": "engineer", "title": "Lead Eng", "capabilities": "Builds stuff",
             "metadata": {"papervoice": {"enabled": True, "voice_id": "v2", "livekit_identity": "agent-eng",
                                         "display_name": "Eng", "roster_order": 1}}},
        ]
        with mock.patch("httpx.get", return_value=self._fake_agents_resp(agents)):
            result = pc.get_voice_enabled_agents()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].agent_id, "a1")
        self.assertEqual(result[1].agent_id, "a3")

    def test_get_voice_enabled_agents_sorted_by_roster_order(self):
        self._set_env()
        agents = [
            {"id": "a2", "name": "Eng", "role": "engineer", "title": None, "capabilities": None,
             "metadata": {"papervoice": {"enabled": True, "voice_id": "v2", "roster_order": 1}}},
            {"id": "a1", "name": "CEO", "role": "ceo", "title": None, "capabilities": None,
             "metadata": {"papervoice": {"enabled": True, "voice_id": "v1", "roster_order": 0}}},
        ]
        with mock.patch("httpx.get", return_value=self._fake_agents_resp(agents)):
            result = pc.get_voice_enabled_agents()
        self.assertEqual(result[0].agent_id, "a1")
        self.assertEqual(result[1].agent_id, "a2")

    def test_get_voice_enabled_agents_accepts_paperclip_camel_case_metadata(self):
        self._set_env()
        agents = [{
            "id": "a1", "name": "CEO", "role": "ceo", "title": None, "capabilities": None,
            "metadata": {"papervoice": {"enabled": True, "voiceId": "v1",
                "identity": "agent-ceo", "displayName": "Chief", "order": 0}},
        }]
        with mock.patch("httpx.get", return_value=self._fake_agents_resp(agents)):
            cfg = pc.get_voice_enabled_agents()[0]
        self.assertEqual(cfg.voice_id, "v1")
        self.assertEqual(cfg.livekit_identity, "agent-ceo")
        self.assertEqual(cfg.display_name, "Chief")
        self.assertEqual(cfg.roster_order, 0)

    def test_get_voice_enabled_agents_skips_missing_voice_id(self):
        self._set_env()
        agents = [
            {"id": "a1", "name": "CEO", "role": "ceo", "title": None, "capabilities": None,
             "metadata": {"papervoice": {"enabled": True}}},  # no voice_id
        ]
        with mock.patch("httpx.get", return_value=self._fake_agents_resp(agents)):
            result = pc.get_voice_enabled_agents()
        self.assertEqual(result, [])

    def test_get_voice_enabled_agents_derives_defaults_when_optional_fields_missing(self):
        self._set_env()
        agents = [
            {"id": "a1", "name": "My Agent", "role": "general", "title": "Title A", "capabilities": "Does stuff",
             "metadata": {"papervoice": {"enabled": True, "voice_id": "v1", "roster_order": 0}}},
        ]
        with mock.patch("httpx.get", return_value=self._fake_agents_resp(agents)):
            result = pc.get_voice_enabled_agents()
        self.assertEqual(len(result), 1)
        cfg = result[0]
        self.assertEqual(cfg.livekit_identity, "agent-my-agent")
        self.assertEqual(cfg.display_name, "My Agent")
        self.assertEqual(cfg.title, "Title A")
        self.assertEqual(cfg.capabilities, "Does stuff")

    def test_get_voice_enabled_agents_handles_null_metadata(self):
        self._set_env()
        agents = [
            {"id": "a1", "name": "Headless", "role": "general", "title": None, "capabilities": None,
             "metadata": None},
        ]
        with mock.patch("httpx.get", return_value=self._fake_agents_resp(agents)):
            result = pc.get_voice_enabled_agents()
        self.assertEqual(result, [])

    def test_is_auth_error_true_for_401_and_403(self):
        self.assertTrue(pc.is_auth_error(_status_error(401)))
        self.assertTrue(pc.is_auth_error(_status_error(403)))

    def test_is_auth_error_false_for_other_status_and_other_exceptions(self):
        self.assertFalse(pc.is_auth_error(_status_error(500)))
        self.assertFalse(pc.is_auth_error(RuntimeError("boom")))

    # --- get_all_agents (PER-91 dashboard) ---

    def test_get_all_agents_returns_full_records(self):
        self._set_env()
        agents = [
            {"id": "a1", "name": "CEO", "role": "ceo", "title": "Chief Executive",
             "capabilities": "Leads the company", "metadata": {"papervoice": {"enabled": True, "voice_id": "v1"}}},
            {"id": "a2", "name": "Eng", "role": "engineer", "title": None, "capabilities": None, "metadata": None},
        ]
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=agents)
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            result = pc.get_all_agents()
        self.assertEqual(result, agents)
        _, kwargs = get.call_args
        self.assertIn("agents", kwargs.get("url", "") + get.call_args[0][0])

    def test_get_all_agents_raises_on_api_error(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock(side_effect=_status_error(503))
        with mock.patch("httpx.get", return_value=fake_resp):
            with self.assertRaises(httpx.HTTPStatusError):
                pc.get_all_agents()

    # --- update_agent_voice_config (PER-91 dashboard) ---

    def test_update_agent_voice_config_sends_patch_with_metadata(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        config = {"enabled": True, "voice_id": "v1", "display_name": "CEO",
                  "livekit_identity": "agent-ceo", "roster_order": 0}
        with mock.patch("httpx.patch", return_value=fake_resp) as patch:
            pc.update_agent_voice_config("agent-1", config)
        _, kwargs = patch.call_args
        self.assertEqual(kwargs["json"], {"metadata": {"papervoice": config}})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pc-test-key")
        self.assertIn("agent-1", patch.call_args[0][0])

    def test_update_agent_voice_config_sends_none_to_clear(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        with mock.patch("httpx.patch", return_value=fake_resp) as patch:
            pc.update_agent_voice_config("agent-1", None)
        _, kwargs = patch.call_args
        self.assertEqual(kwargs["json"], {"metadata": {"papervoice": None}})

    def test_update_agent_voice_config_raises_on_403(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock(side_effect=_status_error(403))
        with mock.patch("httpx.patch", return_value=fake_resp):
            with self.assertRaises(httpx.HTTPStatusError):
                pc.update_agent_voice_config("agent-1", {"enabled": True, "voice_id": "v1"})


if __name__ == "__main__":
    unittest.main()
