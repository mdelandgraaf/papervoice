"""Unit tests for the thin vendor adapters — no live vendor calls, no credentials.

Run: PYTHONPATH=src python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papervoice.vendors import elevenlabs as el
from papervoice.vendors import livekit as lk


class ElevenLabsAdapterTest(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("ELEVENLABS_API_KEY", None)
        os.environ.pop("ELEVENLABS_VOICE_ID", None)

    def tearDown(self):
        self._env.stop()

    def test_api_key_missing_raises(self):
        with self.assertRaisesRegex(RuntimeError, "ELEVENLABS_API_KEY"):
            el._api_key()

    def test_api_key_present(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk-test"
        self.assertEqual(el._api_key(), "sk-test")

    def test_voice_id_defaults_to_rachel(self):
        self.assertEqual(el.voice_id(), el.DEFAULT_VOICE_ID)

    def test_voice_id_override(self):
        os.environ["ELEVENLABS_VOICE_ID"] = "custom-voice"
        self.assertEqual(el.voice_id(), "custom-voice")

    def test_tts_roundtrip_sends_key_header_and_returns_audio(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk-test"
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.content = b"x" * 2000
        with mock.patch("httpx.post", return_value=fake_resp) as post:
            audio = el.tts_roundtrip("hello")
        self.assertEqual(audio, fake_resp.content)
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"]["xi-api-key"], "sk-test")
        self.assertEqual(kwargs["json"]["text"], "hello")

    def test_tts_roundtrip_rejects_suspiciously_small_audio(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk-test"
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.content = b"x" * 10
        with mock.patch("httpx.post", return_value=fake_resp):
            with self.assertRaisesRegex(RuntimeError, "suspiciously small"):
                el.tts_roundtrip("hello")


class LiveKitAdapterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
            os.environ.pop(key, None)

    def tearDown(self):
        self._env.stop()

    def test_require_env_lists_all_missing_vars(self):
        with self.assertRaisesRegex(RuntimeError, "LIVEKIT_URL.*LIVEKIT_API_KEY.*LIVEKIT_API_SECRET"):
            lk._require_env()

    def test_require_env_passes_when_all_set(self):
        os.environ.update(
            LIVEKIT_URL="wss://example.livekit.cloud",
            LIVEKIT_API_KEY="key",
            LIVEKIT_API_SECRET="secret",
        )
        lk._require_env()  # must not raise

    async def test_create_room_requires_env(self):
        with self.assertRaises(RuntimeError):
            await lk.create_room("papervoice-test")

    def test_mint_join_token_requires_env(self):
        with self.assertRaises(RuntimeError):
            lk.mint_join_token("board-member")


if __name__ == "__main__":
    unittest.main()
