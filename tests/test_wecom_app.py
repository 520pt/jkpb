from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from datetime import date
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
        self.news: list[tuple[str, dict]] = []
        self.menus: list[dict] = []

    async def send_text(self, touser: str, content: str) -> None:
        self.texts.append((touser, content))

    async def send_image(self, touser: str, image_bytes: bytes) -> None:
        self.images.append((touser, image_bytes))

    async def send_news(self, touser: str, *, title: str, description: str, image_bytes: bytes, url: str) -> None:
        self.news.append(
            (
                touser,
                {
                    "title": title,
                    "description": description,
                    "image_bytes": image_bytes,
                    "url": url,
                },
            )
        )

    async def create_menu(self, menu: dict) -> None:
        self.menus.append(menu)


def _save_simple_tunnel_template(repo) -> None:
    repo.save_tunnel_mechanical_template(
        {
            "base_url": "https://example.test",
            "people": [{"id": "1001", "name": "商邱宏"}, {"id": "1002", "name": "罗富耀"}],
            "assets": [
                {
                    "assetId": "asset-1",
                    "assetName": "示例隧道上行",
                    "assetCode": "A001",
                    "routeCode": "S41",
                    "routeName": "南涧－宁洱",
                    "maintenanceSectionId": "",
                    "domainId": "",
                    "deptName": "",
                    "devName": "照明设施",
                    "location": "K1+000",
                    "content": "",
                    "result": 1,
                    "carLicense": "",
                    "nums": "",
                }
            ],
        }
    )


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


def test_wecom_app_callback_replies_news_when_query_has_image(tmp_path: Path, monkeypatch):
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
    assert not fake.images
    assert fake.news and fake.news[0][0] == "luofuyao"
    assert fake.news[0][1]["title"] == "帮助菜单"
    assert fake.news[0][1]["image_bytes"].startswith(b"\x89PNG")


def test_wecom_app_menu_click_event_runs_mapped_command(tmp_path: Path, monkeypatch):
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
    assert fake.news and fake.news[0][1]["title"] == "帮助菜单"


def test_wecom_app_today_duty_menu_click_sends_daily_duty_image(tmp_path: Path, monkeypatch):
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
    plain = (
        "<xml><ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
        "<FromUserName><![CDATA[shangqiuhong]]></FromUserName>"
        "<CreateTime>123</CreateTime><MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[click]]></Event><EventKey><![CDATA[DR_MENU_0_0]]></EventKey>"
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
    assert not any("今日在岗信息图片" in content for _, content in fake.texts)
    assert not fake.images
    assert fake.news and fake.news[0][0] == "shangqiuhong"
    assert fake.news[0][1]["title"] == "今日在岗查询"
    assert fake.news[0][1]["image_bytes"].startswith(b"\x89PNG")


def test_wecom_app_unbound_command_requires_binding(tmp_path: Path, monkeypatch):
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

    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "查询今日监控",
        "from_user": "unbound-user",
        "msg_type": "text",
    })()))

    assert any("首次使用企业微信自建应用请先绑定姓名" in content for _, content in fake.texts)
    assert any("绑定商邱宏" in content for _, content in fake.texts)


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
    assert buttons[0]["name"] == "监控在岗"
    assert [item["key"] for item in buttons[0]["sub_button"]] == [
        "DR_TODAY_DUTY",
        "DR_TODAY_MONITOR",
        "DR_TOMORROW_MONITOR",
        "DR_WEEK_MONITOR",
        "DR_MY_MONITOR",
    ]
    assert buttons[1]["name"] == "机电预警"
    assert buttons[1]["sub_button"][0]["name"] == "录入今日机电"
    assert [item["key"] for item in buttons[1]["sub_button"]] == [
        "DR_TUNNEL_TODAY_SUBMIT",
        "DR_TUNNEL_TEMPLATE",
        "DR_TUNNEL_MODIFY_TEMPLATE",
        "DR_ORANGE_PATROL_RECORD",
    ]


def test_wecom_app_menu_moves_tunnel_today_submit_to_top_and_keeps_legacy_key(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["商邱宏", "罗富耀"])
    repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "shangqiuhong"}])
    repo.set_tunnel_mechanical_partner("商邱宏", "罗富耀")
    repo.save_wecom_app_menu_config(
        [
            {
                "name": "机电预警",
                "items": [
                    {"name": "机电模板", "command": "模板"},
                    {"name": "修改模板", "command": "修改模板"},
                    {"name": "橙色预警巡查记录查询", "command": "橙色预警巡查记录查询"},
                    {"name": "录入今日机电", "command": "录入今日机电"},
                ],
            }
        ]
    )
    _save_simple_tunnel_template(repo)
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 8, 16))
    main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.clear()

    preview = main_module._public_wecom_app_menu_preview(repo)
    assert preview["groups"][0]["items"][0]["command"] == "录入今日机电"
    assert preview["groups"][0]["items"][0]["key"] == "DR_TUNNEL_TODAY_SUBMIT"

    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "",
        "event_key": "DR_MENU_1_3",
        "from_user": "shangqiuhong",
        "msg_type": "event",
    })()))

    assert any("请确认今日隧道机电录入信息" in content for _, content in fake.texts)


