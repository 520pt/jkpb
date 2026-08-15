from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)


class WeComAiBotManager:
    def __init__(self, *, sidecar_dir: Path | None = None) -> None:
        self.sidecar_dir = sidecar_dir or (Path(__file__).resolve().parent.parent / "wechat_bridge" / "sidecar")
        self.process: subprocess.Popen[str] | None = None
        self.message_handler: Callable[[dict[str, Any]], None] | None = None
        self.enabled = False
        self.bot_id = ""
        self.secret = ""
        self.status = "disabled"
        self.last_error = ""
        self.last_message_at = ""
        self._lock = threading.RLock()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def set_message_handler(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        self.message_handler = handler

    def configure(self, *, enabled: bool, bot_id: str, secret: str, restart: bool = True) -> None:
        clean_id = str(bot_id or "").strip()
        clean_secret = str(secret or "").strip()
        changed = (self.enabled, self.bot_id, self.secret) != (bool(enabled), clean_id, clean_secret)
        self.enabled = bool(enabled)
        self.bot_id = clean_id
        self.secret = clean_secret
        if restart and changed:
            self.stop()
            self.start()

    def start(self) -> None:
        with self._lock:
            if not self.enabled:
                self.status = "disabled"
                return
            if not self.bot_id or not self.secret:
                self.status = "unconfigured"
                self.last_error = "请先配置智能机器人 Bot ID 和 Secret"
                return
            if self.process and self.process.poll() is None:
                return
            self.status = "starting"
            self.last_error = ""
            command = [
                os.getenv("WECOM_AIBOT_NODE", os.getenv("WECHAT_BRIDGE_NODE", "node")),
                "wecom-aibot-sidecar.mjs",
                json.dumps({"bot_id": self.bot_id, "secret": self.secret}, ensure_ascii=False),
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
            if process and process.poll() is None:
                try:
                    self._send_command({"type": "stop"})
                    process.wait(timeout=5)
                except Exception:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except Exception:
                        process.kill()
            self.process = None
            self.status = "disabled" if not self.enabled else "stopped"

    def reconnect(self) -> None:
        self.stop()
        self.start()

    def reply_progress(self, message: dict[str, Any], content: str = "正在查询，请稍候…") -> None:
        self._send_reply_command("reply_progress", message, content=content)

    def reply_result(self, message: dict[str, Any], content: str, *, image_path: str = "") -> None:
        self._send_reply_command("reply_final", message, content=content, image_path=image_path)

    def _send_reply_command(
        self,
        command_type: str,
        message: dict[str, Any],
        *,
        content: str,
        image_path: str = "",
    ) -> None:
        self._send_command(
            {
                "type": command_type,
                "headers": message.get("headers") or {},
                "stream_id": str(message.get("stream_id") or ""),
                "content": str(content or ""),
                "image_path": str(image_path or ""),
            }
        )

    def status_snapshot(self) -> dict[str, Any]:
        process_running = bool(self.process and self.process.poll() is None)
        return {
            "enabled": self.enabled,
            "configured": bool(self.bot_id and self.secret),
            "connected": process_running and self.status == "authenticated",
            "status": self.status,
            "message": self.last_error,
            "last_message_at": self.last_message_at,
        }

    def _send_command(self, command: dict[str, Any]) -> None:
        with self._lock:
            process = self.process
            stdin = process.stdin if process else None
            if not process or process.poll() is not None or stdin is None:
                raise RuntimeError("企业微信智能机器人当前未连接")
            stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            stdin.flush()

    def _read_loop(self) -> None:
        stdout = self.process.stdout if self.process else None
        if not stdout:
            return
        for line in stdout:
            try:
                event = json.loads(line)
            except ValueError:
                LOGGER.warning("企业微信智能机器人 sidecar 输出异常：%s", line.strip()[:300])
                continue
            event_type = str(event.get("type") or "")
            if event_type == "status":
                self.status = str(event.get("status") or self.status)
                self.last_error = str(event.get("message") or "")
                continue
            if event_type == "message":
                self.last_message_at = str(event.get("received_at") or "")
                handler = self.message_handler
                if handler:
                    threading.Thread(target=handler, args=(event,), daemon=True).start()
                continue
            if event_type == "error":
                self.status = "error"
                self.last_error = str(event.get("message") or "企业微信智能机器人异常")
                LOGGER.warning("企业微信智能机器人异常：%s", self.last_error)
                continue
            if event_type == "reply_result" and not bool(event.get("success", True)):
                LOGGER.warning("企业微信智能机器人回复失败：%s", event.get("message") or "unknown error")

    def _stderr_loop(self) -> None:
        stderr = self.process.stderr if self.process else None
        if not stderr:
            return
        for line in stderr:
            text = line.strip()
            if text:
                LOGGER.info("企业微信智能机器人 sidecar：%s", text[:500])
