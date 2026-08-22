from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.custom_reminders import normalize_custom_reminder_time_for_import

DEFAULT_MESSAGE_TEMPLATE = "{name} {date}（{time_range})是你的{shift_label}"
LEGACY_DAILY_DUTY_TEMPLATE = (
    "今日在岗人员\n"
    "监控班：早班：{early}，中班：{middle}，晚班：{night}\n"
    "驾驶员：大车：{big_drivers} 小车：{small_drivers}\n"
    "备勤人员：{standby}"
)
DEFAULT_DAILY_DUTY_TEMPLATE = (
    "今日在岗人员\n"
    "监控班：今日早班：{early}，明日早班：{tomorrow_early}，中班：{middle}，晚班：{night}\n"
    "巡查班：{patrol}\n"
    "站管：{station}\n"
    "办公室：{office}\n"
    "驾驶员：大车：{big_drivers} 小车：{small_drivers}\n"
    "备勤人员：{standby}\n"
    "今日下午休息：{afternoon_rest}\n"
    "正在休息：{resting}\n"
    "今日下午到岗：{afternoon_return}"
)
DEFAULT_PATROL_TEAM_GROUP_NAMES = ("一班", "二班", "三班")
LEGACY_REST_MESSAGE_TEMPLATE = "{name} {date} 今天休息"
LEGACY_TOMORROW_REST_MESSAGE_TEMPLATE = "{name} {date} 明天休息"
DEFAULT_REST_MESSAGE_TEMPLATE = "{name} {rest_status}"
DEFAULT_VACATION_START_TEMPLATE = "恭喜你今天下午可以开始休息了，加油一天要苦80块的男人，"
DEFAULT_VACATION_END_TEMPLATE = "假期余额不足，今天下午就该返回站点了，加油天选打工人。"
DEFAULT_VACATION_START_TEMPLATES = [
    DEFAULT_VACATION_START_TEMPLATE,
    "恭喜你，牛马暂时获得喘气权，今天再熬一下。",
    "系统提示：你的休息即将到账，今天请活着下班。",
    "今日 KPI：坚持到下午，然后原地复活。",
    "正式确诊为即将休息的牛马，今天再苦最后一天。",
    "恭喜你，打工暂停键即将生效，请完成今日最后挣扎。",
    "休息申请已被命运批准，今天下午开始短暂做人。",
    "今天再搬最后一天砖，明天开始当废物。",
    "你的假期正在派送中，预计今天下午签收。",
    "再撑一天，灵魂就可以从工位回来了。",
    "温馨提醒：今天活着下班，就是本周最大胜利。",
    "牛马能量即将耗尽，系统已安排休息充电。",
    "今天下午开始休息，恭喜你暂时退出人间疾苦。",
    "坚持住，今天下班后你就不是牛马，是自由的牛马。",
    "今日任务：少说话，多忍耐，下午开始休息。",
    "恭喜你，马上可以从“虽然没挣钱，起码累着了”切换成“虽然没上班，起码躺着了”。",
]
DEFAULT_VACATION_END_TEMPLATES = [
    DEFAULT_VACATION_END_TEMPLATE,
    "假期余额不足，牛马身份即将自动恢复。",
    "系统提示：休息体验卡今日到期，请准备返岗。",
    "你的自由试用期即将结束，明天继续搬砖。",
    "正式确诊为假期余额不足患者，请及时返回站点。",
    "休息模式即将关闭，牛马模式正在重启。",
    "今天是假期最后一天，请珍惜还能躺平的每一分钟。",
    "假期即将清零，明天继续为了生活低头。",
    "温馨提醒：快乐供给不足，请准备恢复上班。",
    "你的灵魂刚回家，身体又要返岗了。",
    "假期余额告急，打工人的钢铁意志即将上线。",
    "今日下午返回站点，继续做一个稳定发疯的成年人。",
    "休息结束不是结束，是下一轮想休息的开始。",
    "恭喜你完成短暂回血，明天继续掉血。",
    "自由倒计时结束，请收拾心情继续当选手。",
    "假期余额不足，别难过，至少你曾经短暂地不是牛马。",
]
DEFAULT_PATROL_WARNING_START_TEMPLATE = (
    "{mention_prefix}请注意监测到 {app_name} 发布 {warning_level_label}\n"
    "路线：{route_text}\n"
    "预警开始时间：{start_time}\n"
    "桩号：{stake_range}"
)
LEGACY_PATROL_WARNING_END_TEMPLATE = (
    "{mention_prefix}请注意监测到 {app_name} 发布 {warning_level_label}\n"
    "路线：{route_text}\n"
    "预警开始时间：{start_time}\n"
    "桩号：{stake_range}\n"
    "预警结束时间：{end_time}\n"
    "预警已结束：{elapsed_hours} 小时\n"
    "距离预警结束后{window_hours}小时内巡查 倒计时结束还有 {remaining_hours} 小时"
)
DEFAULT_PATROL_WARNING_END_TEMPLATE = (
    "{mention_prefix}最新{warning_level_label}已结束\n"
    "路线：{route_text}\n"
    "预警开始时间：{start_time}\n"
    "桩号：{stake_range}\n"
    "预警结束时间：{end_time}\n"
    "预警已结束：{elapsed_hours} 小时\n"
    "距离预警结束后{window_hours}小时内{patrol_frequency_clause}，倒计时结束还有 {remaining_hours} 小时。"
)


