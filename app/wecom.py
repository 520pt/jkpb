from __future__ import annotations

import base64
import hashlib

import httpx


class WeComError(RuntimeError):
    pass


class WeComClient:
    def __init__(
        self,
        *,
        corp_id: str,
        corp_secret: str,
        agent_id: int,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://qyapi.weixin.qq.com/cgi-bin",
    ) -> None:
        self.corp_id = corp_id
        self.corp_secret = corp_secret
        self.agent_id = agent_id
        self.http_client = http_client or httpx.AsyncClient(timeout=10, trust_env=False)
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None

    async def get_access_token(self) -> str:
        if self._token:
            return self._token

        response = await self.http_client.get(
            f"{self.base_url}/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.corp_secret},
        )
        data = response.json()
        if data.get("errcode") != 0:
            raise WeComError(f"WeCom token failed: {data.get('errmsg', 'unknown error')}")
        self._token = data["access_token"]
        return self._token

    async def send_text(self, touser: str, content: str) -> None:
        token = await self.get_access_token()
        payload = {
            "touser": touser,
            "msgtype": "text",
            "agentid": self.agent_id,
            "text": {"content": content},
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 1800,
        }
        response = await self.http_client.post(
            f"{self.base_url}/message/send",
            params={"access_token": token},
            json=payload,
        )
        data = response.json()
        if data.get("errcode") != 0:
            raise WeComError(f"WeCom send failed: {data.get('errmsg', 'unknown error')}")


class WeComWebhookClient:
    def __init__(
        self,
        *,
        webhook_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.http_client = http_client or httpx.AsyncClient(timeout=10, trust_env=False)

    async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None) -> None:
        text: dict[str, object] = {"content": content}
        mobiles = [mobile for mobile in (mentioned_mobile_list or []) if mobile]
        if mobiles:
            text["mentioned_mobile_list"] = mobiles
        try:
            response = await self.http_client.post(
                self.webhook_url,
                json={"msgtype": "text", "text": text},
            )
        except httpx.HTTPError as exc:
            raise WeComError(f"企业微信机器人连接失败: {exc.__class__.__name__}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise WeComError("企业微信机器人返回异常") from exc
        if data.get("errcode") != 0:
            raise WeComError(f"WeCom webhook send failed: {data.get('errmsg', 'unknown error')}")

    async def send_image(self, image_bytes: bytes) -> None:
        try:
            response = await self.http_client.post(
                self.webhook_url,
                json={
                    "msgtype": "image",
                    "image": {
                        "base64": base64.b64encode(image_bytes).decode("ascii"),
                        "md5": hashlib.md5(image_bytes).hexdigest(),
                    },
                },
            )
        except httpx.HTTPError as exc:
            raise WeComError(f"企业微信机器人连接失败: {exc.__class__.__name__}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise WeComError("企业微信机器人返回异常") from exc
        if data.get("errcode") != 0:
            raise WeComError(f"WeCom webhook send failed: {data.get('errmsg', 'unknown error')}")
