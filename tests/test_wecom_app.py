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

    async def send_text(self, touser: str, content: str) -> None:
        self.texts.append((touser, content))

    async def send_image(self, touser: str, image_bytes: bytes) -> None:
        self.images.append((touser, image_bytes))


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
