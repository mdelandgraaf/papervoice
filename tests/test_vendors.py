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

    def test_voice_id_defaults_to_default_premade(self):
        self.assertEqual(el.voice_id(), el.DEFAULT_VOICE_ID)

    def test_default_voice_is_a_present_premade_not_rachel(self):
        # PER-311: DEFAULT_VOICE_ID must be a premade voice actually on this account.
        # Rachel (21m00Tcm4TlvDq8ikWAM) is NOT on it; Sarah is the fallback target.
        self.assertEqual(el.DEFAULT_VOICE_ID, "EXAVITQu4vr4xnSDxMaL")  # Sarah
        self.assertNotEqual(el.DEFAULT_VOICE_ID, "21m00Tcm4TlvDq8ikWAM")  # Rachel

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

    def test_stt_roundtrip_sends_key_header_and_returns_text(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk-test"
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"text": "hello world"})
        with mock.patch("httpx.post", return_value=fake_resp) as post:
            text = el.stt_roundtrip(b"fake-audio-bytes")
        self.assertEqual(text, "hello world")
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"]["xi-api-key"], "sk-test")
        self.assertEqual(kwargs["files"]["file"][1], b"fake-audio-bytes")

    def test_stt_roundtrip_defaults_to_a_fresh_tts_synth(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk-test"
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"text": "hello"})
        with mock.patch.object(el, "tts_roundtrip", return_value=b"synth-audio") as tts:
            with mock.patch("httpx.post", return_value=fake_resp) as post:
                el.stt_roundtrip()
        tts.assert_called_once_with()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["files"]["file"][1], b"synth-audio")

    def test_stt_roundtrip_rejects_empty_transcript(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk-test"
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value={"text": "   "})
        with mock.patch("httpx.post", return_value=fake_resp):
            with self.assertRaisesRegex(RuntimeError, "empty transcript"):
                el.stt_roundtrip(b"fake-audio-bytes")


class IsStreamableCategoryTest(unittest.TestCase):
    """PER-311: streamability is category x subscription entitlement, not library presence."""

    PRO = {"can_use_professional_voice_cloning": True, "can_use_instant_voice_cloning": True}
    NONE = {"can_use_professional_voice_cloning": False, "can_use_instant_voice_cloning": False}
    INSTANT_ONLY = {"can_use_professional_voice_cloning": False, "can_use_instant_voice_cloning": True}

    def test_premade_always_streams_regardless_of_entitlements(self):
        for cat in ("premade", "high_quality", "famous", "PREMADE"):
            self.assertTrue(el._is_streamable_category(cat, self.NONE), cat)

    def test_professional_needs_professional_entitlement(self):
        self.assertFalse(el._is_streamable_category("professional", self.NONE))
        self.assertFalse(el._is_streamable_category("professional", self.INSTANT_ONLY))
        self.assertTrue(el._is_streamable_category("professional", self.PRO))

    def test_cloned_and_generated_need_instant_entitlement(self):
        for cat in ("cloned", "generated"):
            self.assertFalse(el._is_streamable_category(cat, self.NONE), cat)
            self.assertTrue(el._is_streamable_category(cat, self.INSTANT_ONLY), cat)

    def test_unknown_category_biases_strict(self):
        # An unrecognized custom category must not stream without the instant entitlement.
        self.assertFalse(el._is_streamable_category("some-future-category", self.NONE))
        self.assertFalse(el._is_streamable_category("", self.NONE))
        self.assertFalse(el._is_streamable_category(None, self.NONE))
        self.assertTrue(el._is_streamable_category("some-future-category", self.INSTANT_ONLY))


