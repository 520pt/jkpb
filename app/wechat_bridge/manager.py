from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import qrcode

from app.wechat_bridge.protocol import SidecarCommandType, SidecarEvent, SidecarEventType, parse_sidecar_event


LOGGER = logging.getLogger(__name__)
CONNECTED_STATUSES = {"logged_in", "connected"}


def wechat_bridge_enabled() -> bool:
    return os.getenv("WECHAT_BRIDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _data_dir() -> Path:
    configured = os.getenv("WECHAT_BRIDGE_DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(os.getenv("DATA_DIR", "data")) / "wechat_bridge"


def _sidecar_dir() -> Path:
    return Path(__file__).resolve().parent / "sidecar"


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\n".join(str(part or "").strip() for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _qr_image_data_uri(text: str) -> str:
    qr_text = str(text or "").strip()
    if not qr_text:
        return ""
    image = qrcode.make(qr_text)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class WechatBridgeManager:
    STOP_TIMEOUT_SECONDS = 5

    def __init__(self, *, data_dir: Path | None = None, sidecar_dir: Path | None = None) -> None:
        self.data_dir = data_dir or _data_dir()
        self.sidecar_dir = sidecar_dir or _sidecar_dir()
        self.media_dir = self.data_dir / "media"
        self.identity_path = self.data_dir / "identity.json"
        self.process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._room_members_lock = threading.RLock()
        self._room_member_waiters: dict[str, threading.Event] = {}
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self.message_handler: Callable[[dict[str, Any]], None] | None = None

        self.status = "idle"
        self.last_error = ""
        self.qr_code = ""
        self.qrcode_url = ""
        self.qr_image = ""
        self.self_id = ""
        self.self_name = ""
        self.rooms: list[dict[str, Any]] = []
        self.room_members: dict[str, list[dict[str, Any]]] = {}
        self.identity: dict[str, Any] = {"rooms": {}, "members": {}}
        self._load_identity()

    def set_message_handler(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        self.message_handler = handler

    def start(self) -> None:
        with self._lock:
            if self.process and self.process.poll() is None:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.media_dir.mkdir(parents=True, exist_ok=True)
            self.status = "starting"
            self.last_error = ""
            config = {
                "puppet": os.getenv("WECHAT_BRIDGE_PUPPET", "wechaty-puppet-wechat4u"),
                "memory_path": str(self.data_dir / "wechat_group"),
                "media_dir": str(self.media_dir),
            }
            command = [
                os.getenv("WECHAT_BRIDGE_NODE", "node"),
                "wechaty-sidecar.mjs",
                json.dumps(config, ensure_ascii=False),
            ]
            try:
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    cwd=self.sidecar_dir,
                )
            except Exception as exc:
                self.status = "error"
                self.last_error = str(exc)
                raise
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
            self._stderr_thread.start()

    def stop(self) -> None:
        with self._lock:
            process = self.process
            if not process:
                return
            if process.poll() is None:
                try:
                    self._send_command({"type": SidecarCommandType.STOP})
                    process.wait(timeout=self.STOP_TIMEOUT_SECONDS)
                except Exception:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except Exception:
                        process.kill()
            self.process = None
            self.status = "idle"

    def refresh_rooms(self) -> None:
        self.ensure_started()
        self._send_command({"type": SidecarCommandType.LIST_ROOMS})

    def get_room_members(self, room_id: str, *, query: str = "", limit: int = 500, timeout: float = 5.0) -> list[dict[str, Any]]:
        runtime_room_id = self.resolve_runtime_room_id(room_id)
        if not runtime_room_id:
            return []
        self.ensure_started()
        request_id = f"members_{int(time.time() * 1000)}_{threading.get_ident()}"
        waiter = threading.Event()
        with self._room_members_lock:
            self._room_member_waiters[request_id] = waiter
        try:
            self._send_command(
                {
                    "type": SidecarCommandType.LIST_ROOM_MEMBERS,
                    "room_id": runtime_room_id,
                    "request_id": request_id,
                    "query": query,
                }
            )
            waiter.wait(max(timeout, 0))
        finally:
            with self._room_members_lock:
                self._room_member_waiters.pop(request_id, None)
        with self._room_members_lock:
            members = list(self.room_members.get(runtime_room_id, []))
        if query:
            lowered = query.lower()
            members = [
                member
                for member in members
                if lowered in str(member.get("name") or "").lower()
                or lowered in str(member.get("wechat_id") or "").lower()
                or lowered in str(member.get("room_alias") or "").lower()
                or lowered in str(member.get("runtime_sender_id") or member.get("sender_id") or "").lower()
            ]
        return members[: max(int(limit or 0), 0) or 500]

    def send_text(self, room_id: str, text: str, *, mention_ids: list[str] | None = None) -> None:
        runtime_room_id = self.resolve_runtime_room_id(room_id)
        if not runtime_room_id:
            raise RuntimeError(f"目标微信群当前不可发送或未同步：{room_id}")
        self.ensure_started()
        self._send_command(
            {
                "type": SidecarCommandType.SEND_TEXT,
                "room_id": runtime_room_id,
                "text": text,
                "mention_ids": mention_ids or [],
                "alias_sync_cooldown_minutes": 1,
            }
        )

    def send_image_bytes(self, room_id: str, image_bytes: bytes) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        target = self.media_dir / f"send-{int(time.time() * 1000)}-{hashlib.md5(image_bytes).hexdigest()}.png"
        target.write_bytes(image_bytes)
        self.send_image(room_id, str(target))

    def send_image(self, room_id: str, path: str) -> None:
        runtime_room_id = self.resolve_runtime_room_id(room_id)
        if not runtime_room_id:
            raise RuntimeError(f"目标微信群当前不可发送或未同步：{room_id}")
        image_path = Path(path)
        if not image_path.exists():
            raise RuntimeError(f"图片文件不存在：{path}")
        self.ensure_started()
        self._send_command(
            {
                "type": SidecarCommandType.SEND_IMAGE,
                "room_id": runtime_room_id,
                "path": str(image_path),
            }
        )

    def status_snapshot(self) -> dict[str, Any]:
        self.ensure_started()
        rooms = self.rooms_snapshot()
        return {
            "status": "success",
            "connected": self.status in CONNECTED_STATUSES,
            "login_status": self.status,
            "message": self.last_error,
            "qrcode_url": self.qrcode_url,
            "qr_image": self.qr_image,
            "rooms": rooms,
            "sendable_room_count": len([room for room in rooms if room.get("sendable")]),
            "selected_room_ids": [room["id"] for room in rooms if room.get("id")],
            "selected_room_names": [room.get("name", "") for room in rooms],
        }

    def rooms_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(room) for room in self.rooms]

    def resolve_runtime_room_id(self, room_id: str) -> str:
        text = str(room_id or "").strip()
        if not text:
            return ""
        if not text.startswith("wgr_"):
            return text
        for room in self.rooms_snapshot():
            if str(room.get("stable_room_id") or room.get("id") or "").strip() == text:
                return str(room.get("runtime_room_id") or "").strip()
        saved = self.identity.get("rooms", {}).get(text, {})
        return str(saved.get("runtime_room_id") or "").strip()

    def ensure_started(self) -> None:
        self.start()

    def _send_command(self, command: dict[str, Any]) -> None:
        process = self.process
        stdin = process.stdin if process else None
        if not process or process.poll() is not None or stdin is None:
            raise RuntimeError("内置微信桥未启动")
        stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
        stdin.flush()

    def _read_loop(self) -> None:
        stdout = self.process.stdout if self.process else None
        if not stdout:
            return
        for line in stdout:
            try:
                event = parse_sidecar_event(json.loads(line))
                self._consume_event(event)
            except Exception as exc:
                LOGGER.warning("内置微信桥事件解析失败：%s", exc)

    def _stderr_loop(self) -> None:
        stderr = self.process.stderr if self.process else None
        if not stderr:
            return
        for line in stderr:
            text = line.strip()
            if text:
                LOGGER.warning("内置微信桥 sidecar：%s", text)

    def _consume_event(self, event: SidecarEvent) -> None:
        if event.type == SidecarEventType.QR:
            self.status = "qr_ready"
            self.qr_code = str(event.get("qrcode") or "")
            self.qrcode_url = str(event.get("url") or self.qr_code)
            self.qr_image = _qr_image_data_uri(self.qr_code or self.qrcode_url)
            self.last_error = ""
            return
        if event.type == SidecarEventType.STATUS:
            self.status = str(event.get("status") or self.status)
            self.self_id = str(event.get("self_id") or self.self_id)
            self.self_name = str(event.get("self_name") or self.self_name)
            if self.status in CONNECTED_STATUSES:
                self.last_error = ""
            return
        if event.type == SidecarEventType.ROOMS:
            self.rooms = self._normalize_rooms(event.get("rooms") or [])
            if self.rooms and self.status in {"starting", "qr_ready", "logged_in", "error"}:
                self.status = "connected"
            self.last_error = ""
            return
        if event.type == SidecarEventType.ROOM_MEMBERS:
            self._consume_room_members(event)
            return
        if event.type == SidecarEventType.MESSAGE:
            message = self._normalize_message(event.payload)
            handler = self.message_handler
            if handler and message:
                threading.Thread(target=handler, args=(message,), daemon=True).start()
            return
        if event.type == SidecarEventType.ERROR:
            self.last_error = str(event.get("message") or event.get("error") or event.payload)
            if self.status not in CONNECTED_STATUSES and not self.rooms:
                self.status = "error"

    def _normalize_rooms(self, rooms: Any) -> list[dict[str, Any]]:
        normalized = []
        changed = False
        for item in rooms if isinstance(rooms, list) else []:
            if not isinstance(item, dict):
                continue
            runtime_room_id = str(item.get("runtime_room_id") or item.get("room_id") or item.get("id") or "").strip()
            name = str(item.get("name") or item.get("room_name") or item.get("topic") or runtime_room_id).strip()
            if not runtime_room_id:
                continue
            stable_room_id = self._stable_room_id_for_runtime(runtime_room_id, name)
            saved = self.identity.setdefault("rooms", {}).setdefault(stable_room_id, {})
            if saved.get("runtime_room_id") != runtime_room_id or saved.get("name") != name:
                saved.update({"runtime_room_id": runtime_room_id, "name": name, "updated_at": int(time.time())})
                changed = True
            normalized.append(
                {
                    **item,
                    "id": stable_room_id,
                    "stable_room_id": stable_room_id,
                    "runtime_room_id": runtime_room_id,
                    "name": name,
                    "sendable": True,
                    "binding_status": "confirmed",
                }
            )
        if changed:
            self._save_identity()
        return normalized

    def _consume_room_members(self, event: SidecarEvent) -> None:
        runtime_room_id = str(event.get("room_id") or "").strip()
        members = self._normalize_members(runtime_room_id, event.get("members") or [])
        with self._room_members_lock:
            self.room_members[runtime_room_id] = members
            waiter = self._room_member_waiters.get(str(event.get("request_id") or ""))
            if waiter:
                waiter.set()

    def _normalize_members(self, runtime_room_id: str, members: Any) -> list[dict[str, Any]]:
        stable_room_id = self._stable_room_id_for_runtime(runtime_room_id, "")
        normalized = []
        changed = False
        for item in members if isinstance(members, list) else []:
            if not isinstance(item, dict):
                continue
            runtime_sender_id = str(item.get("runtime_sender_id") or item.get("sender_id") or item.get("id") or "").strip()
            name = str(item.get("sender_nickname") or item.get("name") or item.get("display_name") or runtime_sender_id).strip()
            wechat_id = str(item.get("wechat_id") or "").strip()
            room_alias = str(item.get("room_alias") or "").strip()
            if not runtime_sender_id:
                continue
            stable_member_id = _stable_id("wgm", stable_room_id, wechat_id or runtime_sender_id, name)
            saved = self.identity.setdefault("members", {}).setdefault(stable_member_id, {})
            if saved.get("runtime_sender_id") != runtime_sender_id or saved.get("name") != name:
                saved.update(
                    {
                        "runtime_sender_id": runtime_sender_id,
                        "stable_room_id": stable_room_id,
                        "name": name,
                        "wechat_id": wechat_id,
                        "room_alias": room_alias,
                        "updated_at": int(time.time()),
                    }
                )
                changed = True
            normalized.append(
                {
                    **item,
                    "id": runtime_sender_id,
                    "sender_id": runtime_sender_id,
                    "runtime_sender_id": runtime_sender_id,
                    "stable_member_id": stable_member_id,
                    "wechat_group_member_id": stable_member_id,
                    "sender_nickname": name,
                    "display_name": name,
                    "wechat_id": wechat_id,
                    "room_alias": room_alias,
                    "is_raw_id_name": name == runtime_sender_id,
                }
            )
        if changed:
            self._save_identity()
        return normalized

    def _normalize_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime_room_id = str(payload.get("runtime_room_id") or payload.get("room_id") or "").strip()
        room_name = str(payload.get("room_name") or "").strip()
        runtime_sender_id = str(payload.get("runtime_sender_id") or payload.get("sender_id") or "").strip()
        sender_name = str(payload.get("sender_name") or "").strip()
        stable_room_id = self._stable_room_id_for_runtime(runtime_room_id, room_name)
        stable_member_id = _stable_id("wgm", stable_room_id, runtime_sender_id, sender_name)
        return {
            **payload,
            "room_id": runtime_room_id,
            "stable_room_id": stable_room_id,
            "sender_id": stable_member_id,
            "runtime_sender_id": runtime_sender_id,
            "sender_name": sender_name,
            "text": str(payload.get("text") or ""),
            "is_at": bool(payload.get("is_at")),
            "my_msg": bool(payload.get("my_msg")),
        }

    def _stable_room_id_for_runtime(self, runtime_room_id: str, name: str) -> str:
        runtime_text = str(runtime_room_id or "").strip()
        for stable_room_id, saved in self.identity.get("rooms", {}).items():
            if str(saved.get("runtime_room_id") or "").strip() == runtime_text:
                if name and not saved.get("name"):
                    saved["name"] = name
                    self._save_identity()
                return stable_room_id
        return _stable_id("wgr", self.self_id, name or runtime_text)

    def _load_identity(self) -> None:
        try:
            if self.identity_path.exists():
                data = json.loads(self.identity_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.identity = {
                        "rooms": data.get("rooms") if isinstance(data.get("rooms"), dict) else {},
                        "members": data.get("members") if isinstance(data.get("members"), dict) else {},
                    }
        except Exception as exc:
            LOGGER.warning("读取内置微信桥身份映射失败：%s", exc)

    def _save_identity(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="identity-", suffix=".json", dir=str(self.data_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.identity, handle, ensure_ascii=False, indent=2)
            Path(tmp_name).replace(self.identity_path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)


_manager: WechatBridgeManager | None = None


def get_wechat_bridge_manager() -> WechatBridgeManager:
    global _manager
    if _manager is None:
        _manager = WechatBridgeManager()
    return _manager
