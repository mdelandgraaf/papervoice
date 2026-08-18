"""Durable room-preset parsing and live-roster projection (PER-323)."""
from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Any, Iterable
ROOM_PRESET_VERSION=1
PRESET_ROOM_PREFIX="papervoice-preset-"
MAX_PRESETS=100
MAX_NAME_LENGTH=80
_ID=re.compile(r"^[A-Za-z0-9_-]{8,128}$")
@dataclass(frozen=True)
class PresetProjection:
    preset_id: str
    valid_agent_ids: tuple[str,...]
    stale_agent_ids: tuple[str,...]
def normalize_name(value: Any)->str:
    if not isinstance(value,str): raise ValueError("preset name must be a string")
    name=" ".join(value.split())
    if not 1<=len(name)<=MAX_NAME_LENGTH: raise ValueError(f"preset name must be 1-{MAX_NAME_LENGTH} characters")
    return name
def validate_presets(value: Any)->list[dict[str,Any]]:
    if value is None:return []
    if not isinstance(value,list) or len(value)>MAX_PRESETS: raise ValueError("roomPresets exceeds maximum")
    result=[]; names=set()
    for item in value:
        if not isinstance(item,dict) or not isinstance(item.get("id"),str) or not _ID.fullmatch(item["id"]): raise ValueError("each preset needs an opaque stable id")
        name=normalize_name(item.get("name"))
        if name.casefold() in names: raise ValueError(f"duplicate preset name: {name}")
        names.add(name.casefold()); ids=item.get("agentIds")
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or not i for i in ids): raise ValueError("each preset needs at least one agent id")
        result.append({"id":item["id"],"name":name,"agentIds":list(dict.fromkeys(ids))})
    return result
def read_presets(config:dict[str,Any])->list[dict[str,Any]]:
    if config.get("roomPresetsVersion",ROOM_PRESET_VERSION)!=ROOM_PRESET_VERSION: raise ValueError("unsupported roomPresetsVersion")
    return validate_presets(config.get("roomPresets",[]))
def project_preset(preset:dict[str,Any], enabled_agent_ids:Iterable[str])->PresetProjection:
    enabled=set(enabled_agent_ids); selected=tuple(preset["agentIds"])
    return PresetProjection(preset["id"],tuple(i for i in selected if i in enabled),tuple(i for i in selected if i not in enabled))
def preset_room_name(preset_id:str)->str:
    if not isinstance(preset_id,str) or not _ID.fullmatch(preset_id): raise ValueError("invalid preset id")
    return PRESET_ROOM_PREFIX+preset_id