class ResolveVoiceIdTest(unittest.TestCase):
    """PER-306/PER-311: a voice_id that won't STREAM on this tier (absent from the
    account, or a professional/cloned voice the tier can't stream) must fall back to a
    voice that does stream, so an agent is never left silent."""

    # PER-311's exact failure: "Andy C" is a professional voice, present in the library
    # and REST-synthesizable, that the payg tier cannot stream.
    ANDY_C = "P4DhdyNCB4Nl6MA0sL45"
    NO_CLONING = {"can_use_professional_voice_cloning": False, "can_use_instant_voice_cloning": False}
    PRO_CLONING = {"can_use_professional_voice_cloning": True, "can_use_instant_voice_cloning": True}

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("ELEVENLABS_VOICE_ID", None)  # voice_id() -> DEFAULT_VOICE_ID
        el._streamable_voice_ids.cache_clear()

    def tearDown(self):
        el._streamable_voice_ids.cache_clear()
        self._env.stop()

    def _account(self, voices: dict, subscription: dict):
        """Patch the two /voices + /subscription surfaces resolve_voice_id gates on."""
        return (
            mock.patch.object(el, "list_voices", return_value=voices),
            mock.patch.object(el, "get_subscription", return_value=subscription),
        )

    def test_valid_premade_passes_through(self):
        voices = {"good-1": "premade", "good-2": "premade"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id("good-1"), "good-1")

    def test_professional_voice_falls_back_when_tier_cannot_stream_it(self):
        # The live outage: Andy C is present + REST-fine, but not streamable on payg.
        voices = {self.ANDY_C: "professional", el.DEFAULT_VOICE_ID: "premade"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id(self.ANDY_C), el.DEFAULT_VOICE_ID)

    def test_professional_voice_passes_through_when_tier_can_stream_it(self):
        voices = {self.ANDY_C: "professional", el.DEFAULT_VOICE_ID: "premade"}
        v, s = self._account(voices, self.PRO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id(self.ANDY_C), self.ANDY_C)

    def test_absent_voice_falls_back_to_default_when_present(self):
        voices = {el.DEFAULT_VOICE_ID: "premade", "other": "premade"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id("not-on-account"), el.DEFAULT_VOICE_ID)

    def test_falls_back_to_candidate_when_default_absent(self):
        # DEFAULT_VOICE_ID absent, but George present (Sarah IS the default here).
        voices = {"JBFqnCBsd6RMkjVDRZzb": "premade"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id(self.ANDY_C), "JBFqnCBsd6RMkjVDRZzb")

    def test_falls_back_to_any_streamable_as_last_resort(self):
        voices = {"zzz-only-voice": "premade"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id("nope"), "zzz-only-voice")

    def test_no_streamable_voices_trusts_caller(self):
        # Library exists but nothing streams (all professional, no entitlement).
        voices = {"pro-1": "professional", "pro-2": "professional"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id("nope"), "nope")

    def test_empty_request_uses_env_default_then_validates(self):
        os.environ.pop("ELEVENLABS_VOICE_ID", None)  # -> DEFAULT_VOICE_ID (Sarah, premade)
        voices = {el.DEFAULT_VOICE_ID: "premade"}
        v, s = self._account(voices, self.NO_CLONING)
        with v, s:
            self.assertEqual(el.resolve_voice_id(""), el.DEFAULT_VOICE_ID)
            self.assertEqual(el.resolve_voice_id(None), el.DEFAULT_VOICE_ID)

    def test_subscription_unreachable_degrades_to_premade_only(self):
        # Voices list succeeds, subscription fails: only premade is trusted to stream,
        # so a professional configured voice still falls back rather than risking silence.
        voices = {self.ANDY_C: "professional", el.DEFAULT_VOICE_ID: "premade"}
        with mock.patch.object(el, "list_voices", return_value=voices):
            with mock.patch.object(el, "get_subscription", side_effect=RuntimeError("sub down")):
                self.assertEqual(el.resolve_voice_id(self.ANDY_C), el.DEFAULT_VOICE_ID)

    def test_unreachable_account_trusts_caller(self):
        with mock.patch.object(el, "list_voices", side_effect=RuntimeError("api down")):
            self.assertEqual(el.resolve_voice_id("whatever"), "whatever")


