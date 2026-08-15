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

    async def upload_media(self, media_type: str, filename: str, content: bytes) -> str:
        token = await self.get_access_token()
        try:
            response = await self.http_client.post(
                f"{self.base_url}/media/upload",
                params={"access_token": token, "type": media_type},
                files={"media": (filename, content)},
            )
        except httpx.HTTPError as exc:
            raise WeComError(f"WeCom media upload failed: {exc.__class__.__name__}") from exc
        data = response.json()
        if data.get("errcode") != 0:
            raise WeComError(f"WeCom media upload failed: {data.get('errmsg', 'unknown error')}")
        media_id = str(data.get("media_id") or "").strip()
        if not media_id:
            raise WeComError("WeCom media upload failed: media_id missing")
        return media_id

    async def send_image(self, touser: str, image_bytes: bytes) -> None:
        media_id = await self.upload_media("image", "query.png", image_bytes)
        token = await self.get_access_token()
        payload = {
            "touser": touser,
            "msgtype": "image",
            "agentid": self.agent_id,
            "image": {"media_id": media_id},
            "enable_duplicate_check": 0,
        }
        response = await self.http_client.post(
            f"{self.base_url}/message/send",
            params={"access_token": token},
            json=payload,
        )
        data = response.json()
        if data.get("errcode") != 0:
            raise WeComError(f"WeCom image send failed: {data.get('errmsg', 'unknown error')}")


class WeComAppNotifyClient:
    """Notification adapter backed by a WeCom self-built app.

    Unlike a group webhook, app messages are sent to enterprise userids.  When
    the app channel is active this adapter is the single notification sender;
    callers may pass target_ids as userids, otherwise default_tousers are used.
    """

    is_wecom_app_notify = True

    def __init__(self, client: WeComClient, *, default_tousers: list[str] | None = None) -> None:
        self.client = client
        self.default_tousers = _normalize_wecom_tousers(default_tousers or [])

    async def send_text(
        self,
        content: str,
        mentioned_mobile_list: list[str] | None = None,
        *,
        target_ids: list[str] | None = None,
    ) -> None:
        await self.client.send_text(self._touser(target_ids), content)

    async def send_image(self, image_bytes: bytes, *, target_ids: list[str] | None = None) -> None:
        await self.client.send_image(self._touser(target_ids), image_bytes)

    def _touser(self, target_ids: list[str] | None = None) -> str:
        targets = self.default_tousers if target_ids is None else _normalize_wecom_tousers(target_ids)
        if not targets:
            raise WeComError("企业微信自建应用没有可发送的成员，请先让成员发送“绑定姓名”或配置应用可见范围")
        return "|".join(targets)


def _normalize_wecom_tousers(values: list[str]) -> list[str]:
    targets: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in targets:
            targets.append(text)
    return targets


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

    async def send_text(
        self,
        content: str,
        mentioned_mobile_list: list[str] | None = None,
        *,
        target_ids: list[str] | None = None,
    ) -> None:
        text: dict[str, object] = {"content": content}
        await self._post({"msgtype": "text", "text": text}, target_ids=target_ids)

    async def send_image(self, image_bytes: bytes, *, target_ids: list[str] | None = None) -> None:
        await self._post(
            {
                "msgtype": "image",
                "image": {
                    "base64": base64.b64encode(image_bytes).decode("ascii"),
                    "md5": hashlib.md5(image_bytes).hexdigest(),
                },
            },
            target_ids=target_ids,
        )

    async def _post(self, payload: dict[str, object], *, target_ids: list[str] | None = None) -> None:
        if not self.endpoint_url:
            raise WeComError("LightAgent 推送地址未配置")
        targets = _selected_lightagent_targets(self.targets, target_ids)
        if not targets:
            raise WeComError("LightAgent 目标群 room_id 未配置")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        failures: list[str] = []
        sent = 0
        for target in targets:
            body = {"channel": self.channel, "target": target, **payload}
            try:
                response = await self.http_client.post(self.endpoint_url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                failures.append(f"{target}: 连接失败 {exc.__class__.__name__}")
                continue
            if response.status_code >= 400:
                failures.append(f"{target}: HTTP {response.status_code} {_lightagent_error_text(response)}".strip())
                continue
            try:
                data = response.json()
            except ValueError:
                sent += 1
                continue
            if data.get("errcode") not in (None, 0):
                failures.append(f"{target}: {data.get('errmsg', 'unknown error')}")
                continue
            status = str(data.get("status") or "").strip().lower()
            if status in {"error", "failed", "failure"}:
                failures.append(f"{target}: {data.get('message') or data.get('error') or data.get('detail') or 'unknown error'}")
                continue
            if data.get("success") is False or data.get("ok") is False:
                failures.append(f"{target}: {data.get('error') or data.get('detail') or 'unknown error'}")
                continue
            sent += 1
        if sent == 0 and failures:
            raise WeComError(f"LightAgent 推送失败：{'; '.join(failures)}")


def _lightagent_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    detail = data.get("detail") or data.get("error") or data.get("errmsg")
    return str(detail or "")[:200]


def _selected_lightagent_targets(default_targets: list[str], target_ids: list[str] | None) -> list[str]:
    values = default_targets if target_ids is None else target_ids
    selected: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in selected:
            selected.append(text)
    return selected
