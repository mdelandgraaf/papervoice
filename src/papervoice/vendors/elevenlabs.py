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
DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # premade "Sarah" — present on this account and streams on any tier
DEFAULT_TTS_MODEL = "eleven_turbo_v2_5"
DEFAULT_STT_MODEL = "scribe_v1"

# Premade voices to reach for when a persona's configured voice_id isn't streamable
# on this account/tier (PER-306, PER-311). Tried in order after the env default and
# DEFAULT_VOICE_ID; the final fallback is any streamable voice the account lists, so an
# agent is never left mute by a stale/invalid/non-streamable voice_id.
_FALLBACK_VOICE_CANDIDATES = (
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "JBFqnCBsd6RMkjVDRZzb",  # George
)

# ElevenLabs voice categories that stream on ANY tier — the built-in library voices,
# which carry no cloning entitlement. Everything else (professional / cloned / generated
# / unknown custom) only streams on the realtime WebSocket when the account holds the
# matching cloning entitlement (see _is_streamable_category / PER-311).
_ALWAYS_STREAMABLE_CATEGORIES = frozenset({"premade", "high_quality", "famous"})


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set (copy .env.example to .env)")
    return key


def voice_id() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID


def _is_streamable_category(category: str | None, subscription: dict) -> bool:
    """Can a voice of this ElevenLabs `category` be streamed on the realtime WebSocket
    given the account's subscription entitlements?

    This is the crux of PER-311: a professional/cloned voice can be **present in the
    account library** and **synthesize fine over REST**, yet the realtime streaming
    WebSocket that live calls use rejects it with ``voice_id_does_not_exist`` /
    close 1008 when the tier lacks the matching cloning entitlement. So presence is
    not enough — streamability is category × entitlement.

    - premade / high_quality / famous (the built-in library voices): always stream.
    - professional: only with ``can_use_professional_voice_cloning``.
    - cloned / generated / any unknown custom category: only with
      ``can_use_instant_voice_cloning``.

    Unknown categories bias strict (require the instant-clone entitlement): streaming a
    non-streamable voice is silent-call catastrophic, while falling back to a premade is
    a benign, audible voice swap — so when unsure, don't stream it.
    """
    cat = (category or "").strip().lower()
    if cat in _ALWAYS_STREAMABLE_CATEGORIES:
        return True
    if cat == "professional":
        return bool(subscription.get("can_use_professional_voice_cloning"))
    return bool(subscription.get("can_use_instant_voice_cloning"))


@functools.lru_cache(maxsize=1)
def _streamable_voice_ids() -> frozenset[str]:
    """Account voice ids that will actually stream on this tier, fetched once per process.

    Combines the account voice library (``/voices``, for each voice's category) with the
    account's cloning entitlements (``/v1/user/subscription``) — see
    _is_streamable_category. Cached because it gates every TTS construction (plugin_tts)
    and only changes when the account voices or plan change; the worker restarts often
    enough (token refresh) that staleness never outlives a real config change. lru_cache
    never caches the exception, so a transient API blip is retried on the next call.

    If the subscription lookup fails but the voice library succeeds, we degrade to
    treating *only* the always-streamable premade categories as streamable — biasing
    toward the safe premade fallback rather than optimistically streaming a
    professional/cloned voice we can't confirm the tier may stream.
    """
    voices = list_voices()
    try:
        subscription = get_subscription()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not fetch ElevenLabs subscription entitlements (%s); treating only "
            "premade voices as streamable this cycle",
            type(exc).__name__,
        )
        subscription = {}
    return frozenset(vid for vid, cat in voices.items() if _is_streamable_category(cat, subscription))


