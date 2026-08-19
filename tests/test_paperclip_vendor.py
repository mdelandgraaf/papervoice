"""Unit tests for the Paperclip control-plane vendor adapter (Milestone 3).

No live Paperclip API calls, no credentials.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import multiprocessing as mp
import os
import signal
import sys
import tempfile
import time
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


def _concurrent_drain_worker(pending_dir: str, counter, lock, latency_s: float) -> None:
    """Drain the shared queue from a separate process, counting each post.

    The latency stub keeps the process inside drain_pending_comments' post
    window (between claiming a file and unlinking it) so overlapping drainers
    actually race — an instant stub would hide the double-post bug.
    """
    os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending_dir

    def counting_post(issue_id, body):
        time.sleep(latency_s)
        with lock:
            counter.value += 1
        return {"id": "x"}

    pc.post_comment = counting_post
    pc.drain_pending_comments()


def _hang_after_claim_worker(pending_dir: str) -> None:
    """Drain the queue but block forever inside the post, holding the claim.

    Simulates a drainer hard-killed in the window between claiming a queued
    comment (atomic rename to *.draining-<pid>) and unlinking/requeueing it. The
    parent SIGKILLs this process while it sleeps, orphaning the claim.
    """
    os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending_dir

    def hanging_post(issue_id, body):
        time.sleep(3600)  # block inside the claim->unlink window until SIGKILLed
        return {"id": "x"}

    pc.post_comment = hanging_post
    pc.drain_pending_comments()


class PaperclipAdapterTest(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in (
            "PAPERCLIP_API_URL",
            "PAPERCLIP_API_KEY",
            "PAPERCLIP_BOARDROOM_API_KEY",
            "PAPERCLIP_COMPANY_ID",
        ):
            os.environ.pop(key, None)

    def tearDown(self):
        self._env.stop()

    def test_api_base_missing_raises(self):
        with self.assertRaisesRegex(RuntimeError, "PAPERCLIP_API_URL"):
            pc._api_base()

    def test_api_key_missing_raises(self):
        with self.assertRaisesRegex(RuntimeError, "PAPERCLIP_BOARDROOM_API_KEY"):
            pc._api_key()

    def test_api_key_prefers_durable_boardroom_key(self):
        # The durable board-minted key (PER-388) wins over the run-scoped JWT so the
        # long-running worker never depends on a ~1h token (PER-386 option A).
        os.environ["PAPERCLIP_API_KEY"] = "run-scoped-jwt"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_durable_key"
        self.assertEqual(pc._api_key(), "pcp_durable_key")

    def test_api_key_falls_back_to_run_jwt(self):
        # Dev shells without PER-388's secret still work via the run JWT fallback.
        os.environ["PAPERCLIP_API_KEY"] = "run-scoped-jwt"
        self.assertEqual(pc._api_key(), "run-scoped-jwt")

    def test_api_key_ignores_empty_durable_key(self):
        # An empty/blank durable var must not shadow a valid run JWT.
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = ""
        os.environ["PAPERCLIP_API_KEY"] = "run-scoped-jwt"
        self.assertEqual(pc._api_key(), "run-scoped-jwt")

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

    def test_search_issues_sends_query_and_returns_detail_with_description(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(
            return_value=[
                {"identifier": "PER-5", "title": "Voice fix", "status": "done",
                 "priority": "high", "description": "  Shipped the barge-in fix.  "},
            ]
        )
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            issues = pc.search_issues("PER-5")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["identifier"], "PER-5")
        self.assertEqual(issues[0]["status"], "done")  # not status-filtered: closed matches returned
        self.assertEqual(issues[0]["description"], "Shipped the barge-in fix.")
        _, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["q"], "PER-5")
        self.assertNotIn("status", kwargs["params"])  # search spans all statuses
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pc-test-key")

    def test_search_issues_truncates_long_description(self):
        self._set_env()
        long_desc = "x" * (pc._LOOKUP_DESC_CHARS + 200)
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(
            return_value=[{"identifier": "PER-6", "title": "T", "status": "todo",
                           "priority": "low", "description": long_desc}]
        )
        with mock.patch("httpx.get", return_value=fake_resp):
            issues = pc.search_issues("T")
        self.assertLessEqual(len(issues[0]["description"]), pc._LOOKUP_DESC_CHARS + 1)  # +1 for the ellipsis
        self.assertTrue(issues[0]["description"].endswith("…"))

    def test_search_issues_respects_limit(self):
        self._set_env()
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(
            return_value=[{"identifier": f"PER-{i}", "title": "x", "status": "todo",
                           "priority": "low", "description": ""} for i in range(10)]
        )
        with mock.patch("httpx.get", return_value=fake_resp):
            issues = pc.search_issues("x", limit=3)
        self.assertEqual(len(issues), 3)

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

    def test_concurrent_drain_posts_each_comment_exactly_once(self):
        # PER-257 regression (load-test scenario B2, fast variant): overlapping
        # wakes call drain_pending_comments concurrently and must never double-
        # post a queued comment. The atomic os.rename claim in the drainer hands
        # each file to exactly one process; before that fix this posted queued
        # comments several times over. Kept small so it runs in the unit suite.
        self._set_env()
        ctx = mp.get_context("fork")
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            queued = 24
            for i in range(queued):
                pc.queue_comment(f"issue-{i}", f"body {i}")

            counter = ctx.Value("i", 0)
            lock = ctx.Lock()
            procs = [
                ctx.Process(target=_concurrent_drain_worker, args=(pending, counter, lock, 0.01))
                for _ in range(4)
            ]
            for p in procs:
                p.start()
            for p in procs:
                p.join()

            # exactly-once: every queued comment posted exactly once, none left behind
            self.assertEqual(counter.value, queued)
            self.assertEqual(list(os.scandir(pending)), [])

    def test_orphaned_claim_is_reclaimed_after_drainer_crash(self):
        # PER-259 regression (load-test scenario D, fast variant): a drainer
        # hard-killed between claiming a queued comment and unlinking it orphans
        # the *.draining-<pid> file. That name no longer matches the *.json glob,
        # so pre-fix no later wake ever retried it and the comment (a transcript,
        # never a secret) was silently lost. The reclaim step must requeue it so
        # a later drain still posts it — zero lost comments.
        self._set_env()
        ctx = mp.get_context("fork")
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            pc.queue_comment("issue-1", "summary text")

            proc = ctx.Process(target=_hang_after_claim_worker, args=(pending,))
            proc.start()
            # wait until it has claimed the file (renamed *.json -> *.draining-<pid>)
            deadline = time.time() + 5
            claimed = False
            while time.time() < deadline:
                if list(Path(pending).glob("*.draining-*")):
                    claimed = True
                    break
                time.sleep(0.01)
            self.assertTrue(claimed, "drainer never claimed the queued comment")

            os.kill(proc.pid, signal.SIGKILL)  # hard-kill mid-claim -> orphan the claim
            proc.join()

            # the orphan no longer matches *.json, so a naive re-drain would lose it
            self.assertEqual(list(Path(pending).glob("*.json")), [])
            self.assertTrue(list(Path(pending).glob("*.draining-*")))

            # a later wake reclaims the orphan and posts it — zero lost comments
            with mock.patch.object(pc, "post_comment", return_value={"id": "c1"}) as post:
                self.assertEqual(pc.drain_pending_comments(), 1)
            post.assert_called_once_with("issue-1", "summary text")
            self.assertEqual(list(os.scandir(pending)), [])

    def test_reclaim_leaves_live_drainer_claim_untouched(self):
        # A claim owned by a still-running pid and touched recently is in-flight,
        # not orphaned; reclaim must not yank it back and let another drainer
        # double-post it. Use the current (alive) pid with a fresh mtime.
        self._set_env()
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            directory = Path(pending)
            live_claim = directory / f".pending-live.json.draining-{os.getpid()}"
            live_claim.write_text('{"issue_id": "issue-1", "body": "in flight"}')
            self.assertEqual(pc._reclaim_orphaned_claims(directory), 0)
            self.assertTrue(live_claim.exists())

    def test_reclaim_requeues_stale_claim_from_live_pid(self):
        # pid-reuse guard: a claim whose pid is alive but that has sat far longer
        # than any healthy post is stale and must be reclaimed. Force the age
        # threshold to 0 so the current pid's claim counts as stale.
        self._set_env()
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            os.environ["PAPERCLIP_CLAIM_STALE_SECONDS"] = "0"
            try:
                directory = Path(pending)
                original = ".pending-stale.json"
                claim = directory / f"{original}.draining-{os.getpid()}"
                claim.write_text('{"issue_id": "issue-1", "body": "stale"}')
                self.assertEqual(pc._reclaim_orphaned_claims(directory), 1)
                self.assertTrue((directory / original).exists())
                self.assertEqual(list(directory.glob("*.draining-*")), [])
            finally:
                os.environ.pop("PAPERCLIP_CLAIM_STALE_SECONDS", None)

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

    # --- probe_setup / fetch_boardroom_config (PER-415 healthcheck probes) ---

    def test_probe_setup_hits_agent_route_with_pcp_bearer_and_company(self):
        self._set_env()
        client = pc.PaperclipClient(company_id="co-1", api_key="pcp_boardroom")
        body = {
            "companyId": "co-1",
            "setup": [{"key": "livekit", "status": "ok", "title": "LiveKit credentials", "detail": "ok"}],
            "allGreen": True,
            "configToken": {"token": "abc.def", "expiresAt": "2026-08-19T00:00:00Z"},
        }
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=body)
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            result = client.probe_setup()
        self.assertEqual(result, body)
        # Called the /api/plugins/papervoice/api/probe-setup route with the client's own pcp_ bearer.
        url = get.call_args[0][0]
        self.assertIn("/api/plugins/papervoice/api/probe-setup", url)
        _, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pcp_boardroom")
        self.assertEqual(kwargs["params"]["companyId"], "co-1")

    def test_probe_setup_propagates_http_errors(self):
        self._set_env()
        client = pc.PaperclipClient(company_id="co-1", api_key="pcp_boardroom")
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock(side_effect=_status_error(403))
        with mock.patch("httpx.get", return_value=fake_resp):
            with self.assertRaises(httpx.HTTPStatusError):
                client.probe_setup()

    def test_fetch_boardroom_config_uses_hmac_bearer_not_pcp_key(self):
        self._set_env()
        client = pc.PaperclipClient(company_id="co-1", api_key="pcp_boardroom")
        body = {
            "boardroomApiKey": "pcp_returned",
            "liveKitUrl": "wss://x.livekit.cloud",
            "liveKitApiKey": "APIkey",
            "liveKitApiSecret": "APIsecret",
        }
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=body)
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            result = client.fetch_boardroom_config("hmac.token.value")
        self.assertEqual(result, body)
        url = get.call_args[0][0]
        self.assertIn("/api/plugins/papervoice/api/boardroom-config", url)
        _, kwargs = get.call_args
        # HMAC token, NOT the client's own pcp_ key — matches the boardroom worker's path.
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer hmac.token.value")
        self.assertEqual(kwargs["params"]["companyId"], "co-1")

    def test_fetch_boardroom_config_propagates_http_errors(self):
        self._set_env()
        client = pc.PaperclipClient(company_id="co-1", api_key="pcp_boardroom")
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock(side_effect=_status_error(409))
        with mock.patch("httpx.get", return_value=fake_resp):
            with self.assertRaises(httpx.HTTPStatusError):
                client.fetch_boardroom_config("expired.hmac.token")


if __name__ == "__main__":
    unittest.main()
