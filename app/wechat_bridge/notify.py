from __future__ import annotations

from app.wechat_bridge.manager import WechatBridgeManager, get_wechat_bridge_manager
from app.wecom import WeComError


class WechatBridgeNotifyClient:
    is_wechat_bridge = True

    def __init__(
        self,
        *,
        targets: list[str] | None = None,
        manager: WechatBridgeManager | None = None,
    ) -> None:
        self.targets = []
        for target in targets or []:
            text = str(target or "").strip()
            if text and text not in self.targets:
                self.targets.append(text)
        self.target = self.targets[0] if self.targets else ""
        self.manager = manager or get_wechat_bridge_manager()

    async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None) -> None:
        if not self.targets:
            raise WeComError("内置微信通知目标群未配置")
        failures: list[str] = []
        sent = 0
        for target in self.targets:
            try:
                self.manager.send_text(target, content, mention_ids=mentioned_mobile_list or [])
                sent += 1
            except Exception as exc:
                failures.append(f"{target}: {exc}")
        if sent == 0 and failures:
            raise WeComError(f"内置微信推送失败：{'; '.join(failures)}")

    async def send_image(self, image_bytes: bytes) -> None:
        if not self.targets:
            raise WeComError("内置微信通知目标群未配置")
        failures: list[str] = []
        sent = 0
        for target in self.targets:
            try:
                self.manager.send_image_bytes(target, image_bytes)
                sent += 1
            except Exception as exc:
                failures.append(f"{target}: {exc}")
        if sent == 0 and failures:
            raise WeComError(f"内置微信图片推送失败：{'; '.join(failures)}")
