"""Unit tests for the PER-405 multi-tenant surface.

- ``PaperclipClient`` builds the right URLs and headers for its bound company.
- ``load_boardroom_key_map`` merges the three configured sources
  (default env, per-company env vars, JSON file) and normalizes UUIDs.
- ``client_for_company`` picks the right key for a given companyId and fails
  fast with ``KeyError`` when none is configured for a non-default company.
- ``_company_id_from_room`` extracts companyId from LiveKit room metadata and
  falls back cleanly when the field is absent/malformed.
- ``_client_for_ctx`` picks the multi-tenant client for a job or falls back
  to the env default when the room has no companyId.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice import boardroom
from papervoice.vendors import paperclip as pc


class LoadBoardroomKeyMapTest(unittest.TestCase):
    """PER-405: per-company boardroom credential loader.

    Three input sources must merge cleanly and the encoded UUID env-var form
    must round-trip to the canonical dashed lowercase companyId.
    """

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com"

    def tearDown(self):
        self._env.stop()

    def test_returns_empty_when_nothing_configured(self):
        self.assertEqual(pc.load_boardroom_key_map(), {})

    def test_default_env_maps_primary_company_to_boardroom_key(self):
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"
        self.assertEqual(pc.load_boardroom_key_map(), {"co-primary": "pcp_primary"})

    def test_default_env_falls_back_to_run_jwt(self):
        # Preserved single-tenant behaviour: run JWT is used only when the durable
        # key is absent (dev shells that never had PER-388's secret injected).
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_API_KEY"] = "run-jwt"
        self.assertEqual(pc.load_boardroom_key_map(), {"co-primary": "run-jwt"})

    def test_per_company_env_var_decodes_uuid(self):
        # Underscored-uppercase env-var name must round-trip to the canonical
        # dashed-lowercase companyId form; documented for operators.
        uuid_dashed = "8126b511-8dd2-4fa0-8a4f-22d630b83108"
        var_name = "PAPERCLIP_BOARDROOM_API_KEY_" + uuid_dashed.upper().replace("-", "_")
        os.environ[var_name] = "pcp_bvc"
        key_map = pc.load_boardroom_key_map()
        self.assertEqual(key_map, {uuid_dashed: "pcp_bvc"})

    def test_per_company_env_and_default_env_merge(self):
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY_CO_OTHER"] = "pcp_other"
        key_map = pc.load_boardroom_key_map()
        self.assertEqual(key_map, {"co-primary": "pcp_primary", "co-other": "pcp_other"})

    def test_json_file_source_is_merged_and_wins_last(self):
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_stale"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fp:
            json.dump({"co-primary": "pcp_fresh", "co-third": "pcp_third"}, fp)
            path = fp.name
        try:
            os.environ["PAPERCLIP_BOARDROOM_KEYS_JSON"] = path
            key_map = pc.load_boardroom_key_map()
            # JSON is applied last so it can rotate the primary key
            self.assertEqual(key_map["co-primary"], "pcp_fresh")
            self.assertEqual(key_map["co-third"], "pcp_third")
        finally:
            os.unlink(path)

    def test_malformed_json_file_is_logged_and_ignored(self):
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fp:
            fp.write("not-json")
            path = fp.name
        try:
            os.environ["PAPERCLIP_BOARDROOM_KEYS_JSON"] = path
            # Must not raise — a corrupt operator-provided file should degrade
            # to the env-only config, not crash the worker at startup.
            key_map = pc.load_boardroom_key_map()
            self.assertEqual(key_map, {"co-primary": "pcp_primary"})
        finally:
            os.unlink(path)


class ClientForCompanyTest(unittest.TestCase):
    """client_for_company resolves the right key or fails fast."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com"
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"

    def tearDown(self):
        self._env.stop()

    def test_none_or_empty_company_returns_default(self):
        client = pc.client_for_company(None)
        self.assertEqual(client.company_id, "co-primary")
        self.assertEqual(client.api_key, "pcp_primary")

    def test_matching_default_env_company_returns_default(self):
        client = pc.client_for_company("co-primary")
        self.assertEqual(client.company_id, "co-primary")
        self.assertEqual(client.api_key, "pcp_primary")

    def test_second_company_from_env_var(self):
        os.environ["PAPERCLIP_BOARDROOM_API_KEY_CO_BVC"] = "pcp_bvc"
        client = pc.client_for_company("co-bvc")
        self.assertEqual(client.company_id, "co-bvc")
        self.assertEqual(client.api_key, "pcp_bvc")

    def test_unknown_company_raises_key_error(self):
        # Fail-fast: refuse to serve a call with the wrong company's credential.
        with self.assertRaisesRegex(KeyError, "co-unknown"):
            pc.client_for_company("co-unknown")


