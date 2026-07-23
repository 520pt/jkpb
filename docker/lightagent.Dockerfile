FROM python:3.11-slim-bookworm

ARG INSTALL_LIGHTAGENT_BROWSER=false
ARG USE_CN_MIRROR=false

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright
ENV CHATGPT_ON_WECHAT_PREFIX=/app
ENV CHATGPT_ON_WECHAT_CONFIG_PATH=/app/config.json
ENV CHATGPT_ON_WECHAT_EXEC="python app.py"

WORKDIR /app

RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources; \
        pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        espeak \
        ffmpeg \
        fonts-wqy-zenhei \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python - <<'PY'
from pathlib import Path

plugin_dir = Path("/app/plugins/duty_reminder_query")
plugin_dir.mkdir(parents=True, exist_ok=True)
plugin_dir.joinpath("__init__.py").write_text(r'''
# encoding:utf-8

import os
import re
import time

import plugins
import requests
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import Event, EventAction, EventContext, Plugin


@plugins.register(
    name="DutyReminderQuery",
    desire_priority=950,
    hidden=False,
    enabled=True,
    desc="Query duty-reminder monitor status from WeChat group @ messages",
    version="0.1",
    author="520pt",
)
class DutyReminderQuery(Plugin):
    MENU_QUERIES = {
        "1": "查询我的监控",
        "2": "查询今日提醒",
        "3": "查询明日监控",
        "4": "查询本周监控",
        "5": "查询未来7天",
        "6": "查询下次提醒",
        "7": "查询我的绑定",
    }
    MENU_NUMBER_ALIASES = {
        "一": "1",
        "二": "2",
        "两": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
    }

    def __init__(self):
        super().__init__()
        self.endpoint = os.environ.get("DUTY_REMINDER_QUERY_URL", "http://duty-reminder:8080/api/wechat-query").strip()
        self.token = os.environ.get("DUTY_REMINDER_QUERY_TOKEN", "520pt").strip()
        self.timeout = float(os.environ.get("DUTY_REMINDER_QUERY_TIMEOUT", "8") or 8)
        self.menu_ttl = int(os.environ.get("DUTY_REMINDER_QUERY_MENU_TTL", "180") or 180)
        self.menu_sessions = {}
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[DutyReminderQuery] inited, endpoint=%s", self.endpoint)

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return
        if not context.get("isgroup", False):
            return
        msg = context.get("msg")
        if not msg:
            return
        text = self._clean_text(context.get("wechat_group_user_content") or context.content)
        if not text:
            return
        is_at = getattr(msg, "is_at", False) is True
        session_key = self._session_key(context, msg)
        menu_selection = self._normalize_menu_selection(text)

        if menu_selection and self._has_active_menu_session(session_key):
            reply_text = self._reply_for_menu_selection(context, msg, session_key, menu_selection)
            e_context["reply"] = Reply(ReplyType.TEXT, reply_text)
            e_context.action = EventAction.BREAK_PASS
            return

        if not is_at:
            return

        if self._is_help_query(text):
            self._store_menu_session(session_key)
            reply_text = self._query_duty_reminder(context, msg, text)
        elif self._looks_like_duty_query(text):
            reply_text = self._query_duty_reminder(context, msg, text)
        else:
            return

        e_context["reply"] = Reply(ReplyType.TEXT, reply_text)
        e_context.action = EventAction.BREAK_PASS

    def _reply_for_menu_selection(self, context, msg, session_key: str, selection: str) -> str:
        query_text = self.MENU_QUERIES.get(selection)
        if not query_text:
            self._store_menu_session(session_key)
            return self._menu_invalid_reply()
        return self._query_duty_reminder(context, msg, query_text)

    def _session_key(self, context, msg) -> str:
        room_id = str(
            context.get("wechat_group_stable_room_id")
            or context.get("wechat_group_runtime_room_id")
            or getattr(msg, "runtime_room_id", "")
            or getattr(msg, "other_user_id", "")
            or ""
        )
        sender_id = str(
            context.get("wechat_group_stable_member_id")
            or context.get("wechat_group_runtime_sender_id")
            or getattr(msg, "runtime_sender_id", "")
            or getattr(msg, "actual_user_id", "")
            or ""
        )
        return "{}:{}".format(room_id, sender_id)

    def _cleanup_menu_sessions(self):
        now = time.time()
        expired = [key for key, expires_at in self.menu_sessions.items() if expires_at <= now]
        for key in expired:
            self.menu_sessions.pop(key, None)

    def _store_menu_session(self, session_key: str):
        if not session_key or session_key == ":":
            return
        self._cleanup_menu_sessions()
        self.menu_sessions[session_key] = time.time() + max(self.menu_ttl, 30)

    def _has_active_menu_session(self, session_key: str) -> bool:
        if not session_key or session_key == ":":
            return False
        self._cleanup_menu_sessions()
        expires_at = float(self.menu_sessions.get(session_key) or 0)
        return expires_at > time.time()

    def _menu_invalid_reply(self) -> str:
        return (
            "请输入 1-7：\n"
            "1. 查询我的监控\n"
            "2. 查询今日提醒\n"
            "3. 查询明日监控\n"
            "4. 查询本周监控\n"
            "5. 查询未来7天\n"
            "6. 查询下次提醒\n"
            "7. 查询我的绑定"
        )

    def _query_duty_reminder(self, context, msg, text: str) -> str:
        if not self.endpoint:
            return "监控查询未配置：缺少 DUTY_REMINDER_QUERY_URL"
        payload = {
            "text": text,
            "room_id": str(context.get("wechat_group_runtime_room_id") or getattr(msg, "runtime_room_id", "") or getattr(msg, "other_user_id", "") or ""),
            "stable_room_id": str(context.get("wechat_group_stable_room_id") or ""),
            "sender_id": str(getattr(msg, "actual_user_id", "") or ""),
            "runtime_sender_id": str(context.get("wechat_group_runtime_sender_id") or getattr(msg, "runtime_sender_id", "") or getattr(msg, "actual_user_id", "") or ""),
            "stable_member_id": str(context.get("wechat_group_stable_member_id") or ""),
            "sender_name": str(getattr(msg, "actual_user_nickname", "") or ""),
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Duty-Query-Token"] = self.token
        try:
            response = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
            data = response.json() if response.content else {}
            if response.status_code >= 400:
                return "监控查询失败：{}".format(data.get("detail") or response.status_code)
            return str(data.get("reply") or "没有查询到结果")
        except Exception as exc:
            logger.warning("[DutyReminderQuery] query failed: %s", exc)
            return "监控查询失败：无法连接 duty-reminder"

    @staticmethod
    def _clean_text(text: str) -> str:
        value = re.sub(r"@\S+", "", str(text or ""))
        return re.sub(r"\s+", "", value).strip("，,。.!！?？：:、；;")

    @classmethod
    def _normalize_menu_selection(cls, text: str) -> str:
        value = str(text or "").strip()
        match = re.fullmatch(r"(?:序号|选|选择|回复)?([1-9一二两三四五六七八九])(?:项|号)?", value)
        if not match:
            return ""
        return cls.MENU_NUMBER_ALIASES.get(match.group(1), match.group(1))

    @staticmethod
    def _is_help_query(text: str) -> bool:
        if text in {"帮助", "查询帮助", "监控帮助", "提醒帮助"}:
            return True
        return "帮助" in text and any(keyword in text for keyword in ("查询", "监控", "提醒", "绑定"))

    @staticmethod
    def _looks_like_duty_query(text: str) -> bool:
        if text in {
            "查询我的监控",
            "查我的监控",
            "我的监控",
            "查询我的排班",
            "我的排班",
            "查询今日提醒",
            "今日提醒",
            "查询今天提醒",
            "今天提醒",
            "查询明日监控",
            "明日监控",
            "查询明天监控",
            "明天监控",
            "查询明日提醒",
            "明日提醒",
            "查询明天提醒",
            "明天提醒",
            "查询后天监控",
            "后天监控",
            "查询我的提醒",
            "我的提醒",
            "我的班",
            "我的值班",
            "我今天什么班",
            "我明天什么班",
            "我后天什么班",
            "今天我上班吗",
            "明天我上班吗",
            "后天我上班吗",
            "查询本周监控",
            "本周监控",
            "这周监控",
            "本周排班",
            "这周排班",
            "查询下周监控",
            "下周监控",
            "下周排班",
            "查询未来7天",
            "未来7天",
            "未来七天",
            "未来7天监控",
            "接下来7天",
            "接下来七天",
            "查询下次提醒",
            "下次提醒",
            "我的下次提醒",
            "最近提醒",
            "下一次提醒",
            "我下次什么时候提醒",
            "查询我的绑定",
            "我的绑定",
            "查我的绑定",
            "绑定查询",
            "我绑定了吗",
            "我的微信绑定",
            "查询帮助",
            "监控帮助",
            "提醒帮助",
        }:
            return True
        if "帮助" in text and any(keyword in text for keyword in ("查询", "监控", "提醒", "绑定")):
            return True
        if re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[日号]?|\d{1,2}/\d{1,2}", text):
            return any(keyword in text for keyword in ("查询", "监控", "排班", "提醒", "值班", "什么班", "上班吗"))
        if re.search(r"(?:未来|接下来|最近)(?:\d{1,2}|[一二两三四五六七八九十]+)天", text):
            return True
        return "查询" in text and any(
            keyword in text
            for keyword in (
                "我的监控",
                "我的排班",
                "今日提醒",
                "今天提醒",
                "明日监控",
                "明天监控",
                "明日提醒",
                "明天提醒",
                "我的提醒",
                "本周监控",
                "这周监控",
                "下周监控",
                "未来7天",
                "未来七天",
                "接下来7天",
                "接下来七天",
                "下次提醒",
            )
        ) or any(keyword in text for keyword in ("什么班", "上班吗"))
'''.lstrip(), encoding="utf-8")
PY