class StreamabilityReportTest(unittest.TestCase):
    """The healthcheck's streaming-path gate for the live roster (PER-311)."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_reports_category_and_streamability_per_voice(self):
        voices = {"premade-1": "premade", "pro-1": "professional"}
        sub = {"can_use_professional_voice_cloning": False, "can_use_instant_voice_cloning": False}
        with mock.patch.object(el, "list_voices", return_value=voices):
            with mock.patch.object(el, "get_subscription", return_value=sub):
                report = el.streamability_report(["premade-1", "pro-1", "absent-1"])
        self.assertEqual(report, [
            ("premade-1", "premade", True),
            ("pro-1", "professional", False),
            ("absent-1", None, False),
        ])

    def test_subscription_failure_treats_only_premade_as_streamable(self):
        voices = {"premade-1": "premade", "pro-1": "professional"}
        with mock.patch.object(el, "list_voices", return_value=voices):
            with mock.patch.object(el, "get_subscription", side_effect=RuntimeError("down")):
                report = dict((vid, ok) for vid, _cat, ok in el.streamability_report(["premade-1", "pro-1"]))
        self.assertTrue(report["premade-1"])
        self.assertFalse(report["pro-1"])


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

    async def test_verify_room_join_requires_env(self):
        with self.assertRaises(RuntimeError):
            await lk.verify_room_join("papervoice-test")

    async def test_sip_trunk_status_requires_env(self):
        with self.assertRaises(RuntimeError):
            await lk.sip_trunk_status()

    async def test_room_has_human_participant_requires_env(self):
        with self.assertRaises(RuntimeError):
            await lk.room_has_human_participant("papervoice-test")

    async def test_room_has_human_participant_true_for_standard_kind(self):
        from livekit import api

        participants = mock.MagicMock(participants=[mock.MagicMock(kind=api.ParticipantInfo.Kind.STANDARD)])
        fake_room_service = mock.AsyncMock()
        fake_room_service.list_participants = mock.AsyncMock(return_value=participants)
        fake_lk = mock.AsyncMock()
        fake_lk.room = fake_room_service
        fake_lk.__aenter__ = mock.AsyncMock(return_value=fake_lk)
        fake_lk.__aexit__ = mock.AsyncMock(return_value=False)

        os.environ.update(LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s")
        with mock.patch("livekit.api.LiveKitAPI", return_value=fake_lk):
            self.assertTrue(await lk.room_has_human_participant("papervoice-boardroom"))

    async def test_room_has_human_participant_false_when_only_agents_present(self):
        from livekit import api

        participants = mock.MagicMock(
            participants=[
                mock.MagicMock(kind=api.ParticipantInfo.Kind.AGENT),
                mock.MagicMock(kind=api.ParticipantInfo.Kind.AGENT),
            ]
        )
        fake_room_service = mock.AsyncMock()
        fake_room_service.list_participants = mock.AsyncMock(return_value=participants)
        fake_lk = mock.AsyncMock()
        fake_lk.room = fake_room_service
        fake_lk.__aenter__ = mock.AsyncMock(return_value=fake_lk)
        fake_lk.__aexit__ = mock.AsyncMock(return_value=False)

        os.environ.update(LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s")
        with mock.patch("livekit.api.LiveKitAPI", return_value=fake_lk):
            self.assertFalse(await lk.room_has_human_participant("papervoice-boardroom"))

    async def test_room_has_human_participant_false_when_room_missing(self):
        from livekit import api

        not_found = api.TwirpError(api.TwirpErrorCode.NOT_FOUND, "requested room does not exist", status=404)
        fake_room_service = mock.AsyncMock()
        fake_room_service.list_participants = mock.AsyncMock(side_effect=not_found)
        fake_lk = mock.AsyncMock()
        fake_lk.room = fake_room_service
        fake_lk.__aenter__ = mock.AsyncMock(return_value=fake_lk)
        fake_lk.__aexit__ = mock.AsyncMock(return_value=False)

        os.environ.update(LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s")
        with mock.patch("livekit.api.LiveKitAPI", return_value=fake_lk):
            self.assertFalse(await lk.room_has_human_participant("papervoice-boardroom"))

    async def test_delete_room_retries_transient_not_found_then_succeeds(self):
        from livekit import api

        not_found = api.TwirpError(api.TwirpErrorCode.NOT_FOUND, "requested room does not exist", status=404)
        fake_room_service = mock.AsyncMock()
        fake_room_service.delete_room = mock.AsyncMock(side_effect=[not_found, not_found, None])
        fake_lk = mock.AsyncMock()
        fake_lk.room = fake_room_service
        fake_lk.__aenter__ = mock.AsyncMock(return_value=fake_lk)
        fake_lk.__aexit__ = mock.AsyncMock(return_value=False)

        os.environ.update(LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s")
        with mock.patch("livekit.api.LiveKitAPI", return_value=fake_lk):
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                await lk.delete_room("papervoice-test")
        self.assertEqual(fake_room_service.delete_room.call_count, 3)

    async def test_delete_room_gives_up_after_persistent_not_found(self):
        from livekit import api

        not_found = api.TwirpError(api.TwirpErrorCode.NOT_FOUND, "requested room does not exist", status=404)
        fake_room_service = mock.AsyncMock()
        fake_room_service.delete_room = mock.AsyncMock(side_effect=not_found)
        fake_lk = mock.AsyncMock()
        fake_lk.room = fake_room_service
        fake_lk.__aenter__ = mock.AsyncMock(return_value=fake_lk)
        fake_lk.__aexit__ = mock.AsyncMock(return_value=False)

        os.environ.update(LIVEKIT_URL="wss://x", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s")
        with mock.patch("livekit.api.LiveKitAPI", return_value=fake_lk):
            with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
                with self.assertRaises(api.TwirpError):
                    await lk.delete_room("papervoice-test")
        self.assertEqual(fake_room_service.delete_room.call_count, lk._NOT_FOUND_RETRIES)


if __name__ == "__main__":
    unittest.main()
