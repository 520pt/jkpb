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
        self.roster_import_endpoint = os.environ.get("DUTY_REMINDER_ROSTER_IMPORT_URL", "http://duty-reminder:8080/api/wechat-roster/import").strip()
        self.roster_confirm_endpoint = os.environ.get("DUTY_REMINDER_ROSTER_CONFIRM_URL", "http://duty-reminder:8080/api/wechat-roster/confirm").strip()
        self.token = os.environ.get("DUTY_REMINDER_QUERY_TOKEN", "520pt").strip()
        self.timeout = float(os.environ.get("DUTY_REMINDER_QUERY_TIMEOUT", "30") or 30)
        self.menu_ttl = int(os.environ.get("DUTY_REMINDER_QUERY_MENU_TTL", "180") or 180)
        self.roster_ttl = int(os.environ.get("DUTY_REMINDER_ROSTER_IMPORT_TTL", "300") or 300)
        self.menu_sessions = {}
        self.roster_sessions = {}
        self.roster_pending_overwrites = {}
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[DutyReminderQuery] inited, endpoint=%s", self.endpoint)

    def on_handle_context(self, e_context: EventContext):
        context = e_context["context"]
        if not context.get("isgroup", False):
            return
        msg = context.get("msg")
        if not msg:
            return
        if context.type == ContextType.IMAGE:
            self._handle_roster_image_context(e_context, context, msg)
            return
        if context.type != ContextType.TEXT:
            return
        text = self._clean_text(context.get("wechat_group_user_content") or context.content)
        if not text:
            return
        session_key = self._session_key(context, msg)
        menu_selection = self._normalize_menu_selection(text)

        if self._has_active_roster_session(session_key) and self._is_roster_cancel(text):
            self._clear_roster_session(session_key)
            e_context["reply"] = Reply(ReplyType.TEXT, "已取消本次排班表导入。")
            e_context.action = EventAction.BREAK_PASS
            return

        if self._has_pending_roster_overwrite(session_key):
            if self._is_roster_cancel(text):
                self._clear_roster_session(session_key)
                e_context["reply"] = Reply(ReplyType.TEXT, "已取消本次排班表导入。")
                e_context.action = EventAction.BREAK_PASS
                return
            if self._is_roster_overwrite_confirm(text):
                reply_text = self._confirm_pending_roster_overwrite(session_key)
                e_context["reply"] = Reply(ReplyType.TEXT, reply_text)
                e_context.action = EventAction.BREAK_PASS
                return

        if menu_selection and self._has_active_menu_session(session_key):
            reply_text = self._reply_for_menu_selection(context, msg, session_key, menu_selection)
            e_context["reply"] = Reply(ReplyType.TEXT, reply_text)
            e_context.action = EventAction.BREAK_PASS
            return

        if (
            getattr(msg, "is_at", False) is not True
            and context.get("wechat_group_visible_at") is not True
            and not self._has_visible_wechat_mention(context.get("wechat_group_user_content") or context.content)
        ):
            return

        if self._is_roster_import_start(text):
            self._store_roster_session(session_key)
            reply_text = (
                "已开启排班表导入，请在 5 分钟内发送排班表图片。\n"
                "识别成功会自动导入；如果同月排班已存在，会先让你确认是否覆盖。"
            )
        elif self._is_help_query(text):
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

    def _handle_roster_image_context(self, e_context: EventContext, context, msg):
        session_key = self._session_key(context, msg)
        if not self._has_active_roster_session(session_key):
            return
        image_path = str(context.content or getattr(msg, "media_path", "") or "").strip()
        reply_text = self._import_roster_image(session_key, image_path, context)
        e_context["reply"] = Reply(ReplyType.TEXT, reply_text)
        e_context.action = EventAction.BREAK_PASS

    def _import_roster_image(self, session_key: str, image_path: str, context=None) -> str:
        if not self.roster_import_endpoint:
            return "排班表导入未配置：缺少 DUTY_REMINDER_ROSTER_IMPORT_URL"
        if not image_path or not os.path.exists(image_path):
            return "没有拿到排班表图片文件，请重新发送图片。"
        headers = {}
        if self.token:
            headers["X-Duty-Query-Token"] = self.token
        try:
            with open(image_path, "rb") as handle:
                files = {"file": (os.path.basename(image_path) or "roster.png", handle, self._image_content_type(image_path))}
                response = requests.post(
                    self.roster_import_endpoint,
                    files=files,
                    data={
                        "overwrite": "false",
                        "room_id": str((context or {}).get("wechat_group_runtime_room_id") or ""),
                        "stable_room_id": str((context or {}).get("wechat_group_stable_room_id") or ""),
                    },
                    headers=headers,
                    timeout=max(self.timeout, 30),
                )
            data = response.json() if response.content else {}
            if response.status_code >= 400:
                return "排班表导入失败：{}".format(data.get("detail") or response.status_code)
            if data.get("import_status") == "conflict":
                self.roster_pending_overwrites[session_key] = {
                    "expires_at": time.time() + max(self.roster_ttl, 60),
                    "payload": {
                        "year": data.get("year"),
                        "month": data.get("month"),
                        "source_image_path": data.get("source_image_path") or "",
                        "grid": data.get("grid") or [],
                        "overwrite": True,
                        "room_id": str((context or {}).get("wechat_group_runtime_room_id") or ""),
                        "stable_room_id": str((context or {}).get("wechat_group_stable_room_id") or ""),
                    },
                }
                self._store_roster_session(session_key)
            else:
                self._clear_roster_session(session_key)
            return str(data.get("reply") or "排班表导入完成")
        except Exception as exc:
            logger.warning("[DutyReminderQuery] roster import failed: %s", exc)
            return "排班表导入失败：无法连接 duty-reminder 或图片读取失败"

    def _confirm_pending_roster_overwrite(self, session_key: str) -> str:
        item = self.roster_pending_overwrites.get(session_key) or {}
        payload = item.get("payload") if isinstance(item, dict) else {}
        if not payload:
            self._clear_roster_session(session_key)
            return "没有可覆盖的排班表导入任务，请重新发送“导入排班”。"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Duty-Query-Token"] = self.token
        try:
            response = requests.post(self.roster_confirm_endpoint, json=payload, headers=headers, timeout=max(self.timeout, 30))
            data = response.json() if response.content else {}
            if response.status_code >= 400:
                return "覆盖导入失败：{}".format(data.get("detail") or response.status_code)
            return str(data.get("reply") or "覆盖导入完成")
        except Exception as exc:
            logger.warning("[DutyReminderQuery] roster overwrite failed: %s", exc)
            return "覆盖导入失败：无法连接 duty-reminder"
        finally:
            self._clear_roster_session(session_key)

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

    def _store_roster_session(self, session_key: str):
        if not session_key or session_key == ":":
            return
        self._cleanup_roster_sessions()
        self.roster_sessions[session_key] = time.time() + max(self.roster_ttl, 60)

    def _has_active_roster_session(self, session_key: str) -> bool:
        if not session_key or session_key == ":":
            return False
        self._cleanup_roster_sessions()
        expires_at = float(self.roster_sessions.get(session_key) or 0)
        return expires_at > time.time()

    def _has_pending_roster_overwrite(self, session_key: str) -> bool:
        if not session_key or session_key == ":":
            return False
        self._cleanup_roster_sessions()
        item = self.roster_pending_overwrites.get(session_key) or {}
        return float(item.get("expires_at") or 0) > time.time()

    def _cleanup_roster_sessions(self):
        now = time.time()
        expired_sessions = [key for key, expires_at in self.roster_sessions.items() if expires_at <= now]
        for key in expired_sessions:
            self.roster_sessions.pop(key, None)
        expired_pending = [key for key, item in self.roster_pending_overwrites.items() if float((item or {}).get("expires_at") or 0) <= now]
        for key in expired_pending:
            self.roster_pending_overwrites.pop(key, None)

    def _clear_roster_session(self, session_key: str):
        self.roster_sessions.pop(session_key, None)
        self.roster_pending_overwrites.pop(session_key, None)

    @staticmethod
    def _image_content_type(image_path: str) -> str:
        value = image_path.lower()
        if value.endswith(".jpg") or value.endswith(".jpeg"):
            return "image/jpeg"
        if value.endswith(".webp"):
            return "image/webp"
        if value.endswith(".bmp"):
            return "image/bmp"
        return "image/png"

    @staticmethod
    def _clean_text(text: str) -> str:
        value = DutyReminderQuery._strip_leading_wechat_mentions(str(text or ""))
        return re.sub(r"\s+", "", value).strip("，,。.!！?？：:、；;")

    @staticmethod
    def _strip_leading_wechat_mentions(text: str) -> str:
        value = str(text or "").strip()
        mention_separator = r"[\s\u2005\u2006\u2007\u2008\u2009\u200a]+"
        for _ in range(5):
            empty_name = re.match(rf"^@{{2,}}{mention_separator}(?P<rest>.*)$", value, re.DOTALL)
            if empty_name:
                value = str(empty_name.group("rest") or "").strip()
                continue
            match = re.match(rf"^@(?P<name>.*?){mention_separator}(?P<rest>.*)$", value, re.DOTALL)
            if not match:
                break
            name = str(match.group("name") or "").strip()
            if not name:
                break
            value = str(match.group("rest") or "").strip()
        return value

    @staticmethod
    def _has_visible_wechat_mention(text: str) -> bool:
        value = str(text or "").strip()
        if not value.startswith("@"):
            return False
        mention_separator = r"[\s\u2005\u2006\u2007\u2008\u2009\u200a]+"
        return bool(
            re.match(rf"^@{{2,}}{mention_separator}.+", value, re.DOTALL)
            or re.match(rf"^@.+?{mention_separator}.+", value, re.DOTALL)
        )

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
    def _is_roster_import_start(text: str) -> bool:
        return text in {
            "导入排班",
            "导入排班表",
            "上传排班",
            "上传排班表",
            "识别排班",
            "识别排班表",
            "排班表导入",
            "微信群导入排班",
        }

    @staticmethod
    def _is_roster_overwrite_confirm(text: str) -> bool:
        return text in {"覆盖导入", "确认覆盖", "替换导入", "覆盖排班", "确认替换"}

    @staticmethod
    def _is_roster_cancel(text: str) -> bool:
        return text in {"取消导入", "取消排班导入", "放弃导入", "不导入"}

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
            "隧道机电",
            "查询今日机电",
            "查询今天机电",
            "机电日常检查",
        }:
            return True
        if "帮助" in text and any(keyword in text for keyword in ("查询", "监控", "提醒", "绑定")):
            return True
        if "隧道机电" in text or "机电日常检查" in text:
            return True
        if "机电" in text and any(keyword in text for keyword in ("查询", "查", "今日", "今天", "昨日", "昨天", "明日", "明天")):
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