def _clean_name_list(values: list[str] | None) -> list[str]:
    seen: list[str] = []
    for value in values or []:
        name = str(value or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _patrol_team_group_default_name(index: int) -> str:
    if index < len(DEFAULT_PATROL_TEAM_GROUP_NAMES):
        return DEFAULT_PATROL_TEAM_GROUP_NAMES[index]
    return f"班组{index + 1}"


def _normalize_patrol_team_groups(groups: list[dict[str, Any]] | None, fallback_names: list[str] | None = None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    raw_groups = list(groups or [])
    if raw_groups:
        for index, group in enumerate(raw_groups):
            members = group.get("members")
            if members is None:
                members = group.get("names")
            normalized.append(
                {
                    "name": str(group.get("name") or _patrol_team_group_default_name(index)).strip() or _patrol_team_group_default_name(index),
                    "members": _clean_name_list(list(members or [])),
                }
            )
        return normalized
    if fallback_names is not None:
        normalized.append({"name": _patrol_team_group_default_name(0), "members": _clean_name_list(list(fallback_names))})
    while len(normalized) < len(DEFAULT_PATROL_TEAM_GROUP_NAMES):
        normalized.append({"name": _patrol_team_group_default_name(len(normalized)), "members": []})
    return normalized


def _flatten_patrol_team_groups(groups: list[dict[str, Any]] | None, fallback_names: list[str] | None = None) -> list[str]:
    if groups:
        return _clean_name_list([name for group in groups for name in list(group.get("members") or group.get("names") or [])])
    return _clean_name_list(list(fallback_names or []))
NOTIFICATION_SENDER_TYPES = {"wecom_webhook"}
NOTIFICATION_MENTION_MODES = {"none", "all", "person", "custom"}
CONFIG_EXPORT_TABLES = [
    "roster_months",
    "roster_versions",
    "monitored_people",
    "notification_config",
    "feature_channel_config",
    "wechat_interaction_config",
    "wecom_app_menu_config",
    "construction_sites",
    "personnel_names",
    "deleted_personnel",
    "custom_reminders",
    "daily_duty_config",
    "vacation_reminder_config",
    "patrol_warning_config",
    "tunnel_mechanical_config",
    "tunnel_mechanical_template",
]


def _normalize_notification_sender_type(value: str) -> str:
    normalized = str(value or "wecom_webhook").strip().lower()
    return normalized if normalized in NOTIFICATION_SENDER_TYPES else "wecom_webhook"


def _normalize_notification_mention_mode(value: str) -> str:
    normalized = str(value or "person").strip().lower()
    return normalized if normalized in NOTIFICATION_MENTION_MODES else "person"


def _normalize_patrol_send_content_mode(value: str) -> str:
    normalized = str(value or "both").strip().lower()
    return normalized if normalized in {"both", "text", "image"} else "both"


def _normalize_send_content_mode(value: str, default: str = "both") -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in {"both", "text", "image"} else default


def _normalize_template_list(values: Any, fallback: list[str]) -> list[str]:
    items = values if isinstance(values, list) else []
    normalized: list[str] = []
    for value in items:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized or list(fallback)


def _normalize_name_list(values: Any) -> list[str]:
    items = values if isinstance(values, list) else []
    normalized: list[str] = []
    for value in items:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_function_target_names(values: Any) -> dict[str, list[str]]:
    if not isinstance(values, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, raw_names in values.items():
        clean_key = str(key or "").strip()
        if clean_key:
            normalized[clean_key] = _normalize_name_list(raw_names)
    return normalized


def _normalize_patrol_end_template(value: str) -> str:
    text = str(value or "").strip()
    if not text or text == LEGACY_PATROL_WARNING_END_TEMPLATE:
        return DEFAULT_PATROL_WARNING_END_TEMPLATE
    return text


def _loads_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value) if str(value or "").strip() else default
    except Exception:
        return default


def _normalize_feature_channel_rooms(rooms: Any) -> list[dict[str, str]]:
    if not isinstance(rooms, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for room in rooms:
        if not isinstance(room, dict):
            continue
        room_id = str(
            room.get("id")
            or room.get("room_id")
            or room.get("stable_room_id")
            or room.get("wechat_group_room_id")
            or ""
        ).strip()
        if not room_id or room_id in seen:
            continue
        seen.add(room_id)
        normalized.append(
            {
                "id": room_id,
                "name": str(room.get("name") or room.get("room_name") or room.get("wechat_group_room_name") or "").strip(),
            }
        )
    return normalized


class DutyRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            # Some read-only or special SQLite targets cannot switch journal
            # mode. Keep the connection usable instead of failing startup.
            pass
        return conn

    def create_database_backup(self, backup_dir: str | Path | None = None, *, prefix: str = "duty-reminder") -> dict[str, Any]:
        target_dir = Path(backup_dir) if backup_dir is not None else self.db_path.parent / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.db"
        target = target_dir / filename
        with self._connect() as source:
            with sqlite3.connect(target) as dest:
                source.backup(dest)
        return {
            "path": str(target),
            "filename": filename,
            "size": target.stat().st_size if target.exists() else 0,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS roster_months (
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    grid_json TEXT NOT NULL,
                    source_image_path TEXT NOT NULL DEFAULT '',
                    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (year, month)
                );

                CREATE TABLE IF NOT EXISTS roster_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    grid_json TEXT NOT NULL,
                    source_image_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS monitored_people (
                    name TEXT PRIMARY KEY,
                    wecom_userid TEXT NOT NULL,
                    mention_text TEXT NOT NULL DEFAULT '',
                    mention_mobile TEXT NOT NULL DEFAULT '',
                    daily_time TEXT NOT NULL DEFAULT '07:50',
                    before_shift_minutes INTEGER NOT NULL DEFAULT 10,
                    rest_reminder_enabled INTEGER NOT NULL DEFAULT 0,
                    rest_reminder_time TEXT NOT NULL DEFAULT '08:30',
                    rest_message_template TEXT NOT NULL DEFAULT '',
                    notification_room_id TEXT NOT NULL DEFAULT '',
                    notification_room_name TEXT NOT NULL DEFAULT '',
                    send_content_mode TEXT NOT NULL DEFAULT 'both',
                    enabled INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS notification_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    sender_type TEXT NOT NULL DEFAULT 'wecom_webhook',
                    webhook_url TEXT NOT NULL DEFAULT '',
                    wecom_aibot_enabled INTEGER NOT NULL DEFAULT 0,
                    wecom_aibot_id TEXT NOT NULL DEFAULT '',
                    wecom_aibot_secret TEXT NOT NULL DEFAULT '',
                    wecom_app_enabled INTEGER NOT NULL DEFAULT 0,
                    wecom_app_corp_id TEXT NOT NULL DEFAULT '',
                    wecom_app_agent_id TEXT NOT NULL DEFAULT '',
                    wecom_app_secret TEXT NOT NULL DEFAULT '',
                    wecom_app_token TEXT NOT NULL DEFAULT '',
                    wecom_app_encoding_aes_key TEXT NOT NULL DEFAULT '',
                    wecom_app_target_names_json TEXT NOT NULL DEFAULT '[]',
                    wecom_app_function_target_names_json TEXT NOT NULL DEFAULT '{}',
                    lightagent_url TEXT NOT NULL DEFAULT '',
                    lightagent_token TEXT NOT NULL DEFAULT '',
                    lightagent_target TEXT NOT NULL DEFAULT '',
                    lightagent_targets_json TEXT NOT NULL DEFAULT '[]',
                    mention_mode TEXT NOT NULL DEFAULT 'person',
                    mention_targets TEXT NOT NULL DEFAULT '',
                    message_template TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS feature_channel_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    lightagent_web_url TEXT NOT NULL DEFAULT '',
                    lightagent_web_password TEXT NOT NULL DEFAULT '',
                    wechat_group_room_id TEXT NOT NULL DEFAULT '',
                    wechat_group_room_name TEXT NOT NULL DEFAULT '',
                    wechat_group_rooms_json TEXT NOT NULL DEFAULT '[]',
                    allow_tunnel_mechanical INTEGER NOT NULL DEFAULT 1,
                    allow_duty_query INTEGER NOT NULL DEFAULT 1,
                    allow_roster_import INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS wechat_interaction_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    patrol_record_triggers_json TEXT NOT NULL DEFAULT '["巡查记录","查询巡查记录","查巡查记录","巡查记录查询"]',
                    patrol_record_template TEXT NOT NULL DEFAULT '',
                    tunnel_template_triggers_json TEXT NOT NULL DEFAULT '["模板"]',
                    tunnel_template TEXT NOT NULL DEFAULT '',
                    tunnel_modify_template_triggers_json TEXT NOT NULL DEFAULT '["修改","修改模板","改模板"]',
                    tunnel_modify_template TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS wecom_app_menu_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    menu_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS construction_sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS wechat_interaction_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL DEFAULT '',
                    room_name TEXT NOT NULL DEFAULT '',
                    sender_id TEXT NOT NULL DEFAULT '',
                    sender_name TEXT NOT NULL DEFAULT '',
                    command_text TEXT NOT NULL DEFAULT '',
                    query_type TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    reply_text TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS personnel_names (
                    name TEXT PRIMARY KEY,
                    mention_mobile TEXT NOT NULL DEFAULT '',
                    wecom_userid TEXT NOT NULL DEFAULT '',
                    wechat_group_room_id TEXT NOT NULL DEFAULT '',
                    wechat_group_room_name TEXT NOT NULL DEFAULT '',
                    wechat_group_member_id TEXT NOT NULL DEFAULT '',
                    wechat_group_runtime_sender_id TEXT NOT NULL DEFAULT '',
                    wechat_group_member_name TEXT NOT NULL DEFAULT '',
                    tunnel_mechanical_partner TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS deleted_personnel (
                    name TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS custom_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    mention_mobile TEXT NOT NULL DEFAULT '',
                    shift_code TEXT NOT NULL,
                    reminder_time TEXT NOT NULL,
                    message TEXT NOT NULL,
                    notification_room_id TEXT NOT NULL DEFAULT '',
                    notification_room_name TEXT NOT NULL DEFAULT '',
                    send_content_mode TEXT NOT NULL DEFAULT 'both',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS daily_duty_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    reminder_time TEXT NOT NULL DEFAULT '07:50',
                    big_driver_names_json TEXT NOT NULL DEFAULT '[]',
                    small_driver_names_json TEXT NOT NULL DEFAULT '[]',
                    patrol_team_names_json TEXT NOT NULL DEFAULT '[]',
                    patrol_team_groups_json TEXT NOT NULL DEFAULT '[]',
                    station_names_json TEXT NOT NULL DEFAULT '[]',
                    office_names_json TEXT NOT NULL DEFAULT '[]',
                    message_template TEXT NOT NULL DEFAULT '',
                    notification_room_id TEXT NOT NULL DEFAULT '',
                    notification_room_name TEXT NOT NULL DEFAULT '',
                    send_content_mode TEXT NOT NULL DEFAULT 'both',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS vacation_reminder_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    start_reminder_time TEXT NOT NULL DEFAULT '07:50',
                    end_reminder_time TEXT NOT NULL DEFAULT '07:50',
                    start_message_template TEXT NOT NULL DEFAULT '',
                    end_message_template TEXT NOT NULL DEFAULT '',
                    start_message_templates_json TEXT NOT NULL DEFAULT '[]',
                    end_message_templates_json TEXT NOT NULL DEFAULT '[]',
                    send_content_mode TEXT NOT NULL DEFAULT 'both',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sent_reminders (
                    reminder_key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS send_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    notification_room_id TEXT NOT NULL DEFAULT '',
                    notification_room_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS patrol_warning_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    login_url TEXT NOT NULL DEFAULT '',
                    warning_url TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    password TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '2',
                    route_code TEXT NOT NULL DEFAULT '',
                    poll_interval_minutes INTEGER NOT NULL DEFAULT 10,
                    rows INTEGER NOT NULL DEFAULT 5000,
                    end_reminder_enabled INTEGER NOT NULL DEFAULT 1,
                    end_reminder_interval_hours INTEGER NOT NULL DEFAULT 6,
                    end_reminder_window_hours INTEGER NOT NULL DEFAULT 48,
                    mention_all INTEGER NOT NULL DEFAULT 1,
                    mention_mobiles TEXT NOT NULL DEFAULT '',
                    send_content_mode TEXT NOT NULL DEFAULT 'both',
                    start_message_template TEXT NOT NULL DEFAULT '',
                    end_message_template TEXT NOT NULL DEFAULT '',
                    notification_room_id TEXT NOT NULL DEFAULT '',
                    notification_room_name TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS patrol_warning_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    warning_key TEXT NOT NULL DEFAULT '',
                    warning_json TEXT NOT NULL DEFAULT '{}',
                    last_checked_at TEXT NOT NULL DEFAULT '',
                    last_start_sent_key TEXT NOT NULL DEFAULT '',
                    last_end_reminder_slot TEXT NOT NULL DEFAULT '',
                    token TEXT NOT NULL DEFAULT '',
                    token_expires_at TEXT NOT NULL DEFAULT '',
                    next_check_at TEXT NOT NULL DEFAULT '',
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    backoff_until TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tunnel_mechanical_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    base_url TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    password TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tunnel_mechanical_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    access_token TEXT NOT NULL DEFAULT '',
                    refresh_token TEXT NOT NULL DEFAULT '',
                    cookie_header TEXT NOT NULL DEFAULT '',
                    token_expires_at TEXT NOT NULL DEFAULT '',
                    last_login_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tunnel_mechanical_template (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    template_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(monitored_people)").fetchall()}
            if "mention_mobile" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN mention_mobile TEXT NOT NULL DEFAULT ''")
            if "rest_reminder_enabled" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN rest_reminder_enabled INTEGER NOT NULL DEFAULT 0")
            if "rest_reminder_time" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN rest_reminder_time TEXT NOT NULL DEFAULT '08:30'")
            if "rest_message_template" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN rest_message_template TEXT NOT NULL DEFAULT ''")
            if "notification_room_id" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN notification_room_id TEXT NOT NULL DEFAULT ''")
            if "notification_room_name" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN notification_room_name TEXT NOT NULL DEFAULT ''")
            if "send_content_mode" not in columns:
                conn.execute("ALTER TABLE monitored_people ADD COLUMN send_content_mode TEXT NOT NULL DEFAULT 'both'")
            config_columns = {row["name"] for row in conn.execute("PRAGMA table_info(notification_config)").fetchall()}
            if "sender_type" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN sender_type TEXT NOT NULL DEFAULT 'wecom_webhook'")
            if "message_template" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN message_template TEXT NOT NULL DEFAULT ''")
            if "wecom_aibot_enabled" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_aibot_enabled INTEGER NOT NULL DEFAULT 0")
            if "wecom_aibot_id" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_aibot_id TEXT NOT NULL DEFAULT ''")
            if "wecom_aibot_secret" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_aibot_secret TEXT NOT NULL DEFAULT ''")
            if "wecom_app_enabled" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_enabled INTEGER NOT NULL DEFAULT 0")
            if "wecom_app_corp_id" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_corp_id TEXT NOT NULL DEFAULT ''")
            if "wecom_app_agent_id" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_agent_id TEXT NOT NULL DEFAULT ''")
            if "wecom_app_secret" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_secret TEXT NOT NULL DEFAULT ''")
            if "wecom_app_token" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_token TEXT NOT NULL DEFAULT ''")
            if "wecom_app_encoding_aes_key" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_encoding_aes_key TEXT NOT NULL DEFAULT ''")
            if "wecom_app_target_names_json" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_target_names_json TEXT NOT NULL DEFAULT '[]'")
            if "wecom_app_function_target_names_json" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN wecom_app_function_target_names_json TEXT NOT NULL DEFAULT '{}'")
            if "lightagent_url" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN lightagent_url TEXT NOT NULL DEFAULT ''")
            if "lightagent_token" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN lightagent_token TEXT NOT NULL DEFAULT ''")
            if "lightagent_target" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN lightagent_target TEXT NOT NULL DEFAULT ''")
            if "lightagent_targets_json" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN lightagent_targets_json TEXT NOT NULL DEFAULT '[]'")
            if "mention_mode" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN mention_mode TEXT NOT NULL DEFAULT 'person'")
            if "mention_targets" not in config_columns:
                conn.execute("ALTER TABLE notification_config ADD COLUMN mention_targets TEXT NOT NULL DEFAULT ''")
            personnel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(personnel_names)").fetchall()}
            if "wecom_userid" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN wecom_userid TEXT NOT NULL DEFAULT ''")
            feature_columns = {row["name"] for row in conn.execute("PRAGMA table_info(feature_channel_config)").fetchall()}
            for column, definition in {
                "enabled": "INTEGER NOT NULL DEFAULT 1",
                "lightagent_web_url": "TEXT NOT NULL DEFAULT ''",
                "lightagent_web_password": "TEXT NOT NULL DEFAULT ''",
                "wechat_group_room_id": "TEXT NOT NULL DEFAULT ''",
                "wechat_group_room_name": "TEXT NOT NULL DEFAULT ''",
                "wechat_group_rooms_json": "TEXT NOT NULL DEFAULT '[]'",
                "allow_tunnel_mechanical": "INTEGER NOT NULL DEFAULT 1",
                "allow_duty_query": "INTEGER NOT NULL DEFAULT 1",
                "allow_roster_import": "INTEGER NOT NULL DEFAULT 1",
            }.items():
                if column not in feature_columns:
                    conn.execute(f"ALTER TABLE feature_channel_config ADD COLUMN {column} {definition}")
            personnel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(personnel_names)").fetchall()}
            if "mention_mobile" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN mention_mobile TEXT NOT NULL DEFAULT ''")
            if "wechat_group_room_id" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN wechat_group_room_id TEXT NOT NULL DEFAULT ''")
            if "wechat_group_room_name" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN wechat_group_room_name TEXT NOT NULL DEFAULT ''")
            if "wechat_group_member_id" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN wechat_group_member_id TEXT NOT NULL DEFAULT ''")
            if "wechat_group_runtime_sender_id" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN wechat_group_runtime_sender_id TEXT NOT NULL DEFAULT ''")
            if "wechat_group_member_name" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN wechat_group_member_name TEXT NOT NULL DEFAULT ''")
            if "tunnel_mechanical_partner" not in personnel_columns:
                conn.execute("ALTER TABLE personnel_names ADD COLUMN tunnel_mechanical_partner TEXT NOT NULL DEFAULT ''")
            custom_columns = {row["name"] for row in conn.execute("PRAGMA table_info(custom_reminders)").fetchall()}
            if "notification_room_id" not in custom_columns:
                conn.execute("ALTER TABLE custom_reminders ADD COLUMN notification_room_id TEXT NOT NULL DEFAULT ''")
            if "notification_room_name" not in custom_columns:
                conn.execute("ALTER TABLE custom_reminders ADD COLUMN notification_room_name TEXT NOT NULL DEFAULT ''")
            if "send_content_mode" not in custom_columns:
                conn.execute("ALTER TABLE custom_reminders ADD COLUMN send_content_mode TEXT NOT NULL DEFAULT 'both'")
            daily_columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_duty_config)").fetchall()}
            if "notification_room_id" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN notification_room_id TEXT NOT NULL DEFAULT ''")
            if "notification_room_name" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN notification_room_name TEXT NOT NULL DEFAULT ''")
            if "send_content_mode" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN send_content_mode TEXT NOT NULL DEFAULT 'both'")
            if "patrol_team_names_json" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN patrol_team_names_json TEXT NOT NULL DEFAULT '[]'")
            if "patrol_team_groups_json" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN patrol_team_groups_json TEXT NOT NULL DEFAULT '[]'")
            if "station_names_json" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN station_names_json TEXT NOT NULL DEFAULT '[]'")
            if "office_names_json" not in daily_columns:
                conn.execute("ALTER TABLE daily_duty_config ADD COLUMN office_names_json TEXT NOT NULL DEFAULT '[]'")
            vacation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(vacation_reminder_config)").fetchall()}
            if "start_message_templates_json" not in vacation_columns:
                conn.execute("ALTER TABLE vacation_reminder_config ADD COLUMN start_message_templates_json TEXT NOT NULL DEFAULT '[]'")
            if "end_message_templates_json" not in vacation_columns:
                conn.execute("ALTER TABLE vacation_reminder_config ADD COLUMN end_message_templates_json TEXT NOT NULL DEFAULT '[]'")
            patrol_state_columns = {row["name"] for row in conn.execute("PRAGMA table_info(patrol_warning_state)").fetchall()}
            if "token" not in patrol_state_columns:
                conn.execute("ALTER TABLE patrol_warning_state ADD COLUMN token TEXT NOT NULL DEFAULT ''")
            if "token_expires_at" not in patrol_state_columns:
                conn.execute("ALTER TABLE patrol_warning_state ADD COLUMN token_expires_at TEXT NOT NULL DEFAULT ''")
            if "next_check_at" not in patrol_state_columns:
                conn.execute("ALTER TABLE patrol_warning_state ADD COLUMN next_check_at TEXT NOT NULL DEFAULT ''")
            if "failure_count" not in patrol_state_columns:
                conn.execute("ALTER TABLE patrol_warning_state ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
            if "backoff_until" not in patrol_state_columns:
                conn.execute("ALTER TABLE patrol_warning_state ADD COLUMN backoff_until TEXT NOT NULL DEFAULT ''")
            if "last_error" not in patrol_state_columns:
                conn.execute("ALTER TABLE patrol_warning_state ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
            send_record_columns = {row["name"] for row in conn.execute("PRAGMA table_info(send_records)").fetchall()}
            if "notification_room_id" not in send_record_columns:
                conn.execute("ALTER TABLE send_records ADD COLUMN notification_room_id TEXT NOT NULL DEFAULT ''")
            if "notification_room_name" not in send_record_columns:
                conn.execute("ALTER TABLE send_records ADD COLUMN notification_room_name TEXT NOT NULL DEFAULT ''")
            patrol_config_columns = {row["name"] for row in conn.execute("PRAGMA table_info(patrol_warning_config)").fetchall()}
            if "mention_mobiles" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN mention_mobiles TEXT NOT NULL DEFAULT ''")
            if "end_reminder_enabled" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN end_reminder_enabled INTEGER NOT NULL DEFAULT 1")
            if "send_content_mode" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN send_content_mode TEXT NOT NULL DEFAULT 'both'")
            if "start_message_template" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN start_message_template TEXT NOT NULL DEFAULT ''")
            if "end_message_template" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN end_message_template TEXT NOT NULL DEFAULT ''")
            if "notification_room_id" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN notification_room_id TEXT NOT NULL DEFAULT ''")
            if "notification_room_name" not in patrol_config_columns:
                conn.execute("ALTER TABLE patrol_warning_config ADD COLUMN notification_room_name TEXT NOT NULL DEFAULT ''")
            tunnel_state_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tunnel_mechanical_state)").fetchall()}
            if "refresh_token" not in tunnel_state_columns:
                conn.execute("ALTER TABLE tunnel_mechanical_state ADD COLUMN refresh_token TEXT NOT NULL DEFAULT ''")
            if "cookie_header" not in tunnel_state_columns:
                conn.execute("ALTER TABLE tunnel_mechanical_state ADD COLUMN cookie_header TEXT NOT NULL DEFAULT ''")
            wechat_interaction_columns = {row["name"] for row in conn.execute("PRAGMA table_info(wechat_interaction_config)").fetchall()}
            if "tunnel_template" not in wechat_interaction_columns:
                conn.execute("ALTER TABLE wechat_interaction_config ADD COLUMN tunnel_template TEXT NOT NULL DEFAULT ''")
            if "tunnel_modify_template" not in wechat_interaction_columns:
                conn.execute("ALTER TABLE wechat_interaction_config ADD COLUMN tunnel_modify_template TEXT NOT NULL DEFAULT ''")

    def table_names(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {row["name"] for row in rows}

    def export_config_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            tables: dict[str, list[dict[str, Any]]] = {}
            for table in CONFIG_EXPORT_TABLES:
                if table not in self.table_names():
                    tables[table] = []
                    continue
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                tables[table] = [dict(row) for row in rows]
        return {
            "format": "duty-reminder-config",
            "version": 1,
            "tunnel_mechanical_template": self.get_tunnel_mechanical_template(),
            "tables": tables,
        }

    def import_config_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            raise ValueError("配置文件格式不正确")
        if snapshot.get("format") != "duty-reminder-config":
            raise ValueError("不是 duty-reminder 配置文件")
        if int(snapshot.get("version") or 0) != 1:
            raise ValueError("不支持的配置文件版本")
        tables = snapshot.get("tables")
        if not isinstance(tables, dict):
            raise ValueError("配置文件缺少 tables")
        imported: dict[str, int] = {}
        tunnel_template_snapshot = snapshot.get("tunnel_mechanical_template")
        if not isinstance(tunnel_template_snapshot, dict):
            tunnel_template_snapshot = None
        with self._connect() as conn:
            for table in CONFIG_EXPORT_TABLES:
                rows = tables.get(table, [])
                if table == "tunnel_mechanical_template" and tunnel_template_snapshot is not None:
                    rows = [{"id": 1, "template_json": json.dumps(tunnel_template_snapshot, ensure_ascii=False)}]
                if rows is None:
                    rows = []
                if not isinstance(rows, list):
                    raise ValueError(f"{table} 必须是数组")
                existing_columns = {
                    str(row["name"])
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                conn.execute(f"DELETE FROM {table}")
                count = 0
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    clean = {key: value for key, value in row.items() if key in existing_columns}
                    if not clean:
                        continue
                    if table == "custom_reminders" and "reminder_time" in clean:
                        clean["reminder_time"] = normalize_custom_reminder_time_for_import(
                            str(clean.get("shift_code") or ""),
                            str(clean.get("reminder_time") or ""),
                        )
                    columns = list(clean.keys())
                    placeholders = ",".join("?" for _ in columns)
                    names = ",".join(columns)
                    conn.execute(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                        [clean[column] for column in columns],
                    )
                    count += 1
                imported[table] = count
        return {"tables": imported}

    def save_roster_month(
        self,
        year: int,
        month: int,
        grid: list[dict[str, Any]],
        source_image_path: str,
    ) -> None:
        with self._connect() as conn:
            grid_json = json.dumps(grid, ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO roster_months (year, month, grid_json, source_image_path)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(year, month) DO UPDATE SET
                    grid_json = excluded.grid_json,
                    source_image_path = excluded.source_image_path,
                    confirmed_at = CURRENT_TIMESTAMP
                """,
                (year, month, grid_json, source_image_path),
            )
            conn.execute(
                """
                INSERT INTO roster_versions (year, month, grid_json, source_image_path)
                VALUES (?, ?, ?, ?)
                """,
                (year, month, grid_json, source_image_path),
            )
        self.upsert_personnel_names([str(row.get("name", "")).strip() for row in grid])

    def get_roster_month(self, year: int, month: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM roster_months WHERE year = ? AND month = ?",
                (year, month),
            ).fetchone()
        if row is None:
            return None
        return {
            "year": row["year"],
            "month": row["month"],
            "grid": json.loads(row["grid_json"]),
            "source_image_path": row["source_image_path"],
            "confirmed_at": row["confirmed_at"],
        }

    def list_roster_months(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM roster_months ORDER BY year, month").fetchall()
        return [
            {
                "year": row["year"],
                "month": row["month"],
                "grid": json.loads(row["grid_json"]),
                "source_image_path": row["source_image_path"],
                "confirmed_at": row["confirmed_at"],
            }
            for row in rows
        ]

    def list_roster_versions(self, year: int, month: int, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, year, month, grid_json, source_image_path, created_at
                FROM roster_versions
                WHERE year = ? AND month = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (year, month, max(1, min(int(limit), 100))),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "year": row["year"],
                "month": row["month"],
                "grid": json.loads(row["grid_json"]),
                "source_image_path": row["source_image_path"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_roster_version(self, version_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, year, month, grid_json, source_image_path, created_at
                FROM roster_versions
                WHERE id = ?
                """,
                (version_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "year": row["year"],
            "month": row["month"],
            "grid": json.loads(row["grid_json"]),
            "source_image_path": row["source_image_path"],
            "created_at": row["created_at"],
        }

    def count_roster_months(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM roster_months").fetchone()
        return int(row["count"])

    def count_monitored_people(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM monitored_people").fetchone()
        return int(row["count"])

    def upsert_personnel_names(self, names: list[str]) -> None:
        clean_names = sorted({name.strip() for name in names if name and name.strip()})
        with self._connect() as conn:
            for name in clean_names:
                conn.execute("DELETE FROM deleted_personnel WHERE name = ?", (name,))
                conn.execute(
                    """
                    INSERT INTO personnel_names (name) VALUES (?)
                    ON CONFLICT(name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """,
                    (name,),
                )

    def upsert_personnel_contacts(self, contacts: list[dict[str, Any]]) -> None:
        clean_contacts: dict[str, dict[str, str]] = {}
        for contact in contacts:
            name = str(contact.get("name") or "").strip()
            if not name:
                continue
            values = {
                "mention_mobile": str(contact.get("mention_mobile") or "").strip(),
                "wecom_userid": str(contact.get("wecom_userid") or "").strip(),
                "wechat_group_room_id": str(contact.get("wechat_group_room_id") or "").strip(),
                "wechat_group_room_name": str(contact.get("wechat_group_room_name") or "").strip(),
                "wechat_group_member_id": str(contact.get("wechat_group_member_id") or "").strip(),
                "wechat_group_runtime_sender_id": str(contact.get("wechat_group_runtime_sender_id") or "").strip(),
                "wechat_group_member_name": str(contact.get("wechat_group_member_name") or "").strip(),
                "tunnel_mechanical_partner": str(contact.get("tunnel_mechanical_partner") or "").strip(),
            }
            existing = clean_contacts.setdefault(
                name,
                {
                    "mention_mobile": "",
                    "wecom_userid": "",
                    "wechat_group_room_id": "",
                    "wechat_group_room_name": "",
                    "wechat_group_member_id": "",
                    "wechat_group_runtime_sender_id": "",
                    "wechat_group_member_name": "",
                    "tunnel_mechanical_partner": "",
                },
            )
            for key, value in values.items():
                if value:
                    existing[key] = value
        with self._connect() as conn:
            for name, values in sorted(clean_contacts.items()):
                conn.execute("DELETE FROM deleted_personnel WHERE name = ?", (name,))
                conn.execute(
                    """
                    INSERT INTO personnel_names (
                        name, mention_mobile, wecom_userid, wechat_group_room_id, wechat_group_room_name,
                        wechat_group_member_id, wechat_group_runtime_sender_id, wechat_group_member_name,
                        tunnel_mechanical_partner
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        mention_mobile = CASE
                            WHEN excluded.mention_mobile != '' THEN excluded.mention_mobile
                            ELSE personnel_names.mention_mobile
                        END,
                        wecom_userid = CASE
                            WHEN excluded.wecom_userid != '' THEN excluded.wecom_userid
                            ELSE personnel_names.wecom_userid
                        END,
                        wechat_group_room_id = CASE
                            WHEN excluded.wechat_group_room_id != '' THEN excluded.wechat_group_room_id
                            ELSE personnel_names.wechat_group_room_id
                        END,
                        wechat_group_room_name = CASE
                            WHEN excluded.wechat_group_room_name != '' THEN excluded.wechat_group_room_name
                            ELSE personnel_names.wechat_group_room_name
                        END,
                        wechat_group_member_id = CASE
                            WHEN excluded.wechat_group_member_id != '' THEN excluded.wechat_group_member_id
                            ELSE personnel_names.wechat_group_member_id
                        END,
                        wechat_group_runtime_sender_id = CASE
                            WHEN excluded.wechat_group_runtime_sender_id != '' THEN excluded.wechat_group_runtime_sender_id
                            ELSE personnel_names.wechat_group_runtime_sender_id
                        END,
                        wechat_group_member_name = CASE
                            WHEN excluded.wechat_group_member_name != '' THEN excluded.wechat_group_member_name
                            ELSE personnel_names.wechat_group_member_name
                        END,
                        tunnel_mechanical_partner = CASE
                            WHEN excluded.tunnel_mechanical_partner != '' THEN excluded.tunnel_mechanical_partner
                            ELSE personnel_names.tunnel_mechanical_partner
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        name,
                        values["mention_mobile"],
                        values["wecom_userid"],
                        values["wechat_group_room_id"],
                        values["wechat_group_room_name"],
                        values["wechat_group_member_id"],
                        values["wechat_group_runtime_sender_id"],
                        values["wechat_group_member_name"],
                        values["tunnel_mechanical_partner"],
                    ),
                )

    def save_personnel_names(self, names: list[str]) -> None:
        clean_names = sorted({name.strip() for name in names if name and name.strip()})
        with self._connect() as conn:
            if clean_names:
                placeholders = ",".join("?" for _ in clean_names)
                conn.execute(f"DELETE FROM personnel_names WHERE name NOT IN ({placeholders})", clean_names)
            else:
                conn.execute("DELETE FROM personnel_names")
            for name in clean_names:
                conn.execute("DELETE FROM deleted_personnel WHERE name = ?", (name,))
                conn.execute(
                    """
                    INSERT INTO personnel_names (name) VALUES (?)
                    ON CONFLICT(name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """,
                    (name,),
                )

    def list_deleted_personnel_names(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM deleted_personnel ORDER BY name").fetchall()
        return {row["name"] for row in rows}

    def delete_personnel(self, name: str) -> bool:
        clean_name = str(name or "").strip()
        if not clean_name:
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM personnel_names WHERE name = ?", (clean_name,))
            conn.execute("DELETE FROM monitored_people WHERE name = ?", (clean_name,))
            conn.execute("DELETE FROM custom_reminders WHERE name = ?", (clean_name,))
            conn.execute(
                """
                INSERT INTO deleted_personnel (name)
                VALUES (?)
                ON CONFLICT(name) DO UPDATE SET deleted_at = CURRENT_TIMESTAMP
                """,
                (clean_name,),
            )
        return True

    def rename_personnel(self, old_name: str, new_name: str) -> bool:
        clean_old = str(old_name or "").strip()
        clean_new = str(new_name or "").strip()
        if not clean_old or not clean_new:
            return False
        if clean_old == clean_new:
            return True
        with self._connect() as conn:
            conflict = conn.execute(
                """
                SELECT 1 FROM (
                    SELECT name FROM personnel_names
                    UNION ALL
                    SELECT name FROM monitored_people
                    UNION ALL
                    SELECT name FROM custom_reminders
                    UNION ALL
                    SELECT name FROM deleted_personnel
                ) WHERE name = ? LIMIT 1
                """,
                (clean_new,),
            ).fetchone()
            if conflict:
                raise ValueError("新姓名已存在")
            updated = 0
            for table in ("personnel_names", "monitored_people", "custom_reminders", "deleted_personnel"):
                updated += conn.execute(f"UPDATE {table} SET name = ? WHERE name = ?", (clean_new, clean_old)).rowcount
            for table in ("roster_months", "roster_versions"):
                rows = conn.execute(f"SELECT rowid AS rid, grid_json FROM {table}").fetchall()
                for row in rows:
                    try:
                        grid = json.loads(row["grid_json"] or "[]")
                    except Exception:
                        continue
                    changed = False
                    for item in grid:
                        if str(item.get("name") or "").strip() == clean_old:
                            item["name"] = clean_new
                            changed = True
                    if changed:
                        conn.execute(
                            f"UPDATE {table} SET grid_json = ? WHERE rowid = ?",
                            (json.dumps(grid, ensure_ascii=False), row["rid"]),
                        )
                        updated += 1
        return updated > 0

    def save_personnel_contacts(self, contacts: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            for contact in contacts:
                name = str(contact.get("name") or "").strip()
                if not name:
                    continue
                conn.execute("DELETE FROM deleted_personnel WHERE name = ?", (name,))
                conn.execute(
                    """
                    INSERT INTO personnel_names (
                        name, mention_mobile, wecom_userid, wechat_group_room_id, wechat_group_room_name,
                        wechat_group_member_id, wechat_group_runtime_sender_id, wechat_group_member_name,
                        tunnel_mechanical_partner
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        mention_mobile = excluded.mention_mobile,
                        wecom_userid = excluded.wecom_userid,
                        wechat_group_room_id = excluded.wechat_group_room_id,
                        wechat_group_room_name = excluded.wechat_group_room_name,
                        wechat_group_member_id = excluded.wechat_group_member_id,
                        wechat_group_runtime_sender_id = excluded.wechat_group_runtime_sender_id,
                        wechat_group_member_name = excluded.wechat_group_member_name,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        name,
                        str(contact.get("mention_mobile") or "").strip(),
                        str(contact.get("wecom_userid") or "").strip(),
                        str(contact.get("wechat_group_room_id") or "").strip(),
                        str(contact.get("wechat_group_room_name") or "").strip(),
                        str(contact.get("wechat_group_member_id") or "").strip(),
                        str(contact.get("wechat_group_runtime_sender_id") or "").strip(),
                        str(contact.get("wechat_group_member_name") or "").strip(),
                        str(contact.get("tunnel_mechanical_partner") or "").strip(),
                    ),
                )

    def clear_wechat_binding_for_member(self, member_ids: list[str], *, except_name: str = "") -> None:
        ids = sorted({str(member_id or "").strip() for member_id in member_ids if str(member_id or "").strip()})
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        params: list[str] = [*ids, *ids]
        where_name = ""
        if except_name.strip():
            where_name = " AND name != ?"
            params.append(except_name.strip())
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE personnel_names
                SET wechat_group_room_id = '',
                    wechat_group_room_name = '',
                    wechat_group_member_id = '',
                    wechat_group_runtime_sender_id = '',
                    wechat_group_member_name = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE (
                    wechat_group_member_id IN ({placeholders})
                    OR wechat_group_runtime_sender_id IN ({placeholders})
                ){where_name}
                """,
                params,
            )

    def clear_wecom_binding_for_userid(self, userid: str, *, except_name: str = "") -> None:
        clean = str(userid or "").strip()
        if not clean:
            return
        params = [clean]
        where_name = ""
        if except_name.strip():
            where_name = " AND name != ?"
            params.append(except_name.strip())
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE personnel_names
                SET wecom_userid = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE wecom_userid = ?{where_name}
                """,
                params,
            )

    def list_personnel_names(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM personnel_names
                WHERE name NOT IN (SELECT name FROM deleted_personnel)
                ORDER BY name
                """
            ).fetchall()
        return [row["name"] for row in rows]

    def list_personnel(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    name, mention_mobile, wecom_userid, wechat_group_room_id, wechat_group_room_name,
                    wechat_group_member_id, wechat_group_runtime_sender_id, wechat_group_member_name,
                    tunnel_mechanical_partner
                FROM personnel_names
                WHERE name NOT IN (SELECT name FROM deleted_personnel)
                ORDER BY name
                """
            ).fetchall()
        people = []
        for row in rows:
            item = {"name": row["name"], "mention_mobile": row["mention_mobile"]}
            if str(row["wecom_userid"] or "").strip():
                item["wecom_userid"] = row["wecom_userid"]
            wechat_fields = {
                "wechat_group_room_id": row["wechat_group_room_id"],
                "wechat_group_room_name": row["wechat_group_room_name"],
                "wechat_group_member_id": row["wechat_group_member_id"],
                "wechat_group_runtime_sender_id": row["wechat_group_runtime_sender_id"],
                "wechat_group_member_name": row["wechat_group_member_name"],
            }
            if any(str(value or "").strip() for value in wechat_fields.values()):
                item.update(wechat_fields)
            if str(row["tunnel_mechanical_partner"] or "").strip():
                item["tunnel_mechanical_partner"] = row["tunnel_mechanical_partner"]
            people.append(item)
        return people

    def set_tunnel_mechanical_partner(self, name: str, partner_name: str) -> None:
        clean_name = str(name or "").strip()
        clean_partner = str(partner_name or "").strip()
        if not clean_name:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM deleted_personnel WHERE name = ?", (clean_name,))
            conn.execute(
                """
                INSERT INTO personnel_names (name, tunnel_mechanical_partner) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    tunnel_mechanical_partner = excluded.tunnel_mechanical_partner,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (clean_name, clean_partner),
            )

    def save_monitored_person(
        self,
        *,
        name: str,
        original_name: str = "",
        wecom_userid: str = "",
        mention_text: str = "",
        mention_mobile: str = "",
        wechat_group_room_id: str = "",
        wechat_group_room_name: str = "",
        wechat_group_member_id: str = "",
        wechat_group_runtime_sender_id: str = "",
        wechat_group_member_name: str = "",
        daily_time: str = "07:50",
        before_shift_minutes: int = 10,
        rest_reminder_enabled: bool = False,
        rest_reminder_time: str = "08:30",
        rest_message_template: str = DEFAULT_REST_MESSAGE_TEMPLATE,
        notification_room_id: str = "",
        notification_room_name: str = "",
        send_content_mode: str = "both",
        enabled: bool = True,
    ) -> None:
        clean_name = name.strip()
        clean_original_name = original_name.strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monitored_people
                    (
                        name, wecom_userid, mention_text, mention_mobile, daily_time, before_shift_minutes,
                        rest_reminder_enabled, rest_reminder_time, rest_message_template,
                        notification_room_id, notification_room_name, send_content_mode, enabled
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    wecom_userid = excluded.wecom_userid,
                    mention_text = excluded.mention_text,
                    mention_mobile = excluded.mention_mobile,
                    daily_time = excluded.daily_time,
                    before_shift_minutes = excluded.before_shift_minutes,
                    rest_reminder_enabled = excluded.rest_reminder_enabled,
                    rest_reminder_time = excluded.rest_reminder_time,
                    rest_message_template = excluded.rest_message_template,
                    notification_room_id = excluded.notification_room_id,
                    notification_room_name = excluded.notification_room_name,
                    send_content_mode = excluded.send_content_mode,
                    enabled = excluded.enabled
                """,
                (
                    clean_name,
                    wecom_userid,
                    mention_text,
                    mention_mobile,
                    daily_time,
                    before_shift_minutes,
                    int(rest_reminder_enabled),
                    rest_reminder_time or "08:30",
                    _normalize_rest_message_template(rest_message_template),
                    str(notification_room_id or "").strip(),
                    str(notification_room_name or "").strip(),
                    _normalize_send_content_mode(send_content_mode, "both"),
                    int(enabled),
                ),
            )
            if clean_original_name and clean_original_name != clean_name:
                conn.execute("DELETE FROM monitored_people WHERE name = ?", (clean_original_name,))
        self.upsert_personnel_contacts(
            [
                {
                    "name": clean_name,
                    "mention_mobile": mention_mobile,
                    "wecom_userid": wecom_userid,
                    "wechat_group_room_id": wechat_group_room_id,
                    "wechat_group_room_name": wechat_group_room_name,
                    "wechat_group_member_id": wechat_group_member_id,
                    "wechat_group_runtime_sender_id": wechat_group_runtime_sender_id,
                    "wechat_group_member_name": wechat_group_member_name,
                }
            ]
        )

    def delete_monitored_person(self, name: str) -> bool:
        clean_name = name.strip()
        if not clean_name:
            return False
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM monitored_people WHERE name = ?", (clean_name,))
        return cursor.rowcount > 0

    def list_monitored_people(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT
                monitored_people.*,
                personnel_names.wecom_userid AS contact_wecom_userid,
                personnel_names.wechat_group_room_id AS contact_wechat_group_room_id,
                personnel_names.wechat_group_room_name AS contact_wechat_group_room_name,
                personnel_names.wechat_group_member_id AS contact_wechat_group_member_id,
                personnel_names.wechat_group_runtime_sender_id AS contact_wechat_group_runtime_sender_id,
                personnel_names.wechat_group_member_name AS contact_wechat_group_member_name
            FROM monitored_people
            LEFT JOIN personnel_names ON personnel_names.name = monitored_people.name
        """
        if enabled_only:
            query += " WHERE monitored_people.enabled = 1"
        query += " ORDER BY monitored_people.name"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        people = []
        for row in rows:
            item = {
                "name": row["name"],
                "wecom_userid": str(row["wecom_userid"] or "").strip() or str(row["contact_wecom_userid"] or "").strip(),
                "mention_text": row["mention_text"],
                "mention_mobile": row["mention_mobile"],
                "daily_time": row["daily_time"],
                "before_shift_minutes": row["before_shift_minutes"],
                "rest_reminder_enabled": bool(row["rest_reminder_enabled"]),
                "rest_reminder_time": row["rest_reminder_time"],
                "rest_message_template": _normalize_rest_message_template(row["rest_message_template"]),
                "notification_room_id": row["notification_room_id"],
                "notification_room_name": row["notification_room_name"],
                "send_content_mode": _normalize_send_content_mode(row["send_content_mode"], "both"),
                "enabled": bool(row["enabled"]),
            }
            wechat_fields = {
                "wechat_group_room_id": row["contact_wechat_group_room_id"],
                "wechat_group_room_name": row["contact_wechat_group_room_name"],
                "wechat_group_member_id": row["contact_wechat_group_member_id"],
                "wechat_group_runtime_sender_id": row["contact_wechat_group_runtime_sender_id"],
                "wechat_group_member_name": row["contact_wechat_group_member_name"],
            }
            if any(str(value or "").strip() for value in wechat_fields.values()):
                item.update(wechat_fields)
            people.append(item)
        return people

    def save_custom_reminder(
        self,
        *,
        name: str,
        shift_code: str,
        reminder_time: str,
        message: str,
        mention_mobile: str = "",
        wechat_group_room_id: str = "",
        wechat_group_room_name: str = "",
        wechat_group_member_id: str = "",
        wechat_group_runtime_sender_id: str = "",
        wechat_group_member_name: str = "",
        notification_room_id: str = "",
        notification_room_name: str = "",
        send_content_mode: str = "both",
        enabled: bool = True,
        id: int | None = None,
    ) -> int:
        clean_name = name.strip()
        clean_mobile = mention_mobile.strip()
        with self._connect() as conn:
            if id is not None:
                cursor = conn.execute(
                    """
                    UPDATE custom_reminders
                    SET name = ?,
                        mention_mobile = ?,
                        shift_code = ?,
                        reminder_time = ?,
                        message = ?,
                        notification_room_id = ?,
                        notification_room_name = ?,
                        send_content_mode = ?,
                        enabled = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (clean_name, clean_mobile, shift_code, reminder_time, message, str(notification_room_id or "").strip(), str(notification_room_name or "").strip(), _normalize_send_content_mode(send_content_mode, "both"), int(enabled), int(id)),
                )
                if cursor.rowcount > 0:
                    reminder_id = int(id)
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO custom_reminders
                            (name, mention_mobile, shift_code, reminder_time, message, notification_room_id, notification_room_name, send_content_mode, enabled)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (clean_name, clean_mobile, shift_code, reminder_time, message, str(notification_room_id or "").strip(), str(notification_room_name or "").strip(), _normalize_send_content_mode(send_content_mode, "both"), int(enabled)),
                    )
                    reminder_id = int(cursor.lastrowid)
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO custom_reminders
                        (name, mention_mobile, shift_code, reminder_time, message, notification_room_id, notification_room_name, send_content_mode, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (clean_name, clean_mobile, shift_code, reminder_time, message, str(notification_room_id or "").strip(), str(notification_room_name or "").strip(), _normalize_send_content_mode(send_content_mode, "both"), int(enabled)),
                )
                reminder_id = int(cursor.lastrowid)
        self.upsert_personnel_contacts(
            [
                {
                    "name": clean_name,
                    "mention_mobile": clean_mobile,
                    "wechat_group_room_id": str(wechat_group_room_id or "").strip(),
                    "wechat_group_room_name": str(wechat_group_room_name or "").strip(),
                    "wechat_group_member_id": str(wechat_group_member_id or "").strip(),
                    "wechat_group_runtime_sender_id": str(wechat_group_runtime_sender_id or "").strip(),
                    "wechat_group_member_name": str(wechat_group_member_name or "").strip(),
                }
            ]
        )
        return reminder_id

    def delete_custom_reminder(self, reminder_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM custom_reminders WHERE id = ?", (int(reminder_id),))
        return cursor.rowcount > 0

    def list_custom_reminders(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = """
            SELECT
                custom_reminders.*,
                personnel_names.wecom_userid,
                personnel_names.wechat_group_room_id,
                personnel_names.wechat_group_room_name,
                personnel_names.wechat_group_member_id,
                personnel_names.wechat_group_runtime_sender_id,
                personnel_names.wechat_group_member_name
            FROM custom_reminders
            LEFT JOIN personnel_names ON personnel_names.name = custom_reminders.name
        """
        if enabled_only:
            query += " WHERE custom_reminders.enabled = 1"
        query += " ORDER BY custom_reminders.name, custom_reminders.shift_code, custom_reminders.reminder_time, custom_reminders.id"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        reminders = []
        for row in rows:
            item = {
                "id": row["id"],
                "name": row["name"],
                "mention_mobile": row["mention_mobile"],
                "shift_code": row["shift_code"],
                "reminder_time": row["reminder_time"],
                "message": row["message"],
                "notification_room_id": row["notification_room_id"],
                "notification_room_name": row["notification_room_name"],
                "send_content_mode": _normalize_send_content_mode(row["send_content_mode"], "both"),
                "enabled": bool(row["enabled"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            wecom_userid = str(row["wecom_userid"] or "").strip()
            if wecom_userid:
                item["wecom_userid"] = wecom_userid
            wechat_fields = {
                "wechat_group_room_id": row["wechat_group_room_id"],
                "wechat_group_room_name": row["wechat_group_room_name"],
                "wechat_group_member_id": row["wechat_group_member_id"],
                "wechat_group_runtime_sender_id": row["wechat_group_runtime_sender_id"],
                "wechat_group_member_name": row["wechat_group_member_name"],
            }
            if any(str(value or "").strip() for value in wechat_fields.values()):
                item.update(wechat_fields)
            reminders.append(item)
        return reminders

    def save_notification_config(
        self,
        *,
        webhook_url: str,
        wecom_aibot_enabled: bool = False,
        wecom_aibot_id: str = "",
        wecom_aibot_secret: str = "",
        wecom_app_enabled: bool = False,
        wecom_app_corp_id: str = "",
        wecom_app_agent_id: str = "",
        wecom_app_secret: str = "",
        wecom_app_token: str = "",
        wecom_app_encoding_aes_key: str = "",
        wecom_app_target_names: list[str] | None = None,
        wecom_app_function_target_names: dict[str, Any] | None = None,
        message_template: str = DEFAULT_MESSAGE_TEMPLATE,
        sender_type: str = "wecom_webhook",
        lightagent_url: str = "",
        lightagent_token: str = "",
        lightagent_target: str = "",
        lightagent_targets: list[dict[str, str]] | None = None,
        mention_mode: str = "person",
        mention_targets: str = "",
    ) -> None:
        targets = _normalize_feature_channel_rooms(lightagent_targets or [])
        if not targets and str(lightagent_target or "").strip():
            targets = _normalize_feature_channel_rooms([{"id": lightagent_target}])
        primary_target = targets[0]["id"] if targets else str(lightagent_target or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_config
                    (id, sender_type, webhook_url, wecom_aibot_enabled, wecom_aibot_id, wecom_aibot_secret, wecom_app_enabled, wecom_app_corp_id, wecom_app_agent_id, wecom_app_secret, wecom_app_token, wecom_app_encoding_aes_key, wecom_app_target_names_json, wecom_app_function_target_names_json, lightagent_url, lightagent_token, lightagent_target, lightagent_targets_json, mention_mode, mention_targets, message_template)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sender_type = excluded.sender_type,
                    webhook_url = excluded.webhook_url,
                    wecom_aibot_enabled = excluded.wecom_aibot_enabled,
                    wecom_aibot_id = excluded.wecom_aibot_id,
                    wecom_aibot_secret = excluded.wecom_aibot_secret,
                    wecom_app_enabled = excluded.wecom_app_enabled,
                    wecom_app_corp_id = excluded.wecom_app_corp_id,
                    wecom_app_agent_id = excluded.wecom_app_agent_id,
                    wecom_app_secret = excluded.wecom_app_secret,
                    wecom_app_token = excluded.wecom_app_token,
                    wecom_app_encoding_aes_key = excluded.wecom_app_encoding_aes_key,
                    wecom_app_target_names_json = excluded.wecom_app_target_names_json,
                    wecom_app_function_target_names_json = excluded.wecom_app_function_target_names_json,
                    lightagent_url = excluded.lightagent_url,
                    lightagent_token = excluded.lightagent_token,
                    lightagent_target = excluded.lightagent_target,
                    lightagent_targets_json = excluded.lightagent_targets_json,
                    mention_mode = excluded.mention_mode,
                    mention_targets = excluded.mention_targets,
                    message_template = excluded.message_template,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    _normalize_notification_sender_type(sender_type),
                    str(webhook_url or "").strip(),
                    int(bool(wecom_aibot_enabled)),
                    str(wecom_aibot_id or "").strip(),
                    str(wecom_aibot_secret or "").strip(),
                    int(bool(wecom_app_enabled)),
                    str(wecom_app_corp_id or "").strip(),
                    str(wecom_app_agent_id or "").strip(),
                    str(wecom_app_secret or "").strip(),
                    str(wecom_app_token or "").strip(),
                    str(wecom_app_encoding_aes_key or "").strip(),
                    json.dumps(_normalize_name_list(wecom_app_target_names or []), ensure_ascii=False),
                    json.dumps(_normalize_function_target_names(wecom_app_function_target_names or {}), ensure_ascii=False),
                    lightagent_url,
                    lightagent_token,
                    primary_target,
                    json.dumps(targets, ensure_ascii=False),
                    _normalize_notification_mention_mode(mention_mode),
                    mention_targets.strip(),
                    message_template or DEFAULT_MESSAGE_TEMPLATE,
                ),
            )

    def get_notification_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT sender_type, webhook_url, wecom_aibot_enabled, wecom_aibot_id, wecom_aibot_secret, wecom_app_enabled, wecom_app_corp_id, wecom_app_agent_id, wecom_app_secret, wecom_app_token, wecom_app_encoding_aes_key, wecom_app_target_names_json, wecom_app_function_target_names_json, lightagent_url, lightagent_token, lightagent_target, lightagent_targets_json, mention_mode, mention_targets, message_template
                FROM notification_config
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return {
                "sender_type": "wecom_webhook",
                "webhook_url": "",
                "wecom_aibot_enabled": False,
                "wecom_aibot_id": "",
                "wecom_aibot_secret": "",
                "wecom_app_enabled": False,
                "wecom_app_corp_id": "",
                "wecom_app_agent_id": "",
                "wecom_app_secret": "",
                "wecom_app_token": "",
                "wecom_app_encoding_aes_key": "",
                "wecom_app_target_names": [],
                "wecom_app_function_target_names": {},
                "lightagent_url": "",
                "lightagent_token": "",
                "lightagent_target": "",
                "lightagent_targets": [],
                "mention_mode": "person",
                "mention_targets": "",
                "message_template": DEFAULT_MESSAGE_TEMPLATE,
            }
        targets = _normalize_feature_channel_rooms(_loads_json(row["lightagent_targets_json"], []))
        if not targets and str(row["lightagent_target"] or "").strip():
            targets = _normalize_feature_channel_rooms([{"id": row["lightagent_target"]}])
        return {
            "sender_type": _normalize_notification_sender_type(row["sender_type"]),
            "webhook_url": row["webhook_url"],
            "wecom_aibot_enabled": bool(row["wecom_aibot_enabled"]),
            "wecom_aibot_id": row["wecom_aibot_id"],
            "wecom_aibot_secret": row["wecom_aibot_secret"],
            "wecom_app_enabled": bool(row["wecom_app_enabled"]),
            "wecom_app_corp_id": row["wecom_app_corp_id"],
            "wecom_app_agent_id": row["wecom_app_agent_id"],
            "wecom_app_secret": row["wecom_app_secret"],
            "wecom_app_token": row["wecom_app_token"],
            "wecom_app_encoding_aes_key": row["wecom_app_encoding_aes_key"],
            "wecom_app_target_names": _normalize_name_list(_loads_json(row["wecom_app_target_names_json"], [])),
            "wecom_app_function_target_names": _normalize_function_target_names(_loads_json(row["wecom_app_function_target_names_json"], {})),
            "lightagent_url": row["lightagent_url"],
            "lightagent_token": row["lightagent_token"],
            "lightagent_target": row["lightagent_target"],
            "lightagent_targets": targets,
            "mention_mode": _normalize_notification_mention_mode(row["mention_mode"]),
            "mention_targets": row["mention_targets"],
            "message_template": row["message_template"] or DEFAULT_MESSAGE_TEMPLATE,
        }

    def save_feature_channel_config(
        self,
        *,
        enabled: bool = True,
        lightagent_web_url: str = "",
        lightagent_web_password: str = "",
        wechat_group_room_id: str = "",
        wechat_group_room_name: str = "",
        wechat_group_rooms: list[dict[str, Any]] | None = None,
        allow_tunnel_mechanical: bool = True,
        allow_duty_query: bool = True,
        allow_roster_import: bool = True,
    ) -> None:
        rooms = _normalize_feature_channel_rooms(wechat_group_rooms)
        if not rooms and str(wechat_group_room_id or "").strip():
            rooms = _normalize_feature_channel_rooms([
                {
                    "id": wechat_group_room_id,
                    "name": wechat_group_room_name,
                }
            ])
        primary_room = rooms[0] if rooms else {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feature_channel_config
                    (
                        id, enabled, lightagent_web_url, lightagent_web_password,
                        wechat_group_room_id, wechat_group_room_name, wechat_group_rooms_json,
                        allow_tunnel_mechanical, allow_duty_query, allow_roster_import
                    )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    lightagent_web_url = excluded.lightagent_web_url,
                    lightagent_web_password = excluded.lightagent_web_password,
                    wechat_group_room_id = excluded.wechat_group_room_id,
                    wechat_group_room_name = excluded.wechat_group_room_name,
                    wechat_group_rooms_json = excluded.wechat_group_rooms_json,
                    allow_tunnel_mechanical = excluded.allow_tunnel_mechanical,
                    allow_duty_query = excluded.allow_duty_query,
                    allow_roster_import = excluded.allow_roster_import,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(enabled),
                    lightagent_web_url,
                    lightagent_web_password,
                    str(primary_room.get("id") or ""),
                    str(primary_room.get("name") or ""),
                    json.dumps(rooms, ensure_ascii=False),
                    int(allow_tunnel_mechanical),
                    int(allow_duty_query),
                    int(allow_roster_import),
                ),
            )

    def get_feature_channel_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT enabled, lightagent_web_url, lightagent_web_password,
                       wechat_group_room_id, wechat_group_room_name, wechat_group_rooms_json,
                       allow_tunnel_mechanical, allow_duty_query, allow_roster_import
                FROM feature_channel_config
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return {
                "enabled": True,
                "lightagent_web_url": "",
                "lightagent_web_password": "",
                "wechat_group_room_id": "",
                "wechat_group_room_name": "",
                "wechat_group_rooms": [],
                "allow_tunnel_mechanical": True,
                "allow_duty_query": True,
                "allow_roster_import": True,
            }
        rooms = _normalize_feature_channel_rooms(_loads_json(row["wechat_group_rooms_json"], []))
        if not rooms and str(row["wechat_group_room_id"] or "").strip():
            rooms = _normalize_feature_channel_rooms([
                {
                    "id": row["wechat_group_room_id"],
                    "name": row["wechat_group_room_name"],
                }
            ])
        return {
            "enabled": bool(row["enabled"]),
            "lightagent_web_url": row["lightagent_web_url"],
            "lightagent_web_password": row["lightagent_web_password"],
            "wechat_group_room_id": row["wechat_group_room_id"],
            "wechat_group_room_name": row["wechat_group_room_name"],
            "wechat_group_rooms": rooms,
            "allow_tunnel_mechanical": bool(row["allow_tunnel_mechanical"]),
            "allow_duty_query": bool(row["allow_duty_query"]),
            "allow_roster_import": bool(row["allow_roster_import"]),
        }

    def save_wechat_interaction_config(
        self,
        *,
        patrol_record_triggers: list[str],
        patrol_record_template: str,
        tunnel_template_triggers: list[str],
        tunnel_template: str,
        tunnel_modify_template_triggers: list[str],
        tunnel_modify_template: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wechat_interaction_config
                    (
                        id, patrol_record_triggers_json, patrol_record_template,
                        tunnel_template_triggers_json, tunnel_template,
                        tunnel_modify_template_triggers_json, tunnel_modify_template
                    )
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    patrol_record_triggers_json = excluded.patrol_record_triggers_json,
                    patrol_record_template = excluded.patrol_record_template,
                    tunnel_template_triggers_json = excluded.tunnel_template_triggers_json,
                    tunnel_template = excluded.tunnel_template,
                    tunnel_modify_template_triggers_json = excluded.tunnel_modify_template_triggers_json,
                    tunnel_modify_template = excluded.tunnel_modify_template,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    json.dumps([str(item).strip() for item in patrol_record_triggers if str(item).strip()], ensure_ascii=False),
                    str(patrol_record_template or "").strip(),
                    json.dumps([str(item).strip() for item in tunnel_template_triggers if str(item).strip()], ensure_ascii=False),
                    str(tunnel_template or "").strip(),
                    json.dumps([str(item).strip() for item in tunnel_modify_template_triggers if str(item).strip()], ensure_ascii=False),
                    str(tunnel_modify_template or "").strip(),
                ),
            )

    def get_wechat_interaction_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT patrol_record_triggers_json, patrol_record_template,
                       tunnel_template_triggers_json, tunnel_template,
                       tunnel_modify_template_triggers_json, tunnel_modify_template
                FROM wechat_interaction_config
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return {
                "patrol_record_triggers": ["巡查记录", "查询巡查记录", "查巡查记录", "巡查记录查询"],
                "patrol_record_template": "",
                "tunnel_template_triggers": ["模板"],
                "tunnel_template": "",
                "tunnel_modify_template_triggers": ["修改", "修改模板", "改模板"],
                "tunnel_modify_template": "",
            }
        return {
            "patrol_record_triggers": _loads_json(row["patrol_record_triggers_json"], ["巡查记录", "查询巡查记录", "查巡查记录", "巡查记录查询"]),
            "patrol_record_template": row["patrol_record_template"] or "",
            "tunnel_template_triggers": _loads_json(row["tunnel_template_triggers_json"], ["模板"]),
            "tunnel_template": row["tunnel_template"] or "",
            "tunnel_modify_template_triggers": _loads_json(row["tunnel_modify_template_triggers_json"], ["修改", "修改模板", "改模板"]),
            "tunnel_modify_template": row["tunnel_modify_template"] or "",
        }

    def save_wecom_app_menu_config(self, menu: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wecom_app_menu_config (id, menu_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    menu_json = excluded.menu_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (json.dumps(menu, ensure_ascii=False),),
            )

    def get_wecom_app_menu_config(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT menu_json
                FROM wecom_app_menu_config
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return []
        menu = _loads_json(row["menu_json"], [])
        return menu if isinstance(menu, list) else []

    def list_construction_sites(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, created_at, updated_at
                FROM construction_sites
                ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def add_construction_site(self, name: str) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("施工点不能为空")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO construction_sites (name) VALUES (?)
                ON CONFLICT(name) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """,
                (clean_name,),
            )
            row = conn.execute(
                "SELECT id, name, created_at, updated_at FROM construction_sites WHERE name = ?",
                (clean_name,),
            ).fetchone()
        return dict(row)

    def delete_construction_site(self, site_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM construction_sites WHERE id = ?", (int(site_id),))
            return cur.rowcount > 0

    def update_construction_site(self, site_id: int, name: str) -> dict[str, Any] | None:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("施工点不能为空")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE construction_sites
                SET name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (clean_name, int(site_id)),
            )
            row = conn.execute(
                "SELECT id, name, created_at, updated_at FROM construction_sites WHERE id = ?",
                (int(site_id),),
            ).fetchone()
        return dict(row) if row else None

    def save_wechat_interaction_log(
        self,
        *,
        room_id: str = "",
        room_name: str = "",
        sender_id: str = "",
        sender_name: str = "",
        command_text: str = "",
        query_type: str = "",
        status: str = "",
        reply_text: str = "",
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wechat_interaction_logs (
                    room_id, room_name, sender_id, sender_name,
                    command_text, query_type, status, reply_text, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(room_id or "").strip(),
                    str(room_name or "").strip(),
                    str(sender_id or "").strip(),
                    str(sender_name or "").strip(),
                    str(command_text or "").strip(),
                    str(query_type or "").strip(),
                    str(status or "").strip(),
                    str(reply_text or "").strip(),
                    str(error or "").strip(),
                ),
            )

    def list_wechat_interaction_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, room_id, room_name, sender_id, sender_name, command_text,
                       query_type, status, reply_text, error, created_at
                FROM wechat_interaction_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "room_id": row["room_id"],
                "room_name": row["room_name"],
                "sender_id": row["sender_id"],
                "sender_name": row["sender_name"],
                "command_text": row["command_text"],
                "query_type": row["query_type"],
                "status": row["status"],
                "reply_text": row["reply_text"],
                "error": row["error"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_daily_duty_config(
        self,
        *,
        enabled: bool = True,
        reminder_time: str = "07:50",
        big_driver_names: list[str] | None = None,
        small_driver_names: list[str] | None = None,
        patrol_team_names: list[str] | None = None,
        patrol_team_groups: list[dict[str, Any]] | None = None,
        station_names: list[str] | None = None,
        office_names: list[str] | None = None,
        message_template: str = DEFAULT_DAILY_DUTY_TEMPLATE,
        notification_room_id: str = "",
        notification_room_name: str = "",
        send_content_mode: str = "both",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_duty_config
                    (id, enabled, reminder_time, big_driver_names_json, small_driver_names_json, patrol_team_names_json, patrol_team_groups_json, station_names_json, office_names_json, message_template, notification_room_id, notification_room_name, send_content_mode)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    reminder_time = excluded.reminder_time,
                    big_driver_names_json = excluded.big_driver_names_json,
                    small_driver_names_json = excluded.small_driver_names_json,
                    patrol_team_names_json = excluded.patrol_team_names_json,
                    patrol_team_groups_json = excluded.patrol_team_groups_json,
                    station_names_json = excluded.station_names_json,
                    office_names_json = excluded.office_names_json,
                    message_template = excluded.message_template,
                    notification_room_id = excluded.notification_room_id,
                    notification_room_name = excluded.notification_room_name,
                    send_content_mode = excluded.send_content_mode,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(enabled),
                    reminder_time or "07:50",
                    json.dumps(_clean_name_list(big_driver_names), ensure_ascii=False),
                    json.dumps(_clean_name_list(small_driver_names), ensure_ascii=False),
                    json.dumps(_flatten_patrol_team_groups(patrol_team_groups, patrol_team_names), ensure_ascii=False),
                    json.dumps(_normalize_patrol_team_groups(patrol_team_groups, patrol_team_names), ensure_ascii=False),
                    json.dumps(_clean_name_list(station_names), ensure_ascii=False),
                    json.dumps(_clean_name_list(office_names), ensure_ascii=False),
                    _normalize_daily_duty_template(message_template),
                    str(notification_room_id or "").strip(),
                    str(notification_room_name or "").strip(),
                    _normalize_send_content_mode(send_content_mode, "both"),
                ),
            )

    def get_daily_duty_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM daily_duty_config WHERE id = 1").fetchone()
        if row is None:
            return {
                "enabled": True,
                "reminder_time": "07:50",
                "big_driver_names": [],
                "small_driver_names": [],
                "patrol_team_names": [],
                "patrol_team_groups": _normalize_patrol_team_groups(None, []),
                "station_names": [],
                "office_names": [],
                "message_template": DEFAULT_DAILY_DUTY_TEMPLATE,
                "notification_room_id": "",
                "notification_room_name": "",
                "send_content_mode": "both",
            }
        return {
            "enabled": bool(row["enabled"]),
            "reminder_time": row["reminder_time"],
            "big_driver_names": json.loads(row["big_driver_names_json"] or "[]"),
            "small_driver_names": json.loads(row["small_driver_names_json"] or "[]"),
            "patrol_team_names": json.loads(row["patrol_team_names_json"] or "[]"),
            "patrol_team_groups": _normalize_patrol_team_groups(
                json.loads(row["patrol_team_groups_json"] or "[]"),
                json.loads(row["patrol_team_names_json"] or "[]"),
            ),
            "station_names": json.loads(row["station_names_json"] or "[]"),
            "office_names": json.loads(row["office_names_json"] or "[]"),
            "message_template": _normalize_daily_duty_template(row["message_template"]),
            "notification_room_id": row["notification_room_id"],
            "notification_room_name": row["notification_room_name"],
            "send_content_mode": _normalize_send_content_mode(row["send_content_mode"], "both"),
        }

    def save_vacation_reminder_config(
        self,
        *,
        enabled: bool = True,
        start_reminder_time: str = "07:50",
        end_reminder_time: str = "07:50",
        start_message_template: str = DEFAULT_VACATION_START_TEMPLATE,
        end_message_template: str = DEFAULT_VACATION_END_TEMPLATE,
        start_message_templates: list[str] | None = None,
        end_message_templates: list[str] | None = None,
        send_content_mode: str = "both",
    ) -> None:
        start_templates = _normalize_template_list(
            start_message_templates,
            [str(start_message_template or "").strip() or DEFAULT_VACATION_START_TEMPLATE],
        )
        end_templates = _normalize_template_list(
            end_message_templates,
            [str(end_message_template or "").strip() or DEFAULT_VACATION_END_TEMPLATE],
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vacation_reminder_config
                    (id, enabled, start_reminder_time, end_reminder_time, start_message_template, end_message_template, start_message_templates_json, end_message_templates_json, send_content_mode)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    start_reminder_time = excluded.start_reminder_time,
                    end_reminder_time = excluded.end_reminder_time,
                    start_message_template = excluded.start_message_template,
                    end_message_template = excluded.end_message_template,
                    start_message_templates_json = excluded.start_message_templates_json,
                    end_message_templates_json = excluded.end_message_templates_json,
                    send_content_mode = excluded.send_content_mode,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(enabled),
                    start_reminder_time or "07:50",
                    end_reminder_time or "07:50",
                    start_templates[0],
                    end_templates[0],
                    json.dumps(start_templates, ensure_ascii=False),
                    json.dumps(end_templates, ensure_ascii=False),
                    _normalize_send_content_mode(send_content_mode, "both"),
                ),
            )

    def get_vacation_reminder_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM vacation_reminder_config WHERE id = 1").fetchone()
        if row is None:
            return {
                "enabled": True,
                "start_reminder_time": "07:50",
                "end_reminder_time": "07:50",
                "start_message_template": DEFAULT_VACATION_START_TEMPLATE,
                "end_message_template": DEFAULT_VACATION_END_TEMPLATE,
                "start_message_templates": list(DEFAULT_VACATION_START_TEMPLATES),
                "end_message_templates": list(DEFAULT_VACATION_END_TEMPLATES),
                "send_content_mode": "both",
            }
        start_templates = _normalize_template_list(
            _loads_json(row["start_message_templates_json"], []),
            DEFAULT_VACATION_START_TEMPLATES
            if not str(row["start_message_template"] or "").strip() or row["start_message_template"] == DEFAULT_VACATION_START_TEMPLATE
            else [row["start_message_template"]],
        )
        end_templates = _normalize_template_list(
            _loads_json(row["end_message_templates_json"], []),
            DEFAULT_VACATION_END_TEMPLATES
            if not str(row["end_message_template"] or "").strip() or row["end_message_template"] == DEFAULT_VACATION_END_TEMPLATE
            else [row["end_message_template"]],
        )
        return {
            "enabled": bool(row["enabled"]),
            "start_reminder_time": row["start_reminder_time"],
            "end_reminder_time": row["end_reminder_time"],
            "start_message_template": start_templates[0],
            "end_message_template": end_templates[0],
            "start_message_templates": start_templates,
            "end_message_templates": end_templates,
            "send_content_mode": _normalize_send_content_mode(row["send_content_mode"], "both"),
        }

    def save_patrol_warning_config(
        self,
        *,
        enabled: bool = False,
        login_url: str = "",
        warning_url: str = "",
        username: str = "",
        password: str = "",
        project_id: str = "",
        platform: str = "2",
        route_code: str = "",
        poll_interval_minutes: int = 10,
        rows: int = 5000,
        end_reminder_enabled: bool = True,
        end_reminder_interval_hours: int = 6,
        end_reminder_window_hours: int = 48,
        mention_all: bool = True,
        mention_mobiles: str = "",
        send_content_mode: str = "both",
        start_message_template: str = DEFAULT_PATROL_WARNING_START_TEMPLATE,
        end_message_template: str = DEFAULT_PATROL_WARNING_END_TEMPLATE,
        notification_room_id: str = "",
        notification_room_name: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO patrol_warning_config
                    (
                        id, enabled, login_url, warning_url, username, password, project_id, platform,
                        route_code, poll_interval_minutes, rows, end_reminder_enabled, end_reminder_interval_hours,
                        end_reminder_window_hours, mention_all, mention_mobiles,
                        send_content_mode, start_message_template, end_message_template,
                        notification_room_id, notification_room_name
                    )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    login_url = excluded.login_url,
                    warning_url = excluded.warning_url,
                    username = excluded.username,
                    password = excluded.password,
                    project_id = excluded.project_id,
                    platform = excluded.platform,
                    route_code = excluded.route_code,
                    poll_interval_minutes = excluded.poll_interval_minutes,
                    rows = excluded.rows,
                    end_reminder_enabled = excluded.end_reminder_enabled,
                    end_reminder_interval_hours = excluded.end_reminder_interval_hours,
                    end_reminder_window_hours = excluded.end_reminder_window_hours,
                    mention_all = excluded.mention_all,
                    mention_mobiles = excluded.mention_mobiles,
                    send_content_mode = excluded.send_content_mode,
                    start_message_template = excluded.start_message_template,
                    end_message_template = excluded.end_message_template,
                    notification_room_id = excluded.notification_room_id,
                    notification_room_name = excluded.notification_room_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(enabled),
                    login_url.strip(),
                    warning_url.strip(),
                    username.strip(),
                    password,
                    project_id.strip(),
                    str(platform or "2").strip() or "2",
                    route_code.strip(),
                    max(1, min(int(poll_interval_minutes), 1440)),
                    max(1, min(int(rows), 10000)),
                    int(end_reminder_enabled),
                    max(1, min(int(end_reminder_interval_hours), 168)),
                    max(1, min(int(end_reminder_window_hours), 720)),
                    int(mention_all),
                    mention_mobiles.strip(),
                    _normalize_patrol_send_content_mode(send_content_mode),
                    start_message_template.strip() or DEFAULT_PATROL_WARNING_START_TEMPLATE,
                    _normalize_patrol_end_template(end_message_template),
                    str(notification_room_id or "").strip(),
                    str(notification_room_name or "").strip(),
                ),
            )

    def get_patrol_warning_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM patrol_warning_config WHERE id = 1").fetchone()
        if row is None:
            return {
                "enabled": False,
                "login_url": "",
                "warning_url": "",
                "username": "",
                "password": "",
                "project_id": "",
                "platform": "2",
                "route_code": "",
                "poll_interval_minutes": 10,
                "rows": 5000,
                "end_reminder_enabled": True,
                "end_reminder_interval_hours": 6,
                "end_reminder_window_hours": 48,
                "mention_all": True,
                "mention_mobiles": "",
                "send_content_mode": "both",
                "start_message_template": DEFAULT_PATROL_WARNING_START_TEMPLATE,
                "end_message_template": DEFAULT_PATROL_WARNING_END_TEMPLATE,
                "notification_room_id": "",
                "notification_room_name": "",
            }
        return {
            "enabled": bool(row["enabled"]),
            "login_url": row["login_url"],
            "warning_url": row["warning_url"],
            "username": row["username"],
            "password": row["password"],
            "project_id": row["project_id"],
            "platform": row["platform"],
            "route_code": row["route_code"],
            "poll_interval_minutes": int(row["poll_interval_minutes"]),
            "rows": int(row["rows"]),
            "end_reminder_enabled": bool(row["end_reminder_enabled"]),
            "end_reminder_interval_hours": int(row["end_reminder_interval_hours"]),
            "end_reminder_window_hours": int(row["end_reminder_window_hours"]),
            "mention_all": bool(row["mention_all"]),
            "mention_mobiles": row["mention_mobiles"],
            "send_content_mode": _normalize_patrol_send_content_mode(row["send_content_mode"]),
            "start_message_template": row["start_message_template"] or DEFAULT_PATROL_WARNING_START_TEMPLATE,
            "end_message_template": _normalize_patrol_end_template(row["end_message_template"]),
            "notification_room_id": row["notification_room_id"],
            "notification_room_name": row["notification_room_name"],
        }

    def get_patrol_warning_state(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM patrol_warning_state WHERE id = 1").fetchone()
        if row is None:
            return {
                "warning_key": "",
                "warning": {},
                "last_checked_at": "",
                "last_start_sent_key": "",
                "last_end_reminder_slot": "",
                "token": "",
                "token_expires_at": "",
                "next_check_at": "",
                "failure_count": 0,
                "backoff_until": "",
                "last_error": "",
            }
        try:
            warning = json.loads(row["warning_json"] or "{}")
        except json.JSONDecodeError:
            warning = {}
        return {
            "warning_key": row["warning_key"],
            "warning": warning,
            "last_checked_at": row["last_checked_at"],
            "last_start_sent_key": row["last_start_sent_key"],
            "last_end_reminder_slot": row["last_end_reminder_slot"],
            "token": row["token"],
            "token_expires_at": row["token_expires_at"],
            "next_check_at": row["next_check_at"],
            "failure_count": int(row["failure_count"]),
            "backoff_until": row["backoff_until"],
            "last_error": row["last_error"],
        }

    def save_patrol_warning_state(
        self,
        *,
        warning_key: str | None = None,
        warning: dict[str, Any] | None = None,
        last_checked_at: str | None = None,
        last_start_sent_key: str | None = None,
        last_end_reminder_slot: str | None = None,
        token: str | None = None,
        token_expires_at: str | None = None,
        next_check_at: str | None = None,
        failure_count: int | None = None,
        backoff_until: str | None = None,
        last_error: str | None = None,
    ) -> None:
        current = self.get_patrol_warning_state()
        next_warning_key = current["warning_key"] if warning_key is None else warning_key
        next_warning = current["warning"] if warning is None else warning
        next_last_checked_at = current["last_checked_at"] if last_checked_at is None else last_checked_at
        next_last_start_sent_key = current["last_start_sent_key"] if last_start_sent_key is None else last_start_sent_key
        next_last_end_reminder_slot = (
            current["last_end_reminder_slot"] if last_end_reminder_slot is None else last_end_reminder_slot
        )
        next_token = current["token"] if token is None else token
        next_token_expires_at = current["token_expires_at"] if token_expires_at is None else token_expires_at
        next_next_check_at = current["next_check_at"] if next_check_at is None else next_check_at
        next_failure_count = current["failure_count"] if failure_count is None else int(failure_count)
        next_backoff_until = current["backoff_until"] if backoff_until is None else backoff_until
        next_last_error = current["last_error"] if last_error is None else last_error
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO patrol_warning_state
                    (
                        id, warning_key, warning_json, last_checked_at, last_start_sent_key,
                        last_end_reminder_slot, token, token_expires_at, next_check_at,
                        failure_count, backoff_until, last_error
                    )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    warning_key = excluded.warning_key,
                    warning_json = excluded.warning_json,
                    last_checked_at = excluded.last_checked_at,
                    last_start_sent_key = excluded.last_start_sent_key,
                    last_end_reminder_slot = excluded.last_end_reminder_slot,
                    token = excluded.token,
                    token_expires_at = excluded.token_expires_at,
                    next_check_at = excluded.next_check_at,
                    failure_count = excluded.failure_count,
                    backoff_until = excluded.backoff_until,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    next_warning_key,
                    json.dumps(next_warning, ensure_ascii=False),
                    next_last_checked_at,
                    next_last_start_sent_key,
                    next_last_end_reminder_slot,
                    next_token,
                    next_token_expires_at,
                    next_next_check_at,
                    max(0, next_failure_count),
                    next_backoff_until,
                    next_last_error,
                ),
            )

    def save_tunnel_mechanical_config(
        self,
        *,
        base_url: str = "",
        username: str = "",
        password: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tunnel_mechanical_config (id, base_url, username, password)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    base_url = excluded.base_url,
                    username = excluded.username,
                    password = excluded.password,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    str(base_url or "").strip(),
                    username.strip(),
                    password,
                ),
            )

    def get_tunnel_mechanical_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tunnel_mechanical_config WHERE id = 1").fetchone()
        if row is None:
            return {
                "base_url": "",
                "username": "",
                "password": "",
            }
        return {
            "base_url": row["base_url"],
            "username": row["username"],
            "password": row["password"],
        }

    def save_tunnel_mechanical_state(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        cookie_header: str | None = None,
        token_expires_at: str | None = None,
        last_login_at: str | None = None,
        last_error: str | None = None,
    ) -> None:
        current = self.get_tunnel_mechanical_state()
        next_access_token = current["access_token"] if access_token is None else access_token
        next_refresh_token = current["refresh_token"] if refresh_token is None else refresh_token
        next_cookie_header = current["cookie_header"] if cookie_header is None else cookie_header
        next_token_expires_at = current["token_expires_at"] if token_expires_at is None else token_expires_at
        next_last_login_at = current["last_login_at"] if last_login_at is None else last_login_at
        next_last_error = current["last_error"] if last_error is None else last_error
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tunnel_mechanical_state
                    (id, access_token, refresh_token, cookie_header, token_expires_at, last_login_at, last_error)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    cookie_header = excluded.cookie_header,
                    token_expires_at = excluded.token_expires_at,
                    last_login_at = excluded.last_login_at,
                    last_error = excluded.last_error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    next_access_token,
                    next_refresh_token,
                    next_cookie_header,
                    next_token_expires_at,
                    next_last_login_at,
                    next_last_error,
                ),
            )

    def get_tunnel_mechanical_state(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tunnel_mechanical_state WHERE id = 1").fetchone()
        if row is None:
            return {
                "access_token": "",
                "refresh_token": "",
                "cookie_header": "",
                "token_expires_at": "",
                "last_login_at": "",
                "last_error": "",
            }
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
            "cookie_header": row["cookie_header"],
            "token_expires_at": row["token_expires_at"],
            "last_login_at": row["last_login_at"],
            "last_error": row["last_error"],
        }

    def save_tunnel_mechanical_template(self, template: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tunnel_mechanical_template (id, template_json)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    template_json = excluded.template_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (json.dumps(template, ensure_ascii=False),),
            )

    def get_tunnel_mechanical_template(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tunnel_mechanical_template WHERE id = 1").fetchone()
        if row is None:
            return {}
        try:
            template = json.loads(row["template_json"] or "{}")
        except json.JSONDecodeError:
            return {}
        return template if isinstance(template, dict) else {}

    def mark_sent_once(self, reminder_key: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("INSERT INTO sent_reminders (reminder_key) VALUES (?)", (reminder_key,))
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_sent_once(self, reminder_key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sent_reminders WHERE reminder_key = ?", (reminder_key,))
        return cursor.rowcount > 0

    def save_send_record(
        self,
        *,
        kind: str,
        target: str,
        status: str,
        scheduled_at: str = "",
        content: str = "",
        error: str = "",
        notification_room_id: str = "",
        notification_room_name: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO send_records (kind, target, scheduled_at, status, content, error, notification_room_id, notification_room_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, target, scheduled_at, status, content, error, str(notification_room_id or "").strip(), str(notification_room_name or "").strip()),
            )

    def list_send_records(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, target, scheduled_at, status, content, error, notification_room_id, notification_room_name, created_at
                FROM send_records
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "target": row["target"],
                "scheduled_at": row["scheduled_at"],
                "status": row["status"],
                "content": row["content"],
                "error": row["error"],
                "notification_room_id": row["notification_room_id"],
                "notification_room_name": row["notification_room_name"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_send_record(self, record_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, kind, target, scheduled_at, status, content, error, notification_room_id, notification_room_name, created_at
                FROM send_records
                WHERE id = ?
                """,
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "target": row["target"],
            "scheduled_at": row["scheduled_at"],
            "status": row["status"],
            "content": row["content"],
            "error": row["error"],
            "notification_room_id": row["notification_room_id"],
            "notification_room_name": row["notification_room_name"],
            "created_at": row["created_at"],
        }

    def list_send_records_since(self, start_text: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, target, scheduled_at, status, content, error, notification_room_id, notification_room_name, created_at
                FROM send_records
                WHERE created_at >= ?
                ORDER BY id DESC
                """,
                (start_text,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "target": row["target"],
                "scheduled_at": row["scheduled_at"],
                "status": row["status"],
                "content": row["content"],
                "error": row["error"],
                "notification_room_id": row["notification_room_id"],
                "notification_room_name": row["notification_room_name"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def _normalize_rest_message_template(value: str | None) -> str:
    template = (value or "").strip()
    if not template or template in {LEGACY_REST_MESSAGE_TEMPLATE, LEGACY_TOMORROW_REST_MESSAGE_TEMPLATE}:
        return DEFAULT_REST_MESSAGE_TEMPLATE
    return template


def _normalize_daily_duty_template(value: str | None) -> str:
    template = (value or "").strip()
    legacy_with_resting = LEGACY_DAILY_DUTY_TEMPLATE + "\n今日休息人员：{resting}"
    legacy_with_rest_statuses = (
        LEGACY_DAILY_DUTY_TEMPLATE + "\n"
        "今日下午休息：{afternoon_rest}\n"
        "正在休息到：{resting_until}\n"
        "今日下午到岗：{afternoon_return}"
    )
    legacy_with_current_rest_statuses = (
        LEGACY_DAILY_DUTY_TEMPLATE + "\n"
        "今日下午休息：{afternoon_rest}\n"
        "正在休息：{resting}\n"
        "今日下午到岗：{afternoon_return}"
    )
    if not template or template in {LEGACY_DAILY_DUTY_TEMPLATE, legacy_with_resting, legacy_with_rest_statuses, legacy_with_current_rest_statuses}:
        return DEFAULT_DAILY_DUTY_TEMPLATE
    return template
