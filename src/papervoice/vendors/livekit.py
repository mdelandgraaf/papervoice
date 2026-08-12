"""Thin LiveKit adapter — the only module allowed to touch LiveKit server APIs.

Room lifecycle + human join tokens. The live agent worker itself runs through
livekit-agents (see papervoice.agent), which connects with the same env credentials.
"""

import os
from datetime import timedelta

from livekit import api

DEFAULT_ROOM = "papervoice-m1"


def _require_env() -> None:
    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)} (copy .env.example to .env)")


async def create_room(name: str) -> "api.Room":
    _require_env()
    async with api.LiveKitAPI() as lk:
        return await lk.room.create_room(api.CreateRoomRequest(name=name))


async def delete_room(name: str) -> None:
    _require_env()
    async with api.LiveKitAPI() as lk:
        await lk.room.delete_room(api.DeleteRoomRequest(room=name))


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
