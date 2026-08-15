from pathlib import Path

from app.main import _handle_wecom_aibot_message, _public_notification_config
from app.storage import DutyRepository


class FakeAiBotManager:
    def __init__(self) -> None:
        self.progress: list[dict] = []
        self.results: list[dict] = []

    def reply_progress(self, message: dict, content: str = "正在查询，请稍候…") -> None:
        self.progress.append({"message": message, "content": content})

    def reply_result(self, message: dict, content: str, *, image_path: str = "") -> None:
        self.results.append({"message": message, "content": content, "image_path": image_path})


def _message(text: str, *, userid: str = "luofuy ao".replace(" ", "")) -> dict:
    return {
        "type": "message",
        "headers": {"req_id": "req-1"},
        "stream_id": "stream-1",
        "text": text,
        "msgid": "msg-1",
        "chatid": "chat-1",
        "chattype": "group",
        "userid": userid,
        "received_at": "2026-08-15T12:00:00+08:00",
    }


def test_wecom_aibot_ignores_normal_chat(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    manager = FakeAiBotManager()

    _handle_wecom_aibot_message(repo, tmp_path / "uploads", manager, _message("大家好"))

    assert manager.progress == []
    assert manager.results == []


def test_wecom_aibot_can_bind_and_query_current_user(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀"])
    manager = FakeAiBotManager()

    _handle_wecom_aibot_message(repo, tmp_path / "uploads", manager, _message("绑定罗富耀"))

    assert manager.progress
    assert "绑定成功：罗富耀" in manager.results[-1]["content"]
    assert repo.list_personnel() == [
        {"name": "罗富耀", "mention_mobile": "", "wecom_userid": "luofuyao"}
    ]

    _handle_wecom_aibot_message(repo, tmp_path / "uploads", manager, _message("查询我的绑定"))

    assert "已绑定：罗富耀" in manager.results[-1]["content"]
    assert "企业微信成员：luofuyao" in manager.results[-1]["content"]


def test_public_notification_config_masks_wecom_aibot_secret():
    public = _public_notification_config(
        {
            "sender_type": "wecom_webhook",
            "webhook_url": "https://example.test/webhook",
            "wecom_aibot_enabled": True,
            "wecom_aibot_id": "bot-id",
            "wecom_aibot_secret": "secret-value",
        }
    )

    assert public["wecom_aibot_enabled"] is True
    assert public["wecom_aibot_id"] == "bot-id"
    assert public["wecom_aibot_configured"] is True
    assert public["wecom_aibot_secret"] == ""
    assert public["wecom_aibot_secret_configured"] is True