def resolve_voice_id(requested: str | None) -> str:
    """Return a voice_id guaranteed to **stream on this tier** when we can verify it.

    A persona's configured voice_id can be unusable on a live call for two distinct
    reasons, and this guards both:
      1. It has drifted off the account entirely (renamed/deleted voice, stale
         placeholder, or an env default this account doesn't have) — PER-306.
      2. It IS on the account and synthesizes over REST, but is a professional/cloned
         voice the tier may not **stream**, so the realtime WebSocket rejects every
         synthesis with ``voice_id_does_not_exist`` — PER-311. This is the live-outage
         failure a mere presence/REST check stays green on.

    Either way ElevenLabs leaves the agent permanently silent for the whole call, and a
    1:1 direct call to that agent is then dead. Fall back to a voice that actually
    streams so the agent can always speak; a wrong-but-audible voice beats a dead call
    (docs/ARCHITECTURE.md "graceful degradation"). If the account can't be reached to
    verify streamability, trust the caller rather than forcing a fallback that might be
    just as wrong.
    """
    candidate = (requested or "").strip() or voice_id()
    try:
        streamable = _streamable_voice_ids()
    except Exception:
        logger.warning("could not verify streamable account voices for %r; using it as-is", candidate)
        return candidate
    if candidate in streamable:
        return candidate
    for fallback in (voice_id(), DEFAULT_VOICE_ID, *_FALLBACK_VOICE_CANDIDATES):
        if fallback in streamable:
            logger.warning(
                "voice_id %r is not streamable on this ElevenLabs tier; falling back to %s",
                candidate,
                fallback,
            )
            return fallback
    if streamable:
        chosen = sorted(streamable)[0]
        logger.warning(
            "voice_id %r not streamable and no preferred fallback available; using %s",
            candidate,
            chosen,
        )
        return chosen
    logger.error(
        "account lists no streamable voices at all; using %r as-is (streaming will likely fail)", candidate
    )
    return candidate


def plugin_tts(voice_id_override: str | None = None):
    from livekit.plugins import elevenlabs

    return elevenlabs.TTS(
        voice_id=resolve_voice_id(voice_id_override), model=DEFAULT_TTS_MODEL, api_key=_api_key()
    )


def plugin_stt():
    from livekit.plugins import elevenlabs

    return elevenlabs.STT(api_key=_api_key())  # Scribe


def list_voices() -> dict[str, str]:
    """Map of account voice_id -> ElevenLabs category (``premade``, ``professional``,
    ``cloned``, ...). The category is what decides streamability on this tier
    (see _is_streamable_category / PER-311), so we carry it, not just the id set."""
    resp = httpx.get(f"{API_BASE}/voices", headers={"xi-api-key": _api_key()}, timeout=30.0)
    resp.raise_for_status()
    return {v["voice_id"]: (v.get("category") or "") for v in resp.json()["voices"]}


def list_voice_ids() -> set[str]:
    """All voice ids available on this account — used to catch a persona roster drifting off the account.

    Note: library presence alone does NOT mean a voice will stream on a live call — a
    professional/cloned voice can be listed here yet be rejected by the streaming
    WebSocket (PER-311). Use resolve_voice_id() / streamability_report() for the live path."""
    return set(list_voices())


def get_subscription() -> dict:
    """The account's ElevenLabs subscription, including the cloning entitlements that
    gate realtime streaming of professional/cloned voices
    (``can_use_professional_voice_cloning``, ``can_use_instant_voice_cloning``)."""
    resp = httpx.get(f"{API_BASE}/user/subscription", headers={"xi-api-key": _api_key()}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def streamability_report(requested_ids) -> list[tuple[str, str | None, bool]]:
    """For each requested voice_id, return ``(voice_id, category_or_None, streamable)``.

    ``category`` is ``None`` when the voice isn't in the account library at all. Used by
    scripts/healthcheck to flag live-roster voices that won't stream on this tier — the
    streaming-path check the REST ``tts_roundtrip`` cannot make (PER-311)."""
    voices = list_voices()
    try:
        subscription = get_subscription()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not fetch ElevenLabs subscription entitlements (%s); treating only "
            "premade voices as streamable for this report",
            type(exc).__name__,
        )
        subscription = {}
    report: list[tuple[str, str | None, bool]] = []
    for vid in requested_ids:
        category = voices.get(vid)
        streamable = category is not None and _is_streamable_category(category, subscription)
        report.append((vid, category, streamable))
    return report


def tts_roundtrip(text: str = "Papervoice healthcheck.") -> bytes:
    """Synthesize `text` and return the audio bytes. Raises on any failure.

    Note: the REST /text-to-speech path is lenient about voice_id — it will happily
    return audio for a voice_id the streaming WebSocket path (what live calls use)
    rejects with ``voice_id_does_not_exist`` (observed for PER-306's P4Dhdy…, a
    *professional* voice this tier may not stream — PER-311). So this roundtrip is not a
    voice-validity check; streamability (category × entitlement, see streamability_report
    / _is_streamable_category) is the signal that tracks what streaming will accept."""
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