group_path = Path("/app/channel/wechat_group/wechat_group_channel.py")
group_text = group_path.read_text(encoding="utf-8")
if "_handle_duty_reminder_fast_path" not in group_text:
    if "\nimport os\n" not in group_text:
        group_text = group_text.replace("\nimport re\n", "\nimport os\nimport re\n", 1)
    if "from urllib.parse import urljoin, urlparse" not in group_text:
        group_text = group_text.replace(
            "from pathlib import Path\n",
            "from pathlib import Path\nfrom urllib.parse import urljoin, urlparse\n\nimport requests\n",
            1,
        )
    group_text = group_text.replace(
        '        is_pat_self = getattr(msg, "is_pat_self", False) is True\n'
        '        direct_reply = (\n'
        '            getattr(msg, "is_at", False) is True\n'
        '            or getattr(msg, "is_quote_self", False) is True\n'
        '            or is_pat_self\n'
        '        )\n',
        '        is_pat_self = getattr(msg, "is_pat_self", False) is True\n'
        '        visible_at_content = self._visible_bot_mention_content(msg)\n'
        '        visible_at = visible_at_content is not None\n'
        '        direct_reply = (\n'
        '            getattr(msg, "is_at", False) is True\n'
        '            or getattr(msg, "is_quote_self", False) is True\n'
        '            or is_pat_self\n'
        '            or visible_at\n'
        '        )\n',
        1,
    )
    group_text = group_text.replace(
        '        if self._should_suppress_at_during_free_reply_mute(msg):\n'
        '            return\n'
        '        if msg.ctype == ContextType.IMAGE:\n',
        '        if self._should_suppress_at_during_free_reply_mute(msg):\n'
        '            return\n'
        '        if direct_reply and msg.ctype == ContextType.TEXT:\n'
        '            if self._handle_duty_reminder_fast_path(msg, visible_at_content):\n'
        '                return\n'
        '        if msg.ctype == ContextType.IMAGE:\n',
        1,
    )
    group_text = group_text.replace(
        '        trigger_source = "quote_self" if is_quote_self else ("pat_self" if is_pat_self else ("direct_reply" if direct_reply else ""))\n'
        '        context = self._compose_context(\n'
        '            msg.ctype,\n'
        '            msg.content,\n'
        '            isgroup=True,\n'
        '            msg=msg,\n'
        '            wechat_group_force_reply=force_reply,\n'
        '            wechat_group_trigger_source=trigger_source,\n'
        '        )\n',
        '        trigger_source = "quote_self" if is_quote_self else ("pat_self" if is_pat_self else ("direct_reply" if direct_reply else ""))\n'
        '        content = visible_at_content if visible_at and msg.ctype == ContextType.TEXT else msg.content\n'
        '        context = self._compose_context(\n'
        '            msg.ctype,\n'
        '            content,\n'
        '            isgroup=True,\n'
        '            msg=msg,\n'
        '            wechat_group_force_reply=force_reply,\n'
        '            wechat_group_visible_at=visible_at,\n'
        '            wechat_group_trigger_source=trigger_source,\n'
        '        )\n',
        1,
    )
    group_text = group_text.replace(
        '        context["wechat_group_user_content"] = context.content\n'
        '        if kwargs.get("wechat_group_is_free_reply"):\n',
        '        context["wechat_group_user_content"] = context.content\n'
        '        if kwargs.get("wechat_group_visible_at"):\n'
        '            context["wechat_group_visible_at"] = True\n'
        '        if kwargs.get("wechat_group_is_free_reply"):\n',
        1,
    )
    group_text = group_text.replace(
        '    def _handle_free_reply_mute_command(self, msg: WechatGroupMessage) -> bool:\n',
        r'''
    def _handle_duty_reminder_fast_path(self, msg: WechatGroupMessage, visible_at_content) -> bool:
        text = self._clean_duty_reminder_text(
            visible_at_content
            if visible_at_content is not None
            else (getattr(msg, "text", None) or getattr(msg, "content", ""))
        )
        if not text or not self._looks_like_duty_reminder_text(text):
            return False
        endpoint = os.environ.get("DUTY_REMINDER_QUERY_URL", "http://duty-reminder:8080/api/wechat-query").strip()
        token = os.environ.get("DUTY_REMINDER_QUERY_TOKEN", "520pt").strip()
        timeout = float(os.environ.get("DUTY_REMINDER_QUERY_TIMEOUT", "30") or 30)
        if not endpoint:
            self.client.send_text(getattr(msg, "runtime_room_id", "") or msg.other_user_id, "监控查询未配置：缺少 DUTY_REMINDER_QUERY_URL")
            return True
        payload = {
            "text": text,
            "room_id": str(getattr(msg, "runtime_room_id", "") or getattr(msg, "other_user_id", "") or ""),
            "stable_room_id": str(getattr(msg, "wechat_group_stable_room_id", "") or ""),
            "sender_id": str(getattr(msg, "actual_user_id", "") or ""),
            "runtime_sender_id": str(getattr(msg, "runtime_sender_id", "") or getattr(msg, "actual_user_id", "") or ""),
            "stable_member_id": str(getattr(msg, "wechat_group_stable_member_id", "") or ""),
            "sender_name": str(getattr(msg, "actual_user_nickname", "") or ""),
        }
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Duty-Query-Token"] = token
        receiver = str(getattr(msg, "runtime_room_id", "") or getattr(msg, "other_user_id", "") or "")
        image_path = ""
        try:
            logger.info(
                '[wechat_group] duty-reminder fast path: room="{}" text="{}"'.format(
                    _wechat_group_log_value(getattr(msg, "other_user_nickname", "") or receiver),
                    _wechat_group_log_preview(text),
                )
            )
            response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            data = response.json() if response.content else {}
            if response.status_code >= 400:
                reply_text = "监控查询失败：{}".format(data.get("detail") or response.status_code)
            else:
                reply_text = str(data.get("reply") or "没有查询到结果")
                try:
                    image_path = self._download_duty_reminder_image(
                        data.get("image_full_url") or data.get("image_url"),
                        endpoint,
                    )
                except Exception as exc:
                    logger.warning("[wechat_group] duty-reminder image download failed: %s", exc)
        except Exception as exc:
            logger.warning("[wechat_group] duty-reminder fast path failed: %s", exc)
            reply_text = "监控查询失败：无法连接 duty-reminder"
        self.client.send_text(receiver, reply_text)
        logger.info(
            '[wechat_group] duty-reminder fast path text sent: room="{}" chars={}'.format(
                _wechat_group_log_value(receiver),
                len(reply_text),
            )
        )
        if image_path:
            self.client.send_image(receiver, image_path)
            logger.info(
                '[wechat_group] duty-reminder fast path image sent: room="{}" path="{}"'.format(
                    _wechat_group_log_value(receiver),
                    _wechat_group_log_value(image_path),
                )
            )
        return True

    @staticmethod
    def _download_duty_reminder_image(image_url, endpoint: str) -> str:
        url_text = str(image_url or "").strip()
        if not url_text:
            return ""
        if not url_text.startswith(("http://", "https://")):
            url_text = urljoin(str(endpoint or ""), url_text)
        parsed = urlparse(url_text)
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        media_dir = Path(get_wechat_group_sidecar_memory_path()) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        image_path = media_dir / "duty-reminder-result-{}{}".format(int(time.time() * 1000), suffix)
        response = requests.get(url_text, timeout=15)
        response.raise_for_status()
        image_path.write_bytes(response.content)
        return str(image_path)

    @staticmethod
    def _clean_duty_reminder_text(text: str) -> str:
        value = str(text or "").strip()
        mention_separator = r"[\s\u2005\u2006\u2007\u2008\u2009\u200a]+"
        for _ in range(5):
            empty_name = re.match(rf"^@{{2,}}{mention_separator}(?P<rest>.*)$", value, re.DOTALL)
            if empty_name:
                value = str(empty_name.group("rest") or "").strip()
                continue
            match = re.match(rf"^@(?P<name>.*?){mention_separator}(?P<rest>.*)$", value, re.DOTALL)
            if not match:
                break
            value = str(match.group("rest") or "").strip()
        return re.sub(r"\s+", "", value).strip("，。！？；：,.!?;:")

    @staticmethod
    def _looks_like_duty_reminder_text(text: str) -> bool:
        if text in {
            "帮助",
            "查询帮助",
            "监控帮助",
            "提醒帮助",
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
            "查询本周监控",
            "本周监控",
            "查询下周监控",
            "下周监控",
            "查询未来7天",
            "未来7天",
            "查询下次提醒",
            "下次提醒",
            "查询我的绑定",
            "我的绑定",
            "查我的绑定",
            "绑定查询",
            "隧道机电",
            "查询今日机电",
            "查询今天机电",
            "机电日常检查",
            "导入排班",
            "排班导入",
        }:
            return True
        if text.startswith(("隧道机电录入", "隧道机电预览")):
            return True
        if "隧道机电" in text or "机电日常检查" in text:
            return True
        if "机电" in text and any(keyword in text for keyword in ("查询", "查", "今日", "今天", "昨日", "昨天", "明日", "明天")):
            return True
        if "帮助" in text and any(keyword in text for keyword in ("查询", "监控", "提醒", "绑定")):
            return True
        if "我" in text and any(keyword in text for keyword in ("什么班", "上班吗", "值班", "监控", "排班", "提醒")):
            return True
        return "查询" in text and any(
            keyword in text
            for keyword in ("监控", "排班", "提醒", "绑定", "值班", "本周", "下周", "未来", "下次")
        )

    def _visible_bot_mention_content(self, msg: WechatGroupMessage):
        if getattr(msg, "ctype", None) != ContextType.TEXT:
            return None
        if getattr(msg, "is_at", False) is True:
            return None
        text = _wechat_group_log_value(
            getattr(msg, "text", None) or getattr(msg, "content", "")
        ).strip()
        if not text.startswith("@"):
            return None
        separator = r"[\s\u2005\u2006\u2007\u2008\u2009\u200a]+"
        empty_name = re.match(rf"^@{{2,}}{separator}(?P<rest>.*)$", text, re.DOTALL)
        if empty_name:
            return str(empty_name.group("rest") or "").strip()
        match = re.match(rf"^@(?P<name>.*?){separator}(?P<rest>.*)$", text, re.DOTALL)
        if not match:
            return None
        name = self._normalize_visible_mention_name(match.group("name"))
        if not name:
            return None
        bot_names = [
            getattr(msg, "self_display_name", ""),
            getattr(msg, "to_user_nickname", ""),
            getattr(msg, "to_user_id", ""),
            getattr(msg, "runtime_self_id", ""),
            self.name,
        ]
        normalized_bot_names = {
            self._normalize_visible_mention_name(value)
            for value in bot_names
            if self._normalize_visible_mention_name(value)
        }
        if name in normalized_bot_names:
            return str(match.group("rest") or "").strip()
        return None

    @staticmethod
    def _normalize_visible_mention_name(value) -> str:
        return re.sub(r"\s+", "", str(value or "").strip().lstrip("@")).lower()

    def _handle_free_reply_mute_command(self, msg: WechatGroupMessage) -> bool:
'''.lstrip(),
        1,
    )