class PaperclipClientPerCompanyTest(unittest.TestCase):
    """A PaperclipClient issues requests bound to its own companyId + key,
    independent of the process-wide default env."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        # Default env picks a DIFFERENT company than the client under test, so a
        # regression that accidentally re-derives from env would show up here.
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com"
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"

    def tearDown(self):
        self._env.stop()

    def test_agent_context_uses_client_company_and_key(self):
        client = pc.PaperclipClient(company_id="co-bvc", api_key="pcp_bvc")
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=[])
        with mock.patch("httpx.get", return_value=fake_resp) as get:
            client.agent_context("agent-1")
        args, kwargs = get.call_args
        self.assertIn("co-bvc", args[0])
        self.assertNotIn("co-primary", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pcp_bvc")

    def test_create_issue_uses_client_company_and_key(self):
        client = pc.PaperclipClient(company_id="co-bvc", api_key="pcp_bvc")
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"identifier": "BVC-1", "id": "u"})
        with mock.patch("httpx.post", return_value=fake_resp) as post:
            client.create_issue("t")
        args, kwargs = post.call_args
        self.assertIn("co-bvc", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pcp_bvc")


class QueuedCommentDrainCompanyTest(unittest.TestCase):
    """PER-405: queued comments record their owning companyId so a drain in a
    multi-tenant worker picks the right per-company client."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com"
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY_CO_BVC"] = "pcp_bvc"

    def tearDown(self):
        self._env.stop()

    def test_drain_selects_client_per_queued_company(self):
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending

            pc.queue_comment("issue-primary", "hi", company_id="co-primary")
            pc.queue_comment("issue-bvc", "hi", company_id="co-bvc")

            seen: list[tuple[str, str]] = []

            def fake_post(self, issue_id, body):
                seen.append((self.api_key, issue_id))
                return {"id": "c"}

            with mock.patch.object(pc.PaperclipClient, "post_comment", fake_post):
                self.assertEqual(pc.drain_pending_comments(), 2)

            # Each queued comment posted with its matching per-company key.
            self.assertEqual(sorted(seen), sorted([("pcp_primary", "issue-primary"),
                                                     ("pcp_bvc", "issue-bvc")]))

    def test_drain_leaves_unknown_company_queued(self):
        with tempfile.TemporaryDirectory() as pending:
            os.environ["PAPERCLIP_PENDING_POSTS_DIR"] = pending
            pc.queue_comment("issue-x", "hi", company_id="co-orphaned")
            # No key configured for co-orphaned -> should stay queued, not raise.
            self.assertEqual(pc.drain_pending_comments(), 0)
            self.assertEqual(len(list(Path(pending).glob("*.json"))), 1)


class CompanyIdFromRoomTest(unittest.TestCase):
    """boardroom.entrypoint reads companyId from LiveKit room metadata."""

    def _ctx(self, metadata):
        ctx = mock.MagicMock()
        ctx.room.metadata = metadata
        return ctx

    def test_reads_company_id_from_metadata_json(self):
        ctx = self._ctx(json.dumps({"companyId": "co-bvc", "agentIds": ["a"]}))
        self.assertEqual(boardroom._company_id_from_room(ctx), "co-bvc")

    def test_returns_none_when_metadata_absent(self):
        self.assertIsNone(boardroom._company_id_from_room(self._ctx(None)))
        self.assertIsNone(boardroom._company_id_from_room(self._ctx("")))

    def test_returns_none_when_metadata_is_not_json(self):
        # Best-effort: malformed metadata must not crash the entrypoint.
        self.assertIsNone(boardroom._company_id_from_room(self._ctx("not-json")))

    def test_returns_none_when_company_id_absent(self):
        ctx = self._ctx(json.dumps({"agentIds": ["a"]}))
        self.assertIsNone(boardroom._company_id_from_room(ctx))


class ClientForCtxTest(unittest.TestCase):
    """boardroom._client_for_ctx wires room-metadata companyId to the right key."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        os.environ["PAPERCLIP_API_URL"] = "https://paperclip.example.com"
        os.environ["PAPERCLIP_COMPANY_ID"] = "co-primary"
        os.environ["PAPERCLIP_BOARDROOM_API_KEY"] = "pcp_primary"

    def tearDown(self):
        self._env.stop()

    def _ctx(self, metadata):
        ctx = mock.MagicMock()
        ctx.room.metadata = metadata
        return ctx

    def test_falls_back_to_default_when_metadata_missing_company(self):
        # Old join links and single-tenant deployments have no companyId in the
        # metadata; must fall back to the env default cleanly.
        client = boardroom._client_for_ctx(self._ctx(None))
        self.assertEqual(client.company_id, "co-primary")

    def test_matches_metadata_company_to_configured_key(self):
        os.environ["PAPERCLIP_BOARDROOM_API_KEY_CO_BVC"] = "pcp_bvc"
        ctx = self._ctx(json.dumps({"companyId": "co-bvc"}))
        client = boardroom._client_for_ctx(ctx)
        self.assertEqual(client.company_id, "co-bvc")
        self.assertEqual(client.api_key, "pcp_bvc")

    def test_unknown_company_raises_key_error(self):
        ctx = self._ctx(json.dumps({"companyId": "co-unknown"}))
        with self.assertRaisesRegex(KeyError, "co-unknown"):
            boardroom._client_for_ctx(ctx)


if __name__ == "__main__":
    unittest.main()
