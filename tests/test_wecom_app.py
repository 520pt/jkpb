from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app


AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
CORP_ID = "ww-test-corp"
TOKEN = "callback-token"


def _encrypt(xml_text: str) -> str:
    key = base64.b64decode(AES_KEY + "=")
    plain = b"0" * 16 + struct.pack("!I", len(xml_text.encode("utf-8"))) + xml_text.encode("utf-8") + CORP_ID.encode("utf-8")
    pad = 32 - (len(plain) % 32)
    plain += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(plain) + encryptor.finalize()).decode("ascii")


def _signature(encrypted: str, timestamp: str = "123", nonce: str = "nonce") -> str:
    return hashlib.sha1("".join(sorted([TOKEN, timestamp, nonce, encrypted])).encode("utf-8")).hexdigest()


class FakeWeComAppClient:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.images: list[tuple[str, bytes]] = []
        self.menus: list[dict] = []

    async def send_text(self, touser: str, content: str) -> None:
        self.texts.append((touser, content))

    async def send_image(self, touser: str, image_bytes: bytes) -> None:
        self.images.append((touser, image_bytes))

    async def create_menu(self, menu: dict) -> None:
        self.menus.append(menu)


def test_wecom_app_callback_verification_returns_plain_echo(tmp_path: Path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    client = TestClient(app)
    encrypted = _encrypt("hello")

    response = client.get(
        "/api/wecom-app/callback",
        params={"msg_signature": _signature(encrypted), "timestamp": "123", "nonce": "nonce", "echostr": encrypted},
    )

    assert response.status_code == 200
    assert response.text == "hello"


def test_wecom_app_callback_replies_text_and_image(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    client = TestClient(app)
    plain = (
        "<xml><ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
        "<FromUserName><![CDATA[luofuyao]]></FromUserName>"
        "<CreateTime>123</CreateTime><MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[菜单]]></Content><MsgId>1</MsgId><AgentID>1000002</AgentID></xml>"
    )
    encrypted = _encrypt(plain)
    body = f"<xml><ToUserName><![CDATA[{CORP_ID}]]></ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt><AgentID>1000002</AgentID></xml>"

    response = client.post(
        "/api/wecom-app/callback",
        params={"msg_signature": _signature(encrypted), "timestamp": "123", "nonce": "nonce"},
        content=body,
    )

    assert response.status_code == 200
    assert response.text == "success"
    assert fake.texts[0] == ("luofuyao", "正在查询，请稍候…")
    assert any("监控查询菜单" in content for _, content in fake.texts)
    assert fake.images and fake.images[0][1].startswith(b"\x89PNG")


def test_wecom_app_menu_click_event_runs_mapped_command(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    client = TestClient(app)
    plain = (
        "<xml><ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
        "<FromUserName><![CDATA[luofuyao]]></FromUserName>"
        "<CreateTime>123</CreateTime><MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[click]]></Event><EventKey><![CDATA[DR_HELP]]></EventKey>"
        "<AgentID>1000002</AgentID></xml>"
    )
    encrypted = _encrypt(plain)
    body = f"<xml><ToUserName><![CDATA[{CORP_ID}]]></ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt><AgentID>1000002</AgentID></xml>"

    response = client.post(
        "/api/wecom-app/callback",
        params={"msg_signature": _signature(encrypted), "timestamp": "123", "nonce": "nonce"},
        content=body,
    )

    assert response.status_code == 200
    assert fake.texts[0] == ("luofuyao", "正在查询，请稍候…")
    assert any("监控查询菜单" in content for _, content in fake.texts)


def test_wecom_app_menu_templates_require_binding_then_use_bound_name(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["商邱宏"])
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)

    message = type("M", (), {
        "content": "",
        "event_key": "DR_TUNNEL_TEMPLATE",
        "from_user": "shangqiuhong",
        "msg_type": "event",
    })()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", message))

    assert any("首次使用企业微信自建应用请先绑定姓名" in content for _, content in fake.texts)
    assert any("绑定商邱宏" in content for _, content in fake.texts)

    repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "shangqiuhong"}])
    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", message))

    assert any("隧道机电录入" in content and "记录人商邱宏" in content for _, content in fake.texts)


def test_wecom_app_orange_patrol_menu_template_uses_bound_name(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀"])
    repo.upsert_personnel_contacts([{"name": "罗富耀", "wecom_userid": "luofuyao"}])
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)

    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "",
        "event_key": "DR_ORANGE_PATROL_RECORD",
        "from_user": "luofuyao",
        "msg_type": "event",
    })()))

    assert any("查询罗富耀巡查记录 2026-07-01至2026-07-31" in content for _, content in fake.texts)