if "wechat_group_roster_import_probe" not in group_text:
    group_text = group_text.replace(
        '        if msg.ctype == ContextType.IMAGE:\n'
        '            if not direct_reply:\n'
        '                if not conf().get("wechat_group_free_reply_image_understanding_enabled", False):\n'
        '                    return\n',
        '        if msg.ctype == ContextType.IMAGE:\n'
        '            if not direct_reply:\n'
        '                roster_context = self._compose_context(\n'
        '                    ContextType.IMAGE,\n'
        '                    getattr(msg, "media_path", "") or getattr(msg, "content", ""),\n'
        '                    isgroup=True,\n'
        '                    msg=msg,\n'
        '                    wechat_group_trigger_source="wechat_group_roster_import_probe",\n'
        '                )\n'
        '                if roster_context:\n'
        '                    self.produce(roster_context)\n'
        '                if not conf().get("wechat_group_free_reply_image_understanding_enabled", False):\n'
        '                    return\n',
    )
    group_path.write_text(group_text, encoding="utf-8")

sidecar_core_path = Path("/app/channel/wechat_group/sidecar/wechaty-sidecar-core.mjs")
sidecar_core_text = sidecar_core_path.read_text(encoding="utf-8")
sidecar_core_original = sidecar_core_text
if "function shouldRefreshRoomMemberPayload" not in sidecar_core_text:
    sidecar_core_text = sidecar_core_text.replace(
        "\nexport function memberPayloadMatchesQuery(payload = {}, query = '') {\n",
        r'''

export function shouldRefreshRoomMemberPayload(payload = {}) {
  const senderId = String(payload?.sender_id || '').trim()
  const nickname = String(payload?.sender_nickname || '').trim()
  const wechatId = String(payload?.wechat_id || '').trim()
  if (!senderId) return false
  if (!nickname || !wechatId) return true
  if (nickname === senderId || nickname.replace(/^[@\uFF20]+/u, '') === senderId.replace(/^[@\uFF20]+/u, '')) {
    return true
  }
  return looksLikeRawWechatInternalId(nickname)
}
'''.rstrip() + "\n\nexport function memberPayloadMatchesQuery(payload = {}, query = '') {\n",
    )
