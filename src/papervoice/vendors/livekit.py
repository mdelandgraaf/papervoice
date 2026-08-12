"""Thin LiveKit adapter — the only module allowed to touch LiveKit server APIs.

Room lifecycle, human join tokens, and the SIP trunk surface used by
scripts/healthcheck. The live agent worker itself runs through livekit-agents
(see papervoice.agent), which connects with the same env credentials.
"""

import asyncio
import os
from datetime import timedelta

from livekit import api, rtc

DEFAULT_ROOM = "papervoice-m1"

# LiveKit Cloud's room API is eventually consistent across edge regions: a
# room created by one call can 404 on a delete/get made milliseconds later.
# Retry room-not-found errors briefly instead of treating them as real failures.
_NOT_FOUND_RETRIES = 3
_NOT_FOUND_RETRY_DELAY = 0.5


def _require_env() -> None:
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)} (copy .env.example to .env)")


def _room_ws_url() -> str:
    return os.environ["LIVEKIT_URL"]


async def create_room(name: str) -> "api.Room":
    _require_env()
    async with api.LiveKitAPI() as lk:
        return await lk.room.create_room(api.CreateRoomRequest(name=name))


async def delete_room(name: str) -> None:
    _require_env()
    async with api.LiveKitAPI() as lk:
        for attempt in range(_NOT_FOUND_RETRIES):
            try:
                await lk.room.delete_room(api.DeleteRoomRequest(room=name))
                return
            except api.TwirpError as exc:
                if exc.code != api.TwirpErrorCode.NOT_FOUND or attempt == _NOT_FOUND_RETRIES - 1:
                    raise
                await asyncio.sleep(_NOT_FOUND_RETRY_DELAY * (attempt + 1))


def mint_join_token(identity: str, room: str = DEFAULT_ROOM, ttl_hours: int = 2) -> str:
    """Access token for a human participant joining from a browser."""
    _require_env()
    token = (
        api.AccessToken()  # reads LIVEKIT_API_KEY / LIVEKIT_API_SECRET from env
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(timedelta(hours=ttl_hours))
        .with_grants(api.VideoGrants(room_join=True, room=room))
    )
    return token.to_jwt()


async def verify_room_join(room: str, identity: str = "papervoice-healthcheck") -> None:
    """Actually connect a participant to `room` over WebRTC, then disconnect.

    Exercises the real join path (token -> SFU handshake), not just the
    server-side room registry that create_room()/delete_room() check.
    """
    _require_env()
    token = mint_join_token(identity, room, ttl_hours=1)
    conn = rtc.Room()
    await conn.connect(_room_ws_url(), token)
    try:
        if conn.connection_state != rtc.ConnectionState.CONN_CONNECTED:
            raise RuntimeError(f"unexpected connection state: {conn.connection_state}")
    finally:
        await conn.disconnect()


async def sip_trunk_status() -> str:
    """Confirm the LiveKit SIP API is reachable and report configured trunks.

    Telephony (Twilio/SIP dial-in) is optional per docs/ARCHITECTURE.md and not
    yet provisioned, so zero trunks is a healthy result — this check exists to
    catch the SIP *API* going unreachable or the SDK surface breaking, not to
    require a trunk to exist.
    """
    _require_env()
    async with api.LiveKitAPI() as lk:
        inbound = await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
        outbound = await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
    return f"{len(inbound.items)} inbound, {len(outbound.items)} outbound trunk(s) configured"