RUN python - <<'PY'
from pathlib import Path

path = Path("/app/channel/web/web_channel.py")
text = path.read_text(encoding="utf-8")

if "class PushSendHandler:" not in text:
    text = text.replace("import hashlib\n", "import base64\nimport hashlib\n")
    text = text.replace("import shutil\n", "import shutil\nimport sys\n")
    text = text.replace(
        "            '/api/wechat-group/stickers/(.*)', 'WechatGroupStickersHandler',\n",
        "            '/api/wechat-group/stickers/(.*)', 'WechatGroupStickersHandler',\n"
        "            '/api/push/send', 'PushSendHandler',\n",
    )
    handler = r'''


class PushSendHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            _require_push_auth()
            body = json.loads(web.data() or b"{}")
            channel = str(body.get("channel") or "wechat_group")
            if channel != const.WECHAT_GROUP:
                return json.dumps({"success": False, "error": "unsupported channel"}, ensure_ascii=False)
            target = str(body.get("target") or "").strip()
            if not target:
                return json.dumps({"success": False, "error": "target is required"}, ensure_ascii=False)
            group_channel = _get_running_channel(const.WECHAT_GROUP)
            if group_channel is None:
                return json.dumps({"success": False, "error": "wechat_group channel is not running"}, ensure_ascii=False)
            runtime_target = _resolve_push_target_room_id(group_channel, target)
            if not runtime_target:
                return json.dumps(
                    {"success": False, "error": "target room is not active or could not be resolved: {}".format(target)},
                    ensure_ascii=False,
                )
            msgtype = str(body.get("msgtype") or "text").lower()
            if msgtype == "image":
                image = body.get("image") if isinstance(body.get("image"), dict) else {}
                image_path = _write_push_image_file(image)
                group_channel.client.send_image(runtime_target, image_path)
            else:
                text_payload = body.get("text") if isinstance(body.get("text"), dict) else {}
                content = str(text_payload.get("content") or body.get("content") or "")
                if not content:
                    return json.dumps({"success": False, "error": "content is required"}, ensure_ascii=False)
                mention_ids = text_payload.get("mention_ids") or body.get("mention_ids") or []
                if not isinstance(mention_ids, list):
                    mention_ids = []
                group_channel.client.send_text(runtime_target, content, mention_ids=mention_ids)
            return json.dumps({"success": True, "target": target, "runtime_target": runtime_target}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.exception("[PushSendHandler] push send failed: %s", e)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def _require_push_auth():
    token = str(os.environ.get("LIGHTAGENT_PUSH_TOKEN") or conf().get("push_api_token", "") or "").strip()
    if token:
        auth = str(web.ctx.env.get("HTTP_AUTHORIZATION") or "")
        prefix = "Bearer "
        supplied = auth[len(prefix):].strip() if auth.startswith(prefix) else ""
        if not hmac.compare_digest(supplied, token):
            raise web.HTTPError("401 Unauthorized",
                                {"Content-Type": "application/json; charset=utf-8"},
                                json.dumps({"success": False, "error": "Unauthorized"}))
        return
    _require_auth()


def _get_running_channel(channel_type):
    for module_name in ("app", "__main__"):
        module = sys.modules.get(module_name)
        getter = getattr(module, "get_channel_manager", None)
        if not getter:
            continue
        manager = getter()
        if manager is None:
            continue
        channel = manager.get_channel(channel_type)
        if channel is not None:
            return channel
    return None


def _resolve_push_target_room_id(group_channel, target):
    room_id = str(target or "").strip()
    if not room_id:
        return ""
    if not room_id.startswith("wgr_"):
        return room_id
    service = getattr(group_channel, "identity_service", None)
    if service is None:
        try:
            from channel.wechat_group.wechat_group_identity_service import WechatGroupIdentityService

            service = WechatGroupIdentityService()
        except Exception:
            service = None
    if service is not None:
        try:
            runtime_id = str(service.get_active_runtime_room_id(room_id) or "").strip()
            if runtime_id:
                return runtime_id
        except Exception:
            pass
    try:
        rooms = group_channel.get_rooms() if hasattr(group_channel, "get_rooms") else getattr(group_channel, "rooms", [])
    except Exception:
        rooms = []
    for room in rooms or []:
        stable_id = str(room.get("stable_room_id") or room.get("id") or "").strip()
        runtime_id = str(room.get("runtime_room_id") or room.get("room_id") or "").strip()
        if stable_id == room_id and runtime_id:
            return runtime_id
    return ""


def _write_push_image_file(image):
    encoded = str(image.get("base64") or "")
    if not encoded:
        raise ValueError("image.base64 is required")
    raw = base64.b64decode(encoded)
    expected_md5 = str(image.get("md5") or "").strip().lower()
    if expected_md5 and hashlib.md5(raw).hexdigest() != expected_md5:
        raise ValueError("image md5 mismatch")
    upload_dir = _get_upload_dir()
    path = os.path.join(upload_dir, "push-{}.png".format(uuid.uuid4().hex))
    with open(path, "wb") as handle:
        handle.write(raw)
    return path
'''
    marker = "\nclass FeishuRegisterHandler:"
    text = text.replace(marker, handler + marker)
    path.write_text(text, encoding="utf-8")
PY

RUN cp config-template.json config.json \
    && python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-optional.txt \
    && pip install --no-cache-dir -e . \
    && cd /app/channel/wechat_group/sidecar \
    && npm ci --omit=dev \
    && if [ "$INSTALL_LIGHTAGENT_BROWSER" = "true" ]; then \
        pip install --no-cache-dir "playwright==1.52.0" \
        && python -m playwright install-deps chromium \
        && python -m playwright install chromium; \
    fi \
    && mkdir -p /home/agent/lightagent \
    && groupadd -r agent \
    && useradd -r -g agent -s /bin/bash -d /home/agent agent \
    && chown -R agent:agent /home/agent /app /usr/local/lib

COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && sed -i 's/\r$//' /entrypoint.sh \
    && chown agent:agent /entrypoint.sh

EXPOSE 9899

ENTRYPOINT ["/entrypoint.sh"]
