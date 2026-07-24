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


class LightAgentNotifyClient:
    """HTTP push adapter for a LightAgent/Wechat gateway.

    LightAgent's current WeChat group sender is internal to its running channel.
    This client targets a small HTTP gateway in front of LightAgent with a stable
    JSON contract, so duty-reminder does not have to import or vendor LightAgent.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        target: str = "",
        targets: list[str] | None = None,
        token: str = "",
        channel: str = "wechat_group",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url.strip()
        self.targets = []
        for value in [target, *(targets or [])]:
            text = str(value or "").strip()
            if text and text not in self.targets:
                self.targets.append(text)
        self.target = self.targets[0] if self.targets else ""
        self.token = token.strip()
        self.channel = channel.strip() or "wechat_group"
        self.http_client = http_client or httpx.AsyncClient(timeout=10, trust_env=False)

    async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None) -> None:
        text: dict[str, object] = {"content": content}
        mentions = [mobile for mobile in (mentioned_mobile_list or []) if mobile]
        if mentions:
            text["mention_ids"] = mentions
        await self._post({"msgtype": "text", "text": text})

    async def send_image(self, image_bytes: bytes) -> None:
        await self._post(
            {
                "msgtype": "image",
                "image": {
                    "base64": base64.b64encode(image_bytes).decode("ascii"),
                    "md5": hashlib.md5(image_bytes).hexdigest(),
                },
            }
        )

    async def _post(self, payload: dict[str, object]) -> None:
        if not self.endpoint_url:
            raise WeComError("LightAgent 推送地址未配置")
        if not self.targets:
            raise WeComError("LightAgent 目标群 room_id 未配置")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        for target in self.targets:
            body = {"channel": self.channel, "target": target, **payload}
            try:
                response = await self.http_client.post(self.endpoint_url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise WeComError(f"LightAgent 推送连接失败：{exc.__class__.__name__}") from exc
            if response.status_code >= 400:
                raise WeComError(f"LightAgent 推送失败：HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError:
                continue
            if data.get("errcode") not in (None, 0):
                raise WeComError(f"LightAgent 推送失败：{data.get('errmsg', 'unknown error')}")
            if data.get("success") is False or data.get("ok") is False:
                raise WeComError(f"LightAgent 推送失败：{data.get('error') or data.get('detail') or 'unknown error'}")
