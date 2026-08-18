"""Thin ElevenLabs adapter — the only module allowed to touch ElevenLabs surfaces.

Surfaces:
- plugin_tts()/plugin_stt(): configured LiveKit Agents plugin instances for the live call path.
- tts_roundtrip()/stt_roundtrip(): raw REST calls used by scripts/healthcheck, so the
  healthcheck exercises the underlying API even if the plugin's class shapes change.
"""

import functools
import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # premade "Rachel" — usually present, but not on every account
DEFAULT_TTS_MODEL = "eleven_turbo_v2_5"
DEFAULT_STT_MODEL = "scribe_v1"

# Premade voices to reach for when a persona's configured voice_id has drifted off
# the account (PER-306). Tried in order after the env default and DEFAULT_VOICE_ID;
# the final fallback is any voice the account actually lists, so an agent is never
# left mute by a stale/invalid voice_id.
_FALLBACK_VOICE_CANDIDATES = (
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "JBFqnCBsd6RMkjVDRZzb",  # George
)


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set (copy .env.example to .env)")
    return key


def voice_id() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


@functools.lru_cache(maxsize=1)
def _account_voice_ids() -> frozenset[str]:
    """Account voice ids, fetched once per process. Cached because it gates every
    TTS construction (plugin_tts) and the set only changes when someone edits the
    ElevenLabs account — and the worker is restarted often enough (token refresh)
    that staleness never outlives a real config change. lru_cache never caches the
    exception, so a transient API blip is retried on the next call."""
    return frozenset(list_voice_ids())


def resolve_voice_id(requested: str | None) -> str:
    """Return a voice_id guaranteed to be on the account when we can verify it.

    A persona's configured voice_id can drift off the account (renamed/deleted
    voice, a stale placeholder, or an env default that this account simply
    doesn't have) — and ElevenLabs then rejects every synthesis with
    ``voice_id_does_not_exist``, leaving the agent permanently silent for the
    whole call (PER-306). Fall back to a voice the account actually lists so the
    agent can always speak; a wrong-but-audible voice beats a dead call
    (docs/ARCHITECTURE.md "graceful degradation"). If the account list can't be
    fetched, trust the caller rather than forcing a fallback that might be just
    as wrong.

    Caveat: list_voice_ids() is the account *library*. The REST synth path is lenient
    and will speak an unlisted id, but the streaming WebSocket path live calls use only
    accepts library voices (it rejected PER-306's unlisted P4Dhdy…), so library
    membership is the right signal here. Worst case this swaps a rarely-used unlisted
    premade for a listed one — a benign, logged voice change that still speaks; it never
    leaves the agent silent, which is the failure this guards against.
    """
    candidate = (requested or "").strip() or voice_id()
    try:
        available = _account_voice_ids()
    except Exception:
        logger.warning("could not list account voices to validate %r; using it as-is", candidate)
        return candidate
    if candidate in available:
        return candidate
    for fallback in (voice_id(), DEFAULT_VOICE_ID, *_FALLBACK_VOICE_CANDIDATES):
        if fallback in available:
            logger.warning(
                "voice_id %r is not on the ElevenLabs account; falling back to %s",
                candidate,
                fallback,
            )
            return fallback
    if available:
        chosen = sorted(available)[0]
        logger.warning(
            "voice_id %r not on account and no preferred fallback present; using %s",
            candidate,
            chosen,
        )
        return chosen
    logger.error("account lists no voices at all; using %r as-is (synthesis will likely fail)", candidate)
    return candidate


def plugin_tts(voice_id_override: str | None = None):
    from livekit.plugins import elevenlabs

    return elevenlabs.TTS(
        voice_id=resolve_voice_id(voice_id_override), model=DEFAULT_TTS_MODEL, api_key=_api_key()
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
    """Synthesize `text` and return the audio bytes. Raises on any failure.

    Note: the REST /text-to-speech path is lenient about voice_id — it will happily
    return audio for a voice_id the streaming WebSocket path (what live calls use)
    rejects with ``voice_id_does_not_exist`` (observed for PER-306's P4Dhdy…). So this
    roundtrip is not a voice-validity check; list_voice_ids() membership is the signal
    that tracks what streaming will accept (see check_live_roster_voices)."""
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