def test_create_wecom_app_menu_endpoint_uses_limited_grouped_menu(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    client = TestClient(app)

    preview = client.get("/api/wecom-app/menu").json()
    response = client.post("/api/wecom-app/menu/create")

    assert response.status_code == 200
    assert fake.menus
    buttons = fake.menus[0]["button"]
    assert len(buttons) <= preview["limits"]["max_top_buttons"] == 3
    assert all(len(button.get("sub_button", [])) <= preview["limits"]["max_sub_buttons"] for button in buttons)
    assert buttons[0]["name"] == "监控提醒"
    assert buttons[0]["sub_button"][0]["key"] == "DR_MY_MONITOR"
    assert buttons[1]["name"] == "机电预警"
    assert [item["key"] for item in buttons[1]["sub_button"]] == [
        "DR_TUNNEL_TEMPLATE",
        "DR_TUNNEL_MODIFY_TEMPLATE",
        "DR_ORANGE_PATROL_RECORD",
    ]


def test_wecom_app_test_endpoint_sends_interaction_check_message(tmp_path: Path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.upsert_personnel_names(["商邱宏"])
    app.state.repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "shangqiuhong"}])
    app.state.repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    client = TestClient(app)

    response = client.post("/api/wecom-app/test")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["target"] == "已绑定企业微信成员"
    assert fake.texts == [("shangqiuhong", body["content"])]
    assert "回复“菜单”" in body["content"]
    assert "绑定商邱宏" in body["content"]


def test_wecom_app_test_endpoint_requires_callback_fields(tmp_path: Path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
    )
    client = TestClient(app)

    response = client.post("/api/wecom-app/test")

    assert response.status_code == 400
    assert "Token / EncodingAESKey" in response.json()["detail"]


def test_wecom_app_message_binding_uses_enterprise_userid(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀"])
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)

    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "绑定罗富耀",
        "from_user": "luofuyao",
        "msg_type": "text",
    })()))

    assert repo.list_personnel()[0]["wecom_userid"] == "luofuyao"
    assert any("绑定成功：罗富耀" in content for _, content in fake.texts)



def test_wecom_app_enabled_overrides_other_notification_channels(tmp_path: Path, monkeypatch):
    class FailingWebhookClient:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("webhook should not be used when wecom app is enabled")

    class FakeRawWeComClient:
        def __init__(self, *args, **kwargs) -> None:
            self.texts: list[tuple[str, str]] = []
            raw_clients.append(self)

        async def send_text(self, touser: str, content: str) -> None:
            self.texts.append((touser, content))

        async def send_image(self, touser: str, image_bytes: bytes) -> None:
            raise AssertionError("image not expected")

    raw_clients: list[FakeRawWeComClient] = []
    monkeypatch.setattr(main_module, "WeComWebhookClient", FailingWebhookClient)
    monkeypatch.setattr(main_module, "WeComClient", FakeRawWeComClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = app.state.repo
    repo.upsert_personnel_names(["罗富耀"])
    repo.upsert_personnel_contacts([{"name": "罗富耀", "wecom_userid": "luofuyao"}])
    repo.save_notification_config(
        sender_type="wecom_webhook",
        webhook_url="https://example.test/cgi-bin/webhook/send?key=stale-webhook",
        wecom_app_enabled=True,
        wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002",
        wecom_app_secret="app-secret",
        wecom_app_token=TOKEN,
        wecom_app_encoding_aes_key=AES_KEY,
    )
    client = TestClient(app)

    public = client.get("/api/notification-config").json()["config"]
    response = client.post("/api/notification-config/test", json={"person_name": "罗富耀"})

    assert public["effective_sender_type"] == "wecom_app"
    assert public["notification_configured"] is True
    assert response.status_code == 200
    assert raw_clients[-1].texts
    assert raw_clients[-1].texts[0][0] == "luofuyao"


def test_wecom_app_enabled_disables_aibot_manager():
    class FakeManager:
        def __init__(self) -> None:
            self.enabled = None

        def configure(self, **kwargs) -> None:
            self.enabled = kwargs["enabled"]

    manager = FakeManager()

    main_module._configure_wecom_aibot_manager(
        manager,
        {
            "wecom_aibot_enabled": True,
            "wecom_aibot_id": "bot-id",
            "wecom_aibot_secret": "bot-secret",
            "wecom_app_enabled": True,
            "wecom_app_corp_id": CORP_ID,
            "wecom_app_agent_id": "1000002",
            "wecom_app_secret": "app-secret",
            "wecom_app_token": TOKEN,
            "wecom_app_encoding_aes_key": AES_KEY,
        },
        restart=False,
    )

    assert manager.enabled is False