def test_wecom_app_menu_can_be_saved_and_dynamic_key_maps_to_command(tmp_path: Path, monkeypatch):
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

    save_response = client.post(
        "/api/wecom-app/menu",
        json={"groups": [{"name": "常用", "items": [{"name": "今日在岗", "command": "查询今日在岗"}]}]},
    )
    create_response = client.post("/api/wecom-app/menu/create")

    assert save_response.status_code == 200
    assert create_response.status_code == 200
    assert fake.menus[-1]["button"][0]["name"] == "常用"
    assert fake.menus[-1]["button"][0]["sub_button"][0]["key"] == "DR_TODAY_DUTY"

    plain = (
        "<xml><ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
        "<FromUserName><![CDATA[shangqiuhong]]></FromUserName>"
        "<CreateTime>123</CreateTime><MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[click]]></Event><EventKey><![CDATA[DR_TODAY_DUTY]]></EventKey>"
        "<AgentID>1000002</AgentID></xml>"
    )
    encrypted = _encrypt(plain)
    body = f"<xml><ToUserName><![CDATA[{CORP_ID}]]></ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt><AgentID>1000002</AgentID></xml>"
    callback_response = client.post(
        "/api/wecom-app/callback",
        params={"msg_signature": _signature(encrypted), "timestamp": "123", "nonce": "nonce"},
        content=body,
    )

    assert callback_response.status_code == 200
    assert not fake.images
    assert fake.news and fake.news[-1][1]["image_bytes"].startswith(b"\x89PNG")


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


def test_wecom_app_notification_image_mode_uses_single_news_message():
    class FakeNotifyClient:
        is_wecom_app_notify = True

        def __init__(self) -> None:
            self.news: list[dict] = []

        async def send_news(self, *, title: str, description: str, image_bytes: bytes, url: str, target_ids=None) -> None:
            self.news.append(
                {
                    "title": title,
                    "description": description,
                    "image_bytes": image_bytes,
                    "url": url,
                    "target_ids": target_ids,
                }
            )

        async def send_text(self, *args, **kwargs) -> None:
            raise AssertionError("text should not be sent for wecom app image mode")

        async def send_image(self, *args, **kwargs) -> None:
            raise AssertionError("image should not be sent separately for wecom app image mode")

    fake = FakeNotifyClient()

    asyncio.run(
        main_module._send_graphic_or_text_image(
            fake,
            title="图文提醒",
            text="图文内容",
            image_bytes=b"png-bytes",
            mentions=[],
            target_ids=["shangqiuhong"],
            mode="image",
        )
    )

    assert fake.news == [
        {
            "title": "图文提醒",
            "description": "图文内容",
            "image_bytes": b"png-bytes",
            "url": fake.news[0]["url"],
            "target_ids": ["shangqiuhong"],
        }
    ]
    assert "/notification-detail/notification-detail-" in fake.news[0]["url"] or fake.news[0]["url"] == "https://work.weixin.qq.com"


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


def test_wecom_app_tunnel_today_submit_requires_partner_then_confirms_and_submits(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["商邱宏", "罗富耀"])
    repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "shangqiuhong"}])
    _save_simple_tunnel_template(repo)
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 8, 16))
    submitted = []
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "tunnel-result.png").write_bytes(b"png-bytes")

    async def fake_submit(repo, request, result_upload_dir=None):
        submitted.append(request)
        return {"success": True, "result_image_url": "/api/uploads/tunnel-result.png"}

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fake_submit)
    main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.clear()

    message = type("M", (), {
        "content": "录入今日机电",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()
    asyncio.run(main_module._handle_wecom_app_message(repo, uploads, message))

    assert any("第一次使用“录入今日机电”前" in content for _, content in fake.texts)
    assert not submitted

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, uploads, type("M", (), {
        "content": "设置机电负责人罗富耀",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))
    assert any("已设置你的机电负责人/搭档：罗富耀" in content for _, content in fake.texts)

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, uploads, message))
    assert any("请确认今日隧道机电录入信息" in content and "负责人：罗富耀" in content and "记录人：商邱宏" in content for _, content in fake.texts)
    assert not submitted

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, uploads, type("M", (), {
        "content": "1",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))
    assert submitted
    assert submitted[0].checker == "罗富耀"
    assert submitted[0].recorder == "商邱宏"
    assert submitted[0].checkTime == date(2026, 8, 16)
    assert fake.news
    assert fake.news[-1][0] == "shangqiuhong"
    assert fake.news[-1][1]["title"] == "隧道机电录入结果"
    assert fake.news[-1][1]["description"] == "2026-08-16 隧道机电录入，共1条"
    assert all("隧道机电录入完成" not in content for _, content in fake.texts)