if "rawPayload?.DisplayName" not in sidecar_core_text:
    sidecar_core_text = sidecar_core_text.replace(
        "    rawPayload?.User?.NickName,\n",
        "    rawPayload?.User?.NickName,\n"
        "    rawPayload?.DisplayName,\n"
        "    rawPayload?.RemarkName,\n"
        "    rawPayload?.NickName,\n",
    )
if sidecar_core_text != sidecar_core_original:
    sidecar_core_path.write_text(sidecar_core_text, encoding="utf-8")

sidecar_path = Path("/app/channel/wechat_group/sidecar/wechaty-sidecar.mjs")
sidecar_text = sidecar_path.read_text(encoding="utf-8")
sidecar_original = sidecar_text
if "shouldRefreshRoomMemberPayload" not in sidecar_text:
    sidecar_text = sidecar_text.replace(
        "  sendText as sendTextCore,\n} from './wechaty-sidecar-core.mjs'\n",
        "  sendText as sendTextCore,\n  shouldRefreshRoomMemberPayload,\n} from './wechaty-sidecar-core.mjs'\n",
    )
    sidecar_text = sidecar_text.replace(
        "    if (query && !memberPayloadMatchesQuery(payload, query)) {\n",
        "    if (shouldRefreshRoomMemberPayload(payload) || (query && !memberPayloadMatchesQuery(payload, query))) {\n",
    )
if "async function roomMemberRawPayload" not in sidecar_text:
    sidecar_text = sidecar_text.replace(
        "\nasync function contactPayload(contact, room = null, rawPayload = null) {\n",
        r'''

async function roomMemberRawPayload(room, contact) {
  try {
    if (room?.id && contact?.id && typeof state.bot?.puppet?.roomMemberRawPayload === 'function') {
      return await state.bot.puppet.roomMemberRawPayload(room.id, contact.id)
    }
  } catch {}
  return null
}
'''.rstrip() + "\n\nasync function contactPayload(contact, room = null, rawPayload = null) {\n",
    )
    sidecar_text = sidecar_text.replace(
        "      const rawPayload = await contactRawPayload(contact)\n",
        "      const rawPayload = await roomMemberRawPayload(room, contact) || await contactRawPayload(contact)\n",
    )
if sidecar_text != sidecar_original:
    sidecar_path.write_text(sidecar_text, encoding="utf-8")
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
