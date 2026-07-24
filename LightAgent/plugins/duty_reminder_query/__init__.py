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


def _duty_reminder_base_url() -> str:
    return os.environ.get("DUTY_REMINDER_BASE_URL", "http://duty-reminder:8080").strip().rstrip("/")


def _duty_reminder_url(env_name: str, path: str) -> str:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        return configured
    base_url = _duty_reminder_base_url()
    return f"{base_url}{path}" if base_url else ""


@plugins.register(
    name="DutyReminderQuery",
    desire_priority=950,
    hidden=False,
    enabled=True,
    desc="Query duty-reminder status and tunnel mechanical records from WeChat groups",
    version="0.2",
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
        self.endpoint = _duty_reminder_url("DUTY_REMINDER_QUERY_URL", "/api/wechat-query")
        self.roster_import_endpoint = _duty_reminder_url("DUTY_REMINDER_ROSTER_IMPORT_URL", "/api/wechat-roster/import")
        self.roster_confirm_endpoint = _duty_reminder_url("DUTY_REMINDER_ROSTER_CONFIRM_URL", "/api/wechat-roster/confirm")
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
                e_context["reply"] = Reply(ReplyType.TEXT, self._confirm_pending_roster_overwrite(session_key))
                e_context.action = EventAction.BREAK_PASS
                return

        if menu_selection and self._has_active_menu_session(session_key):
            e_context["reply"] = Reply(ReplyType.TEXT, self._reply_for_menu_selection(context, msg, session_key, menu_selection))
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
        return float(self.menu_sessions.get(session_key) or 0) > time.time()

    @staticmethod
    def _menu_invalid_reply() -> str:
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
        e_context["reply"] = Reply(ReplyType.TEXT, self._import_roster_image(session_key, image_path, context))
        e_context.action = EventAction.BREAK_PASS

    def _import_roster_image(self, session_key: str, image_path: str, context=None) -> str:
        if not self.roster_import_endpoint:
            return "排班表导入未配置：缺少 DUTY_REMINDER_BASE_URL"
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
            return "监控查询未配置：缺少 DUTY_REMINDER_BASE_URL"
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
        return float(self.roster_sessions.get(session_key) or 0) > time.time()

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
        expired_pending = [
            key
            for key, item in self.roster_pending_overwrites.items()
            if float((item or {}).get("expires_at") or 0) <= now
        ]
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
        match = re.fullmatch(r"(?:序号|选项|选择|回复)?([1-9一二两三四五六七八九])(?:项|号)?", value)
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
        }:
            return True
        if text.startswith(("隧道机电录入", "隧道机电预览")):
            return True
        if re.fullmatch(r"查询\d{4}-\d{1,2}-\d{1,2}机电", text):
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