def test_wecom_app_tunnel_today_submit_keeps_pending_after_platform_failure(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["商邱宏", "罗富耀"])
    repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "shangqiuhong"}])
    repo.set_tunnel_mechanical_partner("商邱宏", "罗富耀")
    _save_simple_tunnel_template(repo)
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 8, 16))
    main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.clear()

    async def fake_submit(repo, request, result_upload_dir=None):
        raise main_module.HTTPException(status_code=400, detail="用户不存在/密码错误")

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fake_submit)

    menu_message = type("M", (), {
        "content": "",
        "event_key": "DR_TUNNEL_TODAY_SUBMIT",
        "from_user": "shangqiuhong",
        "msg_type": "event",
    })()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", menu_message))
    pending_key = "shangqiuhong"
    assert pending_key in main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "2",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))
    assert fake.texts == []

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "1",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))

    assert pending_key in main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS
    assert any(
        "用户不存在/密码错误" in content
        and "待确认信息仍保留" in content
        and "1. 重试" in content
        and "2. 修改账号密码" in content
        for _, content in fake.texts
    )

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "2",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))

    assert pending_key in main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS
    assert any("配置中心 → 隧道机电" in content and "登录测试" in content for _, content in fake.texts)

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "2",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))
    assert fake.texts == []

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "1",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))
    assert any("用户不存在/密码错误" in content and "1. 重试" in content for _, content in fake.texts)

    fake.texts.clear()
    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", menu_message))

    assert any("请确认今日隧道机电录入信息" in content for _, content in fake.texts)


def test_wecom_app_sends_news_for_all_tunnel_image_results():
    assert main_module._wecom_app_query_result_should_send_news({"success": True, "query_type": "tunnel_mechanical"})
    assert main_module._wecom_app_query_result_should_send_news({"success": True, "query_type": "tunnel_mechanical_modify"})
    assert main_module._wecom_app_query_result_should_send_news({"success": True, "query_type": "tunnel_mechanical_result"})


def test_wecom_app_news_description_is_short_and_useful():
    assert main_module._wecom_app_query_news_description(
        {
            "query_type": "rest_query",
            "details": {"total_days": 10, "rested_days": 5, "remaining_days": 5},
        },
        "休息查询结果如下：",
    ) == "本月休息10天｜已休5天｜剩余5天"
    assert main_module._wecom_app_query_news_description(
        {"query_type": "monitor_all", "reply": "监控查询结果如下：\n2026-08-16 周日 监控排班"},
        "监控查询结果如下：",
    ) == "2026-08-16 周日 监控排班"
    assert main_module._wecom_app_query_news_description(
        {
            "query_type": "daily_duty_query",
            "details": {"early": "罗熙云", "middle": "商邱宏", "night": "罗富耀", "tomorrow_early": "沐春宇"},
        },
        "",
    ) == "早班：罗熙云｜中班：商邱宏｜晚班：罗富耀｜明日早班：沐春宇"


def test_wecom_app_tunnel_manual_entry_updates_pending_weather(tmp_path: Path, monkeypatch):
    repo = main_module.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["商邱宏", "罗富耀"])
    repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "shangqiuhong"}])
    _save_simple_tunnel_template(repo)
    fake = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_wecom_app_client_from_repo", lambda repo: fake)
    submitted = []

    async def fake_submit(repo, request, result_upload_dir=None):
        submitted.append(request)
        return {"success": True, "result_image_url": ""}

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fake_submit)
    main_module.WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.clear()

    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "隧道机电录入 日期2026-08-16 负责人罗富耀 记录人商邱宏 天气雨",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))

    assert any("天气：雨" in content for _, content in fake.texts)
    assert not submitted

    asyncio.run(main_module._handle_wecom_app_message(repo, tmp_path / "uploads", type("M", (), {
        "content": "确认",
        "event_key": "",
        "from_user": "shangqiuhong",
        "msg_type": "text",
    })()))

    assert submitted and submitted[0].weather == "雨"



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
