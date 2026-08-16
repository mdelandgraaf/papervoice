"""Unit tests for the Papervoice web dashboard (PER-91).

No live Paperclip API calls, no live LiveKit, no network.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Prevent the module-level load_dotenv + env reads from blowing up in CI
os.environ.setdefault("PAPERCLIP_API_URL", "https://paperclip.example.com")
os.environ.setdefault("PAPERCLIP_API_KEY", "pc-test-key")
os.environ.setdefault("PAPERCLIP_COMPANY_ID", "company-1")
os.environ.setdefault("LIVEKIT_URL", "wss://livekit.example.com")
os.environ.setdefault("LIVEKIT_API_KEY", "lk-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "lk-secret")

from starlette.testclient import TestClient

from papervoice import dashboard
from papervoice.vendors import paperclip as pc_vendor

client = TestClient(dashboard.app)


_SAMPLE_AGENTS = [
    {
        "id": "a1", "name": "CEO", "role": "ceo", "title": "Chief Executive", "capabilities": "Leads",
        "metadata": {"papervoice": {"enabled": True, "voice_id": "v1", "display_name": "CEO",
                                    "livekit_identity": "agent-ceo", "roster_order": 0}},
    },
    {
        "id": "a2", "name": "Eng", "role": "engineer", "title": "Lead Engineer", "capabilities": "Builds",
        "metadata": {"papervoice": {"enabled": False}},
    },
    {
        "id": "a3", "name": "Ops", "role": "ops", "title": None, "capabilities": None,
        "metadata": None,
    },
]


class DashboardIndexTest(unittest.TestCase):
    def test_index_returns_html(self):
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Papervoice", r.text)
        self.assertIn("Agents", r.text)
        self.assertIn("Join Link", r.text)
        self.assertIn("Settings", r.text)


class DashboardAgentsApiTest(unittest.TestCase):
    def test_agents_returns_sorted_list(self):
        with mock.patch.object(pc_vendor, "get_all_agents", return_value=_SAMPLE_AGENTS):
            r = client.get("/api/agents")
        self.assertEqual(r.status_code, 200)
        agents = r.json()
        self.assertEqual(len(agents), 3)
        # Enabled agent (CEO) comes first
        self.assertEqual(agents[0]["id"], "a1")
        self.assertTrue(agents[0]["enabled"])
        self.assertEqual(agents[0]["voice_id"], "v1")
        self.assertEqual(agents[0]["roster_order"], 0)

    def test_agents_disabled_and_no_metadata_agents(self):
        with mock.patch.object(pc_vendor, "get_all_agents", return_value=_SAMPLE_AGENTS):
            r = client.get("/api/agents")
        agents = {a["id"]: a for a in r.json()}
        self.assertFalse(agents["a2"]["enabled"])
        self.assertFalse(agents["a3"]["enabled"])
        self.assertEqual(agents["a3"]["voice_id"], "")
        # display_name defaults to agent name when not set
        self.assertEqual(agents["a3"]["display_name"], "Ops")

    def test_agents_accepts_paperclip_camel_case_metadata(self):
        agents = [{
            "id": "a1", "name": "CEO", "role": "ceo", "title": None,
            "metadata": {"papervoice": {"enabled": True, "voiceId": "v1",
                "displayName": "Chief", "identity": "agent-ceo", "order": 0}},
        }]
        with mock.patch.object(pc_vendor, "get_all_agents", return_value=agents):
            result = client.get("/api/agents").json()[0]
        self.assertEqual(result["voice_id"], "v1")
        self.assertEqual(result["display_name"], "Chief")
        self.assertEqual(result["livekit_identity"], "agent-ceo")
        self.assertEqual(result["roster_order"], 0)

    def test_static_roster_defaults_make_known_agent_direct_call_ready(self):
        agent = {"id": dashboard.BOARDROOM_ROSTER[0].paperclip_agent_id,
                 "name": "Aissistent", "role": "ceo",
                 "metadata": {"papervoice": {"enabled": True}}}
        with mock.patch.object(pc_vendor, "get_all_agents", return_value=[agent]):
            result = client.get("/api/agents").json()[0]
        self.assertEqual(result["voice_id"], dashboard.BOARDROOM_ROSTER[0].voice_id)
        self.assertEqual(result["livekit_identity"], dashboard.BOARDROOM_ROSTER[0].identity)

    def test_agents_returns_502_on_paperclip_error(self):
        with mock.patch.object(pc_vendor, "get_all_agents", side_effect=RuntimeError("api down")):
            r = client.get("/api/agents")
        self.assertEqual(r.status_code, 502)


class DashboardVoiceConfigApiTest(unittest.TestCase):
    def test_save_config_calls_vendor(self):
        config = {"enabled": True, "voice_id": "v2", "display_name": "CEO",
                  "livekit_identity": "agent-ceo", "roster_order": 0}
        with mock.patch.object(pc_vendor, "update_agent_voice_config") as update:
            r = client.post("/api/agents/a1/voice-config", json=config)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        update.assert_called_once_with("a1", config)

    def test_save_config_disable_sends_false(self):
        config = {"enabled": False}
        with mock.patch.object(pc_vendor, "update_agent_voice_config") as update:
            r = client.post("/api/agents/a2/voice-config", json=config)
        self.assertEqual(r.status_code, 200)
        update.assert_called_once_with("a2", {"enabled": False})

    def test_save_config_returns_502_on_vendor_error(self):
        with mock.patch.object(pc_vendor, "update_agent_voice_config", side_effect=RuntimeError("403")):
            r = client.post("/api/agents/a1/voice-config", json={"enabled": True, "voice_id": "v1"})
        self.assertEqual(r.status_code, 502)


class DashboardJoinLinkApiTest(unittest.TestCase):
    def _fake_token(self, *args, **kwargs):
        return "tok123"

    def test_join_link_returns_url(self):
        from papervoice.vendors import livekit as lk_vendor
        with mock.patch.object(lk_vendor, "mint_join_token", side_effect=self._fake_token):
            r = client.get("/api/join-link?identity=alice&room=test-room&ttl_hours=24")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("https://meet.livekit.io/custom", data["url"])
        self.assertIn("tok123", data["url"])
        self.assertEqual(data["room"], "test-room")
        self.assertEqual(data["identity"], "alice")
        self.assertEqual(data["ttl_hours"], 24)

    def test_join_link_uses_defaults(self):
        from papervoice.vendors import livekit as lk_vendor
        from papervoice.personas import BOARDROOM_ROOM
        with mock.patch.object(lk_vendor, "mint_join_token", side_effect=self._fake_token):
            r = client.get("/api/join-link")
        self.assertEqual(r.json()["room"], BOARDROOM_ROOM)
        self.assertEqual(r.json()["identity"], "board-member")
        self.assertEqual(r.json()["ttl_hours"], 48)

    def test_join_link_returns_502_on_livekit_error(self):
        from papervoice.vendors import livekit as lk_vendor
        with mock.patch.object(lk_vendor, "mint_join_token", side_effect=RuntimeError("no creds")):
            r = client.get("/api/join-link")
        self.assertEqual(r.status_code, 502)


class DashboardWorkerStatusApiTest(unittest.TestCase):
    def test_worker_running(self):
        fake = mock.Mock()
        fake.returncode = 0
        with mock.patch("subprocess.run", return_value=fake):
            r = client.get("/api/worker-status")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["running"])

    def test_worker_not_running(self):
        fake = mock.Mock()
        fake.returncode = 1
        with mock.patch("subprocess.run", return_value=fake):
            r = client.get("/api/worker-status")
        self.assertFalse(r.json()["running"])


class DashboardSettingsApiTest(unittest.TestCase):
    def test_settings_returns_config(self):
        with mock.patch.dict(os.environ, {
            "LIVEKIT_URL": "wss://lk.example.com",
            "PAPERCLIP_API_URL": "https://pc.example.com",
            "PAPERCLIP_COMPANY_ID": "co-123",
        }):
            r = client.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("boardroom_room", data)
        self.assertIn("livekit_url", data)
        self.assertIn("paperclip_api_url", data)
        self.assertIn("paperclip_company_id", data)


if __name__ == "__main__":
    unittest.main()
