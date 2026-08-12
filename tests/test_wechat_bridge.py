from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.wechat_bridge.manager import WechatBridgeManager
from app.wechat_bridge.notify import WechatBridgeNotifyClient
from app.wechat_bridge.protocol import SidecarEvent, SidecarEventType


def test_wechat_bridge_sidecar_saves_session_after_login():
    source = Path("app/wechat_bridge/sidecar/wechaty-sidecar.mjs").read_text(encoding="utf-8")

    assert "async function saveWechatSession(reason)" in source
    assert "await saveWechatSession('login')" in source
    assert "if (state.memory) await state.memory.save()" in source


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


def test_wechat_bridge_resolves_stable_member_ids_to_current_runtime_ids(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"
    manager._normalize_rooms([{"id": "room@@runtime", "name": "test-room"}])

    first = manager._normalize_members(
        "room@@runtime",
        [{"id": "@old-runtime", "name": "Alice", "wechat_id": "alice-wechat"}],
    )[0]
    second = manager._normalize_members(
        "room@@runtime",
        [{"id": "@new-runtime", "name": "Alice", "wechat_id": "alice-wechat"}],
    )[0]

    assert second["stable_member_id"] == first["stable_member_id"]
    assert manager.resolve_runtime_member_ids([first["stable_member_id"], "@direct", first["stable_member_id"]]) == [
        "@new-runtime",
        "@direct",
    ]
    assert manager.resolve_runtime_member_ids([first["stable_member_id"], "@new-runtime"]) == ["@new-runtime"]


def test_wechat_bridge_identity_snapshot_roundtrip(tmp_path):
    source = WechatBridgeManager(data_dir=tmp_path / "source-wechat")
    source.self_id = "@self"
    room = source._normalize_rooms([{"id": "room@@runtime", "name": "通知群"}])[0]
    member = source._normalize_members("room@@runtime", [{"id": "@member", "name": "示例甲", "wechat_id": "sample"}])[0]

    snapshot = source.export_identity_snapshot()
    target = WechatBridgeManager(data_dir=tmp_path / "target-wechat")
    target.import_identity_snapshot(snapshot)

    assert target.resolve_runtime_room_id(room["stable_room_id"]) == "room@@runtime"
    assert target.resolve_runtime_member_ids([member["stable_member_id"]]) == ["@member"]


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
        ("wgr_a", "测试", None),
        ("wgr_b", "测试", None),
    ]
    assert manager.image_calls == [("wgr_a", b"png"), ("wgr_b", b"png")]


def test_wechat_bridge_notify_client_passes_all_mention():
    class FakeManager:
        def __init__(self):
            self.text_calls = []

        def send_text(self, room_id, text, *, mention_ids=None):
            self.text_calls.append((room_id, text, mention_ids))

    manager = FakeManager()
    client = WechatBridgeNotifyClient(targets=["wgr_notice"], manager=manager)

    asyncio.run(client.send_text("预警内容", ["@all"]))

    assert manager.text_calls == [("wgr_notice", "预警内容", None)]


def test_wechat_bridge_send_text_waits_for_success_result_and_records_sent_text(tmp_path):
    class FakeStdin:
        def __init__(self, manager):
            self.manager = manager
            self.commands = []

        def write(self, line):
            command = json.loads(line)
            self.commands.append(command)
            self.manager._consume_event(
                SidecarEvent(
                    SidecarEventType.SEND_RESULT,
                    {
                        "request_id": command["request_id"],
                        "ok": True,
                        "command": "send_text",
                        "room_id": command["room_id"],
                        "sent_text": "@Alice 测试",
                    },
                )
            )

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self, manager):
            self.stdin = FakeStdin(manager)

        def poll(self):
            return None

    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.process = FakeProcess(manager)
    manager.start = lambda: None
    manager.status = "connected"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    manager.send_text(room["stable_room_id"], "测试", mention_ids=["@runtime-member"])

    command = manager.process.stdin.commands[0]
    assert command["mention_ids"] == []
    assert command["request_id"].startswith("send_")
    assert manager._consume_outgoing_echo("room@@runtime", "@Alice 测试") is True


def test_wechat_bridge_send_text_ignores_requested_true_mention_ids(tmp_path):
    class FakeStdin:
        def __init__(self, manager):
            self.manager = manager

        def write(self, line):
            command = json.loads(line)
            self.manager._consume_event(
                SidecarEvent(
                    SidecarEventType.SEND_RESULT,
                    {"request_id": command["request_id"], "ok": True, "command": "send_text", "sent_text": command["text"]},
                )
            )

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self, manager):
            self.stdin = FakeStdin(manager)

        def poll(self):
            return None

    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.process = FakeProcess(manager)
    manager.start = lambda: None
    manager.status = "connected"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    manager.send_text(room["stable_room_id"], "测试", mention_ids=["@runtime-member"])


