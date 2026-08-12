"""Thin ElevenLabs adapter — the only module allowed to touch ElevenLabs surfaces.

Surfaces:
- plugin_tts()/plugin_stt(): configured LiveKit Agents plugin instances for the live call path.
- tts_roundtrip()/stt_roundtrip(): raw REST calls used by scripts/healthcheck, so the
  healthcheck exercises the underlying API even if the plugin's class shapes change.
"""

import os

import httpx

API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # premade "Rachel", present on every account
DEFAULT_TTS_MODEL = "eleven_turbo_v2_5"
DEFAULT_STT_MODEL = "scribe_v1"


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set (copy .env.example to .env)")
    return key


def voice_id() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


def plugin_tts(voice_id_override: str | None = None):
    from livekit.plugins import elevenlabs

    return elevenlabs.TTS(
        voice_id=voice_id_override or voice_id(), model=DEFAULT_TTS_MODEL, api_key=_api_key()
    )


def plugin_stt():
    from livekit.plugins import elevenlabs

    return elevenlabs.STT(api_key=_api_key())  # Scribe


def list_voice_ids() -> set[str]:
    """All voice ids available on this account — used to catch a persona roster drifting off the account."""
    resp = httpx.get(f"{API_BASE}/voices", headers={"xi-api-key": _api_key()}, timeout=30.0)
    resp.raise_for_status()
    return {v["voice_id"] for v in resp.json()["voices"]}


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


def stt_roundtrip(audio: bytes | None = None) -> str:
    """Transcribe `audio` (a fresh TTS synth by default) via Scribe and return the text. Raises on failure.

    Exercises the same STT surface the M2 shared transcriber (boardroom.py) depends on.
    """
    audio = audio if audio is not None else tts_roundtrip()
    resp = httpx.post(
        f"{API_BASE}/speech-to-text",
        headers={"xi-api-key": _api_key()},
        data={"model_id": DEFAULT_STT_MODEL},
        files={"file": ("healthcheck.mp3", audio, "audio/mpeg")},
        timeout=30.0,
    )
    resp.raise_for_status()
    text = resp.json().get("text", "")
    if not text.strip():
        raise RuntimeError("STT returned an empty transcript")
    return text
