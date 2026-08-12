"""Thin ElevenLabs adapter — the only module allowed to touch ElevenLabs surfaces.

Two surfaces:
- plugin_tts()/plugin_stt(): configured LiveKit Agents plugin instances for the live call path.
- tts_roundtrip(): raw REST synthesis used by scripts/healthcheck, so the healthcheck
  exercises the underlying API even if the plugin's class shapes change.
"""

import os

import httpx

API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # premade "Rachel", present on every account
DEFAULT_TTS_MODEL = "eleven_turbo_v2_5"


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set (copy .env.example to .env)")
    return key


def voice_id() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


def plugin_tts():
    from livekit.plugins import elevenlabs

    return elevenlabs.TTS(voice_id=voice_id(), model=DEFAULT_TTS_MODEL, api_key=_api_key())


def plugin_stt():
    from livekit.plugins import elevenlabs

    return elevenlabs.STT(api_key=_api_key())  # Scribe


def tts_roundtrip(text: str = "Papervoice healthcheck.") -> bytes:
    """Synthesize `text` and return the audio bytes. Raises on any failure."""
    resp = httpx.post(
        f"{API_BASE}/text-to-speech/{voice_id()}",
        params={"output_format": "mp3_22050_32"},
        headers={"xi-api-key": _api_key()},
        json={"text": text, "model_id": DEFAULT_TTS_MODEL},
        timeout=30.0,
    )
    resp.raise_for_status()
    audio = resp.content
    if len(audio) < 1000:
        raise RuntimeError(f"TTS returned suspiciously small audio ({len(audio)} bytes)")
    return audio