def test_wechat_bridge_send_image_waits_for_success_result(tmp_path):
    class FakeStdin:
        def __init__(self, manager):
            self.manager = manager
            self.commands = []

        def write(self, line):
            command = json.loads(line)
            self.commands.append(command)
            self.manager._consume_event(
                SidecarEvent(
                    SidecarEventType.SEND_RESULT,
                    {"request_id": command["request_id"], "ok": True, "command": "send_file"},
                )
            )

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self, manager):
            self.stdin = FakeStdin(manager)

        def poll(self):
            return None

    image_path = tmp_path / "test.png"
    image_path.write_bytes(b"png")
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.process = FakeProcess(manager)
    manager.start = lambda: None
    manager.status = "connected"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    manager.send_image(room["stable_room_id"], str(image_path))

    command = manager.process.stdin.commands[0]
    assert command["type"] == "send_image"
    assert command["request_id"].startswith("send_")
    assert command["path"] == str(image_path)


def test_wechat_bridge_send_image_raises_on_sidecar_error(tmp_path):
    class FakeStdin:
        def __init__(self, manager):
            self.manager = manager

        def write(self, line):
            command = json.loads(line)
            self.manager._consume_event(
                SidecarEvent(
                    SidecarEventType.ERROR,
                    {"request_id": command["request_id"], "message": "image blocked"},
                )
            )

        def flush(self):
            pass

    class FakeProcess:
        def __init__(self, manager):
            self.stdin = FakeStdin(manager)

        def poll(self):
            return None

    image_path = tmp_path / "test.png"
    image_path.write_bytes(b"png")
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.process = FakeProcess(manager)
    manager.start = lambda: None
    manager.status = "connected"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    try:
        manager.send_image(room["stable_room_id"], str(image_path))
    except RuntimeError as exc:
        assert "image blocked" in str(exc)
    else:
        raise AssertionError("send_image should fail when sidecar reports an error")


def test_wechat_bridge_send_text_fails_fast_when_not_connected(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.process = object()
    manager.start = lambda: None
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    try:
        manager.send_text(room["stable_room_id"], "测试")
    except RuntimeError as exc:
        assert "未登录或未连接" in str(exc)
    else:
        raise AssertionError("send_text should fail before sending when bridge is offline")


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


def test_wechat_bridge_message_includes_stable_member_id(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "测试群"}])[0]

    message = manager._normalize_message(
        {
            "room_id": "room@@runtime",
            "room_name": "测试群",
            "sender_id": "@member-runtime",
            "sender_name": "张三",
            "self_id": "@self",
            "text": "@机器人 查询我的绑定",
            "is_at": True,
        }
    )

    assert message["stable_room_id"] == room["stable_room_id"]
    assert message["sender_id"].startswith("wgm_")
    assert message["stable_member_id"] == message["sender_id"]
    assert message["runtime_sender_id"] == "@member-runtime"


def test_wechat_bridge_message_runtime_id_updates_unique_bound_member(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"
    room = manager._normalize_rooms([{"id": "room@@runtime", "name": "test-room"}])[0]
    member = manager._normalize_members(
        "room@@runtime",
        [{"id": "@hash-member", "name": "Alice", "wechat_id": ""}],
    )[0]

    message = manager._normalize_message(
        {
            "room_id": "room@@runtime",
            "room_name": "test-room",
            "sender_id": "wxid_alice",
            "sender_name": "Alice",
            "self_id": "@self",
            "text": "hello",
        }
    )

    assert message["sender_id"] == member["stable_member_id"]
    assert manager.resolve_runtime_member_ids([member["stable_member_id"]]) == ["wxid_alice"]


def test_wechat_bridge_message_runtime_id_does_not_update_ambiguous_member_name(tmp_path):
    manager = WechatBridgeManager(data_dir=tmp_path / "wechat")
    manager.self_id = "@self"
    manager._normalize_rooms([{"id": "room@@runtime", "name": "test-room"}])
    first = manager._normalize_members(
        "room@@runtime",
        [{"id": "@hash-member-a", "name": "Alice", "wechat_id": ""}],
    )[0]
    second = manager._normalize_members(
        "room@@runtime",
        [{"id": "@hash-member-b", "name": "Alice", "wechat_id": ""}],
    )[0]

    message = manager._normalize_message(
        {
            "room_id": "room@@runtime",
            "room_name": "test-room",
            "sender_id": "wxid_alice",
            "sender_name": "Alice",
            "self_id": "@self",
            "text": "hello",
        }
    )

    assert message["sender_id"] not in {first["stable_member_id"], second["stable_member_id"]}
    assert manager.resolve_runtime_member_ids([first["stable_member_id"], second["stable_member_id"]]) == [
        "@hash-member-a",
        "@hash-member-b",
    ]



def test_wechat_bridge_notify_client_can_send_to_selected_target_only():
    sent: list[tuple[str, str]] = []

    class FakeManager:
        def send_text(self, room_id, text, *, mention_ids=None):
            sent.append(("text", room_id))

        def send_image_bytes(self, room_id, image_bytes):
            sent.append(("image", room_id))

    client = WechatBridgeNotifyClient(targets=["room-1", "room-2"], manager=FakeManager())

    asyncio.run(client.send_text("测试", target_ids=["room-2"]))
    asyncio.run(client.send_image(b"png", target_ids=["room-2"]))

    assert sent == [("text", "room-2"), ("image", "room-2")]
