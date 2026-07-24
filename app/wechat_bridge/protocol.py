from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SidecarEventType:
    STATUS = "status"
    QR = "qr"
    ROOMS = "rooms"
    ROOM_MEMBERS = "room_members"
    MESSAGE = "message"
    SEND_RESULT = "send_result"
    ERROR = "error"


class SidecarCommandType:
    STOP = "stop"
    LIST_ROOMS = "list_rooms"
    LIST_ROOM_MEMBERS = "list_room_members"
    SEND_TEXT = "send_text"
    SEND_IMAGE = "send_image"


@dataclass
class SidecarEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


def parse_sidecar_event(data: dict[str, Any]) -> SidecarEvent:
    event_type = str(data.get("type") or "").strip()
    if not event_type:
        raise ValueError("sidecar event missing type")
    payload = dict(data)
    payload.pop("type", None)
    return SidecarEvent(event_type, payload)
