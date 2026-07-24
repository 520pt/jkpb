from __future__ import annotations

import asyncio

from app.wechat_bridge.manager import WechatBridgeManager
from app.wechat_bridge.notify import WechatBridgeNotifyClient
from app.wechat_bridge.protocol import SidecarEvent, SidecarEventType


def test_wechat_bridge_normalizes_rooms_and_persists_stable_ids(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"

    rooms = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])

    assert len(rooms) == 1
    assert rooms[0]["id"].startswith("wgr_")
    assert rooms[0]["stable_room_id"] == rooms[0]["id"]
    assert rooms[0]["runtime_room_id"] == "room@@runtime"
    assert rooms[0]["sendable"] is True

    stable_id = rooms[0]["id"]
    reloaded = WechatBridgeManager(data_dir=tmp_path / "wechat")
    reloaded.self_id = "@self"
    assert reloaded.resolve_runtime_room_id(stable_id) == "room@@runtime"


def test_wechat_bridge_normalizes_room_members(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    members = manager._normalize_members(
        "room@@runtime",
        [{"id": "@member", "name": "张三", "wechat_id": "zhangsan"}],
    )

    assert members[0]["runtime_sender_id"] == "@member"
    assert members[0]["sender_id"] == "@member"
    assert members[0]["stable_member_id"].startswith("wgm_")
    assert members[0]["wechat_group_member_id"] == members[0]["stable_member_id"]
    assert members[0]["display_name"] == "张三"
    assert manager.resolve_runtime_room_id(room["stable_room_id"]) == "room@@runtime"


def test_wechat_bridge_qr_event_exposes_image_data_uri(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")

    manager._consume_event(SidecarEvent(SidecarEventType.QR, {"qrcode": "https://login.weixin.qq.com/l/test"}))

    assert manager.status == "qr_ready"
    assert manager.qr_image.startswith("data:image/png;base64,")
    assert manager.qrcode_url == "https://login.weixin.qq.com/l/test"


def test_wechat_bridge_notify_client_sends_to_multiple_targets():
    class FakeManager:
        def __init__(self):
            self.text_calls = []
            self.image_calls = []

        def send_text(self, room_id, text, *, mention_ids=None):
            self.text_calls.append((room_id, text, mention_ids))

        def send_image_bytes(self, room_id, image_bytes):
            self.image_calls.append((room_id, image_bytes))

    manager = FakeManager()
    client = WechatBridgeNotifyClient(targets=["wgr_a", "wgr_b"], manager=manager)

    asyncio.run(client.send_text("测试", ["@member"]))
    asyncio.run(client.send_image(b"png"))

    assert manager.text_calls == [
        ("wgr_a", "测试", ["@member"]),
        ("wgr_b", "测试", ["@member"]),
    ]
    assert manager.image_calls == [("wgr_a", b"png"), ("wgr_b", b"png")]


def test_wechat_bridge_marks_recent_outgoing_text_as_self_message(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    manager._remember_outgoing_text(room["runtime_room_id"], "还没有导入隧道机电模板")
    message = manager._normalize_message(
        {
            "room_id": "room@@runtime",
            "room_name": "测试群",
            "sender_id": "@self-runtime-fallback",
            "self_id": "@self",
            "text": "还没有导入隧道机电模板",
            "my_msg": False,
        }
    )

    assert message["my_msg"] is True
