from __future__ import annotations

import asyncio
import base64
import calendar
import copy
import hashlib
import hmac
import html as html_lib
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from app.custom_reminders import custom_reminder_time_window_text, is_custom_reminder_time_allowed
from app.construction_docx import (
    DEFAULT_CONSTRUCTION_LOCATION,
    build_construction_image_docx,
    construction_docx_contains_location,
)
from app.daily_duty_image import has_cjk_font, render_daily_duty_image
from app.http_client import tunnel_async_httpx_client
from app.ocr import extract_roster_image, extract_template_roster_image, recheck_template_roster_cells
from app.patrol_warning import (
    PatrolWarningError,
    build_end_reminder_message,
    build_start_message,
    due_end_reminder_slot,
    fetch_latest_warning,
    fetch_latest_warning_result,
    fetch_patrol_records_by_name_result,
    failure_backoff_until,
    next_poll_time,
    warning_from_dict,
)
from app.patrol_warning_image import render_patrol_warning_image
from app.patrol_record_image import render_patrol_record_image
from app.reminders import DEFAULT_MESSAGE_TEMPLATE, ReminderEvent, ReminderSettings, plan_reminders_for_day
from app.roster import Shift, ShiftAssignment, normalize_shift_code
from app.roster_import_image import render_roster_import_image
from app.shift_reminder_image import render_shift_reminder_image
from app.storage import (
    DEFAULT_DAILY_DUTY_TEMPLATE,
    DEFAULT_PATROL_WARNING_END_TEMPLATE,
    DEFAULT_PATROL_WARNING_START_TEMPLATE,
    DEFAULT_REST_MESSAGE_TEMPLATE,
    DEFAULT_VACATION_END_TEMPLATE,
    DEFAULT_VACATION_END_TEMPLATES,
    DEFAULT_VACATION_START_TEMPLATE,
    DEFAULT_VACATION_START_TEMPLATES,
    DutyRepository,
)
from app.tunnel_mechanical_image import render_tunnel_mechanical_preview_image, render_tunnel_mechanical_result_image
from app.wecom import LightAgentNotifyClient, WeComAppNotifyClient, WeComClient, WeComError, WeComWebhookClient
from app.wecom_app import WeComAppCrypto, WeComAppCryptoError, encrypted_text_from_xml, parse_wecom_app_message
from app.wecom_aibot import WeComAiBotManager
from app.wechat_query_image import render_wechat_query_image
from app.wechat_bridge.manager import get_wechat_bridge_manager, wechat_bridge_enabled
from app.wechat_bridge.notify import WechatBridgeNotifyClient


TZ = ZoneInfo(os.getenv("TZ", "Asia/Shanghai"))
REMINDER_SEND_GRACE = timedelta(minutes=1)
LOGGER = logging.getLogger(__name__)
HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SESSION_COOKIE_NAME = "duty_session"
SESSION_DURATION_SECONDS = 12 * 60 * 60
REMEMBER_SESSION_SECONDS = 30 * 24 * 60 * 60
ALLOWED_UPLOAD_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALLOWED_UPLOAD_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
UPLOAD_KEEP_DAYS = int(os.getenv("UPLOAD_KEEP_DAYS", "90"))
GENERATED_UPLOAD_KEEP_DAYS = int(os.getenv("GENERATED_UPLOAD_KEEP_DAYS", "1"))
TUNNEL_MECHANICAL_KEEPALIVE_ENABLED = os.getenv("TUNNEL_MECHANICAL_KEEPALIVE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES = max(5, int(os.getenv("TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES", "30") or 30))
TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES = max(5, int(os.getenv("TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES", "30") or 30))
DEFAULT_PATROL_RECORD_TRIGGERS = ["巡查记录", "查询巡查记录", "查巡查记录", "巡查记录查询"]
LEGACY_PATROL_RECORD_TEMPLATE = "查询张三巡查记录 2026-07-01至2026-07-31"
DEFAULT_PATROL_RECORD_TEMPLATE = "查询商邱宏巡查记录 2026-07-01至2026-07-31"
DEFAULT_TUNNEL_TEMPLATE_TRIGGERS = ["模板"]
DEFAULT_TUNNEL_MODIFY_TEMPLATE_TRIGGERS = ["修改", "修改模板", "改模板"]
LEGACY_TUNNEL_TEMPLATE = "隧道机电录入 日期{date} 负责人罗富耀 记录人张三 天气晴"
LEGACY_TUNNEL_MODIFY_TEMPLATE = "隧道机电修改 日期{date} 负责人罗富耀 记录人张三 天气晴 修改日期为{date}"
DEFAULT_TUNNEL_TEMPLATE = "隧道机电录入 日期{date} 负责人罗富耀 记录人商邱宏 天气晴"
DEFAULT_TUNNEL_MODIFY_TEMPLATE = "隧道机电修改 日期{date} 负责人罗富耀 记录人商邱宏 天气晴 修改日期为{date}"
DEFAULT_WECHAT_INTERACTION_CONFIG = {
    "patrol_record_triggers": DEFAULT_PATROL_RECORD_TRIGGERS,
    "patrol_record_template": DEFAULT_PATROL_RECORD_TEMPLATE,
    "tunnel_template_triggers": DEFAULT_TUNNEL_TEMPLATE_TRIGGERS,
    "tunnel_template": DEFAULT_TUNNEL_TEMPLATE,
    "tunnel_modify_template_triggers": DEFAULT_TUNNEL_MODIFY_TEMPLATE_TRIGGERS,
    "tunnel_modify_template": DEFAULT_TUNNEL_MODIFY_TEMPLATE,
}
WECOM_APP_MENU_LIMITS = {
    "max_top_buttons": 3,
    "max_sub_buttons": 5,
    "max_top_name_bytes": 16,
    "max_sub_name_bytes": 40,
}
WECOM_APP_MENU_COMMANDS = {
    # Backward-compatible keys created by older versions.
    "DR_TODAY_DUTY": "查询今日在岗",
    "DR_TODAY_MONITOR": "查询今日监控",
    "DR_MY_MONITOR": "查询我的监控",
    "DR_TOMORROW_MONITOR": "查询明日监控",
    "DR_WEEK_MONITOR": "查询本周监控",
    "DR_TUNNEL_TEMPLATE": "模板",
    "DR_TUNNEL_MODIFY_TEMPLATE": "修改模板",
    "DR_ORANGE_PATROL_RECORD": "橙色预警巡查记录查询",
    "DR_TUNNEL_TODAY_SUBMIT": "录入今日机电",
    "DR_TUNNEL_TODAY_QUERY": "查询今日机电",
    "DR_ROSTER_IMPORT": "导入排班",
    "DR_CONSTRUCTION_IMAGE": "施工图片",
    "DR_CONSTRUCTION_SITE_MANAGE": "施工点维护",
    "DR_NEXT_7_DAYS": "查询未来7天",
    "DR_BINDING": "查询我的绑定",
    "DR_HELP": "菜单",
}
WECOM_APP_MENU_COMMAND_KEYS = {command: key for key, command in WECOM_APP_MENU_COMMANDS.items()}
WECOM_APP_LEGACY_INDEX_MENU_COMMANDS = {
    # Older generated menus used index-based keys. Keep them valid even after
    # reordering built-in menu items, otherwise already-created WeCom menus can
    # silently map to the wrong command until the user recreates the menu.
    "DR_MENU_0_0": "查询今日在岗",
    "DR_MENU_0_1": "查询今日监控",
    "DR_MENU_0_2": "查询明日监控",
    "DR_MENU_0_3": "查询本周监控",
    "DR_MENU_0_4": "查询我的监控",
    "DR_MENU_1_0": "模板",
    "DR_MENU_1_1": "修改模板",
    "DR_MENU_1_2": "橙色预警巡查记录查询",
    "DR_MENU_1_3": "录入今日机电",
    "DR_MENU_2_0": "查询今日机电",
    "DR_MENU_2_1": "查询未来7天",
    "DR_MENU_2_2": "查询休息",
    "DR_MENU_2_3": "查询我的绑定",
    "DR_MENU_2_4": "导入排班",
}
DEFAULT_WECOM_APP_MENU_GROUPS = [
    {
        "name": "监控在岗",
        "items": [
            {"name": "今日在岗", "command": "查询今日在岗"},
            {"name": "今日监控", "command": "查询今日监控"},
            {"name": "明日监控", "command": "查询明日监控"},
            {"name": "本周监控", "command": "查询本周监控"},
            {"name": "我的监控", "command": "查询我的监控"},
        ],
    },
    {
        "name": "机电预警",
        "items": [
            {"name": "录入今日机电", "command": "录入今日机电"},
            {"name": "今日机电", "command": "查询今日机电"},
            {"name": "机电模板", "command": "模板"},
            {"name": "修改模板", "command": "修改模板"},
            {"name": "橙色预警巡查记录查询", "command": "橙色预警巡查记录查询"},
        ],
    },
    {
        "name": "更多查询",
        "items": [
            {"name": "施工图片", "command": "施工图片"},
            {"name": "施工点维护", "command": "施工点维护"},
            {"name": "未来7天", "command": "查询未来7天"},
            {"name": "查询休息", "command": "查询休息"},
            {"name": "导入排班", "command": "导入排班"},
        ],
    },
]


# ponytail: in-memory pending confirmation; restart only requires the user to click the menu again.
WECOM_APP_PENDING_TUNNEL_SUBMISSIONS: dict[str, dict[str, Any]] = {}
WECOM_APP_PENDING_TUNNEL_TTL_SECONDS = 30 * 60
WECOM_APP_PENDING_ROSTER_IMPORTS: dict[str, dict[str, Any]] = {}
WECOM_APP_PENDING_ROSTER_IMAGE_REQUESTS: dict[str, dict[str, Any]] = {}
WECOM_APP_PENDING_ROSTER_TTL_SECONDS = 5 * 60
WECOM_APP_PENDING_CONSTRUCTION_IMAGES: dict[str, dict[str, Any]] = {}
WECOM_APP_PENDING_CONSTRUCTION_SITES: dict[str, dict[str, Any]] = {}
WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS = 30 * 60
WECOM_APP_SHARED_PENDING_KEY = "__shared__"
WECHAT_QUERY_PENDING_MENUS: dict[str, float] = {}
WECHAT_QUERY_MENU_TTL_SECONDS = 5 * 60


class RosterConfirmRequest(BaseModel):
    year: int
    month: int
    source_image_path: str = ""
    grid: list[dict[str, Any]]
    overwrite: bool = False


class RosterRecheckRequest(BaseModel):
    source_image_path: str
    grid: list[dict[str, Any]]
    baseline_grid: list[dict[str, Any]] = Field(default_factory=list)
    year: int | None = None
    month: int | None = None


class MonitoredPersonRequest(BaseModel):
    name: str
    original_name: str = ""
    wecom_userid: str = ""
    mention_text: str = ""
    mention_mobile: str = ""
    wechat_group_room_id: str = ""
    wechat_group_room_name: str = ""
    wechat_group_member_id: str = ""
    wechat_group_runtime_sender_id: str = ""
    wechat_group_member_name: str = ""
    notification_room_id: str = ""
    notification_room_name: str = ""
    daily_time: str = "07:50"
    before_shift_minutes: int = Field(default=10, ge=0, le=1440)
    rest_reminder_enabled: bool = False
    rest_reminder_time: str = "08:30"
    rest_message_template: str = DEFAULT_REST_MESSAGE_TEMPLATE
    enabled: bool = True
    send_content_mode: str = "both"

    @field_validator("daily_time", "rest_reminder_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _validate_hhmm(value)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("姓名不能为空")
        return text


class LightAgentTargetRequest(BaseModel):
    id: str = ""
    name: str = ""


class NotificationConfigRequest(BaseModel):
    sender_type: str = ""
    webhook_url: str = ""
    wecom_aibot_enabled: bool = False
    wecom_aibot_id: str = ""
    wecom_aibot_secret: str = ""
    wecom_app_enabled: bool = False
    wecom_app_corp_id: str = ""
    wecom_app_agent_id: str = ""
    wecom_app_secret: str = ""
    wecom_app_token: str = ""
    wecom_app_encoding_aes_key: str = ""
    wecom_app_target_names: list[str] = Field(default_factory=list)
    wecom_app_function_target_names: dict[str, list[str]] = Field(default_factory=dict)
    lightagent_url: str = ""
    lightagent_token: str = ""
    lightagent_target: str = ""
    lightagent_targets: list[LightAgentTargetRequest] = Field(default_factory=list)
    mention_mode: str = "person"
    mention_targets: str = ""
    message_template: str = DEFAULT_MESSAGE_TEMPLATE


class NotificationTestRequest(BaseModel):
    person_name: str = "示例甲"


class ReminderImagePreviewRequest(BaseModel):
    preview_type: str = "monitor"
    name: str = "商邱宏"
    shift_code: str = "middle"
    reminder_time: str = "07:50"
    message: str = ""
    daily_time: str = "07:50"
    before_shift_minutes: int = Field(default=10, ge=0, le=1440)
    message_template: str = DEFAULT_MESSAGE_TEMPLATE
    send_content_mode: str = "both"


class FeatureChannelRoomRequest(BaseModel):
    id: str = ""
    name: str = ""


class FeatureChannelConfigRequest(BaseModel):
    enabled: bool = True
    lightagent_web_url: str = ""
    lightagent_web_password: str = ""
    wechat_group_room_id: str = ""
    wechat_group_room_name: str = ""
    wechat_group_rooms: list[FeatureChannelRoomRequest] = Field(default_factory=list)
    allow_tunnel_mechanical: bool = True
    allow_duty_query: bool = True
    allow_roster_import: bool = True


class WechatInteractionConfigRequest(BaseModel):
    patrol_record_triggers: list[str] = Field(default_factory=list)
    patrol_record_template: str = ""
    tunnel_template_triggers: list[str] = Field(default_factory=list)
    tunnel_template: str = ""
    tunnel_modify_template_triggers: list[str] = Field(default_factory=list)
    tunnel_modify_template: str = ""


class WechatInteractionTestRequest(BaseModel):
    text: str = ""
    channel: str = ""
    room_id: str = ""
    stable_room_id: str = ""
    room_name: str = ""
    sender_id: str = ""
    runtime_sender_id: str = ""
    stable_member_id: str = ""
    sender_name: str = ""
    target_date: date | None = None


class WeComAppMenuItemRequest(BaseModel):
    name: str = ""
    command: str = ""


class WeComAppMenuGroupRequest(BaseModel):
    name: str = ""
    items: list[WeComAppMenuItemRequest] = Field(default_factory=list)


class WeComAppMenuConfigRequest(BaseModel):
    groups: list[WeComAppMenuGroupRequest] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    target_date: date | None = None


class ConstructionSiteRequest(BaseModel):
    name: str


class PersonnelContactRequest(BaseModel):
    name: str
    mention_mobile: str = ""
    wecom_userid: str = ""
    wechat_group_room_id: str = ""
    wechat_group_room_name: str = ""
    wechat_group_member_id: str = ""
    wechat_group_runtime_sender_id: str = ""
    wechat_group_member_name: str = ""
    tunnel_mechanical_partner: str = ""


class PersonnelRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    people: list[PersonnelContactRequest] = Field(default_factory=list)


class PersonnelRenameRequest(BaseModel):
    name: str


class CustomReminderRequest(BaseModel):
    id: int | None = None
    name: str
    mention_mobile: str = ""
    wechat_group_room_id: str = ""
    wechat_group_room_name: str = ""
    wechat_group_member_id: str = ""
    wechat_group_runtime_sender_id: str = ""
    wechat_group_member_name: str = ""
    notification_room_id: str = ""
    notification_room_name: str = ""
    shift_code: str
    reminder_time: str
    message: str
    enabled: bool = True
    send_content_mode: str = "both"

    @field_validator("name", "message")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @field_validator("shift_code")
    @classmethod
    def validate_shift_code(cls, value: str) -> str:
        text = value.strip()
        allowed = {shift.value for shift in Shift}
        if text not in allowed:
            raise ValueError("班次必须是 early、middle 或 night")
        return text

    @field_validator("reminder_time")
    @classmethod
    def validate_reminder_time(cls, value: str) -> str:
        return _validate_hhmm(value)

    @model_validator(mode="after")
    def validate_reminder_time_matches_shift(self):
        if not is_custom_reminder_time_allowed(self.shift_code, self.reminder_time):
            raise ValueError(custom_reminder_time_window_text(self.shift_code))
        return self


class CustomReminderTestRequest(CustomReminderRequest):
    id: int | None = None


class DailyDutyConfigRequest(BaseModel):
    enabled: bool = True
    reminder_time: str = "07:50"
    big_driver_names: list[str] = []
    small_driver_names: list[str] = []
    patrol_team_names: list[str] = []
    patrol_team_groups: list[dict[str, Any]] = []
    station_names: list[str] = []
    office_names: list[str] = []
    message_template: str = DEFAULT_DAILY_DUTY_TEMPLATE
    notification_room_id: str = ""
    notification_room_name: str = ""
    send_content_mode: str = "both"

    @field_validator("reminder_time")
    @classmethod
    def validate_reminder_time(cls, value: str) -> str:
        return _validate_hhmm(value)


class VacationReminderConfigRequest(BaseModel):
    enabled: bool = True
    start_reminder_time: str = "07:50"
    end_reminder_time: str = "07:50"
    start_message_template: str = DEFAULT_VACATION_START_TEMPLATE
    end_message_template: str = DEFAULT_VACATION_END_TEMPLATE
    start_message_templates: list[str] = Field(default_factory=lambda: list(DEFAULT_VACATION_START_TEMPLATES))
    end_message_templates: list[str] = Field(default_factory=lambda: list(DEFAULT_VACATION_END_TEMPLATES))
    send_content_mode: str = "both"

    @field_validator("start_reminder_time", "end_reminder_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _validate_hhmm(value)


class PatrolWarningConfigRequest(BaseModel):
    enabled: bool = False
    login_url: str = ""
    warning_url: str = ""
    username: str = ""
    password: str = ""
    project_id: str = ""
    platform: str = "2"
    route_code: str = ""
    poll_interval_minutes: int = Field(default=10, ge=1, le=1440)
    rows: int = Field(default=5000, ge=1, le=10000)
    end_reminder_enabled: bool = True
    end_reminder_interval_hours: int = Field(default=6, ge=1, le=168)
    end_reminder_window_hours: int = Field(default=48, ge=1, le=720)
    send_content_mode: str = "both"
    start_message_template: str = DEFAULT_PATROL_WARNING_START_TEMPLATE
    end_message_template: str = DEFAULT_PATROL_WARNING_END_TEMPLATE
    notification_room_id: str = ""
    notification_room_name: str = ""


class PatrolWarningSendRequest(BaseModel):
    mode: str = "start"


class PatrolWarningImagePreviewRequest(BaseModel):
    warning: dict[str, Any]
    window_hours: int = Field(default=48, ge=1, le=720)


class TunnelMechanicalAssetRequest(BaseModel):
    enabled: bool = True
    assetId: str
    assetName: str
    assetCode: str
    routeCode: str = ""
    routeName: str = ""
    maintenanceSectionId: str = ""
    domainId: str = ""
    deptName: str = ""
    devName: str
    location: str
    content: str = ""
    result: int = 1
    carLicense: str = ""
    nums: str | None = ""


class TunnelMechanicalSubmitRequest(BaseModel):
    base_url: str = ""
    authorization: str = ""
    cookie: str = ""
    checkTime: date
    weather: str = ""
    checkerId: str
    checker: str
    recorderId: str
    recorder: str
    rows: list[TunnelMechanicalAssetRequest]
    dry_run: bool = False


class TunnelMechanicalResultImageRequest(BaseModel):
    base_url: str = ""
    authorization: str = ""
    cookie: str = ""
    checkTime: date


class TunnelMechanicalModifyRequest(BaseModel):
    base_url: str = ""
    authorization: str = ""
    cookie: str = ""
    checkTime: date
    weather: str = ""
    checkerId: str = ""
    checker: str = ""
    recorderId: str = ""
    recorder: str = ""
    newCheckTime: date | None = None
    newWeather: str = ""
    newCheckerId: str = ""
    newChecker: str = ""
    newRecorderId: str = ""
    newRecorder: str = ""
    dry_run: bool = False


class TunnelMechanicalConfigRequest(BaseModel):
    base_url: str = ""
    username: str = ""
    password: str = ""


class TunnelMechanicalLoginRequest(BaseModel):
    code: str = ""
    uuid: str = ""


class WechatQueryRequest(BaseModel):
    text: str = ""
    channel: str = ""
    room_id: str = ""
    stable_room_id: str = ""
    room_name: str = ""
    sender_id: str = ""
    runtime_sender_id: str = ""
    stable_member_id: str = ""
    sender_name: str = ""
    target_date: date | None = None


class WechatRosterConfirmRequest(BaseModel):
    year: int
    month: int
    source_image_path: str = ""
    grid: list[dict[str, Any]]
    overwrite: bool = False
    room_id: str = ""
    stable_room_id: str = ""


TUNNEL_MECHANICAL_AES_KEY_TEXT = "vEjLXJ/VMOFJyS6lP6s3hw=="


def create_app(
    *,
    data_dir: str | Path | None = None,
    upload_dir: str | Path | None = None,
    start_scheduler: bool = True,
    admin_password: str | None = None,
) -> FastAPI:
    base_data_dir = Path(data_dir or os.getenv("DATA_DIR", "data"))
    uploads = Path(upload_dir or os.getenv("UPLOAD_DIR", "uploads"))
    base_data_dir.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)

    repo = DutyRepository(base_data_dir / "duty-reminder.db")
    app = FastAPI(title="Duty Reminder")
    app.state.repo = repo
    app.state.upload_dir = uploads
    app.state.patrol_record_cache_path = base_data_dir / "patrol-warning-records-cache.json"
    app.state.scheduler_enabled = start_scheduler
    app.state.cjk_font_ready = has_cjk_font()
    # 个人微信 / LightAgent 通知通道已停用，不再启动内置微信桥。
    app.state.wechat_bridge_enabled = False
    app.state.wechat_bridge = None
    app.state.wecom_aibot = WeComAiBotManager()
    app.state.wecom_aibot.set_message_handler(
        lambda message: _handle_wecom_aibot_message(repo, uploads, app.state.wecom_aibot, message)
    )
    _configure_wecom_aibot_manager(app.state.wecom_aibot, repo.get_notification_config(), restart=False)
    if not app.state.cjk_font_ready:
        LOGGER.warning("未检测到中文字体，今日在岗图片可能出现乱码或方块")

    configured_admin_password = admin_password if admin_password is not None else os.getenv("ADMIN_PASSWORD", "")
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    session_secret = os.getenv("ADMIN_SESSION_SECRET") or configured_admin_password

    if configured_admin_password:
        @app.middleware("http")
        async def require_login(request: Request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            if request.url.path in {"/login", "/logout"}:
                return await call_next(request)
            if request.url.path == "/api/wecom-app/callback":
                return await call_next(request)
            if request.url.path.startswith("/notification-detail/"):
                return await call_next(request)
            if _is_wechat_internal_api_request(request):
                return await call_next(request)
            if _is_request_authorized(request, admin_username, configured_admin_password, session_secret):
                return await call_next(request)
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
            return _login_page_response(static_dir, next_url=request.url.path)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-cache, max-age=0, must-revalidate"},
        )

    @app.get("/settings-redesign")
    def settings_redesign():
        # The standalone redesign shell was abandoned. Keep the old URL as a
        # compatibility alias so opening it still lands on the real main UI.
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-cache, max-age=0, must-revalidate"},
        )

    @app.get("/settings-redesign.js")
    def settings_redesign_js():
        raise HTTPException(status_code=410, detail="旧个人微信前端脚本已停用")

    @app.get("/login")
    def login_page(request: Request):
        if not configured_admin_password:
            return RedirectResponse("/", status_code=303)
        next_url = request.query_params.get("next", "/")
        return _login_page_response(static_dir, next_url=_safe_next_url(next_url))

    @app.post("/login")
    async def login(request: Request):
        if not configured_admin_password:
            return RedirectResponse("/", status_code=303)
        form = await request.form()
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        next_url = _safe_next_url(str(form.get("next") or "/"))
        remember = bool(form.get("remember"))
        if not (
            secrets.compare_digest(username, admin_username)
            and secrets.compare_digest(password, configured_admin_password)
        ):
            return _login_page_response(static_dir, error="账号或密码不正确", next_url=next_url, status_code=401)
        max_age = REMEMBER_SESSION_SECONDS if remember else SESSION_DURATION_SECONDS
        token = _create_session_token(admin_username, session_secret, max_age)
        response = RedirectResponse(next_url, status_code=303)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            max_age=max_age if remember else None,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    @app.get("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return response

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/config/export")
    def export_config():
        snapshot = repo.export_config_snapshot()
        snapshot["exported_at"] = datetime.now(TZ).isoformat()
        if app.state.wechat_bridge:
            snapshot["wechat_bridge_identity"] = app.state.wechat_bridge.export_identity_snapshot()
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"duty-reminder-config-{datetime.now(TZ):%Y%m%d-%H%M%S}.json"
        return Response(
            content=payload,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/config/backups")
    def list_config_backups():
        return {"backups": _list_database_backups(repo)}

    @app.post("/api/config/backups")
    def create_config_backup():
        return {"success": True, "backup": repo.create_database_backup(prefix="manual-backup")}

    @app.get("/api/config/backups/{filename}")
    def download_config_backup(filename: str):
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.endswith(".db"):
            raise HTTPException(status_code=404, detail="备份文件不存在")
        backup_root = (repo.db_path.parent / "backups").resolve()
        target = (backup_root / safe_name).resolve()
        if backup_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="备份文件不存在")
        return FileResponse(target, filename=safe_name, media_type="application/octet-stream")

    @app.post("/api/config/import")
    async def import_config(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".json"):
            raise HTTPException(status_code=400, detail="请上传 JSON 配置文件")
        raw = await file.read()
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="配置文件不能超过 10MB")
        backup: dict[str, Any] | None = None
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            backup = repo.create_database_backup(prefix="before-config-import")
            result = repo.import_config_snapshot(data)
            if app.state.wechat_bridge:
                app.state.wechat_bridge.import_identity_snapshot(data.get("wechat_bridge_identity") or {})
            _configure_wecom_aibot_manager(app.state.wecom_aibot, repo.get_notification_config())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="配置 JSON 格式不正确") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"success": True, "result": result, "backup": backup}

    @app.get("/api/uploads/{filename}")
    def get_uploaded_image(filename: str):
        safe_name = Path(filename).name
        target = (uploads / safe_name).resolve()
        upload_root = uploads.resolve()
        if upload_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.get("/notification-detail/{filename}")
    def get_notification_detail(filename: str):
        safe_name = Path(filename).name
        if not safe_name.startswith("notification-detail-") or not safe_name.endswith(".html"):
            raise HTTPException(status_code=404, detail="not found")
        target = (uploads / safe_name).resolve()
        upload_root = uploads.resolve()
        if upload_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return HTMLResponse(target.read_text(encoding="utf-8"))

    @app.post("/api/rosters/upload")
    def upload_roster(file: UploadFile = File(...)):
        target = _save_roster_upload(file, uploads)
        try:
            result = extract_roster_image(str(target))
            result = _normalize_roster_ocr_names(repo, result)
            result = _apply_roster_role_semantics(repo, result)
            result["source_image_url"] = f"/api/uploads/{Path(result.get('source_image_path') or target).name}"
            return result
        except HTTPException:
            if target.exists():
                target.unlink(missing_ok=True)
            raise

    @app.post("/api/rosters/recheck")
    def recheck_roster(request: RosterRecheckRequest):
        source_path = _resolve_upload_path(request.source_image_path, uploads)
        checked = recheck_template_roster_cells(source_path, list(request.grid or []), year=request.year, month=request.month)
        if checked is None:
            parsed = extract_template_roster_image(source_path)
            if parsed is None:
                raise HTTPException(status_code=422, detail="无法从原图重新核对")
            if request.year and request.month:
                parsed["grid"] = _sanitize_roster_grid_for_month(list(parsed.get("grid", [])), request.year, request.month)
            checked = _merge_rechecked_grid(list(request.grid or []), list(parsed.get("grid", [])), list(request.baseline_grid or []))

        year = request.year or _today_in_tz().year
        month = request.month or _today_in_tz().month
        checked["grid"] = _sanitize_roster_grid_for_month(list(checked.get("grid", [])), year, month)
        checked = _apply_roster_role_semantics(repo, {"grid": checked["grid"], "issues": checked.get("issues", [])})
        checked = _merge_rechecked_grid(
            list(request.grid or []),
            list(checked.get("grid", [])),
            list(request.baseline_grid or []),
        )
        max_day = calendar.monthrange(year, month)[1]
        checked["issues"] = [issue for issue in list(checked.get("issues", [])) if _is_valid_roster_day(str(issue.get("day") or ""), max_day)]
        return {
            "success": True,
            "year": year,
            "month": month,
            "source_image_path": str(source_path),
            "source_image_url": f"/api/uploads/{source_path.name}",
            "grid": checked["grid"],
            "issues": checked["issues"],
        }

    @app.post("/api/rosters/confirm")
    def confirm_roster(request: RosterConfirmRequest):
        grid = _sanitize_roster_grid_for_month(request.grid, request.year, request.month)
        if _has_unconfirmed_roster_names(grid):
            raise HTTPException(status_code=422, detail="请先补全所有人员姓名，再确认导入")
        existing = repo.get_roster_month(request.year, request.month)
        if existing and not request.overwrite:
            diffs = _diff_roster_grids(existing.get("grid", []), grid)
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "conflict": True,
                    "message": f"{request.year}年{request.month}月排班表已存在",
                    "existing": existing,
                    "incoming": {**request.model_dump(), "grid": grid},
                    "diffs": diffs,
                },
            )
        repo.save_roster_month(request.year, request.month, grid, request.source_image_path)
        return {"success": True}

    @app.get("/api/rosters")
    def list_rosters():
        return {"rosters": repo.list_roster_months()}

    @app.get("/api/rosters/{year}/{month}/versions")
    def list_roster_versions(year: int, month: int):
        return {"versions": repo.list_roster_versions(year, month)}

    @app.post("/api/rosters/{year}/{month}/versions/{version_id}/restore")
    def restore_roster_version(year: int, month: int, version_id: int):
        version = repo.get_roster_version(version_id)
        if version is None or int(version["year"]) != year or int(version["month"]) != month:
            raise HTTPException(status_code=404, detail="排班版本不存在")
        repo.save_roster_month(year, month, version["grid"], version["source_image_path"])
        return {"success": True, "roster": repo.get_roster_month(year, month)}

    @app.get("/api/people")
    def list_people():
        return {"people": repo.list_monitored_people()}

    @app.post("/api/people")
    def save_person(request: MonitoredPersonRequest):
        repo.save_monitored_person(**request.model_dump())
        return {"success": True, "people": repo.list_monitored_people()}

    @app.post("/api/people/test")
    async def test_person_reminder(request: MonitoredPersonRequest):
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        notification_client = _notification_client_from_config(config)
        if notification_client is None:
            raise HTTPException(status_code=400, detail="请先配置通知发送通道")
        content = _render_message_template(
            str(config.get("message_template") or DEFAULT_MESSAGE_TEMPLATE),
            {
                "name": request.name.strip(),
                "date": "2025-09-16",
                "time_range": "08:00至16:00",
                "shift_label": "中班",
            },
        )
        event = ReminderEvent(
            kind="monitor_test",
            person_name=request.name.strip(),
            send_at=datetime.now(TZ),
            content=content,
            target_room_id=request.notification_room_id.strip(),
            target_room_name=request.notification_room_name.strip(),
        )
        return await _send_test_reminder_event(repo, notification_client, event, "monitor_test")

    @app.delete("/api/people/{name}")
    def delete_person(name: str):
        if not repo.delete_monitored_person(name):
            raise HTTPException(status_code=404, detail="监控班提醒人员不存在")
        return {"success": True, "people": repo.list_monitored_people()}

    @app.get("/api/personnel")
    def list_personnel():
        return {"names": repo.list_personnel_names(), "people": repo.list_personnel()}

    @app.post("/api/personnel")
    def save_personnel(request: PersonnelRequest):
        repo.save_personnel_names(request.names)
        if request.people:
            repo.save_personnel_contacts([person.model_dump() for person in request.people])
        return {"success": True, "names": repo.list_personnel_names(), "people": repo.list_personnel()}

    @app.delete("/api/personnel/{name}")
    def delete_personnel(name: str):
        if not repo.delete_personnel(name):
            raise HTTPException(status_code=404, detail="人员不存在")
        return {"success": True, "names": repo.list_personnel_names(), "people": repo.list_personnel()}

    @app.put("/api/personnel/{name}")
    def rename_personnel(name: str, request: PersonnelRenameRequest):
        try:
            if not repo.rename_personnel(name, request.name):
                raise HTTPException(status_code=404, detail="人员不存在")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"success": True, "names": repo.list_personnel_names(), "people": repo.list_personnel()}

    @app.get("/api/custom-reminders")
    def list_custom_reminders():
        return {"reminders": repo.list_custom_reminders()}

    @app.post("/api/custom-reminders")
    def save_custom_reminder(request: CustomReminderRequest):
        payload = request.model_dump()
        normalized_message = _custom_reminder_message_for_name(payload["message"], payload["name"])
        if normalized_message is None:
            raise HTTPException(status_code=422, detail="提醒文案开头的 @对象必须和姓名一致；@对象请统一在消息通知渠道配置")
        if not normalized_message:
            raise HTTPException(status_code=422, detail="请填写提醒文案，@对象请统一在消息通知渠道配置")
        payload["message"] = normalized_message
        reminder_id = repo.save_custom_reminder(**payload)
        return {"success": True, "id": reminder_id, "reminders": repo.list_custom_reminders()}

    @app.post("/api/reminder-image-preview")
    def reminder_image_preview(request: ReminderImagePreviewRequest):
        event = _build_reminder_image_preview_event(repo, request)
        return Response(content=render_shift_reminder_image(event), media_type="image/png")

    @app.post("/api/reminder-channel-preview")
    def reminder_channel_preview(request: ReminderImagePreviewRequest):
        event = _build_reminder_image_preview_event(repo, request)
        config = _public_notification_config(repo.get_notification_config())
        sender = str(config.get("effective_sender_type") or config.get("sender_type") or "wecom_webhook")
        content = event.content
        intro = _shift_reminder_intro_content(event, personal_wechat=False) if _event_can_send_image(event) else content
        return {
            "success": True,
            "sender_type": sender,
            "sender_label": _notification_sender_label(sender),
            "title": _event_news_title(event, intro),
            "description": _news_text(intro, 96),
            "mode": _event_send_content_mode(event, "both"),
            "content": content,
        }

    @app.post("/api/custom-reminders/test")
    async def test_custom_reminder(request: CustomReminderTestRequest):
        notification_client = _notification_client_from_repo(repo)
        if notification_client is None:
            raise HTTPException(status_code=400, detail="请先配置通知发送通道")
        shift = Shift(request.shift_code)
        message = _custom_reminder_message_for_name(request.message, request.name)
        if message is None:
            raise HTTPException(status_code=422, detail="提醒文案开头的 @对象必须和姓名一致；@对象请统一在消息通知渠道配置")
        if not message:
            raise HTTPException(status_code=422, detail="请填写提醒文案，@对象请统一在消息通知渠道配置")
        content = _render_simple_template(
            message,
            {
                "name": request.name.strip(),
                "date": "2025-09-16",
                "time_range": f"{shift.start_time:%H:%M}至{shift.end_time:%H:%M}",
                "shift_label": shift.label,
                "reminder_time": request.reminder_time,
            },
        )
        event = ReminderEvent(
            kind="custom_test",
            person_name=request.name.strip(),
            send_at=datetime.now(TZ),
            content=content,
            mention_mobile=request.mention_mobile.strip(),
            target_room_id=request.notification_room_id.strip(),
            target_room_name=request.notification_room_name.strip(),
        )
        return await _send_test_reminder_event(repo, notification_client, event, "custom_test")

    @app.delete("/api/custom-reminders/{reminder_id}")
    def delete_custom_reminder(reminder_id: int):
        if not repo.delete_custom_reminder(reminder_id):
            raise HTTPException(status_code=404, detail="自定义提醒不存在")
        return {"success": True, "reminders": repo.list_custom_reminders()}

    @app.get("/api/daily-duty-config")
    def get_daily_duty_config():
        return {"config": repo.get_daily_duty_config()}

    @app.post("/api/daily-duty-config")
    def save_daily_duty_config(request: DailyDutyConfigRequest):
        repo.save_daily_duty_config(**request.model_dump())
        return {"success": True, "config": repo.get_daily_duty_config()}

    @app.get("/api/vacation-reminder-config")
    def get_vacation_reminder_config():
        return {"config": repo.get_vacation_reminder_config()}

    @app.post("/api/vacation-reminder-config")
    def save_vacation_reminder_config(request: VacationReminderConfigRequest):
        repo.save_vacation_reminder_config(**request.model_dump())
        return {"success": True, "config": repo.get_vacation_reminder_config()}

    @app.post("/api/vacation-reminder-config/test")
    async def test_vacation_reminder_config(request: VacationReminderConfigRequest):
        repo.save_vacation_reminder_config(**request.model_dump())
        notification_client = _notification_client_from_repo(repo)
        if notification_client is None:
            raise HTTPException(status_code=400, detail="请先配置通知发送通道")
        bound_names = list(_wecom_app_userid_lookup(repo).keys()) if _is_wecom_app_notify_client(notification_client) else []
        person_name = next((name for name in bound_names if str(name or "").strip()), "")
        if not person_name:
            person_name = next((name for name in repo.list_personnel_names() if str(name or "").strip()), "测试人员")
        event = ReminderEvent(
            kind="vacation_start",
            person_name=person_name,
            send_at=datetime.now(TZ),
            content=_render_simple_template(
                _choose_template(request.start_message_templates, request.start_message_template or DEFAULT_VACATION_START_TEMPLATE),
                {
                    "name": person_name,
                    "date": _today_in_tz().isoformat(),
                    "rest_start_date": _today_in_tz().isoformat(),
                    "rest_end_date": _today_in_tz().isoformat(),
                },
            ),
            send_content_mode=request.send_content_mode,
        )
        return await _send_test_reminder_event(repo, notification_client, event, "vacation_test")

    @app.post("/api/daily-duty-preview")
    def preview_daily_duty(request: PreviewRequest):
        target = request.target_date or _today_in_tz()
        return _build_daily_duty_preview(repo, target)

    @app.get("/api/daily-duty-image")
    def daily_duty_image(target_date: date | None = None):
        target = target_date or _today_in_tz()
        return Response(content=render_daily_duty_image(_build_daily_duty_preview(repo, target)), media_type="image/png")

    @app.post("/api/daily-duty-config/test")
    async def test_daily_duty_config(request: PreviewRequest):
        notification_client = _notification_client_from_repo(repo)
        if notification_client is None:
            raise HTTPException(status_code=400, detail="请先配置通知发送通道")
        target = request.target_date or _today_in_tz()
        preview = _build_daily_duty_preview(repo, target)
        try:
            event = ReminderEvent(
                kind="daily_duty_test",
                person_name="今日在岗人员",
                send_at=datetime.fromisoformat(preview["send_at"]),
                content=preview["content"],
                send_content_mode=str(preview.get("send_content_mode") or "both"),
            )
            target_ids = _notification_target_ids_for_event(repo, notification_client, event)
            if not _is_wecom_app_notify_client(notification_client):
                target_ids = _daily_duty_target_room_ids(repo)
            mode = _event_send_content_mode(event, "both")
            mentions = _notification_true_mentions_for_event(repo, notification_client, event)
            content = _notification_content_for_event(repo, notification_client, event)
            await _send_graphic_or_text_image(
                notification_client,
                title=_event_news_title(event, content),
                text=content,
                image_bytes=render_daily_duty_image(preview),
                mentions=mentions,
                target_ids=target_ids,
                mode=mode,
            )
            repo.save_send_record(
                kind="daily_duty_test",
                target="今日在岗人员",
                scheduled_at=preview["send_at"],
                status="success",
                content=preview["content"],
                notification_room_id=str(preview.get("notification_room_id") or ""),
                notification_room_name=str(preview.get("notification_room_name") or ""),
            )
        except WeComError as exc:
            repo.save_send_record(
                kind="daily_duty_test",
                target="今日在岗人员",
                scheduled_at=preview["send_at"],
                status="failed",
                content=preview["content"],
                error=str(exc),
                notification_room_id=str(preview.get("notification_room_id") or ""),
                notification_room_name=str(preview.get("notification_room_name") or ""),
            )
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, str(exc))) from exc
        except Exception as exc:
            error = f"测试发送失败：{exc}"
            repo.save_send_record(
                kind="daily_duty_test",
                target="今日在岗人员",
                scheduled_at=preview["send_at"],
                status="failed",
                content=preview["content"],
                error=error,
                notification_room_id=str(preview.get("notification_room_id") or ""),
                notification_room_name=str(preview.get("notification_room_name") or ""),
            )
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, error)) from exc
        return {"success": True, "content": preview["content"], "send_at": preview["send_at"], "details": preview["details"]}

    @app.get("/api/patrol-warning-config")
    def get_patrol_warning_config():
        config = repo.get_patrol_warning_config()
        return {
            "config": _public_patrol_warning_config(config),
            "state": _public_patrol_warning_state(repo.get_patrol_warning_state(), config),
        }

    @app.post("/api/patrol-warning-config")
    def save_patrol_warning_config(request: PatrolWarningConfigRequest):
        existing = repo.get_patrol_warning_config()
        previous_poll_interval = int(existing.get("poll_interval_minutes") or 10)
        next_poll_interval = max(1, min(int(request.poll_interval_minutes), 1440))
        password = request.password if request.password else str(existing.get("password", ""))
        should_reset_state = any(
            str(existing.get(key) or "").strip() != str(getattr(request, key) or "").strip()
            for key in ("login_url", "warning_url", "project_id", "platform", "route_code")
        )
        repo.save_patrol_warning_config(**{**request.model_dump(), "password": password})
        state_updates: dict[str, Any] = {}
        if should_reset_state:
            state_updates.update(
                warning_key="",
                warning={},
                last_checked_at="",
                last_start_sent_key="",
                last_end_reminder_slot="",
                token="",
                token_expires_at="",
                next_check_at="",
                failure_count=0,
                backoff_until="",
                last_error="",
            )
        if not request.enabled:
            state_updates.update(
                next_check_at="",
                backoff_until="",
                failure_count=0,
                last_error="",
            )
        elif next_poll_interval != previous_poll_interval or (request.enabled and not existing.get("enabled")):
            state_updates.update(
                next_check_at=next_poll_time(datetime.now(TZ), next_poll_interval).isoformat(),
                backoff_until="",
                failure_count=0,
                last_error="",
            )
        if state_updates:
            repo.save_patrol_warning_state(
                **state_updates,
            )
        return {
            "success": True,
            "config": _public_patrol_warning_config(repo.get_patrol_warning_config()),
            "state": _public_patrol_warning_state(repo.get_patrol_warning_state(), repo.get_patrol_warning_config()),
        }

    @app.post("/api/patrol-warning-config/test")
    async def test_patrol_warning_config(request: PatrolWarningConfigRequest):
        existing = repo.get_patrol_warning_config()
        config = {**request.model_dump(), "password": request.password or str(existing.get("password", ""))}
        try:
            latest, stats = await fetch_latest_warning(config, TZ)
        except PatrolWarningError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if latest is not None:
            repo.save_patrol_warning_state(warning=latest.as_dict())
        public_latest = latest.as_dict() if latest and _patrol_warning_in_display_window(
            latest,
            config,
            now=datetime.now(TZ),
        ) else None
        return {
            "success": True,
            "stats": stats,
            "latest": public_latest,
        }

    @app.get("/api/patrol-warning-image")
    def patrol_warning_image(mode: str = "auto"):
        config = repo.get_patrol_warning_config()
        now = datetime.now(TZ)
        warning = warning_from_dict(dict(repo.get_patrol_warning_state().get("warning") or {}), TZ)
        if warning is None or not _patrol_warning_in_display_window(warning, config, now=now):
            raise HTTPException(status_code=404, detail="暂无已监测到的公路巡查预警")
        image = render_patrol_warning_image(
            warning,
            now=now,
            window_hours=int(config.get("end_reminder_window_hours") or 48),
            mode=mode,
        )
        return Response(content=image, media_type="image/png")

    @app.post("/api/patrol-warning-image-preview")
    def patrol_warning_image_preview(request: PatrolWarningImagePreviewRequest):
        warning = warning_from_dict(dict(request.warning or {}), TZ)
        if warning is None:
            raise HTTPException(status_code=400, detail="预警数据不完整，无法生成图片预览")
        image = render_patrol_warning_image(
            warning,
            now=datetime.now(TZ),
            window_hours=request.window_hours,
            mode="auto",
        )
        return Response(content=image, media_type="image/png")

    @app.post("/api/patrol-warning-config/send-test")
    async def send_patrol_warning_test(request: PatrolWarningSendRequest):
        config = repo.get_patrol_warning_config()
        webhook_client = _wecom_webhook_client_from_repo(repo)
        if webhook_client is None:
            raise HTTPException(status_code=400, detail="请先配置企业微信群机器人地址")
        warning = warning_from_dict(dict(repo.get_patrol_warning_state().get("warning") or {}), TZ)
        now = datetime.now(TZ)
        if warning is None or not _patrol_warning_in_display_window(warning, config, now=now):
            raise HTTPException(status_code=400, detail="暂无已监测到的预警，请等待后台监测到预警后再发送")
        mode = "end" if request.mode == "end" else "start"
        content = _build_patrol_warning_content(warning, config, now=now, mode=mode)
        try:
            await _send_patrol_warning_message(
                repo,
                webhook_client,
                kind=f"patrol_warning_{mode}_test",
                target=str(config.get("route_code") or warning.route_code or "公路巡查预警"),
                scheduled_at=now.isoformat(),
                content=content,
                warning=warning,
                window_hours=int(config.get("end_reminder_window_hours") or 48),
                now=now,
                image_mode=mode,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, f"发送预警提醒失败：{exc}")) from exc
        return {"success": True, "content": content}

    @app.get("/api/patrol-warning/orange-records")
    async def query_patrol_warning_orange_records(name: str = "", limit: int = 5000):
        query_name = str(name or "").strip()
        if not query_name:
            raise HTTPException(status_code=400, detail="请输入要查询的姓名")
        config = repo.get_patrol_warning_config()
        state = repo.get_patrol_warning_state()
        try:
            result = await fetch_patrol_records_by_name_result(
                config,
                TZ,
                name=query_name,
                token=str(state.get("token") or ""),
                token_expires_at=str(state.get("token_expires_at") or ""),
                limit=max(1, min(int(limit or 5000), 5000)),
                cache_path=app.state.patrol_record_cache_path,
            )
        except PatrolWarningError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        repo.save_patrol_warning_state(
            token=result.token,
            token_expires_at=result.token_expires_at,
            last_error="",
        )
        return {
            "success": True,
            "name": query_name,
            "route_code": str(config.get("route_code") or "").strip(),
            "records": result.records,
            "stats": result.stats,
        }

    @app.get("/api/tunnel-mechanical/templates")
    def get_tunnel_mechanical_templates():
        return _public_tunnel_mechanical_template(repo.get_tunnel_mechanical_template())

    @app.post("/api/tunnel-mechanical/templates/import")
    async def import_tunnel_mechanical_templates(file: UploadFile = File(...)):
        if not file.filename.lower().endswith(".json"):
            raise HTTPException(status_code=400, detail="请上传 JSON 模板文件")
        raw = await file.read()
        if len(raw) > 1024 * 1024:
            raise HTTPException(status_code=400, detail="模板文件不能超过 1MB")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="模板 JSON 格式不正确") from exc
        template = _normalize_tunnel_mechanical_template(data)
        repo.save_tunnel_mechanical_template(template)
        return {
            "success": True,
            "template": _public_tunnel_mechanical_template(repo.get_tunnel_mechanical_template()),
        }

    @app.get("/api/tunnel-mechanical/config")
    def get_tunnel_mechanical_config():
        return {
            "config": _public_tunnel_mechanical_config(repo.get_tunnel_mechanical_config()),
            "state": _public_tunnel_mechanical_state(repo.get_tunnel_mechanical_state()),
        }

    @app.post("/api/tunnel-mechanical/config")
    def save_tunnel_mechanical_config(request: TunnelMechanicalConfigRequest):
        existing = repo.get_tunnel_mechanical_config()
        base_url = request.base_url.strip() or str(existing.get("base_url") or "")
        if base_url:
            _tunnel_mechanical_base_url(base_url)
        username = request.username.strip()
        password = request.password if request.password else str(existing.get("password") or "")
        credentials_changed = (
            base_url != str(existing.get("base_url") or "")
            or username != str(existing.get("username") or "")
            or bool(request.password)
        )
        repo.save_tunnel_mechanical_config(base_url=base_url, username=username, password=password)
        if credentials_changed:
            repo.save_tunnel_mechanical_state(
                access_token="",
                refresh_token="",
                cookie_header="",
                token_expires_at="",
                last_login_at="",
                last_error="",
            )
        return {
            "success": True,
            "config": _public_tunnel_mechanical_config(repo.get_tunnel_mechanical_config()),
            "state": _public_tunnel_mechanical_state(repo.get_tunnel_mechanical_state()),
        }

    @app.get("/api/tunnel-mechanical/captcha")
    async def get_tunnel_mechanical_captcha():
        config = repo.get_tunnel_mechanical_config()
        return await _fetch_tunnel_mechanical_captcha(str(config.get("base_url") or ""))

    @app.post("/api/tunnel-mechanical/login-test")
    async def test_tunnel_mechanical_login(request: TunnelMechanicalLoginRequest):
        await _login_tunnel_mechanical(
            repo,
            repo.get_tunnel_mechanical_config(),
            code=request.code,
            uuid=request.uuid,
        )
        return {
            "success": True,
            "state": _public_tunnel_mechanical_state(repo.get_tunnel_mechanical_state()),
        }

    @app.post("/api/tunnel-mechanical/submit")
    async def submit_tunnel_mechanical(request: TunnelMechanicalSubmitRequest):
        return await _submit_tunnel_mechanical(repo, request, result_upload_dir=uploads)

    @app.post("/api/tunnel-mechanical/result-image")
    async def tunnel_mechanical_result_image(request: TunnelMechanicalResultImageRequest):
        return await _query_tunnel_mechanical_result_image(repo, request, uploads)

    @app.get("/api/construction-sites")
    def list_construction_sites():
        return {"sites": repo.list_construction_sites()}

    @app.post("/api/construction-sites")
    def add_construction_site(request: ConstructionSiteRequest):
        return {"success": True, "site": repo.add_construction_site(request.name)}

    @app.put("/api/construction-sites/{site_id}")
    def update_construction_site(site_id: int, request: ConstructionSiteRequest):
        site = repo.update_construction_site(site_id, request.name)
        if not site:
            raise HTTPException(status_code=404, detail="施工点不存在")
        return {"success": True, "site": site}

    @app.delete("/api/construction-sites/{site_id}")
    def delete_construction_site(site_id: int):
        if not repo.delete_construction_site(site_id):
            raise HTTPException(status_code=404, detail="施工点不存在")
        return {"success": True}

    @app.get("/api/notification-config")
    def get_notification_config():
        return {"config": _public_notification_config(repo.get_notification_config())}

    @app.post("/api/notification-config")
    def save_notification_config(request: NotificationConfigRequest):
        existing = _notification_config_with_env_defaults(repo.get_notification_config())
        sender_type = _normalize_notification_sender_type(request.sender_type.strip() or str(existing.get("sender_type", "wecom_webhook")))
        webhook_url = request.webhook_url.strip() or str(existing.get("webhook_url", "")).strip()
        wecom_aibot_id = request.wecom_aibot_id.strip() or str(existing.get("wecom_aibot_id", "")).strip()
        wecom_aibot_secret = request.wecom_aibot_secret.strip() or str(existing.get("wecom_aibot_secret", "")).strip()
        wecom_app_corp_id = request.wecom_app_corp_id.strip() or str(existing.get("wecom_app_corp_id", "")).strip()
        wecom_app_agent_id = request.wecom_app_agent_id.strip() or str(existing.get("wecom_app_agent_id", "")).strip()
        wecom_app_secret = request.wecom_app_secret.strip() or str(existing.get("wecom_app_secret", "")).strip()
        wecom_app_token = request.wecom_app_token.strip() or str(existing.get("wecom_app_token", "")).strip()
        wecom_app_encoding_aes_key = (
            request.wecom_app_encoding_aes_key.strip()
            or str(existing.get("wecom_app_encoding_aes_key", "")).strip()
        )
        wecom_app_target_names = request.wecom_app_target_names
        wecom_app_function_target_names = request.wecom_app_function_target_names
        # 旧个人微信/LightAgent 通道已下线：保存时强制清空旧目标，避免导入旧配置后页面或调度继续认为可用。
        lightagent_url = ""
        lightagent_token = ""
        lightagent_targets: list[dict[str, str]] = []
        lightagent_target = ""
        repo.save_notification_config(
            sender_type=sender_type,
            webhook_url=webhook_url,
            wecom_aibot_enabled=request.wecom_aibot_enabled,
            wecom_aibot_id=wecom_aibot_id,
            wecom_aibot_secret=wecom_aibot_secret,
            wecom_app_enabled=request.wecom_app_enabled,
            wecom_app_corp_id=wecom_app_corp_id,
            wecom_app_agent_id=wecom_app_agent_id,
            wecom_app_secret=wecom_app_secret,
            wecom_app_token=wecom_app_token,
            wecom_app_encoding_aes_key=wecom_app_encoding_aes_key,
            wecom_app_target_names=wecom_app_target_names,
            wecom_app_function_target_names=wecom_app_function_target_names,
            lightagent_url=lightagent_url,
            lightagent_token=lightagent_token,
            lightagent_target=lightagent_target,
            lightagent_targets=lightagent_targets,
            mention_mode=_normalize_notification_mention_mode(request.mention_mode),
            mention_targets=request.mention_targets,
            message_template=request.message_template.strip() or DEFAULT_MESSAGE_TEMPLATE,
        )
        _configure_wecom_aibot_manager(app.state.wecom_aibot, repo.get_notification_config())
        lightagent_sync = _sync_lightagent_notification_targets(repo, sender_type, lightagent_targets)
        return {
            "success": True,
            "config": _public_notification_config(repo.get_notification_config()),
            "wecom_aibot": app.state.wecom_aibot.status_snapshot(),
            "lightagent_sync": lightagent_sync,
        }

    @app.get("/api/wecom-aibot/status")
    def get_wecom_aibot_status():
        return app.state.wecom_aibot.status_snapshot()

    @app.post("/api/wecom-aibot/reconnect")
    def reconnect_wecom_aibot():
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        _configure_wecom_aibot_manager(app.state.wecom_aibot, config, restart=False)
        try:
            app.state.wecom_aibot.reconnect()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"企业微信智能机器人连接失败：{exc}") from exc
        return app.state.wecom_aibot.status_snapshot()

    @app.get("/api/wecom-app/menu")
    def get_wecom_app_menu_preview():
        return _public_wecom_app_menu_preview(repo)

    @app.post("/api/wecom-app/menu")
    def save_wecom_app_menu_config(request: WeComAppMenuConfigRequest):
        groups = _normalize_wecom_app_menu_groups(
            [group.model_dump() for group in request.groups],
            allow_empty=False,
        )
        repo.save_wecom_app_menu_config(groups)
        return {"success": True, "menu": _public_wecom_app_menu_preview(repo)}

    @app.post("/api/wecom-app/menu/create")
    async def create_wecom_app_menu():
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        if not _wecom_app_config_complete(config, require_callback=False):
            raise HTTPException(status_code=400, detail="请先启用并保存企业微信自建应用 CorpID / AgentId / Secret")
        payload = _wecom_app_menu_payload(repo)
        try:
            client = _wecom_app_client_from_repo(repo)
            await client.create_menu(payload)
        except WeComError as exc:
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, str(exc))) from exc
        return {"success": True, "menu": _public_wecom_app_menu_preview(repo)}

    @app.post("/api/wecom-app/test")
    async def test_wecom_app_interaction():
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        if not _wecom_app_config_complete(config, require_callback=True):
            raise HTTPException(status_code=400, detail="请先启用并保存企业微信自建应用 CorpID / AgentId / Secret / Token / EncodingAESKey")
        # 同时校验 Token / EncodingAESKey 格式，避免只测发送成功但回调配置明显不可用。
        _wecom_app_crypto_from_repo(repo)
        targets = _wecom_app_default_tousers(repo) or ["@all"]
        target_text = "|".join(targets)
        display_target = "已绑定企业微信成员" if targets and targets != ["@all"] else "@all（应用可见范围）"
        content = (
            "企业微信自建应用测试消息发送成功。\n"
            "如果你收到这条消息，说明 CorpID / AgentId / Secret 和应用消息发送已生效。\n"
            "请在这个自建应用里回复“菜单”，如果能收到查询菜单，说明接收消息 URL / Token / EncodingAESKey 回调交互也已生效。\n"
            "首次使用姓名相关功能请回复：绑定商邱宏"
        )
        try:
            await _wecom_app_client_from_repo(repo).send_text(target_text, content)
            repo.save_send_record(
                kind="wecom_app_test",
                target=display_target,
                status="success",
                content=content,
            )
        except WeComError as exc:
            repo.save_send_record(
                kind="wecom_app_test",
                target=display_target,
                status="failed",
                content=content,
                error=str(exc),
            )
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, str(exc))) from exc
        return {
            "success": True,
            "target": display_target,
            "content": content,
            "message": "测试消息已发送。收到后请在企业微信自建应用里回复“菜单”验证回调交互。",
        }

    @app.get("/api/wecom-app/callback")
    def verify_wecom_app_callback(
        msg_signature: str = "",
        timestamp: str = "",
        nonce: str = "",
        echostr: str = "",
    ):
        crypto = _wecom_app_crypto_from_repo(repo)
        if not crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
            raise HTTPException(status_code=403, detail="企业微信自建应用回调签名不正确")
        return Response(crypto.decrypt(echostr), media_type="text/plain")

    @app.post("/api/wecom-app/callback")
    async def receive_wecom_app_callback(
        request: Request,
        background_tasks: BackgroundTasks,
        msg_signature: str = "",
        timestamp: str = "",
        nonce: str = "",
    ):
        raw = await request.body()
        encrypted = encrypted_text_from_xml(raw.decode("utf-8"))
        crypto = _wecom_app_crypto_from_repo(repo)
        if not crypto.verify_signature(msg_signature, timestamp, nonce, encrypted):
            raise HTTPException(status_code=403, detail="企业微信自建应用消息签名不正确")
        message = parse_wecom_app_message(crypto.decrypt(encrypted))
        command_text = _wecom_app_message_command_text(message, repo)
        callback_query = _wecom_app_query_from_message(message, command_text)
        callback_pending = WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.get(_wecom_app_shared_pending_key())
        roster_pending = WECOM_APP_PENDING_ROSTER_IMPORTS.get(_wecom_app_pending_key(callback_query))
        construction_image_pending = WECOM_APP_PENDING_CONSTRUCTION_IMAGES.get(_wecom_app_pending_key(callback_query))
        construction_site_pending = WECOM_APP_PENDING_CONSTRUCTION_SITES.get(_wecom_app_shared_pending_key())
        is_pending_confirm = (
            _wecom_app_pending_allows_confirm(callback_pending)
            and _is_wecom_app_pending_confirm_text(command_text)
        )
        is_pending_account_help = (
            _wecom_app_pending_allows_account_help(callback_pending)
            and _is_wecom_app_pending_account_help_text(command_text)
        )
        is_pending_roster = bool(roster_pending) and _is_wecom_app_roster_pending_text(command_text)
        is_pending_construction = bool(construction_image_pending or construction_site_pending)
        if message.msg_type == "image" and str(message.media_id or "").strip():
            background_tasks.add_task(_handle_wecom_app_message, repo, uploads, message)
        elif message.msg_type in {"text", "voice"} and (
            _is_wecom_app_roster_import_request(command_text)
            or _is_wecom_app_construction_image_request(command_text)
            or _is_wecom_app_construction_site_request(command_text)
            or is_pending_confirm
            or is_pending_account_help
            or is_pending_roster
            or is_pending_construction
            or _is_tunnel_mechanical_partner_command(command_text)
            or _looks_like_duty_wechat_command(_normalize_wechat_query_text(command_text), repo, query=callback_query)
        ):
            background_tasks.add_task(_handle_wecom_app_message, repo, uploads, message)
        elif message.msg_type == "event" and str(message.event or "").strip().lower() == "click" and command_text:
            background_tasks.add_task(_handle_wecom_app_message, repo, uploads, message)
        return Response("success", media_type="text/plain")

    @app.post("/api/notification-config/test")
    async def test_notification_config(request: NotificationTestRequest):
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        notification_client = _notification_client_from_config(config)
        if notification_client is None:
            raise HTTPException(status_code=400, detail="请先配置通知发送通道")
        person_name = str(request.person_name or "示例甲").strip() or "示例甲"
        content = _render_message_template(
            str(config.get("message_template") or DEFAULT_MESSAGE_TEMPLATE),
            {
                "name": person_name,
                "date": "2025-09-16",
                "time_range": "08:00至16:00",
                "shift_label": "中班",
            },
        )
        event = ReminderEvent(kind="notification_test", person_name=person_name, send_at=datetime.now(TZ), content=content)
        _raise_if_wecom_app_unbound_person(repo, notification_client, event)
        mentions = _notification_true_mentions_for_event(repo, notification_client, event)
        content = _notification_content_for_event(repo, notification_client, event)
        target_ids = _notification_target_ids_for_event(repo, notification_client, event)
        record_target = person_name or "测试消息"
        try:
            if not await _notify_send_news(
                notification_client,
                title=_event_news_title(event, content),
                description=content,
                image_bytes=render_shift_reminder_image(event),
                target_ids=target_ids,
            ):
                await _notify_send_text(notification_client, content, mentions, target_ids)
            repo.save_send_record(
                kind="notification_test",
                target=record_target,
                status="success",
                content=content,
            )
        except WeComError as exc:
            repo.save_send_record(
                kind="notification_test",
                target=record_target,
                status="failed",
                content=content,
                error=str(exc),
            )
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, str(exc))) from exc
        except Exception as exc:
            error = f"测试发送失败：{exc}"
            repo.save_send_record(
                kind="notification_test",
                target=record_target,
                status="failed",
                content=content,
                error=error,
            )
            raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, error)) from exc
        return {"success": True, "content": content}

    @app.get("/api/feature-channel-config")
    def get_feature_channel_config():
        raise HTTPException(status_code=410, detail="旧个人微信交互通道已停用，请使用企业微信自建应用")

    @app.post("/api/feature-channel-config")
    def save_feature_channel_config(request: FeatureChannelConfigRequest):
        raise HTTPException(status_code=410, detail="旧个人微信交互通道已停用，请使用企业微信自建应用")

    @app.post("/api/feature-channel-config/test")
    async def test_feature_channel_config():
        raise HTTPException(status_code=410, detail="旧个人微信交互通道已停用，请使用企业微信自建应用")

    @app.get("/api/wechat-interaction-config")
    def get_wechat_interaction_config():
        return {"config": _public_wechat_interaction_config(repo)}

    @app.post("/api/wechat-interaction-config")
    def save_wechat_interaction_config(request: WechatInteractionConfigRequest):
        repo.save_wechat_interaction_config(
            patrol_record_triggers=_normalize_wechat_trigger_list(request.patrol_record_triggers, DEFAULT_PATROL_RECORD_TRIGGERS),
            patrol_record_template=_normalize_wechat_template_text(
                request.patrol_record_template,
                DEFAULT_PATROL_RECORD_TEMPLATE,
                LEGACY_PATROL_RECORD_TEMPLATE,
            ),
            tunnel_template_triggers=_normalize_wechat_trigger_list(request.tunnel_template_triggers, DEFAULT_TUNNEL_TEMPLATE_TRIGGERS),
            tunnel_template=_normalize_wechat_template_text(
                request.tunnel_template,
                DEFAULT_TUNNEL_TEMPLATE,
                LEGACY_TUNNEL_TEMPLATE,
            ),
            tunnel_modify_template_triggers=_normalize_wechat_trigger_list(request.tunnel_modify_template_triggers, DEFAULT_TUNNEL_MODIFY_TEMPLATE_TRIGGERS),
            tunnel_modify_template=_normalize_wechat_template_text(
                request.tunnel_modify_template,
                DEFAULT_TUNNEL_MODIFY_TEMPLATE,
                LEGACY_TUNNEL_MODIFY_TEMPLATE,
            ),
        )
        return {"success": True, "config": _public_wechat_interaction_config(repo)}

    @app.post("/api/wechat-interaction-config/test")
    async def test_wechat_interaction_config():
        room_id = next(iter(_notification_wechat_target_room_ids(repo)), "")
        interaction = _wechat_interaction_config(repo)
        tests = [
            ("patrol_record", next(iter(interaction["patrol_record_triggers"]), "巡查记录")),
            ("tunnel_template", next(iter(interaction["tunnel_template_triggers"]), "模板")),
            ("tunnel_modify_template", next(iter(interaction["tunnel_modify_template_triggers"]), "修改模板")),
        ]
        results: list[dict[str, Any]] = []
        for query_type, trigger in tests:
            query = WechatQueryRequest(
                text=trigger,
                room_id=room_id,
                stable_room_id=room_id,
                sender_id="wechat-interaction-test",
                runtime_sender_id="wechat-interaction-test",
                sender_name="微信交互测试",
            )
            result = await _build_wechat_query_response_with_log(repo, query, uploads=uploads)
            results.append({"query_type": query_type, "trigger": trigger, "result": result})
        primary_result = next((item["result"] for item in results if item["query_type"] == "patrol_record"), results[0]["result"] if results else {})
        summary = "；".join(
            f"{item['trigger']} -> {str(item['result'].get('reply') or '').strip() or '无回复'}"
            for item in results
        )
        return {"success": True, "result": primary_result, "results": results, "summary": summary}

    @app.post("/api/wechat-interaction-config/simulate")
    async def simulate_wechat_interaction(request: WechatInteractionTestRequest):
        if str(request.channel or "").strip().lower() in {"lightagent", "wechat_bridge", "personal_wechat"}:
            raise HTTPException(status_code=410, detail="旧个人微信交互通道已停用，请使用企业微信自建应用")
        room_id = str(request.stable_room_id or request.room_id or "").strip()
        config_room_ids = _notification_wechat_target_room_ids(repo)
        if config_room_ids and room_id and room_id not in config_room_ids:
            room_name = _notification_wechat_target_room_label(repo) or "未命名微信群"
            raise HTTPException(status_code=403, detail=f"当前来源不在允许的交互范围内：{room_name}")
        if not room_id:
            room_id = next(iter(config_room_ids), "")
        query = WechatQueryRequest(
            text=request.text,
            room_id=str(request.room_id or room_id),
            stable_room_id=room_id,
            room_name=request.room_name,
            sender_id=request.sender_id,
            runtime_sender_id=request.runtime_sender_id,
            stable_member_id=request.stable_member_id or request.sender_id,
            sender_name=request.sender_name,
            target_date=request.target_date,
        )
        if not _looks_like_duty_wechat_command(_normalize_wechat_query_text(query.text), repo, query=query):
            return _ignored_wechat_message_response()
        result = await _build_wechat_query_response_with_log(repo, query, uploads=uploads)
        image_path = _wechat_query_result_image_path(result, uploads)
        return {
            "success": True,
            "result": result,
            "image_url": str(result.get("image_url") or result.get("result_image_url") or ""),
            "image_full_url": _public_app_url(str(result.get("image_url") or result.get("result_image_url") or "")),
            "image_exists": bool(image_path and image_path.exists()),
        }

    @app.get("/api/wechat-interaction-logs")
    def get_wechat_interaction_logs(limit: int = 20):
        return {"logs": _public_wechat_interaction_logs(repo, repo.list_wechat_interaction_logs(limit))}


    def _disabled_personal_wechat_channel():
        raise HTTPException(status_code=410, detail="该通知通道已停用，请使用企业微信自建应用或企业微信群机器人")

    @app.get("/api/lightagent/wechat/status")
    def lightagent_wechat_status():
        _disabled_personal_wechat_channel()
        if app.state.wechat_bridge:
            return app.state.wechat_bridge.status_snapshot()
        status = _lightagent_web_request(repo, "GET", "/api/wechat_group/qrlogin")
        channels_error = ""
        channel_info: dict[str, Any] = {}
        try:
            channel_info = _lightagent_wechat_group_channel_info(_lightagent_web_request(repo, "GET", "/api/channels"))
        except Exception as exc:
            channels_error = str(exc)
        if channel_info:
            rooms = _normalize_lightagent_wechat_rooms(channel_info.get("rooms") or [])
            status["connected"] = bool(channel_info.get("connected"))
            status["login_status"] = str(channel_info.get("login_status") or status.get("login_status") or "")
            status["rooms"] = rooms
            status["sendable_room_count"] = len([room for room in rooms if room.get("sendable")])
            status["selected_room_ids"] = channel_info.get("selected_room_ids") or []
            status["selected_room_names"] = channel_info.get("selected_room_names") or []
        elif channels_error:
            status["connected"] = False
            status["channels_error"] = channels_error
        return status

    @app.post("/api/lightagent/wechat/refresh")
    def refresh_lightagent_wechat():
        _disabled_personal_wechat_channel()
        if app.state.wechat_bridge:
            app.state.wechat_bridge.refresh_rooms()
            return app.state.wechat_bridge.status_snapshot()
        return _lightagent_web_request(repo, "POST", "/api/wechat_group/qrlogin", json_body={"action": "refresh"})

    @app.post("/api/lightagent/wechat/refresh-qr")
    def refresh_lightagent_wechat_qr():
        _disabled_personal_wechat_channel()
        if app.state.wechat_bridge:
            app.state.wechat_bridge.refresh_login_qr()
            return app.state.wechat_bridge.status_snapshot()
        return _lightagent_web_request(repo, "POST", "/api/wechat_group/qrlogin", json_body={"action": "refresh"})

    @app.get("/api/lightagent/wechat/rooms")
    def lightagent_wechat_rooms():
        _disabled_personal_wechat_channel()
        if app.state.wechat_bridge:
            app.state.wechat_bridge.refresh_rooms()
            snapshot = app.state.wechat_bridge.status_snapshot()
            return {
                "status": "success",
                "connected": bool(snapshot.get("connected")),
                "login_status": str(snapshot.get("login_status") or ""),
                "rooms": snapshot.get("rooms") or [],
                "sendable_room_count": snapshot.get("sendable_room_count") or 0,
                "selected_room_ids": snapshot.get("selected_room_ids") or [],
                "selected_room_names": snapshot.get("selected_room_names") or [],
            }
        data = _lightagent_web_request(repo, "GET", "/api/channels")
        channels = data.get("channels") if isinstance(data, dict) else []
        for channel in channels or []:
            if str(channel.get("name") or "") == "wechat_group":
                extra = channel.get("extra") if isinstance(channel.get("extra"), dict) else {}
                rooms = _normalize_lightagent_wechat_rooms(extra.get("rooms") or [])
                return {
                    "status": "success",
                    "connected": _lightagent_wechat_group_connected(channel),
                    "login_status": str(channel.get("login_status") or ""),
                    "rooms": rooms,
                    "sendable_room_count": len([room for room in rooms if room.get("sendable")]),
                    "selected_room_ids": extra.get("selected_room_ids") or [],
                    "selected_room_names": extra.get("selected_room_names") or [],
                }
        return {"status": "success", "connected": False, "login_status": "", "rooms": []}

    @app.get("/api/lightagent/wechat/members")
    def lightagent_wechat_members(room_id: str):
        _disabled_personal_wechat_channel()
        room_text = str(room_id or "").strip()
        if not room_text:
            raise HTTPException(status_code=400, detail="room_id is required")
        if app.state.wechat_bridge:
            return {
                "status": "success",
                "members": app.state.wechat_bridge.get_room_members(room_text, limit=500),
            }
        data = _lightagent_web_request(
            repo,
            "GET",
            "/api/wechat-group/members",
            params={"stable_room_id": room_text, "limit": "500"},
        )
        if isinstance(data, dict):
            data["members"] = _normalize_lightagent_wechat_members(data.get("members") or [])
        return data

    @app.post("/api/wechat-query")
    async def wechat_query(http_request: Request, query: WechatQueryRequest):
        _require_wechat_query_auth(http_request)
        if not _looks_like_duty_wechat_command(_normalize_wechat_query_text(query.text), repo, query=query):
            return _ignored_wechat_message_response()
        return await _build_wechat_query_response_with_log(repo, query, uploads=uploads)

    @app.post("/api/wechat-roster/import")
    def wechat_roster_import(
        http_request: Request,
        file: UploadFile = File(...),
        overwrite: bool = Form(False),
        room_id: str = Form(""),
        stable_room_id: str = Form(""),
    ):
        _require_wechat_query_auth(http_request)
        _require_feature_channel_for_roster_import(repo, room_id=room_id, stable_room_id=stable_room_id)
        result = _build_wechat_roster_import_response(repo, uploads, file, overwrite=overwrite)
        result.setdefault("query_type", "roster_import")
        _attach_roster_import_image(result, uploads)
        return result

    @app.post("/api/wechat-roster/confirm")
    def wechat_roster_confirm(http_request: Request, request: WechatRosterConfirmRequest):
        _require_wechat_query_auth(http_request)
        _require_feature_channel_for_roster_import(
            repo,
            room_id=str(request.room_id or ""),
            stable_room_id=str(request.stable_room_id or ""),
        )
        result = _build_wechat_roster_confirm_response(
            repo,
            int(request.year),
            int(request.month),
            list(request.grid or []),
            source_image_path=str(request.source_image_path or ""),
            overwrite=bool(request.overwrite),
        )
        result.setdefault("query_type", "roster_import")
        _attach_roster_import_image(result, uploads)
        return result

    @app.get("/api/send-records")
    def list_send_records(
        limit: int = 100,
        status: str = "",
        kind: str = "",
        target: str = "",
        today_failed: bool = False,
    ):
        raw_records = repo.list_send_records(max(limit, 500) if (status or kind or target or today_failed) else limit)
        public_records = _public_send_records(repo, raw_records)
        records = _filter_send_records(
            public_records,
            status=status,
            kind=kind,
            target=target,
            today_failed=today_failed,
        )[: max(1, min(int(limit), 500))]
        return {"records": records}

    @app.post("/api/send-records/{record_id}/resend")
    async def resend_send_record(record_id: int):
        record = repo.get_send_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="发送记录不存在")
        return await _resend_send_record(repo, record, uploads=uploads)

    @app.get("/api/system-status")
    def system_status():
        return _build_system_status(repo, bool(app.state.scheduler_enabled), bool(app.state.cjk_font_ready), upload_dir=uploads)

    @app.post("/api/uploads/cleanup")
    def cleanup_uploads():
        result = _cleanup_old_uploads(uploads)
        return {"success": True, "result": result, "storage": _upload_storage_status(uploads)}

    @app.get("/api/people-center")
    def people_center():
        return {"people": _build_people_center(repo)}

    @app.get("/api/interaction-commands")
    def interaction_commands():
        return {"commands": _interaction_command_catalog(repo)}

    @app.get("/api/reminders/today")
    def today_reminders():
        today = _today_in_tz()
        return _reminder_events_response(repo, today, now=datetime.now(TZ))

    @app.get("/api/reminders/diagnostics")
    def reminder_diagnostics(target_date: date | None = None):
        target = target_date or _today_in_tz()
        return _reminder_diagnostics_response(repo, target, now=datetime.now(TZ))

    @app.post("/api/reminders/preview")
    def preview_reminders(request: PreviewRequest):
        target = request.target_date or _today_in_tz()
        return _reminder_events_response(repo, target, now=datetime.now(TZ))

    if app.state.wechat_bridge:
        @app.on_event("startup")
        def start_wechat_bridge():
            try:
                app.state.wechat_bridge.start()
            except Exception:
                LOGGER.exception("内置微信桥启动失败")

        @app.on_event("shutdown")
        def stop_wechat_bridge():
            app.state.wechat_bridge.stop()

    @app.on_event("startup")
    def start_wecom_aibot():
        try:
            app.state.wecom_aibot.start()
        except Exception:
            LOGGER.exception("企业微信智能机器人启动失败")

    @app.on_event("shutdown")
    def stop_wecom_aibot():
        app.state.wecom_aibot.stop()

    if start_scheduler:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(timezone=TZ)
        scheduler.add_job(_send_due_reminders, "interval", minutes=1, args=[repo], max_instances=1)
        scheduler.add_job(_check_patrol_warning_monitor, "interval", minutes=1, args=[repo], max_instances=1)
        if TUNNEL_MECHANICAL_KEEPALIVE_ENABLED:
            scheduler.add_job(
                _keepalive_tunnel_mechanical_login,
                "interval",
                minutes=TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES,
                args=[repo],
                max_instances=1,
            )
        scheduler.add_job(
            _cleanup_uploads_job,
            "interval",
            hours=24,
            args=[uploads],
            max_instances=1,
            next_run_time=datetime.now(TZ),
        )

        @app.on_event("startup")
        async def start_jobs():
            scheduler.start()

        @app.on_event("shutdown")
        async def stop_jobs():
            scheduler.shutdown(wait=False)

    return app


def _resolve_upload_path(source_image_path: str, uploads: Path) -> Path:
    safe_name = Path(source_image_path).name
    target = (uploads / safe_name).resolve()
    upload_root = uploads.resolve()
    if upload_root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="原图不存在")
    return target


def _validate_hhmm(value: str) -> str:
    text = str(value or "").strip()
    if not HHMM_PATTERN.match(text):
        raise ValueError("时间必须是 HH:MM 格式")
    return text


def _coerce_hhmm(value: str, default: str) -> str:
    text = str(value or "").strip()
    return text if HHMM_PATTERN.match(text) else default


def _has_unconfirmed_roster_names(grid: list[dict[str, Any]]) -> bool:
    return any(
        not str(row.get("name") or "").strip() or re.fullmatch(r"第\d+行", str(row.get("name") or "").strip())
        for row in grid
    )


def _normalize_roster_ocr_names(repo: DutyRepository, result: dict[str, Any]) -> dict[str, Any]:
    """Correct only unambiguous OCR name typos against existing names.

    OCR can turn one Chinese character into a visually similar character, for
    example ``沐`` into ``沫``. Keeping that typo would create a new person and
    break reminders. Unknown or ambiguous names remain untouched and still
    require manual confirmation.
    """

    grid = list(result.get("grid") or [])
    if not grid:
        return result
    known: set[str] = set()
    try:
        known.update(str(name or "").strip() for name in repo.list_personnel_names())
        for roster in repo.list_roster_months():
            known.update(str(row.get("name") or "").strip() for row in roster.get("grid") or [])
    except Exception:
        LOGGER.exception("排班姓名 OCR 纠正读取已有人员失败")
    known = {name for name in known if name and not re.fullmatch(r"第\d+行", name)}
    if not known:
        return result

    corrections: list[dict[str, str]] = []
    normalized_grid: list[dict[str, Any]] = []
    for row in grid:
        next_row = {**row}
        raw_name = str(row.get("name") or "").strip()
        matched = _match_known_roster_name(raw_name, known)
        if matched and matched != raw_name:
            next_row["name"] = matched
            corrections.append({"before": raw_name, "after": matched})
        normalized_grid.append(next_row)
    return {**result, "grid": normalized_grid, "name_corrections": corrections}


def _apply_roster_role_semantics(repo: DutyRepository, result: dict[str, Any]) -> dict[str, Any]:
    """Translate ambiguous white ``中`` cells after the row name is known.

    The image parser deliberately keeps these cells as blank plus metadata: a
    white ``中`` is visually the same as an ordinary empty cell, and the
    meaning depends on the configured role.  Role names come from the user's
    personnel configuration, never from hard-coded people.
    """

    grid = [dict(row) for row in list(result.get("grid") or [])]
    if not grid:
        return result
    config = repo.get_daily_duty_config()
    station_names = _configured_name_set(config.get("station_names"))
    office_names = _configured_name_set(config.get("office_names"))
    big_driver_names = _configured_name_set(config.get("big_driver_names"))
    small_driver_names = _configured_name_set(config.get("small_driver_names"))
    patrol_names = _configured_patrol_name_set(config)
    inferred_patrol_rows: list[int] = []
    if not patrol_names:
        blocked_names = station_names | office_names
        inferred_patrol_rows = _infer_patrol_rows_from_white_middle(grid, blocked_names)
        patrol_names = {
            str(grid[row_index].get("name") or "").strip()
            for row_index in inferred_patrol_rows
            if str(grid[row_index].get("name") or "").strip()
        }
    if not (station_names or office_names or patrol_names):
        return {**result, "grid": grid}

    role_by_name = {name: "patrol" for name in patrol_names}
    role_by_name.update({name: "office" for name in office_names})
    role_by_name.update({name: "station" for name in station_names})
    patrol_rows = inferred_patrol_rows or [
        index for index, row in enumerate(grid) if role_by_name.get(str(row.get("name") or "").strip()) == "patrol"
    ]
    patrol_modes = _infer_patrol_white_cell_modes(grid, patrol_rows)
    changed = False
    for row_index, row in enumerate(grid):
        name = str(row.get("name") or "").strip()
        role = role_by_name.get(name)
        if role not in {"office", "station", "patrol"}:
            continue
        next_row = {**row, "days": dict(row.get("days", {})), "cell_meta": dict(row.get("cell_meta", {}))}
        for day, meta in next_row["cell_meta"].items():
            if not (isinstance(meta, dict) and meta.get("white_middle")):
                continue
            if role == "office":
                label = "办"
            elif role == "station":
                label = "-"
            else:
                label = patrol_modes.get((row_index, str(day)), "巡")
            if next_row["days"].get(str(day), "") != label:
                next_row["days"][str(day)] = label
                changed = True
        grid[row_index] = next_row
    return {**result, "grid": grid, "role_semantics_applied": changed}


def _configured_name_set(values: Any) -> set[str]:
    return {str(value or "").strip() for value in list(values or []) if str(value or "").strip()}


def _configured_patrol_name_set(config: dict[str, Any]) -> set[str]:
    names = _configured_name_set(config.get("patrol_team_names"))
    for group in list(config.get("patrol_team_groups") or []):
        if not isinstance(group, dict):
            continue
        names.update(_configured_name_set(group.get("members") or group.get("names")))
    return names


def _infer_patrol_rows_from_white_middle(grid: list[dict[str, Any]], blocked_names: set[str]) -> list[int]:
    inferred: list[int] = []
    for row_index, row in enumerate(grid):
        name = str(row.get("name") or "").strip()
        if not name or name in blocked_names:
            continue
        cell_meta = dict(row.get("cell_meta", {}))
        if any(isinstance(meta, dict) and meta.get("white_middle") for meta in cell_meta.values()):
            inferred.append(row_index)
    return inferred


def _infer_patrol_white_cell_modes(
    grid: list[dict[str, Any]], patrol_rows: list[int]
) -> dict[tuple[int, str], str]:
    """Infer patrol/standby from each configured team's repeated roster block.

    A patrol team is represented by consecutive configured rows.  In a block,
    the largest contiguous run of real monitor codes (早/中/晚) is the monitor
    phase; ambiguous white cells inside the same phase are standby, while the
    other ambiguous work phase is patrol.  This handles per-person standby
    exceptions without using names or calendar-specific constants.
    """

    if not patrol_rows:
        return {}
    modes: dict[tuple[int, str], str] = {}
    groups: list[list[int]] = []
    current: list[int] = []
    for row_index in patrol_rows:
        if current and row_index != current[-1] + 1:
            groups.append(current)
            current = []
        current.append(row_index)
    if current:
        groups.append(current)

    split_groups: list[list[int]] = []
    for group in groups:
        split_groups.extend(_split_patrol_rows_by_rest_pattern(grid, group))

    for group in split_groups:
        day_count = max((len(dict(grid[index].get("days", {}))) for index in group), default=0)
        monitor_days = {
            day
            for day in range(1, day_count + 1)
            if any(str(dict(grid[index].get("days", {})).get(str(day), "")).strip() in {"早", "中", "晚", "夜"} for index in group)
        }
        monitor_runs = _contiguous_day_runs(monitor_days)
        for row_index in group:
            row = grid[row_index]
            for raw_day, meta in dict(row.get("cell_meta", {})).items():
                if not isinstance(meta, dict) or not meta.get("white_middle"):
                    continue
                try:
                    day = int(raw_day)
                except (TypeError, ValueError):
                    continue
                in_monitor_phase = any(start <= day <= end for start, end in monitor_runs)
                modes[(row_index, str(raw_day))] = "备" if in_monitor_phase else "巡"
    return modes


def _split_patrol_rows_by_rest_pattern(grid: list[dict[str, Any]], rows: list[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    previous_rest: set[int] | None = None
    for row_index in rows:
        rest_days = _row_rest_days(grid[row_index])
        if current and not _similar_rest_days(previous_rest or set(), rest_days):
            groups.append(current)
            current = []
        current.append(row_index)
        previous_rest = rest_days
    if current:
        groups.append(current)
    return groups


def _row_rest_days(row: dict[str, Any]) -> set[int]:
    days: set[int] = set()
    for raw_day, code in dict(row.get("days", {})).items():
        if not _is_rest_code(str(code)):
            continue
        try:
            days.add(int(raw_day))
        except (TypeError, ValueError):
            continue
    return days


def _similar_rest_days(left: set[int], right: set[int]) -> bool:
    if not left or not right:
        return True
    return len(left & right) / max(1, min(len(left), len(right))) >= 0.5


def _contiguous_day_runs(days: set[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for day in sorted(days):
        if start is None:
            start = previous = day
            continue
        if previous is not None and day == previous + 1:
            previous = day
            continue
        runs.append((start, previous if previous is not None else start))
        start = previous = day
    if start is not None:
        runs.append((start, previous if previous is not None else start))
    return runs


def _match_known_roster_name(raw_name: str, known_names: set[str]) -> str:
    if not raw_name or re.fullmatch(r"第\d+行", raw_name) or raw_name in known_names:
        return raw_name
    candidates = [name for name in known_names if len(name) == len(raw_name)]
    if not candidates:
        return raw_name
    scored = sorted((_levenshtein_distance(raw_name, name), name) for name in candidates)
    best_distance = scored[0][0]
    best = [name for distance, name in scored if distance == best_distance]
    return best[0] if best_distance == 1 and len(best) == 1 else raw_name


def _levenshtein_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current = [row_index]
        for column_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column_index] + 1,
                    previous[column_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _sanitize_roster_grid_for_month(grid: list[dict[str, Any]], year: int, month: int) -> list[dict[str, Any]]:
    max_day = calendar.monthrange(int(year), int(month))[1]
    sanitized: list[dict[str, Any]] = []
    for row in grid:
        days = dict(row.get("days", {}))
        boxes = dict(row.get("boxes", {}))
        next_row = {**row}
        next_row["days"] = {str(day): value for day, value in days.items() if _is_valid_roster_day(str(day), max_day)}
        if boxes:
            next_row["boxes"] = {str(day): value for day, value in boxes.items() if _is_valid_roster_day(str(day), max_day)}
        cell_meta = dict(row.get("cell_meta", {}))
        if cell_meta:
            next_row["cell_meta"] = {
                str(day): value for day, value in cell_meta.items() if _is_valid_roster_day(str(day), max_day)
            }
        sanitized.append(next_row)
    return sanitized


def _is_valid_roster_day(day: str, max_day: int) -> bool:
    if not day.isdigit():
        return False
    value = int(day)
    return 1 <= value <= max_day


def _parse_hhmm(value: str):
    from datetime import time

    text = _validate_hhmm(value)
    hour, minute = text.split(":", 1)
    return time(int(hour), int(minute))


def _save_upload_file(file: UploadFile, target: Path) -> None:
    bytes_written = 0
    with target.open("wb") as output:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail=f"图片不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
            output.write(chunk)


def _save_roster_upload(file: UploadFile, uploads: Path) -> Path:
    suffix = Path(file.filename or "roster.png").suffix.lower() or ".png"
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail="仅支持 jpg、png、webp、bmp 图片")
    if file.content_type and file.content_type.lower() not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="上传文件类型不是图片")
    target = uploads / f"{uuid.uuid4().hex}{suffix}"
    try:
        _save_upload_file(file, target)
        _cleanup_old_uploads(uploads)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _save_roster_upload_bytes(filename: str, content_type: str, content: bytes, uploads: Path) -> Path:
    suffix = Path(filename or "roster.png").suffix.lower() or ".png"
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        suffix = ".png"
    if content_type and content_type.lower() not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="上传文件类型不是图片")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"图片不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    target = uploads / f"{uuid.uuid4().hex}{suffix}"
    try:
        uploads.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        _cleanup_old_uploads(uploads)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _cleanup_old_uploads(uploads: Path) -> dict[str, int]:
    uploads.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.now(TZ).timestamp()
    scanned = 0
    deleted = 0
    deleted_bytes = 0
    for path in uploads.iterdir():
        if not path.is_file():
            continue
        scanned += 1
        keep_days = GENERATED_UPLOAD_KEEP_DAYS if _is_generated_upload(path.name) else UPLOAD_KEEP_DAYS
        if keep_days <= 0:
            continue
        try:
            stat = path.stat()
            expired = stat.st_mtime < now_ts - keep_days * 86400
        except FileNotFoundError:
            continue
        if expired:
            path.unlink(missing_ok=True)
            deleted += 1
            deleted_bytes += stat.st_size
    return {"scanned": scanned, "deleted": deleted, "deleted_bytes": deleted_bytes}


def _is_generated_upload(filename: str) -> bool:
    return filename.startswith(
        (
            "wechat-query-",
            "daily-duty-query-",
            "patrol-record-",
            "tunnel-mechanical-result-",
            "construction-doc-",
            "notification-detail-",
        )
    ) or bool(
        re.match(r"^\d{4}年\d{1,2}月\d{1,2}日.+\.docx$", filename)
    )


def _upload_storage_status(uploads: Path) -> dict[str, Any]:
    uploads.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.now(TZ).timestamp()
    total_count = 0
    total_size = 0
    generated_count = 0
    generated_size = 0
    expired_generated = 0
    expired_regular = 0
    for path in uploads.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        total_count += 1
        total_size += stat.st_size
        is_generated = _is_generated_upload(path.name)
        keep_days = GENERATED_UPLOAD_KEEP_DAYS if is_generated else UPLOAD_KEEP_DAYS
        if is_generated:
            generated_count += 1
            generated_size += stat.st_size
        if keep_days > 0 and stat.st_mtime < now_ts - keep_days * 86400:
            if is_generated:
                expired_generated += 1
            else:
                expired_regular += 1
    return {
        "path": str(uploads),
        "total_count": total_count,
        "total_size": total_size,
        "generated_count": generated_count,
        "generated_size": generated_size,
        "regular_count": total_count - generated_count,
        "regular_size": total_size - generated_size,
        "expired_generated_count": expired_generated,
        "expired_regular_count": expired_regular,
        "generated_keep_days": GENERATED_UPLOAD_KEEP_DAYS,
        "upload_keep_days": UPLOAD_KEEP_DAYS,
    }


def _list_database_backups(repo: DutyRepository, limit: int = 30) -> list[dict[str, Any]]:
    backup_dir = repo.db_path.parent / "backups"
    if not backup_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in backup_dir.iterdir():
        if not path.is_file() or path.suffix.lower() != ".db":
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        items.append(
            {
                "filename": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, TZ).isoformat(),
                "download_url": f"/api/config/backups/{path.name}",
            }
        )
    return sorted(items, key=lambda item: str(item["created_at"]), reverse=True)[:limit]


def _cleanup_uploads_job(uploads: Path) -> None:
    try:
        _cleanup_old_uploads(uploads)
    except Exception:
        LOGGER.exception("清理上传图片失败")


def _is_authorized(header: str, username: str, password: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    supplied_username, separator, supplied_password = decoded.partition(":")
    return (
        bool(separator)
        and secrets.compare_digest(supplied_username, username)
        and secrets.compare_digest(supplied_password, password)
    )


def _is_request_authorized(request: Request, username: str, password: str, session_secret: str) -> bool:
    if _is_authorized(request.headers.get("authorization", ""), username, password):
        return True
    return _verify_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""), username, session_secret)


def _is_wechat_internal_api_request(request: Request) -> bool:
    if request.url.path not in {
        "/api/wechat-query",
        "/api/wechat-roster/import",
        "/api/wechat-roster/confirm",
    }:
        return False
    token = _wechat_query_token()
    if not token:
        return True
    auth = str(request.headers.get("authorization") or "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = str(request.headers.get("x-duty-query-token") or bearer).strip()
    return bool(supplied) and secrets.compare_digest(supplied, token)


def _create_session_token(username: str, secret: str, max_age_seconds: int) -> str:
    expires_at = int(time.time()) + max_age_seconds
    payload = f"{username}|{expires_at}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}|{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii")


def _verify_session_token(token: str, username: str, secret: str) -> bool:
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except Exception:
        return False
    supplied_username, expires_text, supplied_signature = (decoded.split("|", 2) + ["", "", ""])[:3]
    if not supplied_username or not expires_text or not supplied_signature:
        return False
    try:
        expires_at = int(expires_text)
    except ValueError:
        return False
    if expires_at < int(time.time()):
        return False
    payload = f"{supplied_username}|{expires_at}"
    expected_signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return secrets.compare_digest(supplied_username, username) and secrets.compare_digest(supplied_signature, expected_signature)


def _safe_next_url(next_url: str) -> str:
    text = str(next_url or "/").strip()
    if not text.startswith("/") or text.startswith("//"):
        return "/"
    return text


def _normalize_notification_sender_type(value: str) -> str:
    normalized = str(value or "wecom_webhook").strip().lower()
    # 旧通知通道已停用，旧配置统一回落到企业微信群机器人。
    return normalized if normalized in {"wecom_webhook", "wecom_app"} else "wecom_webhook"


def _normalize_notification_mention_mode(value: str) -> str:
    normalized = str(value or "person").strip().lower()
    return normalized if normalized in {"none", "all", "person", "custom"} else "person"


def _env_notification_config_defaults() -> dict[str, Any]:
    return {
        "sender_type": os.getenv("NOTIFICATION_SENDER_TYPE", "").strip(),
        "webhook_url": os.getenv("WECOM_WEBHOOK_URL", "").strip(),
        "wecom_aibot_enabled": os.getenv("WECOM_AIBOT_ENABLED", "").strip(),
        "wecom_aibot_id": os.getenv("WECOM_AIBOT_ID", "").strip(),
        "wecom_aibot_secret": os.getenv("WECOM_AIBOT_SECRET", "").strip(),
        "wecom_app_enabled": os.getenv("WECOM_APP_ENABLED", "").strip(),
        "wecom_app_corp_id": os.getenv("WECOM_APP_CORP_ID", "").strip() or os.getenv("WECOM_CORP_ID", "").strip(),
        "wecom_app_agent_id": os.getenv("WECOM_APP_AGENT_ID", "").strip() or os.getenv("WECOM_AGENT_ID", "").strip(),
        "wecom_app_secret": os.getenv("WECOM_APP_SECRET", "").strip() or os.getenv("WECOM_CORP_SECRET", "").strip(),
        "wecom_app_token": os.getenv("WECOM_APP_TOKEN", "").strip(),
        "wecom_app_encoding_aes_key": os.getenv("WECOM_APP_ENCODING_AES_KEY", "").strip(),
        # 旧个人微信/LightAgent 环境变量不再参与默认配置。
        "lightagent_url": "",
        "lightagent_token": "",
        "lightagent_target": "",
        "lightagent_targets": [],
        "mention_mode": os.getenv("NOTIFICATION_MENTION_MODE", "").strip(),
        "mention_targets": os.getenv("NOTIFICATION_MENTION_TARGETS", "").strip(),
    }


def _notification_config_with_env_defaults(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    env_config = _env_notification_config_defaults()
    sender_type = _normalize_notification_sender_type(str(merged.get("sender_type") or "wecom_webhook"))
    lightagent_targets = _normalize_feature_channel_rooms(merged.get("lightagent_targets"))
    legacy_target = str(merged.get("lightagent_target", "")).strip()
    if legacy_target:
        lightagent_targets = _normalize_feature_channel_rooms(lightagent_targets + [{"id": legacy_target}])
    has_wecom_aibot_config = bool(
        merged.get("wecom_aibot_enabled")
        and str(merged.get("wecom_aibot_id") or "").strip()
        and str(merged.get("wecom_aibot_secret") or "").strip()
    )
    has_active_config = (
        bool(str(merged.get("webhook_url", "")).strip()) or has_wecom_aibot_config
        if sender_type == "wecom_webhook"
        else bool(lightagent_targets and (wechat_bridge_enabled() or str(merged.get("lightagent_url", "")).strip()))
    )
    env_sender_type = _normalize_notification_sender_type(env_config["sender_type"]) if env_config["sender_type"] else ""
    stored_sender_type = str(merged.get("sender_type") or "").strip()
    env_can_select_sender = bool(env_sender_type and not stored_sender_type and not has_active_config)
    if env_can_select_sender:
        sender_type = env_sender_type
        merged["sender_type"] = sender_type

    for key in ("webhook_url", "lightagent_url", "lightagent_token", "lightagent_target", "mention_mode", "mention_targets"):
        if env_config[key] and (env_can_select_sender or not str(merged.get(key, "")).strip()):
            merged[key] = env_config[key]
    for key in ("wecom_aibot_id", "wecom_aibot_secret"):
        if env_config[key] and not str(merged.get(key, "")).strip():
            merged[key] = env_config[key]
    if env_config["wecom_aibot_enabled"]:
        merged["wecom_aibot_enabled"] = env_config["wecom_aibot_enabled"].lower() in {"1", "true", "yes", "on"}
    for key in ("wecom_app_corp_id", "wecom_app_agent_id", "wecom_app_secret", "wecom_app_token", "wecom_app_encoding_aes_key"):
        if env_config[key] and not str(merged.get(key, "")).strip():
            merged[key] = env_config[key]
    if env_config["wecom_app_enabled"]:
        merged["wecom_app_enabled"] = env_config["wecom_app_enabled"].lower() in {"1", "true", "yes", "on"}
    # 旧个人微信/LightAgent 通道已下线：无论数据库或环境变量里是否有旧值，对外和调度都视为未配置。
    merged["lightagent_url"] = ""
    merged["lightagent_token"] = ""
    merged["lightagent_target"] = ""
    merged["lightagent_targets"] = []
    return merged


def _env_feature_channel_config_defaults() -> dict[str, Any]:
    env_room_ids = _split_env_list(os.getenv("FEATURE_CHANNEL_WECHAT_GROUP_ROOM_IDS", ""))
    env_room_names = _split_env_list(os.getenv("FEATURE_CHANNEL_WECHAT_GROUP_ROOM_NAMES", ""))
    env_rooms = _normalize_feature_channel_rooms(
        [
            {"id": room_id, "name": env_room_names[index] if index < len(env_room_names) else ""}
            for index, room_id in enumerate(env_room_ids)
        ]
    )
    single_room_id = os.getenv("FEATURE_CHANNEL_WECHAT_GROUP_ROOM_ID", "").strip()
    single_room_name = os.getenv("FEATURE_CHANNEL_WECHAT_GROUP_ROOM_NAME", "").strip()
    if single_room_id:
        env_rooms = _normalize_feature_channel_rooms(env_rooms + [{"id": single_room_id, "name": single_room_name}])
    return {
        "lightagent_web_url": (
            os.getenv("FEATURE_CHANNEL_LIGHTAGENT_WEB_URL", "").strip()
            or os.getenv("LIGHTAGENT_WEB_URL", "").strip()
            or os.getenv("LIGHTAGENT_BASE_URL", "").strip()
        ),
        "lightagent_web_password": (
            os.getenv("FEATURE_CHANNEL_LIGHTAGENT_WEB_PASSWORD", "").strip()
            or os.getenv("LIGHTAGENT_WEB_PASSWORD", "").strip()
            or os.getenv("LIGHTAGENT_PASSWORD", "").strip()
        ),
        "wechat_group_room_id": single_room_id,
        "wechat_group_room_name": single_room_name,
        "wechat_group_rooms": env_rooms,
    }


def _feature_channel_config_with_env_defaults(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    env_config = _env_feature_channel_config_defaults()
    for key, value in env_config.items():
        if key == "wechat_group_rooms":
            if value and not _normalize_feature_channel_rooms(merged.get("wechat_group_rooms")):
                merged[key] = value
            continue
        if value and not str(merged.get(key, "")).strip():
            merged[key] = value
    for key in ("enabled", "allow_tunnel_mechanical", "allow_duty_query", "allow_roster_import"):
        merged[key] = True
    merged["wechat_group_rooms"] = _feature_channel_config_rooms(merged)
    return merged


def _split_env_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]", str(value or "")) if item.strip()]


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


def _feature_channel_config_rooms(config: dict[str, Any]) -> list[dict[str, str]]:
    rooms = _normalize_feature_channel_rooms(config.get("wechat_group_rooms"))
    legacy_room_id = str(config.get("wechat_group_room_id") or "").strip()
    if legacy_room_id:
        rooms = _normalize_feature_channel_rooms(
            [
                {
                    "id": legacy_room_id,
                    "name": str(config.get("wechat_group_room_name") or "").strip(),
                },
                *rooms,
            ]
        )
    return rooms


def _feature_channel_config_room_ids(config: dict[str, Any]) -> set[str]:
    return {room["id"] for room in _feature_channel_config_rooms(config) if room.get("id")}


def _feature_channel_config_room_label(config: dict[str, Any]) -> str:
    rooms = _feature_channel_config_rooms(config)
    if not rooms:
        return ""
    names = [room.get("name") or room.get("id") or "" for room in rooms]
    return "、".join([name for name in names if name])


def _feature_channel_bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _public_feature_channel_config(config: dict[str, Any]) -> dict[str, Any]:
    config = _feature_channel_config_with_env_defaults(config)
    rooms = _feature_channel_config_rooms(config)
    primary_room = rooms[0] if rooms else {}
    return {
        "enabled": True,
        "wechat_bridge_enabled": False,
        "lightagent_web_url": str(config.get("lightagent_web_url") or ""),
        "lightagent_web_password_configured": bool(str(config.get("lightagent_web_password") or "").strip()),
        "wechat_group_room_id": str(primary_room.get("id") or ""),
        "wechat_group_room_name": str(primary_room.get("name") or ""),
        "wechat_group_rooms": rooms,
        "allow_tunnel_mechanical": True,
        "allow_duty_query": True,
        "allow_roster_import": True,
        "configured": bool(rooms),
    }


def _normalize_wechat_trigger_list(values: Any, defaults: list[str]) -> list[str]:
    if isinstance(values, str):
        candidates = re.split(r"[\n,，;；、]+", values)
    elif isinstance(values, list):
        candidates = values
    else:
        candidates = []
    normalized: list[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    if normalized:
        return normalized
    return list(defaults)


def _normalize_wechat_template_text(value: Any, default: str, *legacy_defaults: str) -> str:
    text = str(value or "").strip()
    if not text or text in set(legacy_defaults):
        return default
    return text


def _wechat_interaction_config(repo: DutyRepository) -> dict[str, Any]:
    raw = repo.get_wechat_interaction_config()
    return {
        "patrol_record_triggers": _normalize_wechat_trigger_list(raw.get("patrol_record_triggers"), DEFAULT_PATROL_RECORD_TRIGGERS),
        "patrol_record_template": _normalize_wechat_template_text(
            raw.get("patrol_record_template"),
            DEFAULT_PATROL_RECORD_TEMPLATE,
            LEGACY_PATROL_RECORD_TEMPLATE,
        ),
        "tunnel_template_triggers": _normalize_wechat_trigger_list(raw.get("tunnel_template_triggers"), DEFAULT_TUNNEL_TEMPLATE_TRIGGERS),
        "tunnel_template": _normalize_wechat_template_text(
            raw.get("tunnel_template"),
            DEFAULT_TUNNEL_TEMPLATE,
            LEGACY_TUNNEL_TEMPLATE,
        ),
        "tunnel_modify_template_triggers": _normalize_wechat_trigger_list(raw.get("tunnel_modify_template_triggers"), DEFAULT_TUNNEL_MODIFY_TEMPLATE_TRIGGERS),
        "tunnel_modify_template": _normalize_wechat_template_text(
            raw.get("tunnel_modify_template"),
            DEFAULT_TUNNEL_MODIFY_TEMPLATE,
            LEGACY_TUNNEL_MODIFY_TEMPLATE,
        ),
    }


def _public_wechat_interaction_config(repo: DutyRepository) -> dict[str, Any]:
    config = _wechat_interaction_config(repo)
    rooms: list[dict[str, str]] = []
    return {
        **config,
        "defaults": copy.deepcopy(DEFAULT_WECHAT_INTERACTION_CONFIG),
        "notification_rooms": rooms,
        "menu_preview": _wechat_query_help_text(),
    }


def _wecom_app_menu_payload(repo: DutyRepository | None = None) -> dict[str, Any]:
    groups = _wecom_app_menu_groups(repo)
    menu = {"button": []}
    for group_index, group in enumerate(groups):
        button = {"name": group["name"], "sub_button": []}
        for item_index, item in enumerate(group["items"]):
            button["sub_button"].append(
                _wecom_app_menu_click_button(item["name"], _wecom_app_menu_key(group_index, item_index, item))
            )
        menu["button"].append(button)
    _validate_wecom_app_menu_payload(menu)
    return menu


def _wecom_app_menu_click_button(name: str, key: str) -> dict[str, str]:
    return {"type": "click", "name": name, "key": key}


def _wecom_app_menu_key(group_index: int, item_index: int, item: dict[str, Any] | None = None) -> str:
    command = str((item or {}).get("command") or "").strip()
    return WECOM_APP_MENU_COMMAND_KEYS.get(command) or f"DR_CUSTOM_{group_index}_{item_index}"


def _wecom_app_menu_command(value: str, repo: DutyRepository | None = None) -> str:
    text = str(value or "").strip()
    if text in WECOM_APP_MENU_COMMANDS:
        return WECOM_APP_MENU_COMMANDS[text]
    custom_match = re.fullmatch(r"DR_CUSTOM_(\d+)_(\d+)", text)
    if custom_match and repo is not None:
        groups = _wecom_app_menu_groups(repo)
        group_index = int(custom_match.group(1))
        item_index = int(custom_match.group(2))
        if 0 <= group_index < len(groups):
            items = groups[group_index]["items"]
            if 0 <= item_index < len(items):
                return str(items[item_index].get("command") or "").strip()
    legacy_match = re.fullmatch(r"DR_MENU_(\d+)_(\d+)", text)
    if legacy_match and repo is not None:
        raw_command = _wecom_app_raw_menu_command(repo, int(legacy_match.group(1)), int(legacy_match.group(2)))
        if raw_command:
            return raw_command
        current_command = _wecom_app_group_menu_command(repo, int(legacy_match.group(1)), int(legacy_match.group(2)))
        if current_command:
            return current_command
    if text in WECOM_APP_LEGACY_INDEX_MENU_COMMANDS:
        return WECOM_APP_LEGACY_INDEX_MENU_COMMANDS[text]
    return text


def _wecom_app_group_menu_command(repo: DutyRepository, group_index: int, item_index: int) -> str:
    try:
        groups = _wecom_app_menu_groups(repo)
    except Exception:
        return ""
    if not (0 <= group_index < len(groups)):
        return ""
    items = groups[group_index].get("items") or []
    if not (0 <= item_index < len(items)):
        return ""
    return str(items[item_index].get("command") or "").strip()


def _wecom_app_raw_menu_command(repo: DutyRepository, group_index: int, item_index: int) -> str:
    try:
        raw_groups = repo.get_wecom_app_menu_config()
    except Exception:
        return ""
    if not isinstance(raw_groups, list) or not (0 <= group_index < len(raw_groups)):
        return ""
    raw_group = raw_groups[group_index]
    if not isinstance(raw_group, dict):
        return ""
    raw_items = raw_group.get("items")
    if not isinstance(raw_items, list) or not (0 <= item_index < len(raw_items)):
        return ""
    raw_item = raw_items[item_index]
    if not isinstance(raw_item, dict):
        return ""
    return str(raw_item.get("command") or "").strip()


def _wecom_app_menu_groups(repo: DutyRepository | None = None) -> list[dict[str, Any]]:
    raw = repo.get_wecom_app_menu_config() if repo is not None else []
    groups = _normalize_wecom_app_menu_groups(raw or DEFAULT_WECOM_APP_MENU_GROUPS, allow_empty=False)
    for group in groups:
        if group.get("name") == "机电预警":
            forced = [
                {"name": "录入今日机电", "command": "录入今日机电"},
                {"name": "今日机电", "command": "查询今日机电"},
            ]
            forced_commands = {item["command"] for item in forced}
            items = [item for item in group.get("items", []) if str(item.get("command") or "").strip() not in forced_commands]
            group["items"] = [*forced, *items][: WECOM_APP_MENU_LIMITS["max_sub_buttons"]]
        if group.get("name") == "更多查询":
            removed_commands = {"查询今日机电", "查询我的绑定", "导入排班", "施工图片", "施工点维护"}
            forced = [
                {"name": "施工图片", "command": "施工图片"},
                {"name": "施工点维护", "command": "施工点维护"},
            ]
            items = [item for item in group.get("items", []) if str(item.get("command") or "").strip() not in removed_commands]
            group["items"] = [
                *forced,
                *items[: max(0, WECOM_APP_MENU_LIMITS["max_sub_buttons"] - len(forced) - 1)],
                {"name": "导入排班", "command": "导入排班"},
            ]
    return groups


def _normalize_wecom_app_menu_groups(groups: Any, *, allow_empty: bool) -> list[dict[str, Any]]:
    if not isinstance(groups, list):
        raise HTTPException(status_code=400, detail="自建应用菜单格式不正确")
    normalized: list[dict[str, Any]] = []
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            continue
        group_name = str(raw_group.get("name") or "").strip()
        raw_items = raw_group.get("items") or []
        if not group_name and not raw_items:
            continue
        if not group_name:
            raise HTTPException(status_code=400, detail="一级菜单名称不能为空")
        items: list[dict[str, str]] = []
        if not isinstance(raw_items, list):
            raise HTTPException(status_code=400, detail=f"一级菜单“{group_name}”的二级菜单格式不正确")
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_name = str(raw_item.get("name") or "").strip()
            command = str(raw_item.get("command") or "").strip()
            if not item_name and not command:
                continue
            if not item_name or not command:
                raise HTTPException(status_code=400, detail=f"一级菜单“{group_name}”下的二级菜单名称和命令都不能为空")
            items.append({"name": item_name, "command": command})
        if not items:
            raise HTTPException(status_code=400, detail=f"一级菜单“{group_name}”至少需要 1 个二级菜单")
        normalized.append({"name": group_name, "items": items})
    if not normalized and not allow_empty:
        normalized = copy.deepcopy(DEFAULT_WECOM_APP_MENU_GROUPS)
    _validate_wecom_app_menu_groups(normalized)
    return normalized


def _is_wecom_app_query(query: WechatQueryRequest) -> bool:
    return str(query.channel or "").strip() == "wecom_app"


def _wecom_app_bind_required_response(repo: DutyRepository, query: WechatQueryRequest) -> dict[str, Any]:
    examples = _wechat_query_known_person_names(repo)
    example = examples[0] if examples else "商邱宏"
    sender_name = _clean_wechat_member_display_name(
        str(query.sender_name or ""),
        str(query.runtime_sender_id or query.sender_id or ""),
    )
    suffix = f"\n当前企业微信成员：{sender_name}" if sender_name else ""
    return {
        "success": False,
        "query_type": "unbound",
        "reply": (
            "首次使用企业微信自建应用请先绑定姓名，后面才能按你的名字生成监控提醒和机电/预警模板。\n"
            f"请直接发送：绑定{example}"
            f"{suffix}"
        ),
    }


def _wechat_query_bound_person_name(repo: DutyRepository, query: WechatQueryRequest) -> str:
    person = _person_for_wechat_query(repo, query)
    return str(person.get("name") or "").strip() if person else ""


def _personalize_recorder_field(text: str, person_name: str) -> str:
    name = str(person_name or "").strip()
    if not name:
        return str(text or "")
    value = str(text or "")
    updated, count = re.subn(r"(记录人\s*)[^\s，,。；;]+", lambda match: f"{match.group(1)}{name}", value, count=1)
    return updated if count else value


def _personalize_checker_field(text: str, person_name: str) -> str:
    name = str(person_name or "").strip()
    if not name:
        return str(text or "")
    value = str(text or "")
    updated, count = re.subn(r"(负责人\s*)[^\s，,。；;]+", lambda match: f"{match.group(1)}{name}", value, count=1)
    return updated if count else value


def _personalize_patrol_record_template(text: str, person_name: str) -> str:
    name = str(person_name or "").strip()
    if not name:
        return str(text or "")
    value = str(text or "")
    updated, count = re.subn(
        r"((?:查询|查)?)[^\s，,。；;]+(巡查记录)",
        lambda match: f"{match.group(1)}{name}{match.group(2)}",
        value,
        count=1,
    )
    return updated if count else value


def _validate_wecom_app_menu_payload(menu: dict[str, Any]) -> None:
    buttons = menu.get("button") or []
    limits = WECOM_APP_MENU_LIMITS
    if len(buttons) > limits["max_top_buttons"]:
        raise ValueError("企业微信自建应用一级菜单最多只能创建 3 个")
    for button in buttons:
        top_name = str(button.get("name") or "")
        if len(top_name.encode("utf-8")) > limits["max_top_name_bytes"]:
            raise ValueError(f"企业微信自建应用一级菜单“{top_name}”超过 16 字节")
        sub_buttons = button.get("sub_button") or []
        if len(sub_buttons) > limits["max_sub_buttons"]:
            raise ValueError(f"企业微信自建应用“{top_name}”二级菜单最多只能创建 5 个")
        for sub_button in sub_buttons:
            sub_name = str(sub_button.get("name") or "")
            if len(sub_name.encode("utf-8")) > limits["max_sub_name_bytes"]:
                raise ValueError(f"企业微信自建应用二级菜单“{sub_name}”超过 40 字节")


def _validate_wecom_app_menu_groups(groups: list[dict[str, Any]]) -> None:
    limits = WECOM_APP_MENU_LIMITS
    if len(groups) > limits["max_top_buttons"]:
        raise HTTPException(status_code=400, detail="企业微信自建应用一级菜单最多只能创建 3 个")
    for group in groups:
        group_name = str(group.get("name") or "")
        if len(group_name.encode("utf-8")) > limits["max_top_name_bytes"]:
            raise HTTPException(status_code=400, detail=f"企业微信自建应用一级菜单“{group_name}”超过 16 字节")
        items = group.get("items") or []
        if len(items) > limits["max_sub_buttons"]:
            raise HTTPException(status_code=400, detail=f"企业微信自建应用“{group_name}”二级菜单最多只能创建 5 个")
        for item in items:
            item_name = str(item.get("name") or "")
            command = str(item.get("command") or "")
            if len(item_name.encode("utf-8")) > limits["max_sub_name_bytes"]:
                raise HTTPException(status_code=400, detail=f"企业微信自建应用二级菜单“{item_name}”超过 40 字节")
            if len(command.encode("utf-8")) > 128:
                raise HTTPException(status_code=400, detail=f"企业微信自建应用二级菜单“{item_name}”命令超过 128 字节")


def _public_wecom_app_menu_preview(repo: DutyRepository | None = None) -> dict[str, Any]:
    groups = _wecom_app_menu_groups(repo)
    payload = _wecom_app_menu_payload(repo)
    public_groups = []
    for group_index, group in enumerate(groups):
        public_groups.append(
            {
                "name": group["name"],
                "items": [
                    {
                        "name": item["name"],
                        "key": _wecom_app_menu_key(group_index, item_index, item),
                        "command": item["command"],
                    }
                    for item_index, item in enumerate(group.get("items", []))
                ],
            }
        )
    return {
        "limits": copy.deepcopy(WECOM_APP_MENU_LIMITS),
        "defaults": copy.deepcopy(DEFAULT_WECOM_APP_MENU_GROUPS),
        "groups": public_groups,
        "payload": payload,
    }


def _public_wechat_interaction_logs(repo: DutyRepository, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del repo
    items: list[dict[str, Any]] = []
    for log in logs:
        reply_text = str(log.get("reply_text") or "").strip()
        error = str(log.get("error") or "").strip()
        items.append(
            {
                "id": log.get("id"),
                "room_id": str(log.get("room_id") or "").strip(),
                "room_name": str(log.get("room_name") or "").strip(),
                "sender_id": str(log.get("sender_id") or "").strip(),
                "sender_name": str(log.get("sender_name") or "").strip(),
                "command_text": str(log.get("command_text") or "").strip(),
                "query_type": str(log.get("query_type") or "").strip(),
                "status": str(log.get("status") or "").strip(),
                "reply_text": reply_text,
                "reply_preview": reply_text[:120],
                "error": error,
                "created_at": str(log.get("created_at") or "").strip(),
            }
        )
    return items


def _save_wechat_interaction_log(
    repo: DutyRepository | None,
    query: WechatQueryRequest,
    result: dict[str, Any],
    *,
    error: str = "",
) -> None:
    if repo is None:
        return
    reply_text = str(result.get("reply") or "").strip()
    repo.save_wechat_interaction_log(
        room_id=str(query.stable_room_id or query.room_id or "").strip(),
        room_name=str(query.room_name or "").strip(),
        sender_id=str(query.runtime_sender_id or query.sender_id or "").strip(),
        sender_name=str(query.sender_name or "").strip(),
        command_text=str(query.text or "").strip(),
        query_type=str(result.get("query_type") or "").strip(),
        status="success" if bool(result.get("success")) else "failed",
        reply_text=reply_text,
        error=str(error or result.get("error") or "").strip(),
    )


async def _build_wechat_query_response_with_log(
    repo: DutyRepository,
    query: WechatQueryRequest,
    *,
    uploads: Path | None = None,
) -> dict[str, Any]:
    try:
        result = await _build_wechat_query_response(repo, query, uploads=uploads)
    except HTTPException as exc:
        _save_wechat_interaction_log(repo, query, {"success": False, "query_type": "", "reply": str(exc.detail)}, error=str(exc.detail))
        raise
    except Exception as exc:
        _save_wechat_interaction_log(repo, query, {"success": False, "query_type": "", "reply": f"查询失败：{exc}"}, error=str(exc))
        raise
    _attach_wechat_query_image(result, uploads)
    _save_wechat_interaction_log(repo, query, result)
    return result


def _attach_wechat_query_image(result: dict[str, Any], uploads: Path | None) -> None:
    if uploads is None or not bool(result.get("success")):
        return
    if str(result.get("image_url") or result.get("result_image_url") or "").strip():
        return
    image_bytes = render_wechat_query_image(result)
    if not image_bytes:
        return
    uploads.mkdir(parents=True, exist_ok=True)
    filename = f"wechat-query-{uuid.uuid4().hex}.png"
    (uploads / filename).write_bytes(image_bytes)
    image_url = f"/api/uploads/{filename}"
    result["image_url"] = image_url
    result["image_full_url"] = _public_app_url(image_url)
    if not result.get("replies"):
        result["replies"] = [f"{_wechat_query_image_reply_title(result)}结果如下："]


def _attach_roster_import_image(result: dict[str, Any], uploads: Path | None) -> None:
    if uploads is None:
        return
    if str(result.get("image_url") or "").strip():
        return
    uploads.mkdir(parents=True, exist_ok=True)
    filename = f"roster-import-{uuid.uuid4().hex}.png"
    (uploads / filename).write_bytes(render_roster_import_image(result))
    image_url = f"/api/uploads/{filename}"
    result["image_url"] = image_url
    result["image_full_url"] = _public_app_url(image_url)


def _wechat_query_image_reply_title(result: dict[str, Any]) -> str:
    query_type = str(result.get("query_type") or "").strip()
    return {
        "help": "帮助菜单",
        "unbound": "查询",
        "binding": "绑定查询",
        "daily_duty_query": "今日在岗查询",
        "monitor": "监控查询",
        "monitor_all": "监控查询",
        "monitor_range": "监控查询",
        "monitor_all_range": "监控查询",
        "reminder": "提醒查询",
        "reminder_all": "提醒查询",
        "reminder_range": "提醒查询",
        "reminder_all_range": "提醒查询",
        "next_reminder": "下次提醒查询",
        "next_reminder_all": "下次提醒查询",
        "rest_query": "休息查询",
        "patrol_record": "巡查记录查询",
        "tunnel_mechanical": "隧道机电录入结果",
        "tunnel_mechanical_modify": "隧道机电修改结果",
        "tunnel_mechanical_result": "隧道机电查询结果",
        "roster_import": "排班导入确认",
    }.get(query_type, "查询")


def _lightagent_web_base_url(config: dict[str, Any]) -> str:
    explicit = os.getenv("LIGHTAGENT_WEB_URL", "").strip() or os.getenv("LIGHTAGENT_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    endpoint = str(config.get("lightagent_url") or "").strip()
    for suffix in ("/api/push/send", "/push/send"):
        if endpoint.endswith(suffix):
            return endpoint[: -len(suffix)].rstrip("/")
    return endpoint.rstrip("/")


def _lightagent_web_password() -> str:
    return os.getenv("LIGHTAGENT_WEB_PASSWORD", "").strip() or os.getenv("LIGHTAGENT_PASSWORD", "").strip()


def _lightagent_web_request(
    repo: DutyRepository,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feature_config = _feature_channel_config_with_env_defaults(repo.get_feature_channel_config())
    base_url = str(feature_config.get("lightagent_web_url") or "").strip().rstrip("/")
    if not base_url:
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        base_url = _lightagent_web_base_url(config)
    if not base_url:
        raise HTTPException(status_code=400, detail="LightAgent Web 地址未配置")
    password = str(feature_config.get("lightagent_web_password") or "").strip() or _lightagent_web_password()
    try:
        with httpx.Client(timeout=10, trust_env=False) as client:
            if password:
                login_response = client.post(f"{base_url}/auth/login", json={"password": password})
                login_response.raise_for_status()
                login_data = login_response.json()
                if login_data.get("status") == "error":
                    raise HTTPException(status_code=502, detail=str(login_data.get("message") or "LightAgent 登录失败"))
            response = client.request(method, f"{base_url}{path}", params=params, json=json_body)
            response.raise_for_status()
            data = response.json()
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"LightAgent Web 请求失败：HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"LightAgent Web 连接失败：{exc.__class__.__name__}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="LightAgent Web 返回非 JSON 数据") from exc
    if isinstance(data, dict) and data.get("status") == "error":
        raise HTTPException(status_code=502, detail=str(data.get("message") or "LightAgent Web 请求失败"))
    return data if isinstance(data, dict) else {"status": "success", "data": data}


def _sync_lightagent_notification_targets(repo: DutyRepository, sender_type: str, targets: list[dict[str, str]]) -> dict[str, Any]:
    return {"success": True, "skipped": True, "reason": "personal_wechat_disabled"}


def _sync_lightagent_feature_channel_rooms(
    repo: DutyRepository,
    enabled: bool,
    rooms: list[dict[str, str]],
) -> dict[str, Any]:
    return {"success": True, "skipped": True, "reason": "personal_wechat_disabled"}


def _sync_lightagent_wechat_group_targets(
    repo: DutyRepository,
    targets: list[str],
    *,
    source: str,
) -> dict[str, Any]:
    target_ids = _merge_lightagent_room_ids(targets)
    if not target_ids:
        return {"success": True, "skipped": True, "reason": "empty_targets"}
    if wechat_bridge_enabled():
        manager = get_wechat_bridge_manager()
        snapshot = manager.status_snapshot()
        if not snapshot.get("connected"):
            login_status = str(snapshot.get("login_status") or "unknown")
            return {
                "success": False,
                "target": target_ids[0],
                "targets": target_ids,
                "source": source,
                "login_status": login_status,
                "message": f"内置微信桥未登录或未连接（当前状态：{login_status}），请先完成微信登录并同步群聊",
            }
        sendable_ids = {
            str(room.get("id") or "").strip()
            for room in snapshot.get("rooms") or []
            if room.get("sendable") and str(room.get("id") or "").strip()
        }
        inactive_targets = [
            target
            for target in target_ids
            if target.startswith("wgr_") and target not in sendable_ids
        ]
        if inactive_targets:
            return {
                "success": False,
                "target": target_ids[0],
                "targets": target_ids,
                "source": source,
                "inactive_targets": inactive_targets,
                "message": "内置微信桥已登录，但目标群当前不可发送。请重新同步群聊，或移除失效群。",
            }
        return {
            "success": True,
            "target": target_ids[0],
            "targets": target_ids,
            "source": source,
            "selected_room_ids": target_ids,
            "action": "local_bridge",
            "restarted": False,
        }
    try:
        data = _lightagent_web_request(repo, "GET", "/api/channels")
        channels = data.get("channels") if isinstance(data, dict) else []
        wechat_group = None
        for channel in channels or []:
            if str(channel.get("name") or "") == "wechat_group":
                wechat_group = channel
                break
        if not _lightagent_wechat_group_connected(wechat_group):
            login_status = str((wechat_group or {}).get("login_status") or "unknown")
            return {
                "success": False,
                "target": target_ids[0],
                "targets": target_ids,
                "source": source,
                "login_status": login_status,
                "message": f"LightAgent 个人微信未登录或未连接（当前状态：{login_status}），请先完成微信登录并同步群聊",
            }
        extra = wechat_group.get("extra") if isinstance(wechat_group, dict) and isinstance(wechat_group.get("extra"), dict) else {}
        selected_ids = _merge_lightagent_room_ids(
            extra.get("stable_selected_room_ids"),
            extra.get("selected_room_ids"),
            target_ids,
        )
        action = "save"
        result = _lightagent_web_request(
            repo,
            "POST",
            "/api/channels",
            json_body={
                "action": action,
                "channel": "wechat_group",
                "config": {"wechat_group_stable_room_ids": selected_ids},
            },
        )
        returned_extra = result.get("extra") if isinstance(result, dict) and isinstance(result.get("extra"), dict) else {}
        returned_ids = _merge_lightagent_room_ids(
            returned_extra.get("stable_selected_room_ids"),
            returned_extra.get("selected_room_ids"),
        )
        missing_targets = [target for target in target_ids if target.startswith("wgr_") and returned_ids and target not in returned_ids]
        if missing_targets:
            return {
                "success": False,
                "target": target_ids[0],
                "targets": target_ids,
                "missing_targets": missing_targets,
                "selected_room_ids": returned_ids,
                "message": "LightAgent 已响应，但未确认目标群已进入当前选中列表",
            }
        returned_rooms = _normalize_lightagent_wechat_rooms(returned_extra.get("rooms") or extra.get("rooms") or [])
        if returned_rooms:
            sendable_ids = {
                str(room.get("id") or "").strip()
                for room in returned_rooms
                if room.get("sendable") and str(room.get("id") or "").strip()
            }
            inactive_targets = [
                target
                for target in target_ids
                if target.startswith("wgr_") and target not in sendable_ids
            ]
            if inactive_targets:
                return {
                    "success": False,
                    "target": target_ids[0],
                    "targets": target_ids,
                    "inactive_targets": inactive_targets,
                    "selected_room_ids": returned_ids or selected_ids,
                    "message": "LightAgent 已同步群配置，但目标群当前没有可发送会话。请先在这些微信群内发一条消息后重新同步群聊，或移除失效群。",
                }
        return {
            "success": True,
            "target": target_ids[0],
            "targets": target_ids,
            "source": source,
            "action": action,
            "selected_room_ids": selected_ids,
            "restarted": bool(result.get("restarted")) if isinstance(result, dict) else False,
        }
    except HTTPException as exc:
        return {"success": False, "target": target_ids[0], "targets": target_ids, "source": source, "message": str(exc.detail)}
    except Exception as exc:
        return {"success": False, "target": target_ids[0], "targets": target_ids, "source": source, "message": str(exc)}


def _merge_lightagent_room_ids(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _lightagent_wechat_group_channel_info(data: Any) -> dict[str, Any]:
    channels = data.get("channels") if isinstance(data, dict) else []
    for channel in channels or []:
        if str(channel.get("name") or "") != "wechat_group":
            continue
        extra = channel.get("extra") if isinstance(channel.get("extra"), dict) else {}
        return {
            "connected": _lightagent_wechat_group_connected(channel),
            "login_status": str(channel.get("login_status") or ""),
            "rooms": extra.get("rooms") or [],
            "selected_room_ids": extra.get("selected_room_ids") or [],
            "selected_room_names": extra.get("selected_room_names") or [],
        }
    return {}


def _lightagent_wechat_group_connected(channel: Any) -> bool:
    if not isinstance(channel, dict):
        return False
    login_status = str(channel.get("login_status") or "").strip().lower()
    if login_status:
        return login_status in {"connected", "logged_in"}
    return bool(channel.get("connected") or channel.get("active"))


def _normalize_lightagent_wechat_rooms(rooms: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(rooms, list):
        return normalized
    for item in rooms:
        if not isinstance(item, dict):
            continue
        room = dict(item)
        raw_id = str(room.get("id") or room.get("room_id") or "").strip()
        stable_room_id = str(room.get("stable_room_id") or room.get("stable_id") or "").strip()
        runtime_room_id = str(room.get("runtime_room_id") or room.get("runtime_id") or "").strip()
        if not stable_room_id and raw_id.startswith("wgr_"):
            stable_room_id = raw_id
        if not runtime_room_id and raw_id and not raw_id.startswith("wgr_"):
            runtime_room_id = raw_id
        if runtime_room_id.startswith("wgr_"):
            runtime_room_id = ""
        room_id = stable_room_id or runtime_room_id or raw_id
        if not room_id:
            continue
        room["id"] = room_id
        room["stable_room_id"] = stable_room_id
        room["runtime_room_id"] = runtime_room_id
        room["sendable"] = bool(runtime_room_id)
        if not room["sendable"]:
            room["sendable_reason"] = "当前没有可发送会话，请先在群内发言后重新同步群聊"
        normalized.append(room)
    return normalized


def _looks_like_wechat_runtime_id(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(
        text.startswith("@")
        or text.startswith("wxid_")
        or re.fullmatch(r"[A-Za-z0-9_-]{18,}", text)
    )


def _looks_like_wechat_room_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text.startswith("wgr_") or text.startswith("@@") or text.startswith("room@@"))


def _normalize_lightagent_wechat_members(members: Any) -> list[dict[str, Any]]:
    if not isinstance(members, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    name_fields = (
        "display_name",
        "sender_nickname",
        "wechat_group_member_name",
        "room_alias",
        "sender_room_alias",
        "profile_nickname",
        "primary_nickname",
        "remark",
        "alias",
        "contact_name",
        "name",
        "nickName",
        "nickname",
    )
    id_fields = (
        "runtime_sender_id",
        "sender_id",
        "wechat_group_runtime_sender_id",
        "id",
    )
    for raw in members:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        runtime_id = next(
            (str(item.get(field) or "").strip() for field in id_fields if str(item.get(field) or "").strip()),
            "",
        )
        if not runtime_id:
            continue
        if runtime_id in seen:
            continue
        seen.add(runtime_id)
        display_name = next(
            (
                str(item.get(field) or "").strip()
                for field in name_fields
                if str(item.get(field) or "").strip()
            ),
            "",
        )
        if not display_name or _looks_like_wechat_runtime_id(display_name):
            display_name = runtime_id
        item["runtime_sender_id"] = runtime_id
        item.setdefault("sender_id", runtime_id)
        item["display_name"] = display_name
        item["sender_nickname"] = display_name
        item["is_raw_id_name"] = display_name == runtime_id or _looks_like_wechat_runtime_id(display_name)
        normalized.append(item)
    return normalized


def _wechat_query_token() -> str:
    return (
        os.getenv("DUTY_REMINDER_QUERY_TOKEN", "").strip()
        or os.getenv("DUTY_QUERY_TOKEN", "").strip()
        or "520pt"
    )


def _require_wechat_query_auth(request: Request) -> None:
    token = _wechat_query_token()
    if not token:
        return
    auth = str(request.headers.get("authorization") or "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    supplied = str(request.headers.get("x-duty-query-token") or bearer).strip()
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _feature_channel_query_room_ids(query: WechatQueryRequest) -> set[str]:
    return {
        str(query.stable_room_id or "").strip(),
        str(query.room_id or "").strip(),
    } - {""}


def _notification_wechat_target_rooms(repo: DutyRepository) -> list[dict[str, str]]:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    rooms = _normalize_feature_channel_rooms(config.get("lightagent_targets"))
    legacy_room_id = str(config.get("lightagent_target") or "").strip()
    if legacy_room_id:
        rooms = _normalize_feature_channel_rooms(rooms + [{"id": legacy_room_id}])
    return rooms


def _notification_wechat_target_room_ids(repo: DutyRepository) -> set[str]:
    return {room["id"] for room in _notification_wechat_target_rooms(repo) if room.get("id")}


def _notification_wechat_target_room_label(repo: DutyRepository) -> str:
    rooms = _notification_wechat_target_rooms(repo)
    names = [room.get("name") or room.get("id") or "" for room in rooms]
    return "、".join([name for name in names if name])


def _require_feature_channel_for_wechat_query(
    repo: DutyRepository,
    query: WechatQueryRequest,
    permission_key: str,
) -> None:
    if str(query.channel or "").strip() in {"wecom_aibot", "wecom_app"}:
        return
    configured_room_ids = _notification_wechat_target_room_ids(repo)
    if configured_room_ids and not (configured_room_ids & _feature_channel_query_room_ids(query)):
        room_name = _notification_wechat_target_room_label(repo) or "未命名微信群"
        raise HTTPException(status_code=403, detail=f"当前来源不在允许的交互范围内：{room_name}")


def _require_feature_channel_for_roster_import(
    repo: DutyRepository,
    room_id: str = "",
    stable_room_id: str = "",
) -> None:
    configured_room_ids = _notification_wechat_target_room_ids(repo)
    supplied = {str(room_id or "").strip(), str(stable_room_id or "").strip()} - {""}
    if configured_room_ids and not (configured_room_ids & supplied):
        room_name = _notification_wechat_target_room_label(repo) or "未命名微信群"
        raise HTTPException(status_code=403, detail=f"当前来源不在允许的交互范围内：{room_name}")


def _build_wechat_roster_import_response(
    repo: DutyRepository,
    uploads: Path,
    file: UploadFile,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    target = _save_roster_upload(file, uploads)
    try:
        result = extract_roster_image(str(target))
        result = _auto_recheck_wechat_roster_result(target, result)
        result = _normalize_roster_ocr_names(repo, result)
        result = _apply_roster_role_semantics(repo, result)
    except Exception as exc:
        target.unlink(missing_ok=True)
        LOGGER.exception("微信群排班表识别失败：%s", exc)
        return {
            "success": False,
            "import_status": "ocr_failed",
            "reply": "排班表图片识别失败，请换一张更清晰的原图，或到 duty-reminder 网页端上传校对。",
        }
    result["source_image_url"] = f"/api/uploads/{Path(result.get('source_image_path') or target).name}"
    grid = list(result.get("grid") or [])
    year = int(result.get("year") or _today_in_tz().year)
    month = int(result.get("month") or _today_in_tz().month)
    if result.get("ocr_status") not in {"ok", "template_ok"} or not grid:
        return {
            "success": False,
            "import_status": "ocr_failed",
            "ocr_status": str(result.get("ocr_status") or ""),
            "year": year,
            "month": month,
            "source_image_path": str(result.get("source_image_path") or target),
            "source_image_url": result["source_image_url"],
            "reply": "没有从图片中识别到可导入的排班表，请换一张完整、清晰的排班表图片。",
        }
    return _build_wechat_roster_confirm_response(
        repo,
        year,
        month,
        grid,
        source_image_path=str(result.get("source_image_path") or target),
        overwrite=overwrite,
        ocr_status=str(result.get("ocr_status") or ""),
        name_ocr_status=str(result.get("name_ocr_status") or ""),
        source_image_url=str(result.get("source_image_url") or ""),
        issues=list(result.get("issues") or []),
    )


def _build_wechat_roster_import_response_from_path(
    repo: DutyRepository,
    source_path: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    try:
        result = extract_roster_image(str(source_path))
        result = _auto_recheck_wechat_roster_result(source_path, result)
        result = _normalize_roster_ocr_names(repo, result)
        result = _apply_roster_role_semantics(repo, result)
    except Exception as exc:
        LOGGER.exception("企业微信排班表识别失败：%s", exc)
        return {
            "success": False,
            "query_type": "roster_import",
            "import_status": "ocr_failed",
            "reply": "排班表图片识别失败，请换一张更清晰的原图，或到 duty-reminder 网页端上传校对。",
        }
    result["source_image_url"] = f"/api/uploads/{Path(result.get('source_image_path') or source_path).name}"
    grid = list(result.get("grid") or [])
    year = int(result.get("year") or _today_in_tz().year)
    month = int(result.get("month") or _today_in_tz().month)
    if result.get("ocr_status") not in {"ok", "template_ok"} or not grid:
        return {
            "success": False,
            "query_type": "roster_import",
            "import_status": "ocr_failed",
            "ocr_status": str(result.get("ocr_status") or ""),
            "year": year,
            "month": month,
            "source_image_path": str(result.get("source_image_path") or source_path),
            "source_image_url": result["source_image_url"],
            "reply": "没有从图片中识别到可导入的排班表，请换一张完整、清晰的排班表图片。",
        }
    return _build_wechat_roster_confirm_response(
        repo,
        year,
        month,
        grid,
        source_image_path=str(result.get("source_image_path") or source_path),
        overwrite=overwrite,
        ocr_status=str(result.get("ocr_status") or ""),
        source_image_url=str(result.get("source_image_url") or ""),
        issues=list(result.get("issues") or []),
    )


def _auto_recheck_wechat_roster_result(source_path: Path, result: dict[str, Any]) -> dict[str, Any]:
    grid = list(result.get("grid") or [])
    if not grid:
        return result
    year = int(result.get("year") or _today_in_tz().year)
    month = int(result.get("month") or _today_in_tz().month)
    try:
        checked = recheck_template_roster_cells(source_path, grid, year=year, month=month)
    except Exception:
        LOGGER.exception("排班图片自动核对失败")
        checked = None
    if not checked:
        return result
    return {
        **result,
        "grid": checked.get("grid") or grid,
        "issues": checked.get("issues") or [],
        "auto_rechecked": True,
    }


def _remember_wecom_app_roster_import(query: WechatQueryRequest, result: dict[str, Any]) -> None:
    key = _wecom_app_pending_key(query)
    if not key or not result.get("conflict"):
        return
    WECOM_APP_PENDING_ROSTER_IMPORTS[key] = {
        "expires_at": time.time() + WECOM_APP_PENDING_ROSTER_TTL_SECONDS,
        "payload": {
            "year": int(result.get("year") or 0),
            "month": int(result.get("month") or 0),
            "grid": list(result.get("grid") or []),
            "source_image_path": str(result.get("source_image_path") or ""),
            "source_image_url": str(result.get("source_image_url") or ""),
            "issues": list(result.get("issues") or []),
        },
    }


def _is_wecom_app_roster_import_request(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {
        "导入排班",
        "导入排班表",
        "上传排班",
        "上传排班表",
        "排班导入",
        "排班表导入",
    }


def _wecom_app_roster_conflict_action_text(*, compact: bool = False) -> str:
    if compact:
        return "待确认：回复 1 覆盖｜2 取消；也可回复覆盖导入/取消导入"
    return (
        "请选择本次排班导入操作：\n"
        "1. 覆盖现有排班：回复 1 / 覆盖导入 / 确认覆盖 / 覆盖 / 确认导入 / 导入\n"
        "2. 取消本次导入：回复 2 / 取消导入 / 取消 / 放弃\n"
        "提示：5 分钟内有效，过期后需要重新点击“导入排班”并发送图片。"
    )


def _remember_wecom_app_roster_image_request(query: WechatQueryRequest) -> None:
    key = _wecom_app_pending_key(query)
    if key:
        WECOM_APP_PENDING_ROSTER_IMAGE_REQUESTS[key] = {
            "expires_at": time.time() + WECOM_APP_PENDING_ROSTER_TTL_SECONDS,
        }


def _consume_wecom_app_roster_image_request(query: WechatQueryRequest) -> bool:
    key = _wecom_app_pending_key(query)
    if not key:
        return False
    pending = WECOM_APP_PENDING_ROSTER_IMAGE_REQUESTS.get(key)
    if not pending:
        return False
    if float(pending.get("expires_at") or 0) < time.time():
        WECOM_APP_PENDING_ROSTER_IMAGE_REQUESTS.pop(key, None)
        return False
    WECOM_APP_PENDING_ROSTER_IMAGE_REQUESTS.pop(key, None)
    return True


def _build_wecom_app_roster_import_prompt_response(query: WechatQueryRequest) -> dict[str, Any]:
    _remember_wecom_app_roster_image_request(query)
    return {
        "success": True,
        "query_type": "roster_import_prompt",
        "reply": (
            "已进入排班导入模式。\n"
            "请在 5 分钟内发送一张完整、清晰的排班表图片。\n"
            "导入后系统会自动核对并发送图文确认；如果月份已存在，会先让你确认是否覆盖。"
        ),
    }


def _is_wecom_app_roster_overwrite_text(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"覆盖导入", "确认覆盖", "覆盖", "确认导入", "导入", "1"}


def _is_wecom_app_roster_cancel_text(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"取消导入", "取消", "放弃", "2"}


def _is_wecom_app_roster_pending_text(text: str) -> bool:
    return _is_wecom_app_roster_overwrite_text(text) or _is_wecom_app_roster_cancel_text(text)


def _build_wecom_app_pending_roster_response(repo: DutyRepository, query: WechatQueryRequest, text: str) -> dict[str, Any]:
    key = _wecom_app_pending_key(query)
    pending = WECOM_APP_PENDING_ROSTER_IMPORTS.get(key)
    if not pending:
        return {"success": False, "query_type": "roster_import", "import_status": "expired", "reply": "没有待确认覆盖的排班导入，请重新发送排班表图片。"}
    if float(pending.get("expires_at") or 0) < time.time():
        WECOM_APP_PENDING_ROSTER_IMPORTS.pop(key, None)
        return {"success": False, "query_type": "roster_import", "import_status": "expired", "reply": "排班导入确认已过期，请重新发送排班表图片。"}
    if _is_wecom_app_roster_cancel_text(text):
        WECOM_APP_PENDING_ROSTER_IMPORTS.pop(key, None)
        payload = dict(pending.get("payload") or {})
        return {
            "success": False,
            "query_type": "roster_import",
            "import_status": "cancelled",
            "year": payload.get("year"),
            "month": payload.get("month"),
            "grid": list(payload.get("grid") or []),
            "issues": list(payload.get("issues") or []),
            "reply": f"已取消覆盖导入 {payload.get('year')}年{payload.get('month')}月排班表。",
        }
    if not _is_wecom_app_roster_overwrite_text(text):
        return {
            "success": False,
            "query_type": "roster_import",
            "import_status": "unknown",
            "reply": _wecom_app_roster_conflict_action_text(),
        }
    payload = dict(pending.get("payload") or {})
    result = _build_wechat_roster_confirm_response(
        repo,
        int(payload.get("year") or 0),
        int(payload.get("month") or 0),
        list(payload.get("grid") or []),
        source_image_path=str(payload.get("source_image_path") or ""),
        overwrite=True,
        source_image_url=str(payload.get("source_image_url") or ""),
        issues=list(payload.get("issues") or []),
    )
    WECOM_APP_PENDING_ROSTER_IMPORTS.pop(key, None)
    return result


async def _handle_wecom_app_roster_image(repo: DutyRepository, uploads: Path, client: WeComClient, message: Any) -> None:
    userid = str(message.from_user or "").strip()
    media_id = str(getattr(message, "media_id", "") or getattr(message, "content", "") or "").strip()
    if not userid or not media_id:
        return
    query = _wecom_app_query_from_message(message, "导入排班")
    if not _consume_wecom_app_roster_image_request(query):
        try:
            await client.send_text(userid, "收到图片，但当前没有进入排班导入模式。如需导入排班，请先点击“更多查询 → 导入排班”，再发送排班表图片。")
        except Exception:
            LOGGER.exception("企业微信自建应用发送排班导入入口提示失败")
        return
    try:
        await client.send_text(userid, "收到排班表图片，正在识别并自动核对，请稍候…")
        image_bytes = await client.download_media(media_id)
        source_path = _save_roster_upload_bytes(f"wecom-roster-{media_id}.jpg", "image/jpeg", image_bytes, uploads)
        result = _build_wechat_roster_import_response_from_path(repo, source_path)
        _remember_wecom_app_roster_import(query, result)
        _attach_roster_import_image(result, uploads)
        await _send_wecom_app_result(client, userid, result, uploads)
    except Exception as exc:
        LOGGER.exception("企业微信自建应用排班图片导入失败")
        try:
            await client.send_text(userid, f"排班表图片导入失败：{exc}")
        except Exception:
            LOGGER.exception("企业微信自建应用发送排班导入失败提示失败")


def _is_wecom_app_construction_image_request(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"施工图片", "施工照片", "施工影像", "安全影像", "使用图片"}


def _is_wecom_app_construction_site_request(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"施工点维护", "施工地点维护", "施工点", "施工地点"}


def _construction_pending_valid(pending: dict[str, Any] | None) -> bool:
    return bool(pending) and float((pending or {}).get("expires_at") or 0) >= time.time()


def _construction_site_lines(sites: list[dict[str, Any]]) -> str:
    return "\n".join(f"{index}. {site.get('name')}" for index, site in enumerate(sites, 1))


def _remember_wecom_app_construction_image_request(repo: DutyRepository, query: WechatQueryRequest) -> dict[str, Any]:
    sites = repo.list_construction_sites()
    pending = {
        "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS,
        "stage": "location",
        "sites": sites,
        "location": "",
        "image_paths": [],
    }
    WECOM_APP_PENDING_CONSTRUCTION_IMAGES[_wecom_app_pending_key(query)] = pending
    return pending


def _build_wecom_app_construction_image_prompt(repo: DutyRepository, query: WechatQueryRequest) -> str:
    pending = _remember_wecom_app_construction_image_request(repo, query)
    sites = list(pending.get("sites") or [])
    if sites:
        return (
            "已进入施工图片模式，请选择施工地点或直接回复完整施工地点。\n"
            f"{_construction_site_lines(sites)}\n\n"
            f"也可以直接回复：{DEFAULT_CONSTRUCTION_LOCATION}\n"
            "回复“取消”可退出。"
        )
    return (
        "已进入施工图片模式，请先回复施工地点。\n"
        f"例如：{DEFAULT_CONSTRUCTION_LOCATION}\n"
        "随后我会提醒你发送 2 张施工图片。回复“取消”可退出。"
    )


def _build_wecom_app_construction_site_menu(repo: DutyRepository, query: WechatQueryRequest) -> str:
    sites = repo.list_construction_sites()
    WECOM_APP_PENDING_CONSTRUCTION_SITES[_wecom_app_shared_pending_key()] = {
        "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS,
        "stage": "action",
    }
    site_text = f"\n\n当前施工点：\n{_construction_site_lines(sites)}" if sites else "\n\n当前还没有维护施工点。"
    return (
        "施工点维护：\n"
        "1. 新增\n"
        "2. 删除\n"
        "3. 修改\n"
        "回复对应数字继续，回复“取消”退出。"
        f"{site_text}"
    )


def _construction_cancel_text(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"取消", "退出", "放弃", "取消操作"}


def _construction_numeric_choice(text: str) -> int | None:
    value = _normalize_wechat_query_text(text)
    match = re.match(r"^([1-9]\d*)", value)
    return int(match.group(1)) if match else None


async def _handle_wecom_app_construction_text(
    repo: DutyRepository,
    uploads: Path,
    client: WeComClient,
    message: Any,
    text: str,
    query: WechatQueryRequest,
) -> bool:
    del uploads
    userid = str(message.from_user or "").strip()
    user_key = _wecom_app_pending_key(query)
    if _is_wecom_app_construction_image_request(text):
        await client.send_text(userid, _build_wecom_app_construction_image_prompt(repo, query))
        return True
    if _is_wecom_app_construction_site_request(text):
        await _send_wecom_app_shared_text(repo, client, userid, _build_wecom_app_construction_site_menu(repo, query))
        return True

    shared_key = _wecom_app_shared_pending_key()
    site_pending = WECOM_APP_PENDING_CONSTRUCTION_SITES.get(shared_key)
    if site_pending:
        if not _construction_pending_valid(site_pending):
            WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(shared_key, None)
            await _send_wecom_app_shared_text(repo, client, userid, "施工点维护已过期，请重新点击“施工点维护”。")
            return True
        if _construction_cancel_text(text):
            WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(shared_key, None)
            await _send_wecom_app_shared_text(repo, client, userid, "已退出施工点维护。")
            return True
        handled = await _handle_wecom_app_construction_site_text(repo, client, userid, shared_key, site_pending, text)
        if handled:
            return True

    image_pending = WECOM_APP_PENDING_CONSTRUCTION_IMAGES.get(user_key)
    if not image_pending:
        return False
    if not _construction_pending_valid(image_pending):
        WECOM_APP_PENDING_CONSTRUCTION_IMAGES.pop(user_key, None)
        await client.send_text(userid, "施工图片模式已过期，请重新点击“施工图片”。")
        return True
    if _construction_cancel_text(text):
        WECOM_APP_PENDING_CONSTRUCTION_IMAGES.pop(user_key, None)
        await client.send_text(userid, "已退出施工图片模式。")
        return True
    if str(image_pending.get("stage") or "") != "location":
        await client.send_text(userid, "请继续发送施工图片；如需退出请回复“取消”。")
        return True
    location = _construction_location_from_text(image_pending, text)
    if not location:
        await client.send_text(userid, "没有识别到施工地点，请重新发送完整施工地点，或回复施工点序号。")
        return True
    image_pending.update(
        {
            "stage": "images",
            "location": location,
            "image_paths": [],
            "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS,
        }
    )
    await client.send_text(userid, f"已记录施工地点：{location}\n请发送第 1 张施工图片。")
    return True


async def _handle_wecom_app_construction_site_text(
    repo: DutyRepository,
    client: WeComClient,
    userid: str,
    key: str,
    pending: dict[str, Any],
    text: str,
) -> bool:
    stage = str(pending.get("stage") or "action")
    choice = _construction_numeric_choice(text)
    if stage == "action":
        if choice == 1:
            pending.update({"stage": "add", "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS})
            await _send_wecom_app_shared_text(repo, client, userid, f"请发送要新增的施工点名称，例如：{DEFAULT_CONSTRUCTION_LOCATION}")
            return True
        if choice == 2:
            sites = repo.list_construction_sites()
            if not sites:
                await _send_wecom_app_shared_text(repo, client, userid, "当前没有可删除的施工点。")
                WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(key, None)
                return True
            pending.update({"stage": "delete", "sites": sites, "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS})
            await _send_wecom_app_shared_text(repo, client, userid, f"请选择要删除的施工点序号：\n{_construction_site_lines(sites)}")
            return True
        if choice == 3:
            sites = repo.list_construction_sites()
            if not sites:
                await _send_wecom_app_shared_text(repo, client, userid, "当前没有可修改的施工点。")
                WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(key, None)
                return True
            pending.update({"stage": "modify_select", "sites": sites, "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS})
            await _send_wecom_app_shared_text(repo, client, userid, f"请选择要修改的施工点序号：\n{_construction_site_lines(sites)}")
            return True
        await _send_wecom_app_shared_text(repo, client, userid, "请回复 1 新增、2 删除、3 修改，或回复“取消”退出。")
        return True
    if stage == "add":
        site = repo.add_construction_site(text)
        WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(key, None)
        await _send_wecom_app_shared_text(repo, client, userid, f"施工点已新增：{site['name']}")
        return True
    if stage == "delete":
        sites = list(pending.get("sites") or repo.list_construction_sites())
        if not choice or not (1 <= choice <= len(sites)):
            await _send_wecom_app_shared_text(repo, client, userid, "序号不正确，请重新回复要删除的施工点序号，或回复“取消”。")
            return True
        site = sites[choice - 1]
        repo.delete_construction_site(int(site["id"]))
        WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(key, None)
        await _send_wecom_app_shared_text(repo, client, userid, f"施工点已删除：{site['name']}")
        return True
    if stage == "modify_select":
        sites = list(pending.get("sites") or repo.list_construction_sites())
        if not choice or not (1 <= choice <= len(sites)):
            await _send_wecom_app_shared_text(repo, client, userid, "序号不正确，请重新回复要修改的施工点序号，或回复“取消”。")
            return True
        pending.update({"stage": "modify_name", "site_id": sites[choice - 1]["id"], "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS})
        await _send_wecom_app_shared_text(repo, client, userid, f"请发送新的施工点名称。\n当前：{sites[choice - 1]['name']}")
        return True
    if stage == "modify_name":
        site = repo.update_construction_site(int(pending.get("site_id") or 0), text)
        WECOM_APP_PENDING_CONSTRUCTION_SITES.pop(key, None)
        await _send_wecom_app_shared_text(repo, client, userid, f"施工点已修改为：{site['name'] if site else text}")
        return True
    return False


def _construction_location_from_text(pending: dict[str, Any], text: str) -> str:
    choice = _construction_numeric_choice(text)
    sites = list(pending.get("sites") or [])
    if choice:
        return str(sites[choice - 1].get("name") or "").strip() if 1 <= choice <= len(sites) else ""
    return str(text or "").strip()


async def _handle_wecom_app_construction_image(repo: DutyRepository, uploads: Path, client: WeComClient, message: Any) -> bool:
    userid = str(message.from_user or "").strip()
    query = _wecom_app_query_from_message(message, "施工图片")
    key = _wecom_app_pending_key(query)
    pending = WECOM_APP_PENDING_CONSTRUCTION_IMAGES.get(key)
    if not pending:
        return False
    if not _construction_pending_valid(pending):
        WECOM_APP_PENDING_CONSTRUCTION_IMAGES.pop(key, None)
        await client.send_text(userid, "施工图片模式已过期，请重新点击“施工图片”。")
        return True
    if str(pending.get("stage") or "") != "images":
        await client.send_text(userid, "收到图片，但还没有施工地点。请先回复施工地点，再发送两张施工图片。")
        return True
    media_id = str(getattr(message, "media_id", "") or getattr(message, "content", "") or "").strip()
    if not media_id:
        return True
    try:
        image_bytes = await client.download_media(media_id)
        image_path = _save_roster_upload_bytes(f"construction-{media_id}.jpg", "image/jpeg", image_bytes, uploads)
        image_paths = [*list(pending.get("image_paths") or []), str(image_path)]
        pending.update({"image_paths": image_paths, "expires_at": time.time() + WECOM_APP_PENDING_CONSTRUCTION_TTL_SECONDS})
        if len(image_paths) < 2:
            await client.send_text(userid, "已收到第 1 张施工图片，请继续发送第 2 张施工图片。")
            return True
        await client.send_text(userid, "已收到 2 张施工图片，正在生成 Word 文档，请稍候…")
        result = _build_wecom_app_construction_docx(repo, uploads, str(pending.get("location") or ""), [Path(p) for p in image_paths[:2]])
        WECOM_APP_PENDING_CONSTRUCTION_IMAGES.pop(key, None)
        await _send_wecom_app_construction_docx(client, userid, result)
        repo.save_send_record(kind="construction_docx_wechat", target=userid, status="success", content=result["location"])
    except Exception as exc:
        LOGGER.exception("企业微信自建应用施工图片生成失败")
        await client.send_text(userid, f"施工图片 Word 生成失败：{exc}")
        repo.save_send_record(kind="construction_docx_wechat", target=userid, status="failed", content=str(pending.get("location") or ""), error=str(exc))
    return True


def _construction_template_path() -> Path:
    candidates = [
        Path(os.getenv("CONSTRUCTION_IMAGE_TEMPLATE_PATH", "")).expanduser() if os.getenv("CONSTRUCTION_IMAGE_TEMPLATE_PATH", "").strip() else None,
        Path(__file__).resolve().parent / "templates" / "construction_images.docx",
        Path(r"D:\17457\桌面\只放施工图片.docx"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    return Path(__file__).resolve().parent / "templates" / "construction_images.docx"


def _construction_docx_filename(uploads: Path, location: str) -> str:
    target_date = _today_in_tz()
    date_text = f"{target_date.year}年{target_date.month}月{target_date.day}日"
    safe_location = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", str(location or "").strip()).strip(" .")
    safe_location = safe_location or "施工图片"
    base = f"{date_text}{safe_location}"[:140].rstrip(" .")
    candidate = f"{base}.docx"
    counter = 2
    while (uploads / candidate).exists():
        candidate = f"{base}-{counter}.docx"
        counter += 1
    return candidate


def _build_wecom_app_construction_docx(repo: DutyRepository, uploads: Path, location: str, image_paths: list[Path]) -> dict[str, Any]:
    del repo
    clean_location = str(location or "").strip() or DEFAULT_CONSTRUCTION_LOCATION
    uploads.mkdir(parents=True, exist_ok=True)
    filename = _construction_docx_filename(uploads, clean_location)
    output_path = uploads / filename
    build_construction_image_docx(
        template_path=_construction_template_path(),
        output_path=output_path,
        location=clean_location,
        image_paths=image_paths,
    )
    if not construction_docx_contains_location(output_path, clean_location):
        raise RuntimeError("生成后的 Word 未写入施工地点")
    _cleanup_old_uploads(uploads)
    return {
        "success": True,
        "location": clean_location,
        "file_path": str(output_path),
        "file_url": f"/api/uploads/{quote(filename)}",
        "file_full_url": _public_app_url(f"/api/uploads/{quote(filename)}"),
    }


async def _send_wecom_app_construction_docx(client: WeComClient, userid: str, result: dict[str, Any]) -> None:
    path = Path(str(result.get("file_path") or ""))
    link = str(result.get("file_full_url") or result.get("file_url") or "")
    content = f"施工图片 Word 已生成：{result.get('location')}\n下载：{link}"
    if path.is_file() and hasattr(client, "send_file"):
        try:
            await client.send_file(userid, path.name, path.read_bytes())
        except Exception as exc:
            LOGGER.exception("企业微信自建应用发送施工图片 Word 文件失败")
            content += f"\n文件消息发送失败：{exc}，请使用下载链接。"
    await client.send_text(userid, content)


def _build_wechat_roster_confirm_response(
    repo: DutyRepository,
    year: int,
    month: int,
    grid: list[dict[str, Any]],
    *,
    source_image_path: str = "",
    overwrite: bool = False,
    ocr_status: str = "",
    name_ocr_status: str = "",
    source_image_url: str = "",
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        sanitized_grid = _sanitize_roster_grid_for_month(grid, year, month)
    except Exception:
        return {
            "success": False,
            "import_status": "invalid_month",
            "reply": f"排班表年月无效：{year}年{month}月，请到网页端上传后手动校对。",
        }
    if _has_unconfirmed_roster_names(sanitized_grid):
        name_status = str(name_ocr_status or "").strip()
        if name_status == "empty":
            name_hint = "原图没有识别到姓名列文字，请确认图片左侧的“序号/姓名”列没有被裁掉，并发送完整、清晰的原图。"
        elif name_status == "partial":
            name_hint = "原图的姓名列只识别到一部分，请到网页端补全未识别姓名后再确认导入。"
        else:
            name_hint = "请到网页端补全未识别姓名后再确认导入。"
        return {
            "success": False,
            "import_status": "needs_names",
            "year": year,
            "month": month,
            "people_count": len(sanitized_grid),
            "source_image_path": source_image_path,
            "source_image_url": source_image_url,
            "grid": sanitized_grid,
            "issues": list(issues or []),
            "query_type": "roster_import",
            "reply": (
                f"已识别 {year}年{month}月排班表，共 {len(sanitized_grid)} 行，"
                f"但姓名没有识别完整，暂不自动导入。{name_hint}"
            ),
        }
    existing = repo.get_roster_month(year, month)
    if existing and not overwrite:
        diffs = _diff_roster_grids(existing.get("grid", []), sanitized_grid)
        preview = f"，发现 {len(diffs)} 处差异" if diffs else "，内容看起来没有明显差异"
        return {
            "success": False,
            "import_status": "conflict",
            "conflict": True,
            "year": year,
            "month": month,
            "people_count": len(sanitized_grid),
            "source_image_path": source_image_path,
            "source_image_url": source_image_url,
            "grid": sanitized_grid,
            "diffs": diffs,
            "issues": list(issues or []),
            "query_type": "roster_import",
            "action_text": _wecom_app_roster_conflict_action_text(),
            "reply": (
                f"{year}年{month}月排班表已存在{preview}。\n"
                "可回复 1/覆盖导入 覆盖现有排班；回复 2/取消导入 放弃本次导入。"
            ),
        }
    repo.save_roster_month(year, month, sanitized_grid, source_image_path)
    return {
        "success": True,
        "query_type": "roster_import",
        "import_status": "imported_overwrite" if existing and overwrite else "imported",
        "ocr_status": ocr_status,
        "year": year,
        "month": month,
        "people_count": len(sanitized_grid),
        "source_image_path": source_image_path,
        "source_image_url": source_image_url,
        "grid": sanitized_grid,
        "issues": list(issues or []),
        "reply": (
            f"已导入 {year}年{month}月排班表，共 {len(sanitized_grid)} 人。"
            if not existing
            else f"已覆盖导入 {year}年{month}月排班表，共 {len(sanitized_grid)} 人。"
        ),
    }


async def _build_wechat_query_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    *,
    uploads: Path | None = None,
) -> dict[str, Any]:
    raw_text = _normalize_wechat_query_text(query.text)
    pending_response = await _build_wecom_app_pending_tunnel_response(repo, query, raw_text, uploads=uploads)
    if pending_response is not None:
        return pending_response
    text = _consume_wechat_query_menu_selection(query, raw_text)
    if _is_wecom_app_query(query) and _is_wecom_app_roster_import_request(text):
        return _build_wecom_app_roster_import_prompt_response(query)
    if _is_wecom_app_query(query) and _is_wecom_app_construction_image_request(text):
        return {"success": True, "query_type": "construction_image_prompt", "reply": _build_wecom_app_construction_image_prompt(repo, query)}
    if _is_wecom_app_query(query) and _is_wecom_app_construction_site_request(text):
        return {"success": True, "query_type": "construction_site_menu", "reply": _build_wecom_app_construction_site_menu(repo, query)}
    if (
        _is_wecom_app_query(query)
        and not _is_wechat_self_bind_command(text)
        and not _is_wechat_query_help(text)
        and not _is_tunnel_mechanical_partner_command(text)
        and not _wechat_query_bound_person_name(repo, query)
    ):
        return _wecom_app_bind_required_response(repo, query)
    partner_response = _build_wecom_app_tunnel_partner_response(repo, query, text)
    if partner_response is not None:
        return partner_response
    if (
        _is_tunnel_mechanical_wechat_request(text)
        or _is_tunnel_mechanical_wechat_template_shortcut(text, repo)
        or _is_tunnel_mechanical_wechat_modify_template_shortcut(text, repo)
    ):
        _require_feature_channel_for_wechat_query(repo, query, "allow_tunnel_mechanical")
    else:
        _require_feature_channel_for_wechat_query(repo, query, "allow_duty_query")
    tunnel_response = await _build_tunnel_mechanical_wechat_response(repo, query, text, uploads=uploads)
    if tunnel_response is not None:
        return tunnel_response
    patrol_record_response = await _build_wechat_patrol_record_response(repo, query, text, uploads=uploads)
    if patrol_record_response is not None:
        return patrol_record_response
    if _is_wechat_daily_duty_query(text):
        return _build_wechat_daily_duty_query_response(repo, query, uploads=uploads)
    if _is_wechat_rest_query(text):
        return _build_wechat_rest_query_response(repo, query, text)
    if _is_wechat_query_help(text):
        _remember_wechat_query_menu_prompt(query)
        return _wechat_query_help_response()
    person = _person_for_wechat_query(repo, query)
    requested_person_name = _wechat_query_requested_person_name(repo, text)
    if _is_wechat_binding_query(text):
        if not person:
            return _wechat_query_unbound_response(query)
        if str(query.channel or "").strip() in {"wecom_aibot", "wecom_app"}:
            member_label = str(person.get("wecom_userid") or query.sender_name or "已绑定").strip()
            member_kind = "企业微信成员"
        else:
            member_label = _clean_wechat_member_display_name(
                str(person.get("wechat_group_member_name") or query.sender_name or ""),
                str(person.get("wechat_group_runtime_sender_id") or query.runtime_sender_id or query.sender_id or ""),
            ) or "已绑定"
            member_kind = "微信成员"
        return {
            "success": True,
            "query_type": "binding",
            "person_name": person["name"],
            "reply": (
                f"已绑定：{person['name']}\n"
                f"{member_kind}：{member_label}"
            ),
        }
    if _is_wechat_self_bind_command(text):
        return _build_wechat_self_bind_response(repo, query, text)
    if _is_wechat_next_reminder_query(text):
        if person:
            return _build_person_next_reminder_query_response(repo, str(person["name"]))
        if _is_wechat_self_scoped_query(text):
            return _wechat_query_unbound_response(query)
        return _build_all_next_reminder_query_response(repo)
    if not _is_wechat_monitor_query(text):
        response = _wechat_query_help_response()
        response.update({"success": False, "query_type": "unknown"})
        return response
    reminder_query = _is_wechat_reminder_query(text)
    person_name = requested_person_name or (str(person["name"]) if person and _is_wechat_self_scoped_query(text) else "")
    if not person_name:
        if _is_wechat_self_scoped_query(text):
            return _wechat_query_unbound_response(query)
        start, days = _wechat_query_range(text, query.target_date)
        if days > 1:
            return _build_all_reminder_range_query_response(repo, start, days) if reminder_query else _build_all_monitor_range_query_response(repo, start, days)
        return _build_all_reminder_query_response(repo, start) if reminder_query else _build_all_monitor_query_response(repo, start)
    start, days = _wechat_query_range(text, query.target_date)
    if _is_wechat_generic_self_monitor_query(text) and not query.target_date:
        start, days = _today_in_tz(), 7
    if days > 1:
        return _build_person_reminder_range_query_response(repo, person_name, start, days) if reminder_query else _build_person_monitor_range_query_response(repo, person_name, start, days)
    target = start
    return _build_person_reminder_query_response(repo, person_name, target) if reminder_query else _build_person_monitor_query_response(repo, person_name, target)


def _handle_wechat_bridge_message(repo: DutyRepository, uploads: Path, message: dict[str, Any]) -> None:
    if bool(_notification_config_with_env_defaults(repo.get_notification_config()).get("wecom_app_enabled")):
        return
    if message.get("my_msg"):
        return
    if not bool(message.get("is_at")):
        return
    text = str(message.get("text") or "").strip()
    if not text:
        return
    query = WechatQueryRequest(
        text=text,
        channel="wechat_bridge",
        room_id=str(message.get("room_id") or ""),
        stable_room_id=str(message.get("stable_room_id") or ""),
        room_name=str(message.get("room_name") or ""),
        sender_id=str(message.get("sender_id") or ""),
        runtime_sender_id=str(message.get("runtime_sender_id") or ""),
        stable_member_id=str(message.get("stable_member_id") or message.get("sender_id") or ""),
        sender_name=str(message.get("sender_name") or ""),
    )
    normalized = _normalize_wechat_query_text(text)
    if not _looks_like_duty_wechat_command(normalized, repo, query=query):
        return
    LOGGER.warning(
        "内置微信桥收到功能命令：room=%s sender=%s text=%s",
        message.get("stable_room_id") or message.get("room_id") or "",
        message.get("sender_name") or message.get("sender_id") or "",
        text,
    )
    manager = get_wechat_bridge_manager()
    try:
        result = asyncio.run(_build_wechat_query_response_with_log(repo, query, uploads=uploads))
    except HTTPException as exc:
        result = {"success": False, "reply": str(exc.detail)}
    except Exception as exc:
        LOGGER.exception("内置微信桥处理群消息失败")
        result = {"success": False, "reply": f"查询失败：{exc}"}
    replies = [str(item or "").strip() for item in (result.get("replies") or []) if str(item or "").strip()]
    reply = str(result.get("reply") or "").strip()
    if not replies and reply:
        replies = [reply]
    room_id = str(message.get("stable_room_id") or message.get("room_id") or "").strip()
    if not room_id:
        return
    image_path = _wechat_query_result_image_path(result, uploads)
    try:
        for reply_text in replies:
            manager.send_text(room_id, reply_text)
        if image_path:
            manager.send_image(room_id, str(image_path))
    except Exception:
        LOGGER.exception("内置微信桥发送查询回复失败")


def _configure_wecom_aibot_manager(
    manager: WeComAiBotManager,
    config: dict[str, Any],
    *,
    restart: bool = True,
) -> None:
    merged = _notification_config_with_env_defaults(config)
    # 企业微信自建应用是独立且更稳定的交互入口；启用后不要再同时启动智能机器人长连接。
    enabled = bool(merged.get("wecom_aibot_enabled")) and not bool(merged.get("wecom_app_enabled"))
    try:
        manager.configure(
            enabled=enabled,
            bot_id=str(merged.get("wecom_aibot_id") or ""),
            secret=str(merged.get("wecom_aibot_secret") or ""),
            restart=restart,
        )
    except Exception:
        LOGGER.exception("企业微信智能机器人配置已保存，但连接启动失败")


def _wecom_app_crypto_from_repo(repo: DutyRepository) -> WeComAppCrypto:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    if not bool(config.get("wecom_app_enabled")):
        raise HTTPException(status_code=400, detail="企业微信自建应用交互未启用")
    try:
        return WeComAppCrypto(
            token=str(config.get("wecom_app_token") or ""),
            encoding_aes_key=str(config.get("wecom_app_encoding_aes_key") or ""),
            corp_id=str(config.get("wecom_app_corp_id") or ""),
        )
    except WeComAppCryptoError as exc:
        raise HTTPException(status_code=400, detail=f"企业微信自建应用回调配置不正确：{exc}") from exc


def _wecom_app_client_from_repo(repo: DutyRepository) -> WeComClient:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    corp_id = str(config.get("wecom_app_corp_id") or "").strip()
    secret = str(config.get("wecom_app_secret") or "").strip()
    agent_id = str(config.get("wecom_app_agent_id") or "").strip()
    if not bool(config.get("wecom_app_enabled")) or not corp_id or not secret or not agent_id:
        raise WeComError("企业微信自建应用 CorpID / AgentId / Secret 未配置")
    return WeComClient(corp_id=corp_id, corp_secret=secret, agent_id=int(agent_id))


def _wecom_app_config_complete(config: dict[str, Any], *, require_callback: bool = True) -> bool:
    required = [
        "wecom_app_corp_id",
        "wecom_app_agent_id",
        "wecom_app_secret",
    ]
    if require_callback:
        required.extend(["wecom_app_token", "wecom_app_encoding_aes_key"])
    if not bool(config.get("wecom_app_enabled")) or not all(str(config.get(key) or "").strip() for key in required):
        return False
    try:
        int(str(config.get("wecom_app_agent_id") or "").strip())
    except ValueError:
        return False
    return True


def _wecom_app_userid_lookup(repo: DutyRepository) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for source in (repo.list_personnel(), repo.list_monitored_people()):
        for person in source:
            name = str(person.get("name") or "").strip()
            userid = str(person.get("wecom_userid") or "").strip()
            if name and userid and name not in lookup:
                lookup[name] = userid
    return lookup


def _wecom_app_default_tousers(repo: DutyRepository) -> list[str]:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    target_names = [str(name or "").strip() for name in config.get("wecom_app_target_names", []) if str(name or "").strip()]
    lookup = _wecom_app_userid_lookup(repo)
    if target_names:
        userids = [lookup.get(name, "") for name in target_names if lookup.get(name, "")]
        return list(dict.fromkeys(userids))
    userids = list(dict.fromkeys(lookup.values()))
    return userids


def _wecom_app_tousers_for_function(repo: DutyRepository, function_key: str) -> list[str]:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    function_targets = config.get("wecom_app_function_target_names") if isinstance(config.get("wecom_app_function_target_names"), dict) else {}
    target_names = [
        str(name or "").strip()
        for name in list(function_targets.get(function_key) or [])
        if str(name or "").strip()
    ]
    if not target_names:
        return _wecom_app_default_tousers(repo)
    lookup = _wecom_app_userid_lookup(repo)
    return list(dict.fromkeys([lookup[name] for name in target_names if lookup.get(name)]))


def _wecom_app_function_key_for_event_kind(kind: str) -> str:
    base = _base_reminder_kind(kind)
    if base in {"daily_duty", "daily_duty_test"}:
        return "daily_duty"
    if base in {"patrol_warning_start", "patrol_warning_end", "patrol_warning_test"}:
        return "patrol_warning"
    if base in {"notification_test", "wecom_app_test"}:
        return "system"
    return ""


def _wecom_app_notify_client_from_config(config: dict[str, Any], repo: DutyRepository | None = None) -> WeComAppNotifyClient | None:
    config = _notification_config_with_env_defaults(config)
    if not bool(config.get("wecom_app_enabled")):
        return None
    if not _wecom_app_config_complete(config, require_callback=True):
        return None
    try:
        agent_id = int(str(config.get("wecom_app_agent_id") or "0").strip())
    except ValueError:
        return None
    return WeComAppNotifyClient(
        WeComClient(
            corp_id=str(config.get("wecom_app_corp_id") or "").strip(),
            corp_secret=str(config.get("wecom_app_secret") or "").strip(),
            agent_id=agent_id,
        ),
        default_tousers=_wecom_app_default_tousers(repo) if repo is not None else ["@all"],
    )


async def _handle_wecom_app_message(repo: DutyRepository, uploads: Path, message) -> None:
    userid = str(message.from_user or "").strip()
    if not userid:
        return
    client = _wecom_app_client_from_repo(repo)
    if str(message.msg_type or "").strip() == "image":
        if await _handle_wecom_app_construction_image(repo, uploads, client, message):
            return
        await _handle_wecom_app_roster_image(repo, uploads, client, message)
        return
    text = _wecom_app_message_command_text(message, repo)
    if not text:
        return
    query = _wecom_app_query_from_message(message, text)
    if await _handle_wecom_app_construction_text(repo, uploads, client, message, text, query):
        return
    roster_pending = WECOM_APP_PENDING_ROSTER_IMPORTS.get(_wecom_app_pending_key(query))
    if _is_wecom_app_roster_pending_text(text) and roster_pending:
        try:
            await client.send_text(userid, "正在处理排班导入，请稍候…")
            result = _build_wecom_app_pending_roster_response(repo, query, text)
            _attach_roster_import_image(result, uploads)
            await _send_wecom_app_result(client, userid, result, uploads)
        except Exception:
            LOGGER.exception("企业微信自建应用处理排班导入确认失败")
            try:
                await client.send_text(userid, "排班导入确认处理失败，请稍后再试或到网页端导入。")
            except Exception:
                LOGGER.exception("企业微信自建应用发送排班导入失败提示失败")
        return
    if _is_wecom_app_roster_import_request(text):
        await _send_wecom_app_result(client, userid, _build_wecom_app_roster_import_prompt_response(query), uploads)
        return
    pending = WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.get(_wecom_app_shared_pending_key())
    if _is_wecom_app_pending_account_help_text(text) and _wecom_app_pending_allows_account_help(pending):
        pending["prompt"] = "account_help_retry"
        pending["expires_at"] = time.time() + WECOM_APP_PENDING_TUNNEL_TTL_SECONDS
        await _send_wecom_app_shared_text(repo, client, userid, _wecom_app_tunnel_account_help_reply())
        return
    normalized = _normalize_wechat_query_text(text)
    if not (
        (_is_wecom_app_pending_confirm_text(text) and _wecom_app_pending_allows_confirm(pending))
        or _is_tunnel_mechanical_partner_command(text)
        or _looks_like_duty_wechat_command(normalized, repo, query=query)
    ):
        return
    try:
        await client.send_text(userid, "正在查询，请稍候…")
        result = await _build_wechat_query_response_with_log(repo, query, uploads=uploads)
        result_userid = _wecom_app_shared_touser(repo, userid) if result.get("shared_scope") else userid
        await _send_wecom_app_result(client, result_userid, result, uploads)
    except Exception:
        LOGGER.exception("企业微信自建应用处理消息失败")
        try:
            await client.send_text(userid, "查询失败，请稍后再试或联系管理员查看后台日志。")
        except Exception:
            LOGGER.exception("企业微信自建应用发送失败提示失败")


async def _send_wecom_app_result(client: WeComClient, userid: str, result: dict[str, Any], uploads: Path) -> None:
    replies = [str(item or "").strip() for item in (result.get("replies") or []) if str(item or "").strip()]
    reply = str(result.get("reply") or "").strip()
    if not replies and reply:
        replies = [reply]
    content = "\n".join(replies).strip() or ("处理完成" if result.get("success") else "没有查询到结果")
    image_path = _wechat_query_result_image_path(result, uploads)
    if image_path and _wecom_app_query_result_should_send_news(result):
        image_bytes = image_path.read_bytes()
        title = _wechat_query_image_reply_title(result)
        description = _wecom_app_query_news_description(result, content)
        await client.send_news(
            userid,
            title=title,
            description=description,
            image_bytes=image_bytes,
            url=_notification_news_url(
                title=title,
                description=description,
                image_bytes=image_bytes,
            ),
        )
    elif content:
        await client.send_text(userid, content)


def _wecom_app_message_command_text(message: Any, repo: DutyRepository) -> str:
    event_key = str(getattr(message, "event_key", "") or "").strip()
    if event_key:
        return _wecom_app_menu_command(event_key, repo)
    return str(getattr(message, "content", "") or "").strip()


def _wecom_app_query_from_message(message: Any, text: str) -> WechatQueryRequest:
    userid = str(getattr(message, "from_user", "") or "").strip()
    return WechatQueryRequest(
        text=text,
        channel="wecom_app",
        room_id=f"wecom_app_user:{userid}",
        stable_room_id=f"wecom_app_user:{userid}",
        room_name="企业微信自建应用",
        sender_id=f"wecom_user:{userid}",
        runtime_sender_id=f"wecom_user:{userid}",
        stable_member_id=f"wecom_user:{userid}",
        sender_name=userid,
    )


def _wecom_app_query_result_should_send_news(result: dict[str, Any]) -> bool:
    query_type = str(result.get("query_type") or "").strip()
    return query_type in {
        "help",
        "daily_duty_query",
        "monitor",
        "monitor_all",
        "monitor_range",
        "monitor_all_range",
        "reminder",
        "reminder_all",
        "reminder_range",
        "reminder_all_range",
        "next_reminder",
        "next_reminder_all",
        "rest_query",
        "patrol_record",
        "roster_import",
        "tunnel_mechanical",
        "tunnel_mechanical_modify",
        "tunnel_mechanical_result",
    }


def _compact_wecom_news_text(value: Any, limit: int = 72) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _wecom_app_query_news_description(result: dict[str, Any], fallback: str = "") -> str:
    query_type = str(result.get("query_type") or "").strip()
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    if query_type == "rest_query":
        total = int(details.get("total_days") or 0)
        rested = int(details.get("rested_days") or 0)
        remaining = int(details.get("remaining_days") or 0)
        return f"本月休息{total}天｜已休{rested}天｜剩余{remaining}天" if total else "本月暂无休息排班"
    if query_type == "daily_duty_query":
        parts = [
            f"{label}：{details.get(key)}"
            for label, key in (("早班", "early"), ("中班", "middle"), ("晚班", "night"), ("明日早班", "tomorrow_early"))
            if details.get(key)
        ]
        return _compact_wecom_news_text("｜".join(parts) or f"{result.get('target_date') or '今日'} 在岗信息")
    if query_type == "patrol_record":
        return _compact_wecom_news_text(
            f"{result.get('name') or ''} {result.get('start_date') or ''}至{result.get('end_date') or ''}，"
            f"{int(result.get('count') or 0)}条，{_patrol_record_group_count(result.get('records') or [])}次"
        )
    if query_type == "roster_import":
        status = str(result.get("import_status") or "")
        if status == "conflict":
            return _compact_wecom_news_text(
                f"{result.get('year')}年{result.get('month')}月已存在｜差异{len(result.get('diffs') or [])}处｜"
                f"{_wecom_app_roster_conflict_action_text(compact=True)}"
            )
        if result.get("success"):
            return _compact_wecom_news_text(f"{result.get('year')}年{result.get('month')}月｜{result.get('people_count') or len(result.get('grid') or [])}人｜已导入")
        return _compact_wecom_news_text(str(result.get("reply") or "排班导入需要处理"))
    if query_type in {"tunnel_mechanical", "tunnel_mechanical_modify", "tunnel_mechanical_result"}:
        date_text = str(result.get("finalCheckTime") or result.get("checkTime") or "").strip()
        count = int(result.get("count") or 0)
        labels = {"tunnel_mechanical": "录入", "tunnel_mechanical_modify": "修改", "tunnel_mechanical_result": "查询"}
        return _compact_wecom_news_text(f"{date_text} 隧道机电{labels.get(query_type, '结果')}，共{count}条")
    title = _wechat_query_image_reply_title(result)
    source_text = "\n".join(dict.fromkeys([str(fallback or ""), str(result.get("reply") or "")]))
    for line in source_text.splitlines():
        text = line.strip(" -\t")
        if not text or "图片已生成" in text or "正在发送" in text or text in {title, f"{title}结果如下："} or text.endswith("结果如下："):
            continue
        return _compact_wecom_news_text(text)
    return "点击查看完整结果"


def _handle_wecom_aibot_message(
    repo: DutyRepository,
    uploads: Path,
    manager: WeComAiBotManager,
    message: dict[str, Any],
) -> None:
    if bool(_notification_config_with_env_defaults(repo.get_notification_config()).get("wecom_app_enabled")):
        return
    text = str(message.get("text") or "").strip()
    userid = str(message.get("userid") or "").strip()
    chatid = str(message.get("chatid") or "").strip()
    chattype = str(message.get("chattype") or "").strip()
    room_id = f"wecom_chat:{chatid}" if chatid else f"wecom_user:{userid}"
    sender_id = f"wecom_user:{userid}" if userid else ""
    query = WechatQueryRequest(
        text=text,
        channel="wecom_aibot",
        room_id=room_id,
        stable_room_id=room_id,
        room_name="企业微信群" if chattype == "group" else "企业微信单聊",
        sender_id=sender_id,
        runtime_sender_id=sender_id,
        stable_member_id=sender_id,
        sender_name=userid,
    )
    normalized = _normalize_wechat_query_text(text)
    if not text or not _looks_like_duty_wechat_command(normalized, repo, query=query):
        return
    try:
        manager.reply_progress(message)
    except Exception:
        LOGGER.exception("企业微信智能机器人发送查询进度失败")
        return
    try:
        result = asyncio.run(_build_wechat_query_response_with_log(repo, query, uploads=uploads))
    except HTTPException as exc:
        result = {"success": False, "reply": str(exc.detail)}
    except Exception as exc:
        LOGGER.exception("企业微信智能机器人处理消息失败")
        result = {"success": False, "reply": f"查询失败：{exc}"}
    replies = [str(item or "").strip() for item in (result.get("replies") or []) if str(item or "").strip()]
    reply = str(result.get("reply") or "").strip()
    if not replies and reply:
        replies = [reply]
    content = "\n".join(replies).strip() or ("查询完成" if result.get("success") else "没有查询到结果")
    image_path = _wechat_query_result_image_path(result, uploads)
    try:
        manager.reply_result(message, content, image_path=str(image_path or ""))
    except Exception:
        LOGGER.exception("企业微信智能机器人发送查询结果失败")


def _looks_like_duty_wechat_command(
    text: str,
    repo: DutyRepository | None = None,
    query: WechatQueryRequest | None = None,
) -> bool:
    raw_value = str(text or "").strip()
    value = (
        _wechat_query_menu_selection_command(raw_value)
        if query is not None and _is_wechat_query_pending_menu_selection(query, raw_value)
        else raw_value
    )
    if not value:
        return False
    if repo is not None and _is_wechat_interaction_trigger(repo, value):
        return True
    return any(
        checker(value)
        for checker in (
            _is_tunnel_mechanical_wechat_request,
            _is_tunnel_mechanical_wechat_template_shortcut,
            _is_tunnel_mechanical_wechat_modify_template_shortcut,
            _is_wechat_patrol_record_command,
            _is_wechat_query_help,
            _is_wechat_self_bind_command,
            _is_wechat_binding_query,
            _is_wechat_daily_duty_query,
            _is_wechat_rest_query,
            _is_wecom_app_roster_import_request,
            _is_wecom_app_construction_image_request,
            _is_wecom_app_construction_site_request,
            _is_wechat_next_reminder_query,
            _is_wechat_monitor_query,
        )
    )


def _ignored_wechat_message_response() -> dict[str, Any]:
    return {
        "success": False,
        "query_type": "ignored",
        "reply": "",
        "replies": [],
        "ignored": True,
    }


async def _build_wechat_patrol_record_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    text: str,
    *,
    uploads: Path | None = None,
) -> dict[str, Any] | None:
    if (
        not _is_wechat_patrol_record_command(text)
        and not _is_wechat_patrol_record_template_trigger(repo, text)
        and not _is_orange_warning_patrol_record_template_shortcut(text)
    ):
        return None
    if _is_wechat_patrol_record_template_trigger(repo, text) or _is_orange_warning_patrol_record_template_shortcut(text):
        person_name = _wechat_query_bound_person_name(repo, query)
        if _is_wecom_app_query(query) and not person_name:
            return _wecom_app_bind_required_response(repo, query)
        template = _wechat_patrol_record_template(repo, person_name=person_name)
        return {
            "success": True,
            "query_type": "patrol_record_template",
            "reply": template,
            "replies": [template],
            "template": template,
        }
    if uploads is None:
        return {"success": False, "query_type": "patrol_record", "reply": "当前服务未配置上传目录，无法生成巡查记录图片。"}
    name = _wechat_patrol_record_name(repo, text)
    date_range = _wechat_patrol_record_date_range(text)
    if not name:
        return {
            "success": False,
            "query_type": "patrol_record",
            "reply": f"没有识别到姓名，请按格式发送：{DEFAULT_PATROL_RECORD_TEMPLATE}",
        }
    if date_range is None:
        return {
            "success": False,
            "query_type": "patrol_record",
            "reply": f"没有识别到起止日期，请按格式发送：查询{name}巡查记录 2026-07-01至2026-07-31",
        }
    start_date, end_date = date_range
    config = repo.get_patrol_warning_config()
    state = repo.get_patrol_warning_state()
    try:
        result = await fetch_patrol_records_by_name_result(
            config,
            TZ,
            name=name,
            known_names=_wechat_query_known_person_names(repo),
            token=str(state.get("token") or ""),
            token_expires_at=str(state.get("token_expires_at") or ""),
            limit=5000,
            cache_path=repo.db_path.parent / "patrol-warning-records-cache.json",
        )
    except PatrolWarningError as exc:
        repo.save_send_record(kind="patrol_record_wechat", target=name, status="failed", content=query.text, error=str(exc))
        return {"success": False, "query_type": "patrol_record", "reply": f"巡查记录查询失败：{exc}"}
    repo.save_patrol_warning_state(token=result.token, token_expires_at=result.token_expires_at, last_error="")
    records = [
        record for record in result.records
        if start_date <= _record_start_date(record) <= end_date
    ]
    image_name = f"patrol-record-{uuid.uuid4().hex}.png"
    image_path = uploads / image_name
    uploads.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(
        render_patrol_record_image(
            records,
            name=name,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            route_code=str(config.get("route_code") or "").strip(),
        )
    )
    image_url = f"/api/uploads/{image_name}"
    reply = (
        f"已查询 {name} 巡查记录：{start_date.isoformat()}至{end_date.isoformat()}，"
        f"共 {len(records)} 条，实际次数 {_patrol_record_group_count(records)} 次，图片已生成，正在发送。"
    )
    repo.save_send_record(kind="patrol_record_wechat", target=name, status="success", content=query.text)
    return {
        "success": True,
        "query_type": "patrol_record",
        "name": name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "records": records,
        "count": len(records),
        "image_url": image_url,
        "image_full_url": _public_app_url(image_url),
        "reply": reply,
        "replies": [reply],
    }


def _is_wechat_patrol_record_command(text: str) -> bool:
    value = str(text or "").strip()
    return "巡查记录" in value or value in {"巡查记录", "查询巡查", "查巡查"}


def _is_wechat_patrol_record_template_trigger(repo: DutyRepository, text: str) -> bool:
    value = str(text or "").strip()
    return value in _wechat_interaction_config(repo)["patrol_record_triggers"]


def _is_orange_warning_patrol_record_template_shortcut(text: str) -> bool:
    return str(text or "").strip() in {"橙色预警巡查记录查询", "橙色预警巡查查询", "橙色巡查记录查询"}


def _is_wechat_interaction_trigger(repo: DutyRepository, text: str) -> bool:
    value = str(text or "").strip()
    config = _wechat_interaction_config(repo)
    return value in {
        *config["patrol_record_triggers"],
        *config["tunnel_template_triggers"],
        *config["tunnel_modify_template_triggers"],
    }


def _wechat_patrol_record_template(repo: DutyRepository | None = None, *, person_name: str = "") -> str:
    if repo is None:
        template = DEFAULT_PATROL_RECORD_TEMPLATE
    else:
        template = _wechat_interaction_config(repo)["patrol_record_template"]
    return _personalize_patrol_record_template(template, person_name)


def _wechat_patrol_record_name(repo: DutyRepository, text: str) -> str:
    value = str(text or "")
    known_names = sorted(
        {str(person.get("name") or "").strip() for person in repo.list_personnel() if str(person.get("name") or "").strip()},
        key=len,
        reverse=True,
    )
    for name in known_names:
        if name in value and name not in {"巡查记录", "查询巡查记录"}:
            return name
    match = re.search(r"(?:查询|查)?(.+?)巡查记录", value)
    if match:
        candidate = re.sub(r"[，,、：: ]", "", match.group(1)).strip()
        return candidate if candidate not in {"", "查询"} else ""
    match = re.search(r"巡查记录(?:查询)?(.+)", value)
    if match:
        return re.sub(r"[，,、：: ]", "", match.group(1)).strip()
    return ""


def _wechat_patrol_record_date_range(text: str) -> tuple[date, date] | None:
    matches = re.findall(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", str(text or ""))
    if len(matches) < 2:
        return None
    parsed: list[date] = []
    for item in matches[:2]:
        try:
            parsed.append(date.fromisoformat(item.replace("/", "-")))
        except ValueError:
            return None
    start_date, end_date = parsed
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _record_start_date(record: dict[str, Any]) -> date:
    value = str(record.get("start_time") or "")
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return date.min


def _patrol_record_group_count(records: list[dict[str, Any]]) -> int:
    ordered = sorted(records, key=lambda record: (str(record.get("start_time") or ""), str(record.get("id") or "")))
    count = 0
    index = 0
    while index < len(ordered):
        count += 1
        current = ordered[index]
        while index + 1 < len(ordered) and _patrol_records_can_join(current, ordered[index + 1]):
            index += 1
            current = ordered[index]
        index += 1
    return count


def _patrol_records_can_join(current: dict[str, Any], following: dict[str, Any]) -> bool:
    if str(current.get("route_code") or "").strip().upper() != str(following.get("route_code") or "").strip().upper():
        return False
    current_start = _patrol_record_datetime(current, "start_time", "end_time")
    current_end = _patrol_record_datetime(current, "end_time", "start_time")
    following_start = _patrol_record_datetime(following, "start_time", "end_time")
    if not current_start or not current_end or not following_start:
        return False
    return following_start >= current_start and 0 <= (following_start - current_end).total_seconds() <= 3600


def _patrol_record_datetime(record: dict[str, Any], field: str, fallback_field: str) -> datetime | None:
    value = str(record.get(field) or record.get(fallback_field) or "")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _wechat_query_result_image_path(result: dict[str, Any], uploads: Path) -> Path | None:
    image_url = str(result.get("image_url") or result.get("result_image_url") or "").strip()
    if not image_url.startswith("/api/uploads/"):
        return None
    filename = Path(image_url).name
    path = uploads / filename
    return path if path.exists() else None


def _wecom_app_pending_key(query: WechatQueryRequest) -> str:
    userid = str(query.runtime_sender_id or query.sender_id or "").strip()
    return userid.removeprefix("wecom_user:")


def _wecom_app_shared_pending_key() -> str:
    return WECOM_APP_SHARED_PENDING_KEY


def _wecom_app_shared_touser(repo: DutyRepository, fallback_userid: str) -> str:
    targets = _wecom_app_default_tousers(repo)
    return "|".join(targets) or str(fallback_userid or "").strip()


async def _send_wecom_app_shared_text(repo: DutyRepository, client: WeComClient, fallback_userid: str, content: str) -> None:
    await client.send_text(_wecom_app_shared_touser(repo, fallback_userid), content)


def _wecom_app_pending_prompt(pending: dict[str, Any] | None) -> str:
    return str((pending or {}).get("prompt") or "confirm").strip() or "confirm"


def _wecom_app_pending_allows_confirm(pending: dict[str, Any] | None) -> bool:
    return _wecom_app_pending_prompt(pending) in {"confirm", "retry_options", "account_help_retry"}


def _wecom_app_pending_allows_account_help(pending: dict[str, Any] | None) -> bool:
    return _wecom_app_pending_prompt(pending) == "retry_options"


def _is_wecom_app_pending_confirm_text(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"确认", "1", "重试", "1.重试", "1重试", "重新提交"}


def _is_wecom_app_pending_account_help_text(text: str) -> bool:
    return _normalize_wechat_query_text(text) in {"2", "修改账号密码", "2.修改账号密码", "2修改账号密码", "修改密码", "修改账号"}


def _wecom_app_tunnel_account_help_reply() -> str:
    return (
        "请到网页端修改智慧养护平台账号密码：\n"
        "配置中心 → 隧道机电 → 智慧养护平台账号\n\n"
        "修改并保存后，建议先点“登录测试”。登录测试成功后，回到企业微信自建应用继续回复：1 或 重试。"
    )


def _is_tunnel_mechanical_partner_command(text: str) -> bool:
    value = _normalize_wechat_query_text(text)
    return value.startswith(("设置机电负责人", "设置机电搭档", "设置负责人", "负责人")) and not any(
        keyword in value for keyword in ("录入", "提交", "新增", "添加", "预览", "修改")
    )


def _tunnel_mechanical_partner_name(repo: DutyRepository, text: str, self_name: str = "") -> str:
    value = _normalize_wechat_query_text(text)
    value = re.sub(r"^(设置机电负责人|设置机电搭档|设置负责人|负责人)", "", value, count=1).strip()
    names = _wechat_query_known_person_names(repo)
    for name in sorted(names, key=len, reverse=True):
        if name and (value == name or name in value):
            return name
    return value if value in names and value != self_name else ""


def _personnel_contact_by_name(repo: DutyRepository, name: str) -> dict[str, str]:
    clean = str(name or "").strip()
    return next((person for person in repo.list_personnel() if str(person.get("name") or "").strip() == clean), {})


def _tunnel_mechanical_first_partner_reply(template: dict[str, Any], recorder: str) -> str:
    people = [str(person.get("name") or "").strip() for person in (template.get("people") or []) if person.get("name")]
    examples = "、".join([name for name in people if name and name != recorder][:12])
    suffix = f"\n可选负责人：{examples}" if examples else ""
    return (
        "第一次使用“录入今日机电”前，请先设置你的负责人/搭档。\n"
        "发送：设置机电负责人罗富耀\n"
        "设置好后再点击“录入今日机电”。"
        f"{suffix}"
    )


def _build_tunnel_mechanical_request(
    repo: DutyRepository,
    template: dict[str, Any],
    params: dict[str, Any],
) -> TunnelMechanicalSubmitRequest:
    config = repo.get_tunnel_mechanical_config()
    return TunnelMechanicalSubmitRequest(
        base_url=str(config.get("base_url") or "") or str(template.get("base_url") or ""),
        checkTime=params["checkTime"],
        weather=str(params.get("weather") or ""),
        checkerId=str(params["checker"]["id"]),
        checker=str(params["checker"]["name"]),
        recorderId=str(params["recorder"]["id"]),
        recorder=str(params["recorder"]["name"]),
        rows=[TunnelMechanicalAssetRequest(**asset) for asset in template["assets"]],
        dry_run=bool(params.get("dry_run")),
    )


def _tunnel_mechanical_copy_line(request: TunnelMechanicalSubmitRequest) -> str:
    return (
        f"隧道机电录入 日期{request.checkTime.isoformat()} "
        f"负责人{request.checker} 记录人{request.recorder} 天气{request.weather or '晴'}"
    )


def _remember_wecom_app_tunnel_submission(query: WechatQueryRequest, request: TunnelMechanicalSubmitRequest) -> dict[str, Any]:
    WECOM_APP_PENDING_TUNNEL_SUBMISSIONS[_wecom_app_shared_pending_key()] = {
        "expires_at": time.time() + WECOM_APP_PENDING_TUNNEL_TTL_SECONDS,
        "prompt": "confirm",
        "payload": request.model_dump(mode="json"),
    }
    return _tunnel_mechanical_confirmation_response(request)


def _tunnel_mechanical_confirmation_response(request: TunnelMechanicalSubmitRequest) -> dict[str, Any]:
    selected_count = len([row for row in request.rows if row.enabled])
    copy_line = _tunnel_mechanical_copy_line(request)
    reply = (
        "请确认今日隧道机电录入信息：\n"
        f"日期：{request.checkTime.isoformat()}\n"
        f"负责人：{request.checker}\n"
        f"记录人：{request.recorder}\n"
        f"天气：{request.weather or '晴'}\n"
        f"资产数量：{selected_count} 条\n\n"
        "无误请回复：确认 或 1\n"
        "需要修改请复制下面这行，改好后发送：\n"
        f"{copy_line}"
    )
    return {
        "success": True,
        "query_type": "tunnel_mechanical_confirm",
        "status": "pending",
        "shared_scope": True,
        "reply": reply,
        "replies": [reply],
        "template": copy_line,
    }


def _rebuild_tunnel_mechanical_request(payload: dict[str, Any]) -> TunnelMechanicalSubmitRequest:
    return TunnelMechanicalSubmitRequest(
        **{
            **payload,
            "rows": [TunnelMechanicalAssetRequest(**row) for row in payload.get("rows", [])],
            "checkTime": date.fromisoformat(str(payload.get("checkTime"))),
            "dry_run": False,
        }
    )


async def _submit_tunnel_mechanical_wechat_request(
    repo: DutyRepository,
    query: WechatQueryRequest,
    request: TunnelMechanicalSubmitRequest,
    *,
    uploads: Path | None = None,
) -> dict[str, Any]:
    try:
        result = await _submit_tunnel_mechanical(repo, request, result_upload_dir=uploads)
    except HTTPException as exc:
        detail = str(exc.detail)
        reply = f"隧道机电录入失败：{detail}"
        repo.save_send_record(
            kind="tunnel_mechanical_wechat",
            target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
            status="failed",
            content=reply,
            error=detail,
        )
        return {"success": False, "query_type": "tunnel_mechanical", "reply": reply}
    except Exception as exc:
        reply = f"隧道机电录入失败：{exc}"
        repo.save_send_record(
            kind="tunnel_mechanical_wechat",
            target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
            status="failed",
            content=reply,
            error=str(exc),
        )
        return {"success": False, "query_type": "tunnel_mechanical", "reply": reply}
    selected_count = len([row for row in request.rows if row.enabled])
    result_image_url = _public_app_url(str(result.get("result_image_url") or ""))
    reply = (
        f"隧道机电{'预览' if request.dry_run else '录入'}完成：{request.checkTime.isoformat()}，"
        f"负责人{request.checker}，记录人{request.recorder}，天气{request.weather}，共{selected_count}条。"
        + (f"\n查询结果生成失败：{result.get('result_query_error')}" if result.get("result_query_error") else "")
        if result.get("success")
        else "隧道机电录入未全部成功，请到页面查看提交结果。"
    )
    repo.save_send_record(
        kind="tunnel_mechanical_wechat",
        target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
        status="success" if result.get("success") else "failed",
        content=reply,
        error="" if result.get("success") else "平台返回部分记录失败",
    )
    return {
        "success": bool(result.get("success")),
        "query_type": "tunnel_mechanical",
        "dry_run": request.dry_run,
        "status": "preview" if request.dry_run else ("success" if result.get("success") else "failed"),
        "checkTime": request.checkTime.isoformat(),
        "checkerId": request.checkerId,
        "checker": request.checker,
        "recorderId": request.recorderId,
        "recorder": request.recorder,
        "weather": request.weather,
        "count": selected_count,
        "reply": reply,
        "replies": [reply],
        "image_url": result.get("result_image_url") or "",
        "image_full_url": result_image_url,
        "result": result,
    }


async def _build_wecom_app_pending_tunnel_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    text: str,
    *,
    uploads: Path | None = None,
) -> dict[str, Any] | None:
    if not _is_wecom_app_query(query):
        return None
    pending = WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.get(_wecom_app_shared_pending_key())
    if not pending:
        if _is_wecom_app_pending_confirm_text(text) or _is_wecom_app_pending_account_help_text(text):
            return {"success": False, "query_type": "tunnel_mechanical_confirm", "reply": "没有待确认的机电录入，请先点击“录入今日机电”。"}
        return None
    if (
        _normalize_wechat_query_text(text) == "录入今日机电"
        and float(pending.get("expires_at") or 0) >= time.time()
    ):
        return _tunnel_mechanical_confirmation_response(
            _rebuild_tunnel_mechanical_request(dict(pending.get("payload") or {}))
        )
    if _is_wecom_app_pending_account_help_text(text):
        if not _wecom_app_pending_allows_account_help(pending):
            return None
        pending["prompt"] = "account_help_retry"
        pending["expires_at"] = time.time() + WECOM_APP_PENDING_TUNNEL_TTL_SECONDS
        return {
            "success": True,
            "query_type": "tunnel_mechanical_account_help",
            "shared_scope": True,
            "reply": _wecom_app_tunnel_account_help_reply(),
        }
    if not _is_wecom_app_pending_confirm_text(text):
        return None
    if not _wecom_app_pending_allows_confirm(pending):
        return None
    if float(pending.get("expires_at") or 0) < time.time():
        WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.pop(_wecom_app_shared_pending_key(), None)
        return {"success": False, "query_type": "tunnel_mechanical_confirm", "reply": "待确认信息已过期，请重新点击“录入今日机电”。"}
    request = _rebuild_tunnel_mechanical_request(dict(pending.get("payload") or {}))
    result = await _submit_tunnel_mechanical_wechat_request(repo, query, request, uploads=uploads)
    result["shared_scope"] = True
    if result.get("success"):
        WECOM_APP_PENDING_TUNNEL_SUBMISSIONS.pop(_wecom_app_shared_pending_key(), None)
    else:
        pending["expires_at"] = time.time() + WECOM_APP_PENDING_TUNNEL_TTL_SECONDS
        pending["prompt"] = "retry_options"
        reply = str(result.get("reply") or "").strip()
        if reply:
            result["reply"] = (
                f"{reply}\n\n"
                "这次待确认信息仍保留，请选择：\n"
                "1. 重试\n"
                "2. 修改账号密码\n\n"
                "也可以重新点击“录入今日机电”生成新的确认信息。"
            )
            result["replies"] = [result["reply"]]
    return result


def _build_wecom_app_tunnel_partner_response(repo: DutyRepository, query: WechatQueryRequest, text: str) -> dict[str, Any] | None:
    if not _is_wecom_app_query(query) or not _is_tunnel_mechanical_partner_command(text):
        return None
    recorder = _wechat_query_bound_person_name(repo, query)
    if not recorder:
        return _wecom_app_bind_required_response(repo, query)
    partner = _tunnel_mechanical_partner_name(repo, text, recorder)
    template = _public_tunnel_mechanical_template(repo.get_tunnel_mechanical_template())
    people = {str(person.get("name") or "").strip() for person in (template.get("people") or []) if person.get("name")}
    if not partner or partner not in people:
        return {
            "success": False,
            "query_type": "tunnel_mechanical_partner",
            "reply": _tunnel_mechanical_first_partner_reply(template, recorder),
        }
    if partner == recorder:
        return {
            "success": False,
            "query_type": "tunnel_mechanical_partner",
            "reply": "负责人/搭档不能和记录人相同，请发送：设置机电负责人姓名。",
        }
    repo.set_tunnel_mechanical_partner(recorder, partner)
    reply = f"已设置你的机电负责人/搭档：{partner}\n以后点击“录入今日机电”会默认使用负责人{partner}、记录人{recorder}。"
    return {"success": True, "query_type": "tunnel_mechanical_partner", "reply": reply, "replies": [reply]}


async def _build_tunnel_mechanical_wechat_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    text: str,
    *,
    uploads: Path | None = None,
) -> dict[str, Any] | None:
    if (
        not _is_tunnel_mechanical_wechat_request(text)
        and not _is_tunnel_mechanical_wechat_template_shortcut(text, repo)
        and not _is_tunnel_mechanical_wechat_modify_template_shortcut(text, repo)
    ):
        return None
    template = _public_tunnel_mechanical_template(repo.get_tunnel_mechanical_template())
    bound_person_name = _wechat_query_bound_person_name(repo, query)
    if _is_tunnel_mechanical_wechat_modify_template_shortcut(text, repo):
        if _is_wecom_app_query(query) and not bound_person_name:
            return _wecom_app_bind_required_response(repo, query)
        template_line = _tunnel_mechanical_wechat_modify_template_line(template, query.target_date, repo=repo, person_name=bound_person_name)
        return {
            "success": True,
            "query_type": "tunnel_mechanical_modify_template",
            "reply": template_line,
            "replies": [template_line],
            "template": template_line,
        }
    if _is_tunnel_mechanical_wechat_template_shortcut(text, repo):
        if _is_wecom_app_query(query) and not bound_person_name:
            return _wecom_app_bind_required_response(repo, query)
        template_line = _tunnel_mechanical_wechat_template_line(template, query.target_date, repo=repo, person_name=bound_person_name)
        return {
            "success": True,
            "query_type": "tunnel_mechanical_template",
            "reply": template_line,
            "replies": [template_line],
            "template": template_line,
        }
    if _is_tunnel_mechanical_wechat_modify_command(text):
        return await _build_tunnel_mechanical_wechat_modify_response(repo, query, text, template, uploads=uploads)
    if _is_tunnel_mechanical_wechat_template_command(text, repo):
        if _is_wecom_app_query(query) and not bound_person_name:
            return _wecom_app_bind_required_response(repo, query)
        return _tunnel_mechanical_wechat_template_response(template, query.target_date, repo=repo, person_name=bound_person_name)
    if _is_tunnel_mechanical_wechat_result_query_command(text):
        return await _build_tunnel_mechanical_wechat_result_query_response(repo, query, text, template, uploads=uploads)
    if not _is_tunnel_mechanical_wechat_submit_command(text):
        return _tunnel_mechanical_wechat_template_response(template, query.target_date, repo=repo, person_name=bound_person_name)
    if not template["assets"] or not template["people"]:
        return {
            "success": False,
            "query_type": "tunnel_mechanical",
            "reply": "还没有导入隧道机电模板，请先在页面点击“导入模板”。",
        }
    if _is_wecom_app_query(query) and _normalize_wechat_query_text(text) == "录入今日机电":
        if not bound_person_name:
            return _wecom_app_bind_required_response(repo, query)
        partner = str(_personnel_contact_by_name(repo, bound_person_name).get("tunnel_mechanical_partner") or "").strip()
        if not partner:
            return {"success": False, "query_type": "tunnel_mechanical_partner", "reply": _tunnel_mechanical_first_partner_reply(template, bound_person_name)}
        text = _personalize_checker_field(
            _tunnel_mechanical_wechat_template_line(template, query.target_date, repo=repo, person_name=bound_person_name),
            partner,
        )
    params = _parse_tunnel_mechanical_wechat_params(text, template["people"], query.target_date)
    missing = []
    if not params.get("checker"):
        missing.append("负责人/检查人")
    if not params.get("recorder"):
        missing.append("记录人")
    if missing:
        example = _tunnel_mechanical_wechat_template_line(template, params["checkTime"], repo=repo, person_name=bound_person_name)
        return {
            "success": False,
            "query_type": "tunnel_mechanical",
            "reply": (
                "隧道机电录入参数不完整：缺少" + "、".join(missing) + "。\n"
                f"示例：{example}"
            ),
        }
    dry_run = "预览" in text
    params["dry_run"] = dry_run
    request = _build_tunnel_mechanical_request(repo, template, params)
    if _is_wecom_app_query(query) and not dry_run:
        return _remember_wecom_app_tunnel_submission(query, request)
    return await _submit_tunnel_mechanical_wechat_request(repo, query, request, uploads=uploads)


async def _build_tunnel_mechanical_wechat_result_query_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    text: str,
    template: dict[str, Any],
    *,
    uploads: Path | None = None,
) -> dict[str, Any]:
    if not template["assets"]:
        return {
            "success": False,
            "query_type": "tunnel_mechanical_result",
            "reply": "还没有导入隧道机电模板，无法按资产匹配查询结果。请先在页面点击“导入模板”。",
        }
    if uploads is None:
        return {
            "success": False,
            "query_type": "tunnel_mechanical_result",
            "reply": "当前服务未配置上传目录，无法生成隧道机电查询结果图片。",
        }
    target_date = _tunnel_mechanical_wechat_date(text, query.target_date)
    config = repo.get_tunnel_mechanical_config()
    request = TunnelMechanicalSubmitRequest(
        base_url=str(config.get("base_url") or "") or str(template.get("base_url") or ""),
        checkTime=target_date,
        weather="",
        checkerId="",
        checker="",
        recorderId="",
        recorder="",
        rows=[TunnelMechanicalAssetRequest(**asset) for asset in template["assets"]],
        dry_run=False,
    )
    try:
        result = await _query_tunnel_mechanical_result_image(repo, request, uploads)
    except HTTPException as exc:
        detail = str(exc.detail)
        reply = f"隧道机电查询失败：{detail}"
        repo.save_send_record(
            kind="tunnel_mechanical_query_wechat",
            target=target_date.isoformat(),
            status="failed",
            content=reply,
            error=detail,
        )
        return {"success": False, "query_type": "tunnel_mechanical_result", "reply": reply}
    except Exception as exc:
        reply = f"隧道机电查询失败：{exc}"
        repo.save_send_record(
            kind="tunnel_mechanical_query_wechat",
            target=target_date.isoformat(),
            status="failed",
            content=reply,
            error=str(exc),
        )
        return {"success": False, "query_type": "tunnel_mechanical_result", "reply": reply}
    result_image_url = str(result.get("result_image_url") or "")
    image_full_url = _public_app_url(result_image_url)
    success = bool(result.get("success"))
    reply = (
        f"已查询 {target_date.isoformat()} 隧道机电结果，共 {len(result.get('result_rows') or [])} 条，图片已生成，正在发送。"
        if success
        else f"隧道机电查询失败：{result.get('result_query_error') or '未生成查询结果图片'}"
    )
    repo.save_send_record(
        kind="tunnel_mechanical_query_wechat",
        target=target_date.isoformat(),
        status="success" if success else "failed",
        content=reply,
        error="" if success else str(result.get("result_query_error") or "未生成查询结果图片"),
    )
    row_count = len(result.get("result_rows") or [])
    return {
        "success": success,
        "query_type": "tunnel_mechanical_result",
        "checkTime": target_date.isoformat(),
        "count": row_count,
        "reply": reply,
        "image_url": result_image_url,
        "image_full_url": image_full_url,
        "result": result,
    }


async def _build_tunnel_mechanical_wechat_modify_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    text: str,
    template: dict[str, Any],
    *,
    uploads: Path | None = None,
) -> dict[str, Any]:
    if not template["assets"] or not template["people"]:
        return {
            "success": False,
            "query_type": "tunnel_mechanical_modify",
            "reply": "还没有导入隧道机电模板，请先在页面点击“导入模板”。",
        }
    source_text, changes_text = _split_tunnel_mechanical_wechat_modify_text(text)
    params = _parse_tunnel_mechanical_wechat_params(source_text, template["people"], None)
    changes, invalid_fields = _parse_tunnel_mechanical_wechat_changes(changes_text, template["people"])
    if invalid_fields:
        return {
            "success": False,
            "query_type": "tunnel_mechanical_modify",
            "reply": "隧道机电修改参数无法识别：" + "、".join(invalid_fields) + "。请使用“修改日期为2026-07-25”“负责人改为姓名”“记录人改为姓名”“修改天气为晴”。",
        }
    if not changes:
        example_date = params["checkTime"].isoformat()
        return {
            "success": False,
            "query_type": "tunnel_mechanical_modify",
            "reply": (
                "请说明要修改的字段。\n"
                f"示例：隧道机电修改 日期{example_date} 负责人罗越 记录人罗富耀 天气晴 修改日期为2026-07-25\n"
                "也可以写：修改天气为多云、负责人改为罗越、记录人改为罗富耀。"
            ),
        }
    dry_run = "预览" in text
    config = repo.get_tunnel_mechanical_config()
    request = TunnelMechanicalModifyRequest(
        base_url=str(config.get("base_url") or "") or str(template.get("base_url") or ""),
        checkTime=params["checkTime"],
        weather=str(params.get("weather") or ""),
        checkerId=str((params.get("checker") or {}).get("id") or ""),
        checker=str((params.get("checker") or {}).get("name") or ""),
        recorderId=str((params.get("recorder") or {}).get("id") or ""),
        recorder=str((params.get("recorder") or {}).get("name") or ""),
        newCheckTime=changes.get("checkTime"),
        newWeather=str(changes.get("weather") or ""),
        newCheckerId=str((changes.get("checker") or {}).get("id") or ""),
        newChecker=str((changes.get("checker") or {}).get("name") or ""),
        newRecorderId=str((changes.get("recorder") or {}).get("id") or ""),
        newRecorder=str((changes.get("recorder") or {}).get("name") or ""),
        dry_run=dry_run,
    )
    try:
        result = await _modify_tunnel_mechanical(repo, request, result_upload_dir=uploads)
    except HTTPException as exc:
        detail = str(exc.detail)
        reply = f"隧道机电修改失败：{detail}"
        repo.save_send_record(
            kind="tunnel_mechanical_wechat_modify",
            target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
            status="failed",
            content=reply,
            error=detail,
        )
        return {"success": False, "query_type": "tunnel_mechanical_modify", "reply": reply}
    except Exception as exc:
        reply = f"隧道机电修改失败：{exc}"
        repo.save_send_record(
            kind="tunnel_mechanical_wechat_modify",
            target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
            status="failed",
            content=reply,
            error=str(exc),
        )
        return {"success": False, "query_type": "tunnel_mechanical_modify", "reply": reply}
    success = bool(result.get("success"))
    final_date = (request.newCheckTime or request.checkTime).isoformat()
    change_text = "，".join(_tunnel_mechanical_modify_change_labels(request))
    reply = (
        f"隧道机电修改完成：{request.checkTime.isoformat()} -> {final_date}，{change_text or '已更新'}。"
        if success
        else "隧道机电修改未全部成功，请到页面查看提交结果。"
    )
    repo.save_send_record(
        kind="tunnel_mechanical_wechat_modify",
        target=f"{request.checkTime.isoformat()} -> {final_date}",
        status="success" if success else "failed",
        content=reply,
        error="" if success else "平台返回部分记录修改失败",
    )
    result_image_url = str(result.get("result_image_url") or "")
    return {
        "success": success,
        "query_type": "tunnel_mechanical_modify",
        "dry_run": dry_run,
        "status": "preview" if dry_run else ("success" if success else "failed"),
        "checkTime": request.checkTime.isoformat(),
        "finalCheckTime": final_date,
        "count": int(result.get("count") or 0),
        "changes": {
            "checkTime": request.newCheckTime.isoformat() if request.newCheckTime else "",
            "weather": request.newWeather,
            "checkerId": request.newCheckerId,
            "checker": request.newChecker,
            "recorderId": request.newRecorderId,
            "recorder": request.newRecorder,
        },
        "reply": (
            f"隧道机电{'预览' if dry_run else '修改'}完成：匹配 {result.get('count') or 0} 条，已{'' if dry_run else '提交'}修改{change_text}。"
            + (f"\n查询结果生成失败：{result.get('result_query_error')}" if result.get("result_query_error") else "")
            if success
            else "隧道机电修改未全部成功，请到页面查看提交结果。"
        ),
        "image_url": result_image_url,
        "image_full_url": _public_app_url(result_image_url),
        "result": result,
    }


def _is_tunnel_mechanical_wechat_request(text: str) -> bool:
    value = str(text or "").strip()
    return (
        value == "机电"
        or "隧道机电" in value
        or "机电日常检查" in value
        or ("机电" in value and any(keyword in value for keyword in ("查询", "查", "今日", "今天", "昨日", "昨天", "明日", "明天")))
    )


def _is_tunnel_mechanical_wechat_template_shortcut(text: str, repo: DutyRepository | None = None) -> bool:
    value = str(text or "").strip()
    triggers = _wechat_interaction_config(repo)["tunnel_template_triggers"] if repo is not None else DEFAULT_TUNNEL_TEMPLATE_TRIGGERS
    return value in triggers


def _is_tunnel_mechanical_wechat_modify_template_shortcut(text: str, repo: DutyRepository | None = None) -> bool:
    value = str(text or "").strip()
    triggers = _wechat_interaction_config(repo)["tunnel_modify_template_triggers"] if repo is not None else DEFAULT_TUNNEL_MODIFY_TEMPLATE_TRIGGERS
    return value in triggers


def _public_app_url(path: str) -> str:
    text = str(path or "").strip()
    if not text or text.startswith("http://") or text.startswith("https://"):
        return text
    base_url = os.getenv("DUTY_REMINDER_PUBLIC_URL", "").strip().rstrip("/")
    return f"{base_url}{text}" if base_url and text.startswith("/") else text


def _is_tunnel_mechanical_wechat_submit_command(text: str) -> bool:
    return _is_tunnel_mechanical_wechat_request(text) and any(
        keyword in text for keyword in ("录入", "提交", "新增", "添加", "预览")
    )


def _is_tunnel_mechanical_wechat_modify_command(text: str) -> bool:
    return _is_tunnel_mechanical_wechat_request(text) and any(
        keyword in text for keyword in ("修改", "更改", "改为", "改成")
    )


def _is_tunnel_mechanical_wechat_template_command(text: str, repo: DutyRepository | None = None) -> bool:
    return _is_tunnel_mechanical_wechat_template_shortcut(text, repo) or (
        _is_tunnel_mechanical_wechat_request(text) and any(keyword in text for keyword in ("格式", "模板", "示例"))
    )


def _is_tunnel_mechanical_wechat_result_query_command(text: str) -> bool:
    return _is_tunnel_mechanical_wechat_request(text) and any(keyword in text for keyword in ("查询", "查"))


def _tunnel_mechanical_wechat_template_response(
    template: dict[str, Any],
    target_date: date | None = None,
    *,
    repo: DutyRepository | None = None,
    person_name: str = "",
) -> dict[str, Any]:
    intro = _tunnel_mechanical_wechat_template_reply(template, target_date, repo=repo, person_name=person_name)
    template_line = _tunnel_mechanical_wechat_template_line(template, target_date, repo=repo, person_name=person_name)
    return {
        "success": True,
        "query_type": "tunnel_mechanical_template",
        "reply": f"{intro}\n\n{template_line}",
        "replies": [intro, template_line],
        "template": template_line,
    }


def _render_wechat_template_text(pattern: str, target_date: date | None = None) -> str:
    check_time = (target_date or _today_in_tz()).isoformat()
    text = str(pattern or "").strip()
    if not text:
        return ""
    return text.replace("{{date}}", check_time).replace("{date}", check_time)


def _tunnel_mechanical_wechat_template_line(
    template: dict[str, Any],
    target_date: date | None = None,
    *,
    repo: DutyRepository | None = None,
    person_name: str = "",
) -> str:
    del template
    pattern = _wechat_interaction_config(repo)["tunnel_template"] if repo is not None else DEFAULT_TUNNEL_TEMPLATE
    rendered = _render_wechat_template_text(pattern, target_date) or _render_wechat_template_text(DEFAULT_TUNNEL_TEMPLATE, target_date)
    return _personalize_recorder_field(rendered, person_name)


def _tunnel_mechanical_wechat_modify_template_line(
    template: dict[str, Any],
    target_date: date | None = None,
    *,
    repo: DutyRepository | None = None,
    person_name: str = "",
) -> str:
    del template
    pattern = _wechat_interaction_config(repo)["tunnel_modify_template"] if repo is not None else DEFAULT_TUNNEL_MODIFY_TEMPLATE
    rendered = _render_wechat_template_text(pattern, target_date) or _render_wechat_template_text(DEFAULT_TUNNEL_MODIFY_TEMPLATE, target_date)
    return _personalize_recorder_field(rendered, person_name)


def _tunnel_mechanical_wechat_template_reply(
    template: dict[str, Any],
    target_date: date | None = None,
    *,
    repo: DutyRepository | None = None,
    person_name: str = "",
) -> str:
    check_time = (target_date or _today_in_tz()).isoformat()
    asset_count = len(template.get("assets") or [])
    people = [str(person.get("name") or "").strip() for person in (template.get("people") or []) if person.get("name")]
    people_line = f"\n可用人员：{'、'.join(people[:20])}" if people else ""
    asset_line = f"\n当前模板资产：{asset_count} 条" if asset_count else "\n当前还没有导入隧道模板，请先在页面导入模板。"
    return (
        "隧道机电功能\n"
        "查询结果图：\n"
        "- 查询今日机电\n"
        f"- 查询{check_time}机电\n\n"
        "录入记录：\n"
        "- 发送“模板”获取可复制录入模板\n"
        "- 把模板里的日期、负责人、记录人、天气改好后发送\n"
        "- 只想预览请求，把“录入”改成“预览”\n\n"
        "修改记录：\n"
        "- 发送“修改模板”获取可复制修改模板\n"
        f"- {_tunnel_mechanical_wechat_modify_template_line(template, target_date, repo=repo, person_name=person_name)}\n"
        f"- 也可以修改天气、负责人、记录人，例如：修改天气为多云、负责人改为罗富耀、记录人改为{person_name or '商邱宏'}\n"
        f"登录失效时会自动重新登录；验证码识别失败会自动重试。"
        f"{asset_line}"
        f"{people_line}"
    )


def _parse_tunnel_mechanical_wechat_params(
    text: str,
    people: list[dict[str, Any]],
    requested_date: date | None = None,
) -> dict[str, Any]:
    return {
        "checkTime": _tunnel_mechanical_wechat_date(text, requested_date),
        "weather": _tunnel_mechanical_wechat_weather(text),
        "checker": _tunnel_mechanical_wechat_person(text, people, ("负责人", "检查人", "checker")),
        "recorder": _tunnel_mechanical_wechat_person(text, people, ("记录人", "recorder")),
    }


def _split_tunnel_mechanical_wechat_modify_text(text: str) -> tuple[str, str]:
    marker_pattern = "|".join(
        re.escape(marker)
        for marker in (
            "修改日期为",
            "日期改为",
            "日期改成",
            "改日期为",
            "修改天气为",
            "天气改为",
            "天气改成",
            "改天气为",
            "修改负责人为",
            "负责人改为",
            "负责人改成",
            "检查人改为",
            "检查人改成",
            "修改记录人为",
            "记录人改为",
            "记录人改成",
        )
    )
    match = re.search(marker_pattern, text)
    if not match:
        return text, ""
    return text[: match.start()], text[match.start() :]


def _parse_tunnel_mechanical_wechat_changes(
    text: str,
    people: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    changes: dict[str, Any] = {}
    invalid_fields: list[str] = []
    specs = {
        "checkTime": ("日期", ("修改日期为", "日期改为", "日期改成", "改日期为")),
        "weather": ("天气", ("修改天气为", "天气改为", "天气改成", "改天气为")),
        "checker": ("负责人", ("修改负责人为", "负责人改为", "负责人改成", "检查人改为", "检查人改成")),
        "recorder": ("记录人", ("修改记录人为", "记录人改为", "记录人改成")),
    }
    all_markers = tuple(marker for _, markers in specs.values() for marker in markers)
    for field, (label, markers) in specs.items():
        segment = _tunnel_mechanical_wechat_change_segment(text, markers, all_markers)
        if segment is None:
            continue
        if field == "checkTime":
            parsed_date = _tunnel_mechanical_wechat_optional_date(segment)
            if parsed_date is None:
                invalid_fields.append(label)
            else:
                changes[field] = parsed_date
        elif field == "weather":
            weather = _tunnel_mechanical_wechat_weather(segment)
            if not weather:
                invalid_fields.append(label)
            else:
                changes[field] = weather
        else:
            person = _tunnel_mechanical_wechat_person(segment, people, ("负责人", "检查人", "记录人", "checker", "recorder"))
            if person is None:
                person = _tunnel_mechanical_wechat_named_person(segment, people)
            if person is None:
                invalid_fields.append(label)
            else:
                changes[field] = person
    return changes, invalid_fields


def _tunnel_mechanical_wechat_change_segment(
    text: str,
    markers: tuple[str, ...],
    all_markers: tuple[str, ...],
) -> str | None:
    lowered = text.lower()
    starts = [(index, marker) for marker in markers if (index := lowered.find(marker.lower())) >= 0]
    if not starts:
        return None
    start, marker = min(starts, key=lambda item: item[0])
    after_start = start + len(marker)
    next_boundaries = [
        index
        for other_marker in all_markers
        if (index := lowered.find(other_marker.lower(), after_start)) > after_start
    ]
    end = min(next_boundaries, default=len(text))
    return text[start:end]


def _tunnel_mechanical_wechat_optional_date(text: str) -> date | None:
    today = _today_in_tz()
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text or "明日" in text:
        return today + timedelta(days=1)
    if "昨天" in text or "昨日" in text:
        return today - timedelta(days=1)
    if "今天" in text or "今日" in text:
        return today
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|号)?", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.search(r"(?<!\d)(\d{1,2})[-/.月](\d{1,2})(?:日|号)?", text)
    if match:
        return _wechat_query_month_day(today, int(match.group(1)), int(match.group(2)))
    return None


def _tunnel_mechanical_wechat_named_person(
    text: str,
    people: list[dict[str, Any]],
) -> dict[str, str] | None:
    sorted_people = sorted(people, key=lambda person: len(str(person["name"])), reverse=True)
    for person in sorted_people:
        name = str(person["name"])
        if name and name in text:
            return {"id": str(person["id"]), "name": name}
    return None


def _tunnel_mechanical_modify_change_labels(request: TunnelMechanicalModifyRequest) -> list[str]:
    labels: list[str] = []
    if request.newCheckTime:
        labels.append(f"日期为{request.newCheckTime.isoformat()}")
    if request.newChecker:
        labels.append(f"负责人为{request.newChecker}")
    if request.newRecorder:
        labels.append(f"记录人为{request.newRecorder}")
    if request.newWeather:
        labels.append(f"天气为{request.newWeather}")
    return labels


def _tunnel_mechanical_wechat_date(text: str, requested_date: date | None = None) -> date:
    if requested_date:
        return requested_date
    today = _today_in_tz()
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text or "明日" in text:
        return today + timedelta(days=1)
    if "昨天" in text or "昨日" in text:
        return today - timedelta(days=1)
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|号)?", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return today
    match = re.search(r"(?<!\d)(\d{1,2})[-/.月](\d{1,2})(?:日|号)?", text)
    if match:
        parsed = _wechat_query_month_day(today, int(match.group(1)), int(match.group(2)))
        if parsed:
            return parsed
    return today


def _tunnel_mechanical_wechat_person(
    text: str,
    people: list[dict[str, Any]],
    labels: tuple[str, ...],
) -> dict[str, str] | None:
    sorted_people = sorted(people, key=lambda person: len(str(person["name"])), reverse=True)
    boundary_labels = ("负责人", "检查人", "checker", "记录人", "recorder", "日期", "时间", "天气", "weather")
    for label in labels:
        index = text.lower().find(label.lower())
        if index < 0:
            continue
        after_label = text[index + len(label):]
        boundary = min(
            [next_index for marker in boundary_labels if (next_index := after_label.lower().find(marker.lower())) > 0],
            default=len(after_label),
        )
        segment = after_label[:boundary]
        for person in sorted_people:
            name = str(person["name"])
            if name and name in segment:
                return {"id": str(person["id"]), "name": name}
    return None


def _tunnel_mechanical_wechat_weather(text: str) -> str:
    weather_words = ["雷阵雨", "暴雨", "大雨", "中雨", "小雨", "阵雨", "多云", "阴", "晴", "雨", "雪", "雾"]
    for label in ("天气", "weather"):
        index = text.lower().find(label.lower())
        if index < 0:
            continue
        after_label = text[index + len(label):].lstrip(":：= ")
        for word in weather_words:
            if after_label.startswith(word):
                return word
    for word in weather_words:
        if word in text:
            return word
    return ""


def _strip_leading_wechat_mentions(text: str) -> str:
    value = str(text or "").strip()
    mention_separator = r"[\s\u2005\u2006\u2007\u2008\u2009\u200a]+"
    for _ in range(5):
        match = re.match(rf"^@(?P<name>.*?){mention_separator}(?P<rest>.*)$", value, re.DOTALL)
        if not match:
            break
        name = str(match.group("name") or "").strip()
        if not name:
            break
        value = str(match.group("rest") or "").strip()
    return value


def _strip_wechat_mentions(text: str) -> str:
    value = _strip_leading_wechat_mentions(str(text or ""))
    mention_pattern = r"@[^@\s\u2005\u2006\u2007\u2008\u2009\u200a，,。.!！?？：:]+"
    return re.sub(mention_pattern, "", value).strip()


def _normalize_wechat_query_text(text: str) -> str:
    value = _strip_wechat_mentions(str(text or ""))
    return re.sub(r"\s+", "", value).strip("，,。.!！?？：:")


def _is_wechat_query_help(text: str) -> bool:
    if text in {"查询", "查", "菜单", "帮助", "查询帮助", "监控帮助", "提醒帮助"}:
        return True
    return "帮助" in text and any(keyword in text for keyword in ("查询", "监控", "提醒", "绑定"))


def _wechat_query_menu_selection_command(text: str) -> str:
    today = _today_in_tz().isoformat()
    return {
        "1": "查询我的监控",
        "2": "查询今日在岗",
        "3": "查询今日监控",
        "4": "查询明日监控",
        "5": "查询本周监控",
        "6": "查询未来7天",
        "7": "查询我的绑定",
        "8": "查询今日机电",
        "9": f"查询{today}机电",
        "10": "隧道机电",
        "11": "查询休息",
        "12": "施工图片",
        "13": "施工点维护",
    }.get(text, text)


def _wechat_query_context_key(query: WechatQueryRequest) -> str:
    channel = str(query.channel or "").strip() or "wechat"
    room = str(query.stable_room_id or query.room_id or "").strip()
    sender = str(
        query.runtime_sender_id
        or query.sender_id
        or query.stable_member_id
        or query.sender_name
        or ""
    ).strip()
    return "|".join([channel, room, sender])


def _remember_wechat_query_menu_prompt(query: WechatQueryRequest) -> None:
    key = _wechat_query_context_key(query)
    if key.strip("|"):
        WECHAT_QUERY_PENDING_MENUS[key] = time.time() + WECHAT_QUERY_MENU_TTL_SECONDS


def _consume_wechat_query_menu_selection(query: WechatQueryRequest, text: str) -> str:
    value = str(text or "").strip()
    mapped = _wechat_query_menu_selection_command(value)
    if mapped == value:
        return value
    key = _wechat_query_context_key(query)
    expires_at = float(WECHAT_QUERY_PENDING_MENUS.get(key) or 0)
    if expires_at < time.time():
        WECHAT_QUERY_PENDING_MENUS.pop(key, None)
        return value
    WECHAT_QUERY_PENDING_MENUS.pop(key, None)
    return mapped


def _is_wechat_query_pending_menu_selection(query: WechatQueryRequest, text: str) -> bool:
    value = str(text or "").strip()
    if _wechat_query_menu_selection_command(value) == value:
        return False
    key = _wechat_query_context_key(query)
    expires_at = float(WECHAT_QUERY_PENDING_MENUS.get(key) or 0)
    if expires_at < time.time():
        WECHAT_QUERY_PENDING_MENUS.pop(key, None)
        return False
    return True


def _is_wechat_binding_query(text: str) -> bool:
    return text in {"查询我的绑定", "我的绑定", "查我的绑定", "绑定查询", "我绑定了吗", "我的微信绑定"}


def _is_wechat_self_bind_command(text: str) -> bool:
    value = str(text or "").strip()
    return not _is_wechat_binding_query(value) and bool(re.match(r"^(绑定|我是|我叫).+", value))


def _is_wechat_next_reminder_query(text: str) -> bool:
    return text in {"查询下次提醒", "下次提醒", "我的下次提醒", "最近提醒", "下一次提醒", "我下次什么时候提醒"}


def _is_wechat_daily_duty_query(text: str) -> bool:
    value = str(text or "").strip()
    return value in {
        "查询今日在岗",
        "今日在岗",
        "查询今天在岗",
        "今天在岗",
        "查询在岗",
        "在岗查询",
        "今日值守",
        "查询今日值守",
    }


def _is_wechat_rest_query(text: str) -> bool:
    value = str(text or "").strip()
    if value in {"查询休息", "休息查询", "我的休息", "查询我的休息", "我什么时候休息", "本月休息"}:
        return True
    return bool(re.match(r"^(查询|查).{1,12}休息$", value))


def _is_wechat_self_scoped_query(text: str) -> bool:
    value = str(text or "").strip()
    return "我的" in value or value.startswith("我") or "我今天" in value or "我明天" in value or "我后天" in value


def _is_wechat_reminder_query(text: str) -> bool:
    value = str(text or "").strip()
    return "提醒" in value and "监控" not in value and "排班" not in value


def _is_wechat_generic_self_monitor_query(text: str) -> bool:
    return str(text or "").strip() in {
        "查询我的监控",
        "查我的监控",
        "我的监控",
        "查询我的排班",
        "我的排班",
        "查询我的值班",
        "我的值班",
        "我的班",
    }


def _is_wechat_monitor_query(text: str) -> bool:
    if text in {
        "查询我的监控",
        "查我的监控",
        "我的监控",
        "查询我的排班",
        "我的排班",
        "查询今日监控",
        "今日监控",
        "查询今天监控",
        "今天监控",
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
    }:
        return True
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[日号]?|\d{1,2}/\d{1,2}", text):
        return any(keyword in text for keyword in ("查询", "监控", "排班", "提醒", "值班", "什么班", "上班吗"))
    if re.search(r"(?:未来|接下来|最近)(?:\d{1,2}|[一二两三四五六七八九十]+)天", text):
        return True
    if text.startswith(("查询", "查")) and any(keyword in text for keyword in ("监控", "排班", "提醒", "值班", "什么班", "上班吗")):
        return True
    return any(
        keyword in text
        for keyword in (
            "我的监控",
            "我的排班",
            "今日监控",
            "今天监控",
            "今日提醒",
            "今天提醒",
            "明日监控",
            "明天监控",
            "明日提醒",
            "明天提醒",
            "什么班",
            "上班吗",
            "本周监控",
            "这周监控",
            "下周监控",
            "未来7天",
            "未来七天",
            "接下来7天",
            "接下来七天",
        )
    ) and any(prefix in text for prefix in ("查询", "查", "我", "今天", "明天", "后天", "本周", "这周", "下周", "未来", "接下来"))


def _wechat_query_target_date(text: str) -> date:
    today = _today_in_tz()
    explicit = _wechat_query_explicit_date(text, today)
    if explicit:
        return explicit
    if "后天" in text:
        return today + timedelta(days=2)
    if "明日" in text or "明天" in text:
        return today + timedelta(days=1)
    return today


def _wechat_query_range(text: str, requested_date: date | None = None) -> tuple[date, int]:
    today = _today_in_tz()
    start = requested_date or _wechat_query_target_date(text)
    if "下周" in text:
        next_monday = today + timedelta(days=(7 - today.weekday()))
        return next_monday, 7
    if "本周" in text or "这周" in text:
        return today, max(1, 7 - today.weekday())
    match = re.search(r"(?:未来|接下来|最近)(\d{1,2}|[一二两三四五六七八九十]+)天", text)
    if match:
        return today, min(max(_wechat_query_chinese_int(match.group(1)), 1), 14)
    if "未来七天" in text or "接下来七天" in text:
        return today, 7
    return start, 1


def _wechat_query_explicit_date(text: str, today: date) -> date | None:
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})月(\d{1,2})(?:日|号)?", text)
    if match:
        return _wechat_query_month_day(today, int(match.group(1)), int(match.group(2)))
    match = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", text)
    if match:
        return _wechat_query_month_day(today, int(match.group(1)), int(match.group(2)))
    return None


def _wechat_query_month_day(today: date, month: int, day: int) -> date | None:
    try:
        target = date(today.year, month, day)
    except ValueError:
        return None
    if target < today - timedelta(days=1):
        try:
            target = date(today.year + 1, month, day)
        except ValueError:
            return None
    return target


def _wechat_query_chinese_int(value: str) -> int:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    mapping = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        return 10 + mapping.get(text[1], 0)
    if text.endswith("十") and len(text) == 2:
        return mapping.get(text[0], 0) * 10
    if "十" in text and len(text) == 3:
        return mapping.get(text[0], 0) * 10 + mapping.get(text[2], 0)
    return mapping.get(text, 1)


def _wechat_query_help_response() -> dict[str, Any]:
    menu = _wechat_query_help_text()
    template_line = _tunnel_mechanical_wechat_template_line({}, _today_in_tz())
    return {
        "success": True,
        "reply": menu,
        "replies": [menu, template_line],
        "query_type": "help",
        "template": template_line,
    }


def _wechat_query_help_text() -> str:
    today = _today_in_tz().isoformat()
    return (
        "监控查询菜单：\n"
        "1. 查询我的监控\n"
        "2. 查询今日在岗\n"
        "3. 查询今日监控\n"
        "4. 查询明日监控\n"
        "5. 查询本周监控\n"
        "6. 查询未来7天\n"
        "7. 查询我的绑定\n"
        "8. 查询今日机电\n"
        f"9. 查询{today}机电\n"
        "10. 查看隧道机电录入格式\n"
        "11. 查询休息\n"
        "12. 施工图片\n"
        "13. 施工点维护\n"
        "直接回复序号即可执行。\n"
        "发送“巡查记录”可获取巡查记录查询模板。\n"
        f"示例：{DEFAULT_PATROL_RECORD_TEMPLATE}\n"
        "发送“模板”可单独获取隧道机电录入模板。\n"
        "发送“修改模板”可单独获取隧道机电修改模板。\n"
        "发送“机电”可查看隧道机电菜单。\n"
        "也可以问：我今天什么班、明天我上班吗、查询7月24日监控、查询罗熙云监控。\n"
        "说明：群成员可以查询全员或指定姓名；只有“我的监控/我的绑定”这类个人查询需要先绑定微信成员。"
    )


def _wechat_query_unbound_response(query: WechatQueryRequest) -> dict[str, Any]:
    sender_name = _clean_wechat_member_display_name(str(query.sender_name or ""), str(query.runtime_sender_id or query.sender_id or ""))
    suffix = f"\n当前微信成员：{sender_name}" if sender_name else ""
    return {
        "success": False,
        "query_type": "unbound",
        "reply": "还没有识别到“我”对应的人员。可以直接回复“绑定姓名”，例如：绑定商邱宏；也可以改发“查询商邱宏监控”按姓名查询。" + suffix,
    }


def _person_for_wechat_query(repo: DutyRepository, query: WechatQueryRequest) -> dict[str, str] | None:
    if str(query.channel or "").strip() in {"wecom_aibot", "wecom_app"}:
        userid = str(query.runtime_sender_id or query.sender_id or "").strip()
        if userid.startswith("wecom_user:"):
            userid = userid.removeprefix("wecom_user:")
        for person in repo.list_personnel():
            if userid and str(person.get("wecom_userid") or "").strip() == userid:
                return person
        for monitored in repo.list_monitored_people():
            if userid and str(monitored.get("wecom_userid") or "").strip() == userid:
                return next(
                    (person for person in repo.list_personnel() if person.get("name") == monitored.get("name")),
                    {"name": str(monitored.get("name") or ""), "wecom_userid": userid},
                )
    runtime_ids = {
        str(query.runtime_sender_id or "").strip(),
        str(query.sender_id or "").strip(),
    }
    stable_ids = {
        str(query.stable_member_id or "").strip(),
        str(query.sender_id or "").strip(),
    }
    runtime_ids.discard("")
    stable_ids.discard("")
    for person in repo.list_personnel():
        runtime_id = str(person.get("wechat_group_runtime_sender_id") or "").strip()
        stable_id = str(person.get("wechat_group_member_id") or "").strip()
        if runtime_id and runtime_id in runtime_ids:
            return person
        if stable_id and stable_id in stable_ids:
            return person
    return None


def _wechat_self_bind_requested_name(repo: DutyRepository, text: str) -> str:
    value = re.sub(r"^(绑定|我是|我叫)", "", str(text or "").strip(), count=1).strip()
    value = value.strip("，,。.!！?？：:")
    if not value:
        return ""
    for name in _wechat_query_known_person_names(repo):
        if value == name or name in value:
            return name
    return value


def _build_wechat_self_bind_response(repo: DutyRepository, query: WechatQueryRequest, text: str) -> dict[str, Any]:
    requested_name = _wechat_self_bind_requested_name(repo, text)
    known_names = set(_wechat_query_known_person_names(repo))
    if not requested_name or requested_name not in known_names:
        examples = "、".join(_wechat_query_known_person_names(repo)[:8])
        suffix = f"\n当前可绑定人员示例：{examples}" if examples else "\n当前还没有人员名单，请先在后台添加人员或导入排班。"
        return {
            "success": False,
            "query_type": "binding_update",
            "reply": f"没有找到人员“{requested_name or '未填写'}”，请发送“绑定姓名”，姓名必须和后台人员名单一致。{suffix}",
        }

    stable_member_id = str(query.stable_member_id or "").strip()
    sender_id = str(query.sender_id or "").strip()
    if not stable_member_id and sender_id.startswith("wgm_"):
        stable_member_id = sender_id
    runtime_sender_id = str(query.runtime_sender_id or "").strip()
    if not runtime_sender_id and sender_id and not sender_id.startswith("wgm_"):
        runtime_sender_id = sender_id
    member_ids = [stable_member_id, runtime_sender_id]
    if not any(member_ids):
        return {
            "success": False,
            "query_type": "binding_update",
            "reply": "没有识别到当前微信成员标识，请在群里重新发送：绑定姓名。",
        }

    sender_name = _clean_wechat_member_display_name(str(query.sender_name or ""), runtime_sender_id or stable_member_id)
    room_id = str(query.stable_room_id or query.room_id or "").strip()
    room_name = _clean_wechat_member_display_name(str(query.room_name or ""), room_id)
    if str(query.channel or "").strip() in {"wecom_aibot", "wecom_app"}:
        userid = runtime_sender_id.removeprefix("wecom_user:")
        if not userid:
            return {
                "success": False,
                "query_type": "binding_update",
                "reply": "没有识别到当前企业微信成员，请重新发送：绑定姓名。",
            }
        repo.clear_wecom_binding_for_userid(userid, except_name=requested_name)
        repo.upsert_personnel_contacts([{"name": requested_name, "wecom_userid": userid}])
        return {
            "success": True,
            "query_type": "binding_update",
            "person_name": requested_name,
            "reply": f"绑定成功：{requested_name}\n企业微信成员：{userid}\n现在可以发送“查询我的监控”或“查询我的绑定”。",
        }
    repo.clear_wechat_binding_for_member(member_ids, except_name=requested_name)
    repo.upsert_personnel_contacts(
        [
            {
                "name": requested_name,
                "wechat_group_room_id": room_id,
                "wechat_group_room_name": room_name,
                "wechat_group_member_id": stable_member_id,
                "wechat_group_runtime_sender_id": runtime_sender_id,
                "wechat_group_member_name": sender_name,
            }
        ]
    )
    display_name = sender_name or "当前微信成员"
    return {
        "success": True,
        "query_type": "binding_update",
        "person_name": requested_name,
        "reply": f"绑定成功：{requested_name}\n微信成员：{display_name}\n现在可以发送“查询我的监控”或“查询我的绑定”。",
    }


def _wechat_query_known_person_names(repo: DutyRepository) -> list[str]:
    names: list[str] = []
    names.extend(str(name or "").strip() for name in repo.list_personnel_names())
    names.extend(str(person.get("name") or "").strip() for person in repo.list_personnel())
    names.extend(str(person.get("name") or "").strip() for person in repo.list_monitored_people())
    unique = {name for name in names if name}
    return sorted(unique, key=len, reverse=True)


def _wechat_query_requested_person_name(repo: DutyRepository, text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    for name in _wechat_query_known_person_names(repo):
        if name in value:
            return name
    return ""


def _build_wechat_daily_duty_query_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    *,
    uploads: Path | None = None,
) -> dict[str, Any]:
    target = query.target_date or _today_in_tz()
    preview = _build_daily_duty_preview(repo, target)
    image_url = ""
    if uploads is not None:
        uploads.mkdir(parents=True, exist_ok=True)
        filename = f"daily-duty-query-{uuid.uuid4().hex}.png"
        (uploads / filename).write_bytes(render_daily_duty_image(preview))
        image_url = f"/api/uploads/{filename}"
    reply = f"已生成 {target.isoformat()} 今日在岗信息图片，正在发送。"
    result = {
        "success": True,
        "query_type": "daily_duty_query",
        "target_date": target.isoformat(),
        "reply": reply,
        "replies": [reply],
        "content": preview.get("content") or "",
        "details": preview.get("details") or {},
    }
    if image_url:
        result["image_url"] = image_url
        result["image_full_url"] = _public_app_url(image_url)
    return result


def _build_wechat_rest_query_response(repo: DutyRepository, query: WechatQueryRequest, text: str) -> dict[str, Any]:
    target = query.target_date or _today_in_tz()
    requested_person = _wechat_query_requested_person_name(repo, text)
    bound = _person_for_wechat_query(repo, query)
    person_name = requested_person or (str(bound["name"]) if bound else "")
    if not person_name:
        return _wechat_query_unbound_response(query)
    summary = _monthly_rest_summary(repo, person_name, target)
    return {
        "success": True,
        "query_type": "rest_query",
        "person_name": person_name,
        "target_date": target.isoformat(),
        "reply": summary["reply"],
        "details": summary,
    }


def _monthly_rest_summary(repo: DutyRepository, person_name: str, target: date) -> dict[str, Any]:
    roster = repo.get_roster_month(target.year, target.month)
    if not roster:
        reply = f"{person_name} {target.year}年{target.month}月没有导入排班，无法查询休息。"
        return {"reply": reply, "total_days": 0, "ranges": []}
    row = next((item for item in roster.get("grid", []) if str(item.get("name") or "").strip() == person_name), None)
    if not row:
        reply = f"{person_name} {target.year}年{target.month}月排班表里没有找到这个人。"
        return {"reply": reply, "total_days": 0, "ranges": []}
    rest_days: list[date] = []
    for day_text, code in dict(row.get("days") or {}).items():
        if not _is_rest_code(str(code or "")):
            continue
        try:
            rest_days.append(date(target.year, target.month, int(day_text)))
        except ValueError:
            continue
    rest_days = sorted(set(rest_days))
    ranges = _date_ranges_from_days(rest_days)
    total = len(rest_days)
    rested = len([day for day in rest_days if day < target])
    if _is_rest_code(_roster_code_for_person(repo, person_name, target)):
        rested = len([day for day in rest_days if day <= target])
    remaining = max(0, total - rested)
    if not rest_days:
        reply = f"{person_name} {target.year}年{target.month}月没有休息排班。"
        return {"reply": reply, "total_days": 0, "rested_days": 0, "remaining_days": 0, "ranges": []}
    prefix = f"{person_name} 本月休息共{total}天，分{len(ranges)}次休息"
    if rested > 0:
        prefix += f"，已经休息{rested}天，本月休息还剩{remaining}天"
    pieces = [prefix]
    for index, (start, end) in enumerate(ranges, start=1):
        label = _ordinal_zh(index)
        range_text = f"从{_month_day_week_label(start)}到{_month_day_week_label(end)}"
        if target < start:
            days_left = (start - target).days
            pieces.append(f"距离第{label}次休息还剩{days_left}天，{range_text}")
        elif start <= target <= end:
            left = (end - target).days + 1
            pieces.append(f"正在第{label}次休息，假期余额{left}天，{range_text}")
        else:
            pieces.append(f"第{label}次休息已结束，{range_text}")
    return {
        "reply": "，".join(pieces),
        "total_days": total,
        "rested_days": rested,
        "remaining_days": remaining,
        "ranges": [{"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days + 1} for start, end in ranges],
    }


def _date_ranges_from_days(days: list[date]) -> list[tuple[date, date]]:
    if not days:
        return []
    ranges: list[tuple[date, date]] = []
    start = prev = days[0]
    for day in days[1:]:
        if day == prev + timedelta(days=1):
            prev = day
            continue
        ranges.append((start, prev))
        start = prev = day
    ranges.append((start, prev))
    return ranges


def _month_day_week_label(day: date) -> str:
    return f"{day.month}月{day.day}日（星期{'一二三四五六日'[day.weekday()]}）"


def _ordinal_zh(index: int) -> str:
    return {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}.get(index, str(index))


def _build_person_monitor_query_response(repo: DutyRepository, person_name: str, target: date) -> dict[str, Any]:
    return {
        "success": True,
        "query_type": "monitor",
        "person_name": person_name,
        "target_date": target.isoformat(),
        "reply": "\n".join(
            [
                f"{person_name} {_wechat_query_date_label(target)} {_wechat_query_weekday(target)} 监控排班",
                f"排班：{_person_roster_status_text(repo, person_name, target)}",
            ]
        ),
    }


def _build_person_reminder_query_response(repo: DutyRepository, person_name: str, target: date) -> dict[str, Any]:
    monitored = next((person for person in repo.list_monitored_people() if person["name"] == person_name), None)
    events = [event for event in _plan_all_events(repo, target) if event.person_name == person_name]
    roster_status = _person_roster_status_text(repo, person_name, target)
    lines = [
        f"{person_name} {target:%Y-%m-%d} 监控查询",
        f"排班：{roster_status}",
    ]
    if monitored:
        enabled_text = "启用" if monitored.get("enabled") else "停用"
        lines.append(
            "监控提醒：{}，每日 {}，班前 {} 分钟".format(
                enabled_text,
                _coerce_hhmm(str(monitored.get("daily_time") or ""), "07:50"),
                int(monitored.get("before_shift_minutes") or 0),
            )
        )
        if monitored.get("rest_reminder_enabled"):
            lines.append(f"休息提醒：{_coerce_hhmm(str(monitored.get('rest_reminder_time') or ''), '08:30')}")
    else:
        lines.append("监控提醒：未配置")
    if events:
        lines.append("计划提醒：")
        for event in events[:8]:
            content = str(event.content or "").splitlines()[0]
            lines.append(f"- {event.send_at:%H:%M} {_wechat_query_event_label(event.kind)}：{content}")
        if len(events) > 8:
            lines.append(f"- 另有 {len(events) - 8} 条提醒未显示")
    else:
        lines.append("计划提醒：无")
    return {
        "success": True,
        "query_type": "reminder",
        "person_name": person_name,
        "target_date": target.isoformat(),
        "reply": "\n".join(lines),
    }


def _build_person_monitor_range_query_response(repo: DutyRepository, person_name: str, start: date, days: int) -> dict[str, Any]:
    lines = [
        f"{person_name} {start:%Y-%m-%d} 起 {days} 天监控排班",
    ]
    for offset in range(days):
        target = start + timedelta(days=offset)
        lines.append(
            f"- {_wechat_query_date_label(target)} {_wechat_query_weekday(target)}：{_person_roster_status_text(repo, person_name, target)}"
        )
    return {
        "success": True,
        "query_type": "monitor_range",
        "person_name": person_name,
        "start_date": start.isoformat(),
        "days": days,
        "reply": "\n".join(lines),
    }


def _build_person_reminder_range_query_response(repo: DutyRepository, person_name: str, start: date, days: int) -> dict[str, Any]:
    monitored = next((person for person in repo.list_monitored_people() if person["name"] == person_name), None)
    lines = [
        f"{person_name} {start:%Y-%m-%d} 起 {days} 天提醒汇总",
        _wechat_query_monitor_config_line(monitored),
    ]
    for offset in range(days):
        target = start + timedelta(days=offset)
        events = [event for event in _plan_all_events(repo, target) if event.person_name == person_name]
        event_times = "、".join(f"{event.send_at:%H:%M}{_wechat_query_event_label(event.kind)}" for event in events[:4])
        if len(events) > 4:
            event_times += f"等{len(events)}条"
        if not event_times:
            event_times = "无计划提醒"
        lines.append(f"- {_wechat_query_date_label(target)} {_wechat_query_weekday(target)}：{_person_roster_status_text(repo, person_name, target)}；{event_times}")
    return {
        "success": True,
        "query_type": "reminder_range",
        "person_name": person_name,
        "start_date": start.isoformat(),
        "days": days,
        "reply": "\n".join(lines),
    }


def _wechat_query_all_person_names_for_date(repo: DutyRepository, target: date) -> list[str]:
    names: set[str] = set()
    names.update(str(person.get("name") or "").strip() for person in repo.list_monitored_people())
    names.update(str(row.get("name") or "").strip() for row in _roster_rows_for_date(repo, target))
    names.discard("")
    return sorted(names)


def _build_all_monitor_query_response(repo: DutyRepository, target: date) -> dict[str, Any]:
    names = _wechat_query_all_person_names_for_date(repo, target)
    if not names:
        return {
            "success": True,
            "query_type": "monitor_all",
            "target_date": target.isoformat(),
            "reply": f"{target:%Y-%m-%d} 暂无人员排班或监控提醒配置。",
        }
    lines = [f"{_wechat_query_date_label(target)} {_wechat_query_weekday(target)} 监控排班"]
    lines.extend(_wechat_query_shift_summary_lines(repo, target))
    return {
        "success": True,
        "query_type": "monitor_all",
        "target_date": target.isoformat(),
        "reply": "\n".join(lines),
    }


def _build_all_monitor_range_query_response(repo: DutyRepository, start: date, days: int) -> dict[str, Any]:
    lines = [f"{start:%Y-%m-%d} 起 {days} 天监控排班"]
    for offset in range(days):
        target = start + timedelta(days=offset)
        lines.append(f"- {_wechat_query_date_label(target)} {_wechat_query_weekday(target)}")
        lines.extend(f"  {line}" for line in _wechat_query_shift_summary_lines(repo, target))
    return {
        "success": True,
        "query_type": "monitor_all_range",
        "start_date": start.isoformat(),
        "days": days,
        "reply": "\n".join(lines),
    }


def _build_all_reminder_query_response(repo: DutyRepository, target: date) -> dict[str, Any]:
    events = _plan_all_events(repo, target)
    names = _wechat_query_all_person_names_for_date(repo, target)
    if not names and not events:
        return {
            "success": True,
            "query_type": "reminder_all",
            "target_date": target.isoformat(),
            "reply": f"{target:%Y-%m-%d} 暂无人员排班或提醒配置。",
        }
    lines = [f"{_wechat_query_date_label(target)} {_wechat_query_weekday(target)} 全员提醒汇总"]
    for name in names[:30]:
        person_events = [event for event in events if event.person_name == name]
        event_times = "、".join(f"{event.send_at:%H:%M}{_wechat_query_event_label(event.kind)}" for event in person_events[:4])
        if len(person_events) > 4:
            event_times += f"等{len(person_events)}条"
        lines.append(f"- {name}：{_person_roster_status_text(repo, name, target)}；{event_times or '无计划提醒'}")
    if len(names) > 30:
        lines.append(f"- 另有 {len(names) - 30} 人未显示")
    return {
        "success": True,
        "query_type": "reminder_all",
        "target_date": target.isoformat(),
        "reply": "\n".join(lines),
    }


def _build_all_reminder_range_query_response(repo: DutyRepository, start: date, days: int) -> dict[str, Any]:
    lines = [f"{start:%Y-%m-%d} 起 {days} 天全员提醒汇总"]
    for offset in range(days):
        target = start + timedelta(days=offset)
        events = _plan_all_events(repo, target)
        preview = "、".join(f"{event.person_name}{event.send_at:%H:%M}" for event in events[:6])
        if len(events) > 6:
            preview += f"等{len(events)}条"
        lines.append(f"- {_wechat_query_date_label(target)} {_wechat_query_weekday(target)}：{preview or '无计划提醒'}")
    return {
        "success": True,
        "query_type": "reminder_all_range",
        "start_date": start.isoformat(),
        "days": days,
        "reply": "\n".join(lines),
    }


def _build_person_next_reminder_query_response(repo: DutyRepository, person_name: str) -> dict[str, Any]:
    now = datetime.now(TZ)
    upcoming = []
    for offset in range(14):
        target = now.date() + timedelta(days=offset)
        upcoming.extend(
            event
            for event in _plan_all_events(repo, target)
            if event.person_name == person_name and event.send_at >= now
        )
    upcoming.sort(key=lambda event: event.send_at)
    if not upcoming:
        reply = f"{person_name} 未来14天没有计划提醒。"
    else:
        lines = [f"{person_name} 下次提醒"]
        seen: set[tuple[str, str, str]] = set()
        for event in upcoming[:5]:
            display_date = _next_reminder_display_date(event)
            display_content = _next_reminder_display_content(event)
            display_key = (display_date, person_name, display_content)
            if display_key in seen:
                continue
            seen.add(display_key)
            lines.append(f"- {display_date}：{display_content}")
        reply = "\n".join(lines)
    return {
        "success": True,
        "query_type": "next_reminder",
        "person_name": person_name,
        "reply": reply,
    }


def _build_all_next_reminder_query_response(repo: DutyRepository) -> dict[str, Any]:
    now = datetime.now(TZ)
    upcoming = []
    for offset in range(14):
        target = now.date() + timedelta(days=offset)
        upcoming.extend(event for event in _plan_all_events(repo, target) if event.send_at >= now)
    upcoming.sort(key=lambda event: event.send_at)
    if not upcoming:
        reply = "未来14天没有计划提醒。"
    else:
        lines = ["全员下次提醒"]
        seen: set[tuple[str, str, str]] = set()
        for event in upcoming[:10]:
            display_date = _next_reminder_display_date(event)
            display_content = _next_reminder_display_content(event)
            display_key = (display_date, event.person_name, display_content)
            if display_key in seen:
                continue
            seen.add(display_key)
            lines.append(f"- {display_date} {event.person_name}：{display_content}")
        if len(upcoming) > 10:
            lines.append(f"- 另有 {len(upcoming) - 10} 条提醒未显示")
        reply = "\n".join(lines)
    return {
        "success": True,
        "query_type": "next_reminder_all",
        "reply": reply,
    }


def _wechat_query_monitor_config_line(monitored: dict[str, Any] | None) -> str:
    if not monitored:
        return "监控提醒：未配置"
    enabled_text = "启用" if monitored.get("enabled") else "停用"
    return "监控提醒：{}，每日 {}，班前 {} 分钟".format(
        enabled_text,
        _coerce_hhmm(str(monitored.get("daily_time") or ""), "07:50"),
        int(monitored.get("before_shift_minutes") or 0),
    )


def _wechat_query_weekday(target: date) -> str:
    return "周" + "一二三四五六日"[target.weekday()]


def _wechat_query_date_label(target: date) -> str:
    today = _today_in_tz()
    if target == today:
        return f"今天 {target:%Y-%m-%d}"
    if target == today + timedelta(days=1):
        return f"明天 {target:%Y-%m-%d}"
    if target == today + timedelta(days=2):
        return f"后天 {target:%Y-%m-%d}"
    return f"{target:%Y-%m-%d}"


def _wechat_query_shift_summary_lines(repo: DutyRepository, target: date) -> list[str]:
    rows = _roster_rows_for_date(repo, target)
    if not rows:
        return ["暂无排班"]
    early = [row["name"] for row in rows if row["code"] == "早"]
    middle = [row["name"] for row in rows if row["code"] == "中"]
    night = [row["name"] for row in rows if row["code"] in {"晚", "夜"}]
    rest = [row["name"] for row in rows if _is_rest_code(row["code"])]
    known = set(early) | set(middle) | set(night) | set(rest)
    other = [row["name"] for row in rows if row["name"] not in known and str(row.get("code") or "").strip()]
    lines = [
        f"早班：{_join_names(early)}",
        f"中班：{_join_names(middle)}",
        f"晚班：{_join_names(night)}",
    ]
    if rest:
        lines.append(f"休息：{_join_names(rest)}")
    if other:
        lines.append(f"其他：{_join_names(other)}")
    return lines


def _person_roster_status_text(repo: DutyRepository, person_name: str, target: date) -> str:
    row = next((item for item in _roster_rows_for_date(repo, target) if item["name"] == person_name), None)
    if row is None:
        return "未找到排班"
    code = str(row.get("code") or "").strip()
    shift = normalize_shift_code(code)
    if shift:
        return f"{shift.label} {shift.start_time:%H:%M}至{shift.end_time:%H:%M}"
    if _is_rest_code(code):
        return "休息"
    if code == "出差":
        return "出差"
    if code:
        return code
    return "在岗/备勤"


def _wechat_query_event_label(kind: str) -> str:
    return {
        "daily": "每日提醒",
        "before_shift": "班前提醒",
        "rest": "休息提醒",
        "custom": "自定义提醒",
        "vacation_start": "假期开始提醒",
        "vacation_end": "假期余额提醒",
    }.get(kind, kind)


_NEXT_REMINDER_CONTENT_RE = re.compile(
    r"^(?P<name>.+?) (?P<date>\d{4}-\d{2}-\d{2})（(?P<time_range>[^)]+)\)是你的(?P<shift_label>早班|中班|晚班|夜班)$"
)


def _next_reminder_display_date(event: ReminderEvent) -> str:
    content = str(event.content or "").splitlines()[0].strip()
    match = _NEXT_REMINDER_CONTENT_RE.match(content)
    if match:
        return match.group("date")
    return f"{event.send_at:%Y-%m-%d}"


def _next_reminder_display_content(event: ReminderEvent) -> str:
    content = str(event.content or "").splitlines()[0].strip()
    match = _NEXT_REMINDER_CONTENT_RE.match(content)
    if not match:
        return content

    time_range = match.group("time_range")
    shift_label = match.group("shift_label")
    if shift_label == "早班":
        prefix = "今晚凌晨" if event.send_at.hour >= 18 else "今天凌晨"
        return f"请注意{prefix}{time_range}是你的早班 记得检查隧道灯是否关闭 7点50分记得开启隧道灯"
    if shift_label == "中班":
        return f"请注意今天{time_range}是你的中班 记得写一二楼的卫生间消毒清洁记录"
    if shift_label in {"晚班", "夜班"}:
        return f"请注意今天下午{time_range}是你的晚班 记得在晚上21点关闭隧道灯"
    return content


def _login_page_response(static_dir: Path, *, error: str = "", next_url: str = "/", status_code: int = 200) -> HTMLResponse:
    template = (static_dir / "login.html").read_text(encoding="utf-8")
    error_html = f'<div class="login-error">{html_lib.escape(error)}</div>' if error else ""
    page_html = (
        template.replace("{{error_html}}", error_html)
        .replace("{{next_url}}", html_lib.escape(_safe_next_url(next_url), quote=True))
    )
    return HTMLResponse(page_html, status_code=status_code)


def _public_notification_config(config: dict[str, Any]) -> dict[str, Any]:
    config = _notification_config_with_env_defaults(config)
    webhook_url = str(config.get("webhook_url", "")).strip()
    wecom_aibot_id = str(config.get("wecom_aibot_id", "")).strip()
    wecom_aibot_secret = str(config.get("wecom_aibot_secret", "")).strip()
    wecom_app_corp_id = str(config.get("wecom_app_corp_id", "")).strip()
    wecom_app_agent_id = str(config.get("wecom_app_agent_id", "")).strip()
    wecom_app_secret = str(config.get("wecom_app_secret", "")).strip()
    wecom_app_token = str(config.get("wecom_app_token", "")).strip()
    wecom_app_encoding_aes_key = str(config.get("wecom_app_encoding_aes_key", "")).strip()
    lightagent_url = ""
    lightagent_targets: list[dict[str, str]] = []
    lightagent_target = ""
    lightagent_token = ""
    sender_type = _normalize_notification_sender_type(str(config.get("sender_type") or "wecom_webhook"))
    mention_mode = _normalize_notification_mention_mode(str(config.get("mention_mode") or "person"))
    wecom_app_configured = _wecom_app_config_complete(config, require_callback=True)
    wecom_app_active = bool(config.get("wecom_app_enabled"))
    effective_sender_type = "wecom_app" if wecom_app_active else sender_type
    webhook_active = not wecom_app_active and sender_type == "wecom_webhook"
    lightagent_active = False
    active_configured = wecom_app_configured if effective_sender_type == "wecom_app" else bool(webhook_url)
    return {
        "sender_type": effective_sender_type if wecom_app_active else sender_type,
        "effective_sender_type": effective_sender_type,
        "wechat_bridge_enabled": False,
        "webhook_url": "",
        "webhook_configured": bool(webhook_url),
        "webhook_active": webhook_active and bool(webhook_url),
        "webhook_display": "已配置" if webhook_url else "未配置",
        "wecom_aibot_enabled": bool(config.get("wecom_aibot_enabled")),
        "wecom_aibot_id": wecom_aibot_id,
        "wecom_aibot_configured": bool(wecom_aibot_id and wecom_aibot_secret),
        "wecom_aibot_secret": "",
        "wecom_aibot_secret_configured": bool(wecom_aibot_secret),
        "wecom_app_enabled": wecom_app_active,
        "wecom_app_corp_id": wecom_app_corp_id,
        "wecom_app_agent_id": wecom_app_agent_id,
        "wecom_app_configured": wecom_app_configured,
        "wecom_app_secret": "",
        "wecom_app_secret_configured": bool(wecom_app_secret),
        "wecom_app_token": "",
        "wecom_app_token_configured": bool(wecom_app_token),
        "wecom_app_encoding_aes_key": "",
        "wecom_app_encoding_aes_key_configured": bool(wecom_app_encoding_aes_key),
        "wecom_app_target_names": list(config.get("wecom_app_target_names") or []),
        "wecom_app_target_names_text": "\n".join(list(config.get("wecom_app_target_names") or [])),
        "wecom_app_function_target_names": dict(config.get("wecom_app_function_target_names") or {}),
        "wecom_app_daily_duty_target_names_text": "\n".join(list((config.get("wecom_app_function_target_names") or {}).get("daily_duty") or [])),
        "wecom_app_patrol_warning_target_names_text": "\n".join(list((config.get("wecom_app_function_target_names") or {}).get("patrol_warning") or [])),
        "wecom_app_system_target_names_text": "\n".join(list((config.get("wecom_app_function_target_names") or {}).get("system") or [])),
        "wecom_app_callback_url": _public_app_url("/api/wecom-app/callback"),
        "lightagent_url": lightagent_url,
        "lightagent_configured": False,
        "lightagent_active": False,
        "lightagent_display": "已停用",
        "lightagent_token_configured": False,
        "lightagent_target": lightagent_target,
        "lightagent_targets": lightagent_targets,
        "mention_mode": mention_mode,
        "mention_targets": str(config.get("mention_targets") or ""),
        "notification_configured": active_configured,
        "notification_display": "已配置" if active_configured else "未配置",
        "message_template": config.get("message_template") or DEFAULT_MESSAGE_TEMPLATE,
    }


def _public_patrol_warning_config(config: dict[str, Any]) -> dict[str, Any]:
    password = str(config.get("password", "")).strip()
    return {
        "enabled": bool(config.get("enabled")),
        "login_url": str(config.get("login_url") or ""),
        "warning_url": str(config.get("warning_url") or ""),
        "username": str(config.get("username") or ""),
        "password": "",
        "password_configured": bool(password),
        "password_display": "已配置" if password else "未配置",
        "project_id": str(config.get("project_id") or ""),
        "platform": str(config.get("platform") or "2"),
        "route_code": str(config.get("route_code") or ""),
        "poll_interval_minutes": int(config.get("poll_interval_minutes") or 10),
        "rows": int(config.get("rows") or 5000),
        "end_reminder_enabled": bool(config.get("end_reminder_enabled", True)),
        "end_reminder_interval_hours": int(config.get("end_reminder_interval_hours") or 6),
        "end_reminder_window_hours": int(config.get("end_reminder_window_hours") or 48),
        "send_content_mode": _patrol_send_content_mode(config),
        "start_message_template": str(config.get("start_message_template") or DEFAULT_PATROL_WARNING_START_TEMPLATE),
        "end_message_template": str(config.get("end_message_template") or DEFAULT_PATROL_WARNING_END_TEMPLATE),
        "notification_room_id": str(config.get("notification_room_id") or ""),
        "notification_room_name": str(config.get("notification_room_name") or ""),
    }


def _public_patrol_warning_state(state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    warning = warning_from_dict(dict(state.get("warning") or {}), TZ)
    public_warning = (
        warning.as_dict()
        if warning is not None and _patrol_warning_in_display_window(warning, config or {}, now=datetime.now(TZ))
        else {}
    )
    return {
        "warning_key": str(state.get("warning_key") or ""),
        "warning": public_warning,
        "last_checked_at": str(state.get("last_checked_at") or ""),
        "last_start_sent_key": str(state.get("last_start_sent_key") or ""),
        "last_end_reminder_slot": str(state.get("last_end_reminder_slot") or ""),
        "token_configured": bool(str(state.get("token") or "").strip()),
        "token_expires_at": str(state.get("token_expires_at") or ""),
        "next_check_at": str(state.get("next_check_at") or ""),
        "failure_count": int(state.get("failure_count") or 0),
        "backoff_until": str(state.get("backoff_until") or ""),
        "last_error": str(state.get("last_error") or ""),
    }


def _public_tunnel_mechanical_config(config: dict[str, Any]) -> dict[str, Any]:
    password = str(config.get("password") or "")
    return {
        "base_url": str(config.get("base_url") or ""),
        "username": str(config.get("username") or ""),
        "password": "",
        "password_configured": bool(password),
        "password_display": "已配置" if password else "未配置",
    }


def _public_tunnel_mechanical_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "token_configured": bool(str(state.get("access_token") or "").strip()),
        "cookie_configured": bool(str(state.get("cookie_header") or "").strip()),
        "token_expires_at": str(state.get("token_expires_at") or ""),
        "last_login_at": str(state.get("last_login_at") or ""),
        "last_error": str(state.get("last_error") or ""),
    }


def _empty_tunnel_mechanical_template() -> dict[str, Any]:
    return {
        "imported": False,
        "base_url": "",
        "submit_path": "",
        "list_path": "",
        "update_path": "",
        "people": [],
        "assets": [],
        "defaults": {
            "checkerId": "",
            "checker": "",
            "recorderId": "",
            "recorder": "",
            "checkTime": "",
            "weather": "",
            "carLicense": "",
            "nums": "",
        },
    }


def _public_tunnel_mechanical_template(template: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_tunnel_mechanical_template(template, require_assets=False)
    normalized["imported"] = bool(normalized["people"] or normalized["assets"])
    return normalized


def _normalize_tunnel_mechanical_template(data: Any, *, require_assets: bool = True) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="模板必须是 JSON 对象")
    template = _empty_tunnel_mechanical_template()
    template["base_url"] = str(data.get("base_url") or "").strip()
    if template["base_url"]:
        _tunnel_mechanical_base_url(template["base_url"])
    template["submit_path"] = str(data.get("submit_path") or "").strip()
    template["list_path"] = str(data.get("list_path") or "").strip()
    template["update_path"] = str(data.get("update_path") or data.get("edit_path") or "").strip()
    people = []
    for person in data.get("people") or []:
        if not isinstance(person, dict):
            continue
        person_id = str(person.get("id") or "").strip()
        name = str(person.get("name") or "").strip()
        if person_id and name:
            people.append({"id": person_id, "name": name})
    assets = []
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        normalized_asset = {
            "enabled": bool(asset.get("enabled", True)),
            "assetId": str(asset.get("assetId") or "").strip(),
            "assetName": str(asset.get("assetName") or "").strip(),
            "assetCode": str(asset.get("assetCode") or "").strip(),
            "routeCode": str(asset.get("routeCode") or "").strip(),
            "routeName": str(asset.get("routeName") or "").strip(),
            "maintenanceSectionId": str(asset.get("maintenanceSectionId") or "").strip(),
            "domainId": str(asset.get("domainId") or "").strip(),
            "deptName": str(asset.get("deptName") or "").strip(),
            "devName": str(asset.get("devName") or "").strip(),
            "location": str(asset.get("location") or "").strip(),
            "content": str(asset.get("content") or "").strip(),
            "result": int(asset.get("result") or 1),
            "carLicense": str(asset.get("carLicense") or "").strip(),
            "nums": None if asset.get("nums") is None else str(asset.get("nums") or "").strip(),
        }
        if normalized_asset["assetId"] and normalized_asset["assetName"] and normalized_asset["assetCode"]:
            assets.append(normalized_asset)
    defaults_data = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    template["people"] = people
    template["assets"] = assets
    template["defaults"] = {
        "checkerId": str(defaults_data.get("checkerId") or "").strip(),
        "checker": str(defaults_data.get("checker") or "").strip(),
        "recorderId": str(defaults_data.get("recorderId") or "").strip(),
        "recorder": str(defaults_data.get("recorder") or "").strip(),
        "checkTime": str(defaults_data.get("checkTime") or "").strip(),
        "weather": str(defaults_data.get("weather") or "").strip(),
        "carLicense": str(defaults_data.get("carLicense") or "").strip(),
        "nums": str(defaults_data.get("nums") or "").strip(),
    }
    if require_assets and (not people or not assets):
        raise HTTPException(status_code=400, detail="模板至少需要 people 和 assets")
    return template


def _tunnel_mechanical_allowed_hosts(*base_urls: str) -> set[str]:
    hosts = set()
    for base_url in base_urls:
        parsed = urlparse(str(base_url or "").strip())
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def _tunnel_mechanical_base_url(base_url: str, *, allowed_hosts: set[str] | None = None) -> str:
    text = str(base_url or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="请先配置或导入平台地址")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="目标地址格式不正确")
    if allowed_hosts is not None:
        if not allowed_hosts:
            raise HTTPException(status_code=400, detail="请先保存账号平台地址或导入带平台地址的模板")
        if parsed.hostname.lower() not in allowed_hosts:
            raise HTTPException(status_code=400, detail="隧道机电录入只允许提交到已配置或已导入的平台地址")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _tunnel_mechanical_api_path(path: str, *, fallback: str = "") -> str:
    text = str(path or fallback or "").strip()
    if not text:
        return ""
    if not text.startswith("/") or text.startswith("//"):
        raise HTTPException(status_code=400, detail="隧道机电接口路径必须以 / 开头")
    return text


def _tunnel_mechanical_password_cipher(text: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="服务缺少 cryptography 依赖，无法加密登录密码") from exc
    key = TUNNEL_MECHANICAL_AES_KEY_TEXT.encode("utf-8")
    data = str(text or "").encode("utf-8")
    pad_size = 16 - (len(data) % 16)
    padded = data + bytes([pad_size]) * pad_size
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def _tunnel_mechanical_decrypt_text(text: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="服务缺少 cryptography 依赖，无法解密验证码") from exc
    try:
        encrypted = base64.b64decode(str(text or ""))
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="验证码图片格式不正确") from exc
    key = TUNNEL_MECHANICAL_AES_KEY_TEXT.encode("utf-8")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    if not padded:
        return ""
    pad_size = padded[-1]
    data = padded[:-pad_size] if 1 <= pad_size <= 16 else padded
    return data.decode("utf-8")


def _solve_tunnel_mechanical_captcha(img_base64: str) -> str:
    image_bytes = _tunnel_mechanical_captcha_bytes(img_base64)
    text_code = _solve_tunnel_mechanical_captcha_text(_read_tunnel_mechanical_captcha_text(image_bytes))
    if text_code:
        return text_code
    image_code = _solve_tunnel_mechanical_captcha_image(image_bytes)
    if image_code:
        return image_code
    raise HTTPException(status_code=422, detail="无法自动识别隧道机电登录验证码，请手动获取验证码后填写")


def _tunnel_mechanical_captcha_bytes(img_base64: str) -> bytes:
    text = str(img_base64 or "").strip()
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="验证码图片格式不正确") from exc


def _read_tunnel_mechanical_captcha_text(image_bytes: bytes) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
    except Exception:
        return ""

    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        try:
            raw_result, _ = RapidOCR()(str(temp_path))
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception:
        return ""
    if not isinstance(raw_result, list):
        return ""
    parts: list[str] = []
    for line in raw_result:
        try:
            parts.append(str(line[1] or ""))
        except Exception:
            continue
    return "".join(parts)


def _solve_tunnel_mechanical_captcha_text(text: str) -> str:
    normalized = _normalize_tunnel_mechanical_captcha_text(text)
    match = re.search(r"(-?\d{1,2})([+\-*/x×÷])(-?\d{1,2})", normalized, re.IGNORECASE)
    if not match:
        return ""
    left = int(match.group(1))
    operator = match.group(2).lower()
    right = int(match.group(3))
    value = _calculate_tunnel_mechanical_captcha(left, operator, right)
    return str(value) if value is not None else ""


def _normalize_tunnel_mechanical_captcha_text(text: str) -> str:
    normalized = str(text or "").strip()
    replacements = {
        " ": "",
        "\t": "",
        "？": "?",
        "＝": "=",
        "×": "*",
        "X": "x",
        "÷": "/",
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized


def _calculate_tunnel_mechanical_captcha(left: int, operator: str, right: int) -> int | None:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator in {"*", "x"}:
        return left * right
    if operator == "/" and right != 0 and left % right == 0:
        return left // right
    return None


def _solve_tunnel_mechanical_captcha_image(image_bytes: bytes) -> str:
    try:
        import cv2
        import numpy as np
    except Exception:
        return ""

    data = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        return ""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mask = _tunnel_mechanical_captcha_mask(rgb)
    if int(mask.sum()) < 40:
        return ""
    symbols = _segment_tunnel_mechanical_captcha_symbols(mask)
    if len(symbols) < 3:
        return ""
    first = _classify_tunnel_mechanical_captcha_digit(symbols[0])
    operator = _classify_tunnel_mechanical_captcha_operator(symbols[1])
    second = _classify_tunnel_mechanical_captcha_digit(symbols[2])
    if first is None or second is None or not operator:
        return ""
    value = _calculate_tunnel_mechanical_captcha(first, operator, second)
    return str(value) if value is not None else ""


def _tunnel_mechanical_captcha_mask(rgb: Any) -> Any:
    import cv2
    import numpy as np

    red = rgb[:, :, 0].astype("float32")
    green = rgb[:, :, 1].astype("float32")
    blue = rgb[:, :, 2].astype("float32")
    blue_mask = (blue > 70) & (blue > red * 1.2) & (blue > green * 1.05) & (red < 170) & (green < 190)
    if int(blue_mask.sum()) < 40:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        blue_mask = gray < 150

    raw_mask = blue_mask.astype("uint8")
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, 8)
    mask = np.zeros_like(raw_mask)
    for index in range(1, component_count):
        x, y, width, height, area = [int(value) for value in stats[index]]
        if area >= 12 and height >= 4 and width >= 1:
            mask[labels == index] = 1
    return mask


def _segment_tunnel_mechanical_captcha_symbols(mask: Any) -> list[Any]:
    import cv2
    import numpy as np

    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []
    crop = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    counts = crop.sum(axis=0)
    columns = np.where(counts > 0)[0]
    if len(columns) == 0:
        return []

    groups: list[tuple[int, int]] = []
    start = previous = int(columns[0])
    for raw_column in columns[1:]:
        column = int(raw_column)
        if column <= previous + 2:
            previous = column
        else:
            groups.append((start, previous))
            start = previous = column
    groups.append((start, previous))

    expanded: list[tuple[int, int]] = []
    for left, right in groups:
        width = right - left + 1
        if width >= max(18, int(crop.shape[1] * 0.22)):
            split = _captcha_widest_valley(counts[left : right + 1])
            if split is not None and 4 <= split <= width - 5:
                expanded.append((left, left + split - 1))
                expanded.append((left + split + 1, right))
                continue
        expanded.append((left, right))

    symbols: list[Any] = []
    for left, right in expanded:
        symbol = crop[:, left : right + 1]
        ys, xs = np.where(symbol > 0)
        if len(xs) == 0:
            continue
        symbol = symbol[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        if int(symbol.sum()) >= 10:
            symbols.append(symbol.astype("uint8"))
    return symbols[:3]


def _captcha_widest_valley(counts: Any) -> int | None:
    values = [int(value) for value in counts]
    if len(values) < 12:
        return None
    start = max(3, len(values) // 4)
    end = min(len(values) - 3, len(values) * 3 // 4)
    if end <= start:
        return None
    return min(range(start, end), key=lambda index: values[index])


def _classify_tunnel_mechanical_captcha_digit(symbol: Any) -> int | None:
    best_digit = None
    best_score = 0.0
    normalized = _normalize_captcha_symbol(symbol)
    for digit, template in _tunnel_mechanical_digit_templates():
        score = _binary_jaccard_score(normalized, template)
        if score > best_score:
            best_digit = digit
            best_score = score
    if best_digit is not None and best_score >= 0.28:
        return best_digit
    return None


def _classify_tunnel_mechanical_captcha_operator(symbol: Any) -> str:
    import cv2
    import numpy as np

    height, width = symbol.shape[:2]
    if height <= 0 or width <= 0:
        return ""
    density = float(symbol.sum()) / float(height * width)
    row_counts = symbol.sum(axis=1)
    column_counts = symbol.sum(axis=0)
    strong_rows = int((row_counts >= max(2, width * 0.45)).sum())
    strong_columns = int((column_counts >= max(2, height * 0.45)).sum())
    if height <= width * 0.45 and strong_rows >= 1:
        return "-"
    if strong_rows >= 1 and strong_columns >= 1 and density < 0.55:
        return "+"

    lines = cv2.HoughLinesP(
        (symbol * 255).astype("uint8"),
        1,
        np.pi / 180,
        threshold=5,
        minLineLength=max(4, min(height, width) // 3),
        maxLineGap=2,
    )
    if lines is not None:
        angles = []
        for line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = [int(value) for value in line]
            if x1 == x2 and y1 == y2:
                continue
            angle = abs(float(np.degrees(np.arctan2(y2 - y1, x2 - x1))))
            angle = 180.0 - angle if angle > 90.0 else angle
            angles.append(angle)
        if any(20.0 <= angle <= 70.0 for angle in angles):
            return "*"
    return "*"


def _normalize_captcha_symbol(symbol: Any, *, size: int = 28) -> Any:
    import cv2
    import numpy as np

    ys, xs = np.where(symbol > 0)
    output = np.zeros((size, size), dtype="uint8")
    if len(xs) == 0:
        return output
    crop = symbol[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].astype("uint8")
    height, width = crop.shape[:2]
    scale = min((size - 4) / max(1, width), (size - 4) / max(1, height))
    resized_width = max(1, int(round(width * scale)))
    resized_height = max(1, int(round(height * scale)))
    resized = cv2.resize(crop, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    resized = (resized > 0).astype("uint8")
    x_offset = (size - resized_width) // 2
    y_offset = (size - resized_height) // 2
    output[y_offset : y_offset + resized_height, x_offset : x_offset + resized_width] = resized
    return output


@lru_cache(maxsize=1)
def _tunnel_mechanical_digit_templates() -> tuple[tuple[int, Any], ...]:
    import cv2
    import numpy as np

    templates: list[tuple[int, Any]] = []
    for digit in range(10):
        for scale in (0.8, 0.9, 1.0, 1.1):
            for thickness in (1, 2, 3):
                canvas = np.zeros((48, 48), dtype="uint8")
                cv2.putText(
                    canvas,
                    str(digit),
                    (7, 38),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    scale,
                    1,
                    thickness=thickness,
                    lineType=cv2.LINE_AA,
                )
                templates.append((digit, _normalize_captcha_symbol((canvas > 0).astype("uint8"))))
    return tuple(templates)


def _binary_jaccard_score(left: Any, right: Any) -> float:
    import numpy as np

    left_bool = left > 0
    right_bool = right > 0
    union = int(np.logical_or(left_bool, right_bool).sum())
    if union == 0:
        return 0.0
    return float(np.logical_and(left_bool, right_bool).sum()) / float(union)


def _tunnel_mechanical_auth_value(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    return text if text.lower().startswith("bearer ") else f"Bearer {text}"


def _tunnel_mechanical_token_valid(state: dict[str, Any], now: datetime | None = None) -> bool:
    token = str(state.get("access_token") or "").strip()
    if not token:
        return False
    expires_at = _state_datetime(str(state.get("token_expires_at") or ""))
    return expires_at is not None and (now or datetime.now(TZ)) < expires_at


def _tunnel_mechanical_token_needs_keepalive(state: dict[str, Any], now: datetime | None = None) -> bool:
    token = str(state.get("access_token") or "").strip()
    if not token:
        return True
    expires_at = _state_datetime(str(state.get("token_expires_at") or ""))
    if expires_at is None:
        return True
    refresh_before = timedelta(minutes=TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES)
    return expires_at <= (now or datetime.now(TZ)) + refresh_before


def _tunnel_mechanical_cookie_header(cookies: httpx.Cookies) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in cookies.jar)


def _httpx_error_message(exc: httpx.HTTPError) -> str:
    parts = [exc.__class__.__name__]
    detail = str(exc).strip()
    if detail:
        parts.append(detail)
    request = getattr(exc, "request", None)
    if request is not None:
        parts.append(str(request.url))
    return "：".join(parts)


async def _fetch_tunnel_mechanical_captcha(base_url: str, *, solve_attempts: int = 5) -> dict[str, Any]:
    base_url = _tunnel_mechanical_base_url(base_url)
    attempts = max(1, int(solve_attempts or 1))
    last_result: dict[str, Any] | None = None
    for _ in range(attempts):
        try:
            async with tunnel_async_httpx_client(timeout=15) as client:
                response = await client.get(
                    f"{base_url}/prod-api/code",
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "Origin": base_url,
                        "Referer": f"{base_url}/login",
                    },
                )
                body = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"获取智慧养护验证码失败：{_httpx_error_message(exc)}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="获取智慧养护验证码失败：平台返回的不是 JSON") from exc
        if response.status_code != 200 or str(body.get("code") or "") not in {"200", "0"}:
            raise HTTPException(status_code=502, detail=str(body.get("msg") or "获取智慧养护验证码失败"))
        img = _tunnel_mechanical_decrypt_text(str(body.get("img") or "")) if body.get("img") else ""
        captcha_enabled = bool(body.get("captchaEnabled", True))
        code = ""
        if img and captcha_enabled:
            try:
                code = _solve_tunnel_mechanical_captcha(img)
            except HTTPException:
                code = ""
        last_result = {
            "success": True,
            "captcha_enabled": captcha_enabled,
            "img": img,
            "code": code,
            "uuid": str(body.get("uuid") or ""),
        }
        if code or not captcha_enabled:
            return last_result
    return last_result or {"success": True, "captcha_enabled": False, "img": "", "code": "", "uuid": ""}


def _tunnel_mechanical_login_payload(config: dict[str, Any], *, code: str = "", uuid: str = "") -> dict[str, str]:
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="请先配置智慧养护平台账号和密码")
    return {
        "username": _tunnel_mechanical_password_cipher(username),
        "password": _tunnel_mechanical_password_cipher(password),
        "code": str(code or "").strip(),
        "uuid": str(uuid or "").strip(),
    }


def _tunnel_mechanical_token_data(body: dict[str, Any]) -> tuple[str, str, int]:
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    access_token = str(
        data.get("access_token")
        or data.get("accessToken")
        or data.get("token")
        or data.get("ACCESS_TOKEN")
        or ""
    ).strip()
    refresh_token = str(data.get("refresh_token") or data.get("refreshToken") or "").strip()
    expires_in = data.get("expires_in") or data.get("expiresIn") or 7200
    try:
        expires_seconds = max(60, int(float(expires_in)))
    except (TypeError, ValueError):
        expires_seconds = 7200
    return access_token, refresh_token, expires_seconds


def _save_tunnel_mechanical_token_state(
    repo: DutyRepository,
    *,
    body: dict[str, Any],
    cookie_header: str,
    now: datetime,
    fallback_refresh_token: str = "",
) -> dict[str, Any]:
    access_token, refresh_token, expires_seconds = _tunnel_mechanical_token_data(body)
    if not access_token:
        repo.save_tunnel_mechanical_state(last_error="登录成功但平台没有返回 access_token")
        raise HTTPException(status_code=502, detail="登录成功但平台没有返回 access_token")
    token_expires_at = (now + timedelta(seconds=max(30, expires_seconds - 60))).isoformat()
    repo.save_tunnel_mechanical_state(
        access_token=access_token,
        refresh_token=refresh_token or fallback_refresh_token,
        cookie_header=cookie_header,
        token_expires_at=token_expires_at,
        last_login_at=now.isoformat(),
        last_error="",
    )
    return repo.get_tunnel_mechanical_state()


async def _refresh_tunnel_mechanical_token(
    repo: DutyRepository,
    base_url: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    refresh_token = str(state.get("refresh_token") or "").strip()
    if not refresh_token:
        return None
    now = datetime.now(TZ)
    try:
        async with tunnel_async_httpx_client(timeout=20) as client:
            response = await client.post(
                f"{base_url}/prod-api/auth/refresh",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Authorization": _tunnel_mechanical_auth_value(refresh_token),
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": base_url,
                    "Referer": f"{base_url}/login",
                },
            )
            try:
                body: Any = response.json()
            except ValueError:
                body = {"msg": response.text[:500]}
            cookie_header = _tunnel_mechanical_cookie_header(client.cookies) or str(state.get("cookie_header") or "")
    except httpx.HTTPError as exc:
        repo.save_tunnel_mechanical_state(last_error=f"刷新 token 失败：{exc}")
        return None
    if response.status_code != 200 or not isinstance(body, dict) or str(body.get("code") or "") not in {"200", "0"}:
        message = str(body.get("msg") if isinstance(body, dict) else body) or "刷新 token 失败"
        repo.save_tunnel_mechanical_state(last_error=message)
        return None
    return _save_tunnel_mechanical_token_state(
        repo,
        body=body,
        cookie_header=cookie_header,
        now=now,
        fallback_refresh_token=refresh_token,
    )


async def _login_tunnel_mechanical(
    repo: DutyRepository,
    config: dict[str, Any],
    *,
    code: str = "",
    uuid: str = "",
    max_attempts: int = 3,
) -> dict[str, Any]:
    base_url = _tunnel_mechanical_base_url(str(config.get("base_url") or ""))
    initial_code = str(code or "").strip()
    initial_uuid = str(uuid or "").strip()
    attempts = 1 if initial_code or initial_uuid else max(1, max_attempts)
    last_message = ""
    for _ in range(attempts):
        attempt_code = initial_code
        attempt_uuid = initial_uuid
        if not attempt_uuid and not attempt_code:
            captcha = await _fetch_tunnel_mechanical_captcha(base_url)
            if captcha.get("captcha_enabled"):
                attempt_code = str(captcha.get("code") or "").strip()
                if not attempt_code:
                    try:
                        attempt_code = _solve_tunnel_mechanical_captcha(str(captcha.get("img") or ""))
                    except HTTPException as exc:
                        last_message = str(exc.detail or "无法自动识别验证码")
                        continue
            attempt_uuid = str(captcha.get("uuid") or "")
        if not attempt_code and not attempt_uuid:
            last_message = "无法自动获取验证码"
            continue
        payload = _tunnel_mechanical_login_payload(config, code=attempt_code, uuid=attempt_uuid)
        now = datetime.now(TZ)
        try:
            async with tunnel_async_httpx_client(timeout=20) as client:
                response = await client.post(
                    f"{base_url}/prod-api/auth/login",
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "Content-Type": "application/json;charset=UTF-8",
                        "Origin": base_url,
                        "Referer": f"{base_url}/login",
                    },
                    json=payload,
                )
                try:
                    body: Any = response.json()
                except ValueError:
                    body = {"msg": response.text[:500]}
                cookie_header = _tunnel_mechanical_cookie_header(client.cookies)
        except httpx.HTTPError as exc:
            repo.save_tunnel_mechanical_state(last_error=f"登录请求失败：{exc}")
            raise HTTPException(status_code=502, detail=f"登录智慧养护平台失败：{exc}") from exc
        if response.status_code == 200 and isinstance(body, dict) and str(body.get("code") or "") in {"200", "0"}:
            return _save_tunnel_mechanical_token_state(repo, body=body, cookie_header=cookie_header, now=now)
        last_message = str(body.get("msg") if isinstance(body, dict) else body) or "账号、密码或验证码不正确"
        if initial_code or initial_uuid or not _tunnel_mechanical_login_error_retryable(last_message):
            break
    message = last_message or "账号、密码或验证码不正确"
    repo.save_tunnel_mechanical_state(
        access_token="",
        refresh_token="",
        cookie_header="",
        token_expires_at="",
        last_error=message,
    )
    raise HTTPException(status_code=400, detail=message)


def _tunnel_mechanical_login_error_retryable(message: str) -> bool:
    text = str(message or "")
    return any(keyword in text for keyword in ("验证码", "captcha", "校验码"))


def _tunnel_mechanical_response_auth_expired(status_code: int, body: Any) -> bool:
    if int(status_code or 0) in {401, 403}:
        return True
    if not isinstance(body, dict):
        return False
    code = str(body.get("code") or body.get("status") or body.get("errcode") or "").strip()
    message = str(body.get("msg") or body.get("message") or body.get("error") or "")
    if code in {"401", "403", "-14"}:
        return True
    return any(keyword in message for keyword in ("登录状态已过期", "登录已过期", "未登录", "无效token", "token失效", "Unauthorized"))


def _clear_tunnel_mechanical_login_state(repo: DutyRepository, message: str = "智慧养护登录已失效，已自动重新登录") -> None:
    repo.save_tunnel_mechanical_state(
        access_token="",
        refresh_token="",
        cookie_header="",
        token_expires_at="",
        last_error=message,
    )


async def _keepalive_tunnel_mechanical_login(repo: DutyRepository) -> None:
    config = repo.get_tunnel_mechanical_config()
    base_url_text = str(config.get("base_url") or "").strip()
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")
    if not base_url_text or not username or not password:
        return
    try:
        base_url = _tunnel_mechanical_base_url(base_url_text)
        state = repo.get_tunnel_mechanical_state()
        if not _tunnel_mechanical_token_needs_keepalive(state):
            return
        refreshed_state = await _refresh_tunnel_mechanical_token(repo, base_url, state)
        if refreshed_state and _tunnel_mechanical_token_valid(refreshed_state):
            LOGGER.info("隧道机电登录态已通过 refresh token 保活")
            return
        await _login_tunnel_mechanical(repo, {**config, "base_url": base_url})
        LOGGER.info("隧道机电登录态已自动重新登录保活")
    except HTTPException as exc:
        repo.save_tunnel_mechanical_state(last_error=f"隧道机电登录保活失败：{exc.detail}")
        LOGGER.warning("隧道机电登录保活失败：%s", exc.detail)
    except Exception as exc:
        repo.save_tunnel_mechanical_state(last_error=f"隧道机电登录保活失败：{exc}")
        LOGGER.exception("隧道机电登录保活失败")



async def _tunnel_mechanical_auth_headers(
    repo: DutyRepository,
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
    base_url: str,
    *,
    force_login: bool = False,
) -> dict[str, str]:
    if request.authorization.strip() or request.cookie.strip():
        headers: dict[str, str] = {}
        if request.authorization.strip():
            headers["Authorization"] = request.authorization.strip()
        if request.cookie.strip():
            headers["Cookie"] = request.cookie.strip()
        return headers

    state = repo.get_tunnel_mechanical_state()
    if force_login:
        _clear_tunnel_mechanical_login_state(repo)
        state = await _login_tunnel_mechanical(
            repo,
            {**repo.get_tunnel_mechanical_config(), "base_url": base_url},
        )
    elif not _tunnel_mechanical_token_valid(state):
        refreshed_state = await _refresh_tunnel_mechanical_token(repo, base_url, state)
        state = refreshed_state or await _login_tunnel_mechanical(
            repo,
            {**repo.get_tunnel_mechanical_config(), "base_url": base_url},
        )
    token = str(state.get("access_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="没有可用的智慧养护登录 token，请先在页面完成登录测试")
    headers = {"Authorization": _tunnel_mechanical_auth_value(token)}
    cookie_header = str(state.get("cookie_header") or "").strip()
    if cookie_header:
        headers["Cookie"] = cookie_header
    return headers


def _build_tunnel_mechanical_payload(request: TunnelMechanicalSubmitRequest, row: TunnelMechanicalAssetRequest) -> dict[str, Any]:
    return {
        "assetId": str(row.assetId),
        "assetName": row.assetName,
        "assetCode": row.assetCode,
        "routeCode": row.routeCode,
        "routeName": row.routeName,
        "checkerId": str(request.checkerId),
        "checker": request.checker,
        "centerStake": None,
        "deptName": row.deptName,
        "recorder": request.recorder,
        "recorderId": str(request.recorderId),
        "recordType": 2,
        "assetIds": [],
        "domains": [
            {
                "checkId": None,
                "devName": row.devName,
                "location": row.location,
                "content": row.content,
                "result": row.result,
                "describe": None,
                "measures": None,
                "picPaths": None,
                "carLicense": row.carLicense,
                "nums": row.nums,
            }
        ],
        "maintenanceSectionId": row.maintenanceSectionId,
        "domainId": str(row.domainId),
        "checkTime": request.checkTime.isoformat(),
        "weather": request.weather,
        "faultRecordList": [],
    }


async def _post_tunnel_mechanical_submissions(
    submissions: list[dict[str, Any]],
    *,
    submit_url: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    results = []
    try:
        async with tunnel_async_httpx_client(timeout=20) as client:
            for submission in submissions:
                response = await client.post(submit_url, headers=headers, json=submission["payload"])
                try:
                    body: Any = response.json()
                except ValueError:
                    body = response.text[:2000]
                if _tunnel_mechanical_response_auth_expired(response.status_code, body):
                    raise HTTPException(status_code=401, detail="智慧养护登录已失效，正在自动重新登录")
                ok = response.status_code == 200 and (not isinstance(body, dict) or str(body.get("code") or "") == "200")
                results.append(
                    {
                        "assetId": submission["assetId"],
                        "assetName": submission["assetName"],
                        "status": response.status_code,
                        "ok": ok,
                        "body": body,
                    }
                )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"提交到智慧养护平台失败：{exc}") from exc
    return results


async def _submit_tunnel_mechanical(
    repo: DutyRepository,
    request: TunnelMechanicalSubmitRequest,
    *,
    result_upload_dir: Path | None = None,
) -> dict[str, Any]:
    rows = [row for row in request.rows if row.enabled]
    if not rows:
        raise HTTPException(status_code=400, detail="请至少选择一条隧道记录")
    submissions = [
        {"assetId": row.assetId, "assetName": row.assetName, "payload": _build_tunnel_mechanical_payload(request, row)}
        for row in rows
    ]
    if request.dry_run:
        response_body: dict[str, Any] = {"success": True, "dry_run": True, "submissions": submissions}
        if result_upload_dir is not None:
            try:
                result_upload_dir.mkdir(parents=True, exist_ok=True)
                _cleanup_old_uploads(result_upload_dir)
                filename = f"tunnel-mechanical-preview-{request.checkTime.isoformat()}-{uuid.uuid4().hex}.png"
                target = result_upload_dir / filename
                target.write_bytes(render_tunnel_mechanical_preview_image(submissions))
                response_body["preview_image_url"] = f"/api/uploads/{filename}"
            except Exception as exc:
                LOGGER.exception("生成隧道机电预览图片失败")
                response_body["preview_image_error"] = str(exc)
        return response_body

    base_url, headers, template = await _tunnel_mechanical_request_context(repo, request)
    submit_path = _tunnel_mechanical_api_path(str(template.get("submit_path") or ""), fallback="/prod-api/patrol/deviceCheck/add")
    submit_url = f"{base_url}{submit_path}"

    results = []
    try:
        results = await _post_tunnel_mechanical_submissions(submissions, submit_url=submit_url, headers=headers)
    except HTTPException as exc:
        if exc.status_code != 401 or request.authorization.strip() or request.cookie.strip():
            raise
        base_url, headers, template = await _tunnel_mechanical_request_context(repo, request, force_login=True)
        submit_path = _tunnel_mechanical_api_path(str(template.get("submit_path") or ""), fallback="/prod-api/patrol/deviceCheck/add")
        submit_url = f"{base_url}{submit_path}"
        try:
            results = await _post_tunnel_mechanical_submissions(submissions, submit_url=submit_url, headers=headers)
        except HTTPException:
            raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"提交到智慧养护平台失败：{exc}") from exc
    response_body: dict[str, Any] = {"success": all(item["ok"] for item in results), "dry_run": False, "results": results}
    if response_body["success"] and result_upload_dir is not None:
        try:
            query_result = await _save_tunnel_mechanical_result_image(
                repo,
                request,
                base_url=base_url,
                headers=headers,
                upload_dir=result_upload_dir,
            )
        except HTTPException as exc:
            if exc.status_code != 401 or request.authorization.strip() or request.cookie.strip():
                raise
            base_url, headers, _ = await _tunnel_mechanical_request_context(repo, request, force_login=True)
            query_result = await _save_tunnel_mechanical_result_image(
                repo,
                request,
                base_url=base_url,
                headers=headers,
                upload_dir=result_upload_dir,
            )
        response_body.update(query_result)
    return response_body


async def _modify_tunnel_mechanical(
    repo: DutyRepository,
    request: TunnelMechanicalModifyRequest,
    *,
    result_upload_dir: Path | None = None,
) -> dict[str, Any]:
    base_url, headers, template = await _tunnel_mechanical_request_context(repo, request)
    list_path = _tunnel_mechanical_api_path(str(template.get("list_path") or ""))
    if not list_path:
        raise HTTPException(status_code=400, detail="模板未配置 list_path，无法查询要修改的隧道机电记录")
    update_paths = _tunnel_mechanical_update_paths(template)
    try:
        raw_rows = await _query_tunnel_mechanical_raw_records(request, base_url=base_url, headers=headers, list_path=list_path)
        result = await _apply_tunnel_mechanical_updates(
            request,
            raw_rows,
            base_url=base_url,
            headers=headers,
            update_paths=update_paths,
        )
    except HTTPException as exc:
        if exc.status_code != 401 or request.authorization.strip() or request.cookie.strip():
            raise
        base_url, headers, template = await _tunnel_mechanical_request_context(repo, request, force_login=True)
        list_path = _tunnel_mechanical_api_path(str(template.get("list_path") or ""))
        if not list_path:
            raise HTTPException(status_code=400, detail="模板未配置 list_path，无法查询要修改的隧道机电记录") from exc
        update_paths = _tunnel_mechanical_update_paths(template)
        raw_rows = await _query_tunnel_mechanical_raw_records(request, base_url=base_url, headers=headers, list_path=list_path)
        result = await _apply_tunnel_mechanical_updates(
            request,
            raw_rows,
            base_url=base_url,
            headers=headers,
            update_paths=update_paths,
        )
    if result["success"] and result_upload_dir is not None and not request.dry_run:
        final_request = TunnelMechanicalResultImageRequest(
            base_url=request.base_url,
            authorization=request.authorization,
            cookie=request.cookie,
            checkTime=request.newCheckTime or request.checkTime,
        )
        query_result = await _save_tunnel_mechanical_result_image(
            repo,
            final_request,
            base_url=base_url,
            headers=headers,
            upload_dir=result_upload_dir,
        )
        result.update(query_result)
    return result


def _tunnel_mechanical_update_paths(template: dict[str, Any]) -> list[str]:
    configured = _tunnel_mechanical_api_path(str(template.get("update_path") or ""))
    if configured:
        return [configured]
    return ["/prod-api/patrol/deviceCheck/edit", "/prod-api/patrol/deviceCheck/update"]


async def _apply_tunnel_mechanical_updates(
    request: TunnelMechanicalModifyRequest,
    rows: list[dict[str, Any]],
    *,
    base_url: str,
    headers: dict[str, str],
    update_paths: list[str],
) -> dict[str, Any]:
    if not rows:
        raise HTTPException(status_code=404, detail="没有找到匹配的隧道机电记录，请检查原日期、负责人、记录人和天气")
    updates = []
    for row in rows:
        record_id = _tunnel_mechanical_record_id(row)
        detail = await _query_tunnel_mechanical_update_detail(record_id, base_url=base_url, headers=headers)
        source = detail or row
        updates.append(
            {
                "recordId": _tunnel_mechanical_record_id(source) or record_id,
                "assetName": _first_present(source, row, _first_tunnel_mechanical_domain(source), "assetName", "tunnelName", "name"),
                "payload": _build_tunnel_mechanical_update_payload(request, source),
            }
        )
    if request.dry_run:
        return {"success": True, "dry_run": True, "count": len(updates), "updates": updates}
    results = await _post_tunnel_mechanical_updates(
        updates,
        base_url=base_url,
        headers=headers,
        update_paths=update_paths,
    )
    return {"success": all(item["ok"] for item in results), "dry_run": False, "count": len(results), "results": results}


async def _query_tunnel_mechanical_update_detail(
    record_id: str,
    *,
    base_url: str,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    if not record_id:
        return None
    url = f"{base_url}/prod-api/patrol/deviceCheck/get/{record_id}"
    try:
        async with tunnel_async_httpx_client(timeout=20) as client:
            response = await client.get(url, headers=headers)
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text[:2000]
    except httpx.HTTPError:
        return None
    if _tunnel_mechanical_response_auth_expired(response.status_code, body):
        raise HTTPException(status_code=401, detail="智慧养护登录已失效，正在自动重新登录")
    if response.status_code != 200 or not isinstance(body, dict):
        return None
    data = body.get("data")
    if str(body.get("code") or "") == "200" and isinstance(data, dict):
        return data
    return None


async def _post_tunnel_mechanical_updates(
    updates: list[dict[str, Any]],
    *,
    base_url: str,
    headers: dict[str, str],
    update_paths: list[str],
) -> list[dict[str, Any]]:
    results = []
    try:
        async with tunnel_async_httpx_client(timeout=20) as client:
            for update in updates:
                last_result: dict[str, Any] | None = None
                for method in ("post", "put"):
                    for path in update_paths:
                        url = f"{base_url}{path}"
                        response = await getattr(client, method)(url, headers=headers, json=update["payload"])
                        try:
                            body: Any = response.json()
                        except ValueError:
                            body = response.text[:2000]
                        if _tunnel_mechanical_response_auth_expired(response.status_code, body):
                            raise HTTPException(status_code=401, detail="智慧养护登录已失效，正在自动重新登录")
                        ok = response.status_code == 200 and (
                            not isinstance(body, dict) or str(body.get("code") or "") == "200"
                        )
                        last_result = {
                            "recordId": update["recordId"],
                            "assetName": update["assetName"],
                            "url": url,
                            "method": method.upper(),
                            "status": response.status_code,
                            "ok": ok,
                            "body": body,
                        }
                        if ok or not _tunnel_mechanical_update_should_try_next(response.status_code, body):
                            break
                    if last_result and (
                        last_result["ok"]
                        or not _tunnel_mechanical_update_should_try_next(
                            int(last_result["status"] or 0),
                            last_result["body"],
                        )
                    ):
                        break
                if last_result is None:
                    last_result = {
                        "recordId": update["recordId"],
                        "assetName": update["assetName"],
                        "url": "",
                        "method": "",
                        "status": 0,
                        "ok": False,
                        "body": "没有可用的隧道机电修改接口",
                    }
                results.append(last_result)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"修改智慧养护平台记录失败：{exc}") from exc
    return results


def _tunnel_mechanical_update_should_try_next(status_code: int, body: Any) -> bool:
    if int(status_code or 0) in {404, 405}:
        return True
    if not isinstance(body, dict):
        return False
    code = str(body.get("code") or body.get("status") or "").strip()
    message = str(body.get("msg") or body.get("message") or body.get("error") or "")
    if code in {"404", "405"}:
        return True
    return "Request method" in message and "not supported" in message


def _build_tunnel_mechanical_update_payload(
    request: TunnelMechanicalModifyRequest,
    row: dict[str, Any],
) -> dict[str, Any]:
    payload = copy.deepcopy(row)
    record_id = _tunnel_mechanical_record_id(payload)
    if record_id and not _first_present(payload, "id"):
        payload["id"] = record_id
    if request.newCheckTime:
        _set_tunnel_mechanical_payload_value(payload, ("checkTime",), request.newCheckTime.isoformat())
    if request.newWeather:
        _set_tunnel_mechanical_payload_value(payload, ("weather",), request.newWeather)
    if request.newChecker:
        _set_tunnel_mechanical_payload_value(payload, ("checker", "checkerName"), request.newChecker)
        _set_tunnel_mechanical_payload_value(payload, ("checkerId",), request.newCheckerId)
    if request.newRecorder:
        _set_tunnel_mechanical_payload_value(payload, ("recorder", "recorderName"), request.newRecorder)
        _set_tunnel_mechanical_payload_value(payload, ("recorderId",), request.newRecorderId)
    domains = payload.get("domains") or payload.get("domainList") or payload.get("deviceCheckDomainList")
    if not isinstance(domains, list):
        domains = [_build_tunnel_mechanical_update_domain(payload, record_id)]
        payload["domains"] = domains
    payload.setdefault("faultRecordList", [])
    payload.setdefault("assetIds", [])
    if isinstance(domains, list):
        for domain in domains:
            if not isinstance(domain, dict):
                continue
            if request.newCheckTime and _tunnel_mechanical_domain_has_value(domain, ("checkTime", "checkDate")):
                _set_tunnel_mechanical_payload_value(domain, ("checkTime", "checkDate"), request.newCheckTime.isoformat())
            if request.newWeather and _tunnel_mechanical_domain_has_value(domain, ("weather",)):
                _set_tunnel_mechanical_payload_value(domain, ("weather",), request.newWeather)
            if request.newChecker and _tunnel_mechanical_domain_has_value(domain, ("checker", "checkerName", "checkerId")):
                _set_tunnel_mechanical_payload_value(domain, ("checker", "checkerName"), request.newChecker)
                _set_tunnel_mechanical_payload_value(domain, ("checkerId",), request.newCheckerId)
            if request.newRecorder and _tunnel_mechanical_domain_has_value(domain, ("recorder", "recorderName", "recorderId")):
                _set_tunnel_mechanical_payload_value(domain, ("recorder", "recorderName"), request.newRecorder)
                _set_tunnel_mechanical_payload_value(domain, ("recorderId",), request.newRecorderId)
    return payload


def _tunnel_mechanical_domain_has_value(domain: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(str(domain.get(key) or "").strip() for key in keys)


def _build_tunnel_mechanical_update_domain(payload: dict[str, Any], record_id: str) -> dict[str, Any]:
    return {
        "checkId": _first_present(payload, "checkId", "deviceCheckId", "id") or record_id or None,
        "devName": _first_present(payload, "devName"),
        "location": _first_present(payload, "location"),
        "content": _first_present(payload, "content"),
        "result": payload.get("result"),
        "describe": payload.get("describe"),
        "measures": payload.get("measures"),
        "picPaths": payload.get("picPaths"),
        "carLicense": payload.get("carLicense"),
        "nums": payload.get("nums"),
    }


def _set_tunnel_mechanical_payload_value(payload: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    existing_key = next((key for key in keys if key in payload), keys[0])
    payload[existing_key] = value


def _tunnel_mechanical_record_id(row: dict[str, Any]) -> str:
    domain = _first_tunnel_mechanical_domain(row)
    return _first_present(row, "id", "checkId", "deviceCheckId") or _first_present(
        domain,
        "checkId",
        "id",
        "deviceCheckId",
    )


async def _query_tunnel_mechanical_result_image(
    repo: DutyRepository,
    request: TunnelMechanicalResultImageRequest,
    upload_dir: Path,
) -> dict[str, Any]:
    base_url, headers, _ = await _tunnel_mechanical_request_context(repo, request)
    try:
        result = await _save_tunnel_mechanical_result_image(
            repo,
            request,
            base_url=base_url,
            headers=headers,
            upload_dir=upload_dir,
        )
    except HTTPException as exc:
        if exc.status_code != 401 or request.authorization.strip() or request.cookie.strip():
            raise
        base_url, headers, _ = await _tunnel_mechanical_request_context(repo, request, force_login=True)
        result = await _save_tunnel_mechanical_result_image(
            repo,
            request,
            base_url=base_url,
            headers=headers,
            upload_dir=upload_dir,
        )
    return {"success": bool(result.get("result_image_url")), **result}


async def _tunnel_mechanical_request_context(
    repo: DutyRepository,
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
    *,
    force_login: bool = False,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    template = repo.get_tunnel_mechanical_template()
    config = repo.get_tunnel_mechanical_config()
    allowed_hosts = _tunnel_mechanical_allowed_hosts(
        str(config.get("base_url") or ""),
        str(template.get("base_url") or ""),
    )
    base_url = _tunnel_mechanical_base_url(
        request.base_url or str(config.get("base_url") or "") or str(template.get("base_url") or ""),
        allowed_hosts=allowed_hosts,
    )
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": base_url,
        "Referer": f"{base_url}/patrol/deviceCheck/dailyInfo",
    }
    headers.update(await _tunnel_mechanical_auth_headers(repo, request, base_url, force_login=force_login))
    return base_url, headers, template


async def _save_tunnel_mechanical_result_image(
    repo: DutyRepository,
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
    *,
    base_url: str,
    headers: dict[str, str],
    upload_dir: Path,
) -> dict[str, Any]:
    template = repo.get_tunnel_mechanical_template()
    list_path = _tunnel_mechanical_api_path(str(template.get("list_path") or ""))
    if not list_path:
        return {"result_query_error": "模板未配置 list_path，无法自动查询录入结果"}
    try:
        rows = await _query_tunnel_mechanical_records(request, base_url=base_url, headers=headers, list_path=list_path)
        upload_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_old_uploads(upload_dir)
        filename = f"tunnel-mechanical-result-{request.checkTime.isoformat()}-{uuid.uuid4().hex}.png"
        target = upload_dir / filename
        target.write_bytes(
            render_tunnel_mechanical_result_image(
                rows,
                check_time=request.checkTime,
                checker=getattr(request, "checker", ""),
                recorder=getattr(request, "recorder", ""),
            )
        )
        return {"result_rows": rows, "result_image_url": f"/api/uploads/{filename}"}
    except HTTPException as exc:
        if exc.status_code == 401:
            raise
        return {"result_query_error": str(exc.detail)}
    except Exception as exc:
        LOGGER.exception("生成隧道机电录入结果图片失败")
        return {"result_query_error": str(exc)}


async def _query_tunnel_mechanical_records(
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
    *,
    base_url: str,
    headers: dict[str, str],
    list_path: str,
) -> list[dict[str, Any]]:
    raw_rows = await _query_tunnel_mechanical_raw_records(
        request,
        base_url=base_url,
        headers=headers,
        list_path=list_path,
    )
    return _normalize_tunnel_mechanical_result_rows(raw_rows)


async def _query_tunnel_mechanical_raw_records(
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
    *,
    base_url: str,
    headers: dict[str, str],
    list_path: str,
) -> list[dict[str, Any]]:
    url = f"{base_url}{list_path}"
    date_text = request.checkTime.isoformat()
    attempts = [
        {"pageNum": "1", "pageSize": "50", "checkTime": date_text},
        {"pageNum": "1", "pageSize": "50", "beginCheckTime": date_text, "endCheckTime": date_text},
        {"pageNum": "1", "pageSize": "50", "params[beginCheckTime]": date_text, "params[endCheckTime]": date_text},
    ]
    last_error = ""
    unmatched_rows: list[dict[str, Any]] = []
    async with tunnel_async_httpx_client(timeout=20) as client:
        for params in attempts:
            try:
                response = await client.get(url, headers=headers, params=params)
                body: Any = response.json()
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            except ValueError:
                last_error = "平台查询接口返回的不是 JSON"
                continue
            if _tunnel_mechanical_response_auth_expired(response.status_code, body):
                raise HTTPException(status_code=401, detail="智慧养护登录已失效，正在自动重新登录")
            if response.status_code != 200 or not isinstance(body, dict):
                last_error = f"HTTP {response.status_code}"
                continue
            raw_rows = [row for row in _extract_tunnel_mechanical_rows(body) if isinstance(row, dict)]
            filtered = _filter_tunnel_mechanical_raw_rows(raw_rows, request)
            if filtered:
                return filtered
            if raw_rows:
                unmatched_rows = raw_rows
            else:
                last_error = "平台查询接口没有返回记录"
    if unmatched_rows:
        return []
    raise HTTPException(status_code=502, detail=f"查询隧道机电录入结果失败：{last_error or '平台没有返回有效数据'}")


def _extract_tunnel_mechanical_rows(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    candidates: list[Any] = [body]
    for key in ("data", "Data", "result", "rows"):
        value = body.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("rows", "list", "records", "items"):
            value = candidate.get(key)
            if isinstance(value, list):
                return value
    return []


def _filter_tunnel_mechanical_raw_rows(
    rows: list[dict[str, Any]],
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
) -> list[dict[str, Any]]:
    filtered = []
    for row in rows:
        normalized_rows = _normalize_tunnel_mechanical_result_rows([row])
        if not normalized_rows:
            continue
        normalized = normalized_rows[0]
        if not _tunnel_mechanical_result_row_matches_request(normalized, row, request):
            continue
        filtered.append(row)
    return filtered


def _normalize_tunnel_mechanical_result_rows(rows: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        domain = _first_tunnel_mechanical_domain(row)
        result_value = _first_present(row, domain, "result", "checkResult", "checkResultName")
        normalized.append(
            {
                "routeCode": _first_present(row, domain, "routeCode", "route_code"),
                "assetName": _first_present(row, domain, "assetName", "tunnelName", "name"),
                "deptName": _first_present(row, domain, "deptName", "dept_name", "maintenanceSectionName", "orgName"),
                "checkTime": _date_text(_first_present(row, domain, "checkTime", "checkDate", "createTime")),
                "weather": _first_present(row, domain, "weather"),
                "checker": _first_present(row, domain, "checker", "checkerName"),
                "recorder": _first_present(row, domain, "recorder", "recorderName"),
                "devName": _first_present(row, domain, "devName", "deviceName", "facilitiesName"),
                "location": _first_present(row, domain, "location", "checkLocation"),
                "content": _first_present(row, domain, "content", "checkContent"),
                "resultText": _tunnel_mechanical_result_text(result_value),
                "carLicense": _first_present(row, domain, "carLicense", "carNo"),
                "nums": _first_present(row, domain, "nums", "number"),
            }
        )
    return normalized


def _first_tunnel_mechanical_domain(row: dict[str, Any]) -> dict[str, Any]:
    domains = row.get("domains") or row.get("domainList") or row.get("deviceCheckDomainList")
    if isinstance(domains, list) and domains and isinstance(domains[0], dict):
        return domains[0]
    return {}


def _first_present(*sources_and_keys: Any) -> str:
    sources = [item for item in sources_and_keys if isinstance(item, dict)]
    keys = [item for item in sources_and_keys if isinstance(item, str)]
    for key in keys:
        for source in sources:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _date_text(value: str) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text


def _tunnel_mechanical_result_text(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if text in {"1", "正常", "true", "True"}:
        return "正常"
    if text in {"0", "异常", "false", "False"}:
        return "异常"
    return text or "-"


def _filter_tunnel_mechanical_result_rows(
    rows: list[dict[str, Any]],
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
) -> list[dict[str, Any]]:
    filtered = []
    for row in rows:
        if not _tunnel_mechanical_result_row_matches_request(row, {}, request):
            continue
        filtered.append(row)
    return filtered


def _tunnel_mechanical_result_row_matches_request(
    normalized: dict[str, Any],
    raw_row: dict[str, Any],
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest | TunnelMechanicalModifyRequest,
) -> bool:
    date_text = request.checkTime.isoformat()
    row_date = str(normalized.get("checkTime") or "")
    if row_date and row_date != date_text:
        return False
    if not isinstance(request, TunnelMechanicalModifyRequest):
        return True
    domain = _first_tunnel_mechanical_domain(raw_row) if raw_row else {}
    checks = (
        ("checker", request.checker, str(normalized.get("checker") or "")),
        ("recorder", request.recorder, str(normalized.get("recorder") or "")),
        ("weather", request.weather, str(normalized.get("weather") or "")),
        ("checkerId", request.checkerId, _first_present(raw_row, domain, "checkerId", "checker_id")),
        ("recorderId", request.recorderId, _first_present(raw_row, domain, "recorderId", "recorder_id")),
    )
    for _, expected, actual in checks:
        if expected and actual and str(actual).strip() != str(expected).strip():
            return False
    return True


def _reminder_events_response(repo: DutyRepository, target: date, *, now: datetime) -> dict[str, Any]:
    skipped_events = _skipped_reminder_events(repo, target)
    events = [*_plan_all_events(repo, target), *_plan_patrol_warning_display_events(repo, target, now=now)]
    events = sorted(events, key=lambda event: event.send_at)
    return {
        "target_date": target.isoformat(),
        "now_beijing": now.isoformat(),
        "group_statuses": _today_reminder_group_statuses(repo, target, events, skipped_events=skipped_events),
        "skipped_events": skipped_events,
        "events": [
            {
                "kind": event.kind,
                "person_name": event.person_name,
                "send_at": event.send_at.isoformat(),
                "content": event.content,
                "notification_room_id": event.target_room_id,
                "notification_room_name": event.target_room_name,
                "send_content_mode": _event_send_content_mode(event, "both"),
                "sent_state": "sent_or_due" if event.send_at <= now else "pending",
                **_today_reminder_event_media(repo, event, target),
            }
            for event in events
        ],
    }


def _filter_send_records(
    records: list[dict[str, Any]],
    *,
    status: str = "",
    kind: str = "",
    target: str = "",
    today_failed: bool = False,
) -> list[dict[str, Any]]:
    clean_status = str(status or "").strip()
    clean_kind = str(kind or "").strip()
    clean_target = str(target or "").strip()
    if today_failed:
        clean_status = "failed"
        today_start = datetime.combine(_today_in_tz(), datetime.min.time(), tzinfo=TZ).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    else:
        today_start = ""
    filtered: list[dict[str, Any]] = []
    for record in records:
        if clean_status and str(record.get("status") or "") != clean_status:
            continue
        if clean_kind and str(record.get("kind") or "") != clean_kind:
            continue
        if clean_target and clean_target not in str(record.get("target") or ""):
            continue
        if today_start and str(record.get("created_at") or "") < today_start:
            continue
        filtered.append(record)
    return filtered


def _build_people_center(repo: DutyRepository) -> list[dict[str, Any]]:
    names = set(repo.list_personnel_names())
    deleted_names = repo.list_deleted_personnel_names()
    personnel_by_name = {str(person.get("name") or "").strip(): person for person in repo.list_personnel()}
    monitored_by_name = {str(person.get("name") or "").strip(): person for person in repo.list_monitored_people()}
    custom_by_name: dict[str, list[dict[str, Any]]] = {}
    for reminder in repo.list_custom_reminders():
        name = str(reminder.get("name") or "").strip()
        if name:
            custom_by_name.setdefault(name, []).append(reminder)
            names.add(name)
    for name in monitored_by_name:
        if name:
            names.add(name)
    for roster in repo.list_roster_months():
        for row in roster.get("grid") or []:
            name = str(row.get("name") or "").strip()
            if name:
                names.add(name)
    rows: list[dict[str, Any]] = []
    for name in sorted(names, key=lambda value: value):
        if name in deleted_names:
            continue
        personnel = personnel_by_name.get(name, {})
        monitored = monitored_by_name.get(name, {})
        custom = custom_by_name.get(name, [])
        wecom_userid = str(personnel.get("wecom_userid") or monitored.get("wecom_userid") or "").strip()
        wechat_member = str(
            personnel.get("wechat_group_member_name")
            or personnel.get("wechat_group_runtime_sender_id")
            or personnel.get("wechat_group_member_id")
            or ""
        ).strip()
        rows.append(
            {
                "name": name,
                "wecom_userid": wecom_userid,
                "wecom_bound": bool(wecom_userid),
                "wechat_group_member": wechat_member,
                "wechat_group_bound": bool(wechat_member),
                "mention_mobile": str(personnel.get("mention_mobile") or monitored.get("mention_mobile") or "").strip(),
                "monitor_enabled": bool(monitored.get("enabled")) if monitored else False,
                "monitor_configured": bool(monitored),
                "daily_time": str(monitored.get("daily_time") or ""),
                "before_shift_minutes": int(monitored.get("before_shift_minutes") or 0) if monitored else 0,
                "rest_reminder_enabled": bool(monitored.get("rest_reminder_enabled")) if monitored else False,
                "custom_reminder_count": len(custom),
                "custom_enabled_count": len([item for item in custom if item.get("enabled") is not False]),
                "tunnel_mechanical_partner": str(personnel.get("tunnel_mechanical_partner") or "").strip(),
            }
        )
    return rows


def _interaction_command_catalog(repo: DutyRepository) -> list[dict[str, Any]]:
    menu_commands = {
        str(item.get("command") or "").strip()
        for group in _wecom_app_menu_groups(repo)
        for item in group.get("items", [])
        if str(item.get("command") or "").strip()
    }

    def item(command: str, feature: str, bind_required: bool, response_type: str, menu: bool, note: str = "") -> dict[str, Any]:
        return {
            "command": command,
            "feature": feature,
            "bind_required": bind_required,
            "response_type": response_type,
            "menu_available": menu,
            "in_current_menu": command in menu_commands,
            "note": note,
        }

    return [
        item("菜单", "查询菜单", False, "文字", False, "发送后 5 分钟内可用数字选择菜单项"),
        item("绑定商邱宏", "绑定企业微信/微信成员", False, "文字", False, "把当前发送者绑定到系统人员"),
        item("查询我的绑定", "绑定查询", False, "文字", False),
        item("查询今日在岗", "今日在岗", False, "图文", True),
        item("查询今日监控", "监控班查询", False, "图文", True),
        item("查询明日监控", "监控班查询", False, "图文", True),
        item("查询本周监控", "监控班查询", False, "图文", True),
        item("查询我的监控", "个人监控班查询", True, "图文", True),
        item("查询休息", "休息统计", True, "图文", True, "可写 查询商邱宏休息"),
        item("施工图片", "施工影像 Word", False, "文件", True, "按提示发送地点和 2 张图片"),
        item("施工点维护", "施工点维护", False, "文字", True, "1 新增 / 2 删除 / 3 修改"),
        item("机电模板", "隧道机电模板", True, "文字", True),
        item("修改模板", "隧道机电修改模板", True, "文字", True),
        item("录入今日机电", "隧道机电录入", True, "文字确认/图文结果", True),
        item("橙色预警巡查记录查询", "橙色预警巡查记录", True, "文字模板/图文结果", True),
        item("查询商邱宏巡查记录 2026-08-01至2026-08-16", "巡查记录查询", False, "图文", False),
    ]


def _reminder_diagnostics_response(repo: DutyRepository, target: date, *, now: datetime) -> dict[str, Any]:
    all_events = _plan_all_events(repo, target, include_wecom_unbound=True)
    active_events = _plan_all_events(repo, target)
    skipped = _skipped_reminder_events(repo, target)
    skipped_keys = {(item["kind"], item["person_name"], item["send_at"]) for item in skipped}
    active_keys = {(event.kind, event.person_name, event.send_at.isoformat()) for event in active_events}
    rows: list[dict[str, str]] = []
    for event in sorted(all_events, key=lambda item: item.send_at):
        key = (event.kind, event.person_name, event.send_at.isoformat())
        skipped_item = next((item for item in skipped if (item["kind"], item["person_name"], item["send_at"]) == key), None)
        if skipped_item:
            status = "skipped"
            reason = skipped_item["reason"]
        elif key in active_keys:
            status = "due" if event.send_at <= now else "pending"
            reason = "已到提醒时间，等待发送记录确认" if event.send_at <= now else "已生成，等待提醒时间"
        else:
            status = "filtered"
            reason = "被当前通知渠道规则过滤"
        rows.append(
            {
                "kind": event.kind,
                "person_name": event.person_name,
                "send_at": event.send_at.isoformat(),
                "status": status,
                "reason": reason,
                "content": event.content,
            }
        )
    generated_custom_keys = {
        (event.person_name, event.key_suffix)
        for event in all_events
        if event.kind == "custom" and event.key_suffix
    }
    rows.extend(_missing_custom_reminder_diagnostics(repo, target, generated_custom_keys))
    if not rows:
        rows.append({"kind": "none", "person_name": "", "send_at": "", "status": "empty", "reason": "当天没有任何提醒计划", "content": ""})
    return {"target_date": target.isoformat(), "now_beijing": now.isoformat(), "items": rows}


def _missing_custom_reminder_diagnostics(repo: DutyRepository, target: date, generated_custom_keys: set[tuple[str, str]]) -> list[dict[str, str]]:
    assignments = [assignment for roster in repo.list_roster_months() for assignment in _assignments_from_grid(roster)]
    rows: list[dict[str, str]] = []
    for reminder in repo.list_custom_reminders(enabled_only=True):
        name = str(reminder.get("name") or "").strip()
        shift_code = str(reminder.get("shift_code") or "").strip()
        reminder_key = (name, str(reminder.get("id") or ""))
        if not name or (reminder_key[1] and reminder_key in generated_custom_keys):
            continue
        try:
            shift = Shift(shift_code)
        except ValueError:
            reason = "自定义提醒班次配置无效"
        else:
            person_assignments = [item for item in assignments if item.work_date == target and item.person_name == name]
            if not person_assignments:
                reason = f"{name} 当天没有排班"
            elif not any(item.shift is shift for item in person_assignments):
                expected_label = "晚班" if shift is Shift.NIGHT else shift.label
                reason = f"{name} 当天不是{expected_label}"
            else:
                reason = "自定义提醒未生成，请检查文案或配置"
        rows.append(
            {
                "kind": "custom",
                "person_name": name,
                "send_at": "",
                "status": "not_generated",
                "reason": reason,
                "content": str(reminder.get("message") or ""),
            }
        )
    return rows


def _today_reminder_event_media(repo: DutyRepository, event: ReminderEvent, target: date) -> dict[str, str]:
    if event.kind == "daily_duty":
        if _event_send_content_mode(event, "both") in {"both", "image"}:
            return {
                "image_url": f"/api/daily-duty-image?target_date={target.isoformat()}",
                "image_alt": "今日在岗提醒图片",
            }
        return {}
    if event.kind in {"patrol_warning_start", "patrol_warning_end"}:
        send_content_mode = _patrol_send_content_mode(repo.get_patrol_warning_config())
        if send_content_mode in {"both", "image"}:
            mode = "end" if event.kind == "patrol_warning_end" else "start"
            return {
                "image_url": f"/api/patrol-warning-image?mode={mode}&t={event.send_at.timestamp()}",
                "image_alt": "公路巡查预警图片",
            }
    return {}


def _skipped_reminder_events(repo: DutyRepository, target: date) -> list[dict[str, str]]:
    if not bool(_notification_config_with_env_defaults(repo.get_notification_config()).get("wecom_app_enabled")):
        return []
    bound_lookup = _wecom_app_userid_lookup(repo)
    planned = _plan_all_events(repo, target, include_wecom_unbound=True)
    skipped: list[dict[str, str]] = []
    for event in planned:
        missing = _wecom_app_unbound_person_target(bound_lookup, event)
        if missing:
            skipped.append(
                {
                    "kind": event.kind,
                    "person_name": missing,
                    "send_at": event.send_at.isoformat(),
                    "reason": _wecom_app_unbound_detail(missing),
                }
            )
    return skipped


def _plan_patrol_warning_display_events(repo: DutyRepository, target: date, *, now: datetime) -> list[ReminderEvent]:
    config = repo.get_patrol_warning_config()
    if not config.get("enabled"):
        return []
    warning = warning_from_dict(dict(repo.get_patrol_warning_state().get("warning") or {}), TZ)
    if warning is None:
        return []
    if not _patrol_warning_in_display_window(warning, config, now=now):
        return []

    events: list[ReminderEvent] = []
    target_name = warning.route_code or warning.route_name or str(config.get("route_code") or "公路巡查预警")
    room_id = str(config.get("notification_room_id") or "").strip()
    room_name = str(config.get("notification_room_name") or "").strip()
    start_at = warning.start_time or warning.create_time
    if start_at and start_at.date() == target:
        events.append(
            ReminderEvent(
                kind="patrol_warning_start",
                person_name=target_name,
                send_at=start_at,
                content=_build_patrol_warning_content(warning, config, now=now, mode="start"),
                target_room_id=room_id,
                target_room_name=room_name,
            )
        )

    if warning.end_time:
        interval_hours = max(1, int(config.get("end_reminder_interval_hours") or 6))
        window_hours = max(1, int(config.get("end_reminder_window_hours") or 48))
        deadline = warning.end_time + timedelta(hours=window_hours)
        slot = warning.end_time
        while slot <= deadline:
            if slot.date() == target:
                events.append(
                    ReminderEvent(
                        kind="patrol_warning_end",
                        person_name=target_name,
                        send_at=slot,
                        content=_build_patrol_warning_content(warning, config, now=slot, mode="end"),
                        target_room_id=room_id,
                        target_room_name=room_name,
                    )
                )
            if slot.date() > target:
                break
            if not bool(config.get("end_reminder_enabled", True)):
                break
            slot += timedelta(hours=interval_hours)
    return events


def _patrol_warning_in_display_window(warning: Any, config: dict[str, Any], *, now: datetime) -> bool:
    if warning is None:
        return False
    end_time = getattr(warning, "end_time", None)
    window_hours = max(1, int((config or {}).get("end_reminder_window_hours") or 48))
    if end_time:
        return now <= end_time + timedelta(hours=window_hours)
    start_time = getattr(warning, "start_time", None) or getattr(warning, "create_time", None)
    return bool(not start_time or now <= start_time + timedelta(hours=min(window_hours, 48)))


def _patrol_warning_should_send_start(warning: Any, config: dict[str, Any], *, now: datetime) -> bool:
    if warning is None or not _patrol_warning_in_display_window(warning, config, now=now):
        return False
    timestamps = [
        value
        for value in (
            getattr(warning, "start_time", None),
            getattr(warning, "create_time", None),
        )
        if value is not None
    ]
    if not timestamps:
        return True
    freshness_hours = min(max(1, int((config or {}).get("end_reminder_window_hours") or 48)), 48)
    return now - max(timestamps) <= timedelta(hours=freshness_hours)


def _patrol_warning_due_end_slot(warning: Any, config: dict[str, Any], *, now: datetime) -> datetime | None:
    if warning is None or not _patrol_warning_in_display_window(warning, config, now=now):
        return None
    end_time = getattr(warning, "end_time", None)
    if not end_time or now < end_time:
        return None
    if not bool(config.get("end_reminder_enabled", True)):
        return end_time
    return due_end_reminder_slot(
        warning,
        now=now,
        interval_hours=int(config.get("end_reminder_interval_hours") or 6),
        window_hours=int(config.get("end_reminder_window_hours") or 48),
    )


def _today_reminder_group_statuses(
    repo: DutyRepository,
    target: date,
    events: list[ReminderEvent],
    *,
    skipped_events: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    event_kinds = {event.kind for event in events}
    skipped_events = skipped_events or []

    monitored_count = len(repo.list_monitored_people(enabled_only=True))
    has_monitor_events = bool(event_kinds & {"daily", "before_shift", "rest"})
    if monitored_count == 0:
        statuses.append({"key": "monitor", "message": "未配置监控班提醒人员"})
    elif not has_monitor_events:
        skipped_monitor = [item for item in skipped_events if item.get("kind") in {"daily", "before_shift", "rest"}]
        if skipped_monitor:
            names = "、".join(list(dict.fromkeys(item["person_name"] for item in skipped_monitor))[:6])
            statuses.append({"key": "monitor", "message": f"今日监控班提醒已生成但未发送：{names} 未绑定企业微信"})
        else:
            statuses.append({"key": "monitor", "message": "今日没有匹配到监控班提醒"})

    patrol_config = repo.get_patrol_warning_config()
    has_patrol_events = bool(event_kinds & {"patrol_warning_start", "patrol_warning_end"})
    if not patrol_config.get("enabled"):
        statuses.append({"key": "patrol_warning", "message": "公路巡查预警监测未启用"})
    elif not has_patrol_events:
        warning = warning_from_dict(dict(repo.get_patrol_warning_state().get("warning") or {}), TZ)
        if warning is None:
            statuses.append({"key": "patrol_warning", "message": "暂无已监测到的公路巡查预警"})
        elif _patrol_warning_in_display_window(warning, patrol_config, now=datetime.now(TZ)):
            statuses.append({"key": "patrol_warning", "message": f"{target:%Y-%m-%d} 没有公路巡查预警提醒"})
        else:
            pass

    if not repo.list_custom_reminders(enabled_only=True):
        statuses.append({"key": "custom", "message": "未配置自定义提醒"})
    elif "custom" not in event_kinds:
        skipped_custom = [item for item in skipped_events if item.get("kind") == "custom"]
        if skipped_custom:
            names = "、".join(list(dict.fromkeys(item["person_name"] for item in skipped_custom))[:6])
            statuses.append({"key": "custom", "message": f"今日自定义提醒已生成但未发送：{names} 未绑定企业微信"})
        else:
            statuses.append({"key": "custom", "message": "今日没有匹配到自定义提醒"})

    vacation_config = repo.get_vacation_reminder_config()
    if not vacation_config.get("enabled"):
        statuses.append({"key": "vacation", "message": "假期余额提醒未启用"})
    elif not (event_kinds & {"vacation_start", "vacation_end"}):
        skipped_vacation = [item for item in skipped_events if item.get("kind") in {"vacation_start", "vacation_end"}]
        if skipped_vacation:
            names = "、".join(list(dict.fromkeys(item["person_name"] for item in skipped_vacation))[:6])
            statuses.append({"key": "vacation", "message": f"今日假期提醒已生成但未发送：{names} 未绑定企业微信"})
        else:
            statuses.append({"key": "vacation", "message": "今日没有假期余额提醒"})

    return statuses


def _assignments_from_grid(roster_month: dict[str, Any]) -> list[ShiftAssignment]:
    assignments: list[ShiftAssignment] = []
    for row in roster_month["grid"]:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        for day_text, code in dict(row.get("days", {})).items():
            shift = normalize_shift_code(str(code))
            if shift is None:
                continue
            try:
                work_date = date(int(roster_month["year"]), int(roster_month["month"]), int(day_text))
            except ValueError:
                continue
            assignments.append(ShiftAssignment(name, work_date, shift))
    return assignments


def _roster_rows_for_date(repo: DutyRepository, target: date) -> list[dict[str, str]]:
    roster = repo.get_roster_month(target.year, target.month)
    if not roster:
        return []
    rows: list[dict[str, str]] = []
    day = str(target.day)
    for row in roster.get("grid", []):
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        days = dict(row.get("days", {}))
        if day in days:
            rows.append({"name": name, "code": str(days.get(day, "")).strip()})
    return rows


def _roster_code_for_person(repo: DutyRepository, person_name: str, target: date) -> str:
    row = next((row for row in _roster_rows_for_date(repo, target) if row["name"] == person_name), None)
    return row["code"] if row else ""


def _is_rest_code(code: str) -> bool:
    return code.strip() in {"休", "休息"}


def _is_on_duty_code(code: str) -> bool:
    return not _is_rest_code(code) and code.strip() != "出差"


def _join_names(names: list[str]) -> str:
    return "，".join(names) if names else "无"


def _today_in_tz() -> date:
    return datetime.now(TZ).date()


def _render_simple_template(template: str, values: dict[str, str]) -> str:
    content = template
    for key, value in values.items():
        content = content.replace("{" + key + "}", value)
    return content


def _custom_reminder_message_for_name(message: str, name: str) -> str | None:
    """Return custom reminder body, stripping legacy leading @ lines.

    The authoritative custom reminder target is the form's `name` + roster shift.
    A leading manual `@某人` line from older configs must not become a second,
    conflicting target and make reminders look like they fired for the wrong
    person.
    """
    target_name = str(name or "").strip()
    leading_mentions: list[str] = []
    body_lines: list[str] = []
    scanning_prefix = True
    for line in str(message or "").splitlines():
        stripped = line.strip()
        if scanning_prefix:
            match = None
            target_prefix = f"@{target_name}" if target_name else ""
            if target_prefix and stripped.startswith(target_prefix) and (
                len(stripped) == len(target_prefix) or stripped[len(target_prefix)] in " \t，,。.:：;；、"
            ):
                rest = stripped[len(target_prefix) :].lstrip(" \t，,。.:：;；、")
                match = (target_name, rest)
            else:
                regex_match = re.match(r"^@([^\s@，,。.:：;；、]+)\s*(.*)$", stripped)
                if regex_match:
                    match = (regex_match.group(1).strip(), regex_match.group(2).strip())
            if match:
                leading_mentions.append(match[0])
                rest = match[1].strip()
                if rest:
                    body_lines.append(rest)
                continue
            if not stripped:
                continue
            scanning_prefix = False
        body_lines.append(line)
    if leading_mentions and target_name not in leading_mentions:
        return None
    return "\n".join(body_lines).strip()


def _rest_status_for_date(repo: DutyRepository, person_name: str, target: date) -> dict[str, str] | None:
    today_is_rest = _is_rest_code(_roster_code_for_person(repo, person_name, target))
    tomorrow = target + timedelta(days=1)
    tomorrow_is_rest = _is_rest_code(_roster_code_for_person(repo, person_name, tomorrow))
    if not today_is_rest and tomorrow_is_rest:
        return {
            "date": f"{tomorrow:%Y-%m-%d}",
            "rest_start_date": f"{tomorrow:%Y-%m-%d}",
            "rest_end_date": f"{_rest_end_date(repo, person_name, tomorrow):%Y-%m-%d}",
            "rest_status": "今日下午休息",
        }
    if today_is_rest and tomorrow_is_rest:
        rest_start_date = _rest_start_date(repo, person_name, target)
        rest_end_date = _rest_end_date(repo, person_name, target)
        return {
            "date": f"{target:%Y-%m-%d}",
            "rest_start_date": f"{rest_start_date:%Y-%m-%d}",
            "rest_end_date": f"{rest_end_date:%Y-%m-%d}",
            "rest_status": f"正在休息到 {rest_end_date:%Y-%m-%d}",
        }
    if today_is_rest:
        rest_start_date = _rest_start_date(repo, person_name, target)
        return {
            "date": f"{target:%Y-%m-%d}",
            "rest_start_date": f"{rest_start_date:%Y-%m-%d}",
            "rest_end_date": f"{target:%Y-%m-%d}",
            "rest_status": "今日下午到岗",
        }
    return None


def _rest_end_date(repo: DutyRepository, person_name: str, start: date) -> date:
    current = start
    while _is_rest_code(_roster_code_for_person(repo, person_name, current + timedelta(days=1))):
        current += timedelta(days=1)
    return current


def _rest_start_date(repo: DutyRepository, person_name: str, end: date) -> date:
    current = end
    while _is_rest_code(_roster_code_for_person(repo, person_name, current - timedelta(days=1))):
        current -= timedelta(days=1)
    return current


def _rest_range_key(start: date | str, end: date | str) -> str:
    start_text = start.isoformat() if isinstance(start, date) else str(start or "").strip()
    end_text = end.isoformat() if isinstance(end, date) else str(end or "").strip()
    return f"rest_range:{start_text}:{end_text}" if start_text or end_text else ""


def _rest_range_key_from_status(rest_status: dict[str, str]) -> str:
    return _rest_range_key(str(rest_status.get("rest_start_date") or ""), str(rest_status.get("rest_end_date") or ""))


def _build_daily_duty_preview(repo: DutyRepository, target: date) -> dict[str, Any]:
    config = repo.get_daily_duty_config()
    rows = _roster_rows_for_date(repo, target)
    tomorrow = target + timedelta(days=1)
    tomorrow_rows = _roster_rows_for_date(repo, tomorrow)
    shift_names = {
        "early": [row["name"] for row in rows if row["code"] == "早"],
        "tomorrow_early": [row["name"] for row in tomorrow_rows if row["code"] == "早"],
        "middle": [row["name"] for row in rows if row["code"] == "中"],
        "night": [row["name"] for row in rows if row["code"] in {"晚", "夜"}],
    }
    afternoon_rest: list[str] = []
    resting: list[str] = []
    afternoon_return: list[str] = []
    for row in rows:
        rest_status = _rest_status_for_date(repo, row["name"], target)
        if not rest_status:
            continue
        status_text = rest_status["rest_status"]
        if status_text == "今日下午休息":
            afternoon_rest.append(row["name"])
        elif status_text.startswith("正在休息到"):
            resting.append(row["name"])
        elif status_text == "今日下午到岗":
            afternoon_return.append(row["name"])
    big_driver_set = set(config["big_driver_names"])
    small_driver_set = set(config["small_driver_names"])
    patrol_team_set = _configured_patrol_name_set(config)
    patrol_names, patrol_semantics_seen = _patrol_team_summary_names(rows, patrol_team_set)
    station_set = set(config["station_names"])
    office_set = set(config["office_names"])
    on_duty_names = [row["name"] for row in rows if _is_on_duty_code(row["code"])]
    big_drivers = [name for name in on_duty_names if name in big_driver_set]
    small_drivers = [name for name in on_duty_names if name in small_driver_set]
    excluded = (
        set(shift_names["early"])
        | set(shift_names["middle"])
        | set(shift_names["night"])
        | set(big_drivers)
        | set(small_drivers)
        | station_set
        | office_set
        | set(afternoon_rest)
        | set(resting)
        | set(afternoon_return)
    )
    if patrol_semantics_seen:
        excluded |= set(patrol_names)
    else:
        excluded |= patrol_team_set
    standby = [name for name in on_duty_names if name not in excluded]
    values = {
        "early": _join_names(shift_names["early"]),
        "tomorrow_early": _join_names(shift_names["tomorrow_early"]),
        "middle": _join_names(shift_names["middle"]),
        "night": _join_names(shift_names["night"]),
        "patrol": _join_names(patrol_names)
        if patrol_semantics_seen
        else (_join_names([name for name in on_duty_names if name in patrol_team_set]) or _join_names(standby)),
        "station": _join_names([name for name in on_duty_names if name in station_set]),
        "office": _join_names([name for name in on_duty_names if name in office_set]),
        "big_drivers": _join_names(big_drivers),
        "small_drivers": _join_names(small_drivers),
        "standby": _join_names(standby),
        "afternoon_rest": _join_names(afternoon_rest),
        "resting": _join_names(resting),
        "afternoon_return": _join_names(afternoon_return),
    }
    send_at = datetime.combine(target, _parse_hhmm(_coerce_hhmm(str(config["reminder_time"]), "07:50")), tzinfo=TZ)
    return {
        "enabled": config["enabled"],
        "send_at": send_at.isoformat(),
        "content": _render_simple_template(config["message_template"] or DEFAULT_DAILY_DUTY_TEMPLATE, values),
        "details": values,
        "notification_room_id": str(config.get("notification_room_id") or ""),
        "notification_room_name": str(config.get("notification_room_name") or ""),
        "send_content_mode": _normalize_send_content_mode(str(config.get("send_content_mode") or "both"), "both"),
    }


def _patrol_team_summary_names(rows: list[dict[str, str]], patrol_team_set: set[str]) -> tuple[list[str], bool]:
    patrol_names: list[str] = []
    semantics_seen = False
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        code = str(row.get("code") or "").strip()
        if patrol_team_set:
            if name not in patrol_team_set:
                continue
            if code == "巡":
                patrol_names.append(name)
                semantics_seen = True
            elif code in {"备", "早", "中", "晚", "夜"}:
                semantics_seen = True
            continue
        if code == "巡":
            patrol_names.append(name)
            semantics_seen = True
        elif code in {"备", "早", "中", "晚", "夜"}:
            semantics_seen = True
    return patrol_names, semantics_seen


def _diff_roster_grids(existing_grid: list[dict[str, Any]], incoming_grid: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    max_rows = max(len(existing_grid), len(incoming_grid))
    for row_index in range(max_rows):
        existing_row = existing_grid[row_index] if row_index < len(existing_grid) else {}
        incoming_row = incoming_grid[row_index] if row_index < len(incoming_grid) else {}
        existing_name = str(existing_row.get("name", ""))
        incoming_name = str(incoming_row.get("name", ""))
        display_name = incoming_name or existing_name or f"第{row_index + 1}行"
        if existing_name != incoming_name:
            diffs.append({"row": row_index, "name": display_name, "day": "姓名", "before": existing_name, "after": incoming_name})

        existing_days = dict(existing_row.get("days", {}))
        incoming_days = dict(incoming_row.get("days", {}))
        days = sorted(set(existing_days) | set(incoming_days), key=lambda value: int(value) if str(value).isdigit() else 999)
        for day in days:
            before = str(existing_days.get(day, ""))
            after = str(incoming_days.get(day, ""))
            if before != after:
                diffs.append({"row": row_index, "name": display_name, "day": str(day), "before": before, "after": after})
    return diffs


def _merge_rechecked_grid(
    current_grid: list[dict[str, Any]],
    parsed_grid: list[dict[str, Any]],
    baseline_grid: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    corrected_grid: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    baseline_grid = list(baseline_grid or [])
    has_baseline = bool(baseline_grid)
    row_count = max(len(current_grid), len(parsed_grid))
    for row_index in range(row_count):
        current_row = current_grid[row_index] if row_index < len(current_grid) else {}
        parsed_row = parsed_grid[row_index] if row_index < len(parsed_grid) else {}
        baseline_row = baseline_grid[row_index] if row_index < len(baseline_grid) else {}
        if not parsed_row:
            corrected_grid.append(
                {
                    **current_row,
                    "name": str(current_row.get("name") or ""),
                    "days": dict(current_row.get("days", {})),
                    "boxes": dict(current_row.get("boxes", {})),
                }
            )
            continue
        parsed_days = dict(parsed_row.get("days", {}))
        parsed_boxes = dict(parsed_row.get("boxes", {}))
        current_days = dict(current_row.get("days", {}))
        baseline_days = dict(baseline_row.get("days", {}))
        merged_days = dict(current_days)
        for day, parsed_value in parsed_days.items():
            current_value = str(current_days.get(day, ""))
            baseline_value = str(baseline_days.get(day, ""))
            manual_edit = has_baseline and current_value != baseline_value
            parsed_text = str(parsed_value)
            if manual_edit and current_value != parsed_text:
                issues.append(
                    {
                        "row": row_index,
                        "day": day,
                        "before": current_value,
                        "after": parsed_text,
                        "kind": "manual_conflict",
                        "baseline": baseline_value,
                        "box": parsed_boxes.get(day),
                    }
                )
            elif current_value != parsed_text:
                issues.append(
                    {
                        "row": row_index,
                        "day": day,
                        "before": current_value,
                        "after": parsed_text,
                        "kind": "ocr_mismatch",
                        "box": parsed_boxes.get(day),
                    }
                )
            merged_days[day] = current_value if manual_edit else parsed_text
        corrected_grid.append(
            {
                **parsed_row,
                "name": str(current_row.get("name") or parsed_row.get("name") or ""),
                "days": merged_days,
                "boxes": parsed_boxes,
            }
        )
    return {"grid": corrected_grid, "issues": issues}


def _plan_custom_reminder_events(repo: DutyRepository, assignments: list[ShiftAssignment], target: date) -> list[ReminderEvent]:
    events: list[ReminderEvent] = []
    for reminder in repo.list_custom_reminders(enabled_only=True):
        name = str(reminder.get("name") or "").strip()
        shift_code = str(reminder.get("shift_code") or "").strip()
        reminder_time = _coerce_hhmm(str(reminder.get("reminder_time") or ""), "07:50")
        if not name or not shift_code:
            continue
        try:
            shift = Shift(shift_code)
        except ValueError:
            continue
        message_template = _custom_reminder_message_for_name(str(reminder.get("message") or ""), name)
        if message_template is None:
            continue
        for assignment in assignments:
            if assignment.work_date != target or assignment.person_name != name or assignment.shift is not shift:
                continue
            values = {
                "name": assignment.person_name,
                "date": f"{assignment.work_date:%Y-%m-%d}",
                "time_range": assignment.time_range_text,
                "shift_label": assignment.shift.label,
                "reminder_time": reminder_time,
            }
            content = _render_simple_template(message_template, values)
            events.append(
                ReminderEvent(
                    kind="custom",
                    person_name=name,
                    send_at=datetime.combine(target, _parse_hhmm(reminder_time), tzinfo=TZ),
                    content=content,
                    mention_mobile=str(reminder.get("mention_mobile") or "").strip(),
                    key_suffix=str(reminder.get("id") or ""),
                    target_room_id=str(reminder.get("notification_room_id") or "").strip(),
                    target_room_name=str(reminder.get("notification_room_name") or "").strip(),
                    send_content_mode=_normalize_send_content_mode(str(reminder.get("send_content_mode") or "both"), "both"),
                )
            )
    return events


def _plan_vacation_reminder_events(repo: DutyRepository, target: date, *, include_wecom_unbound: bool = False) -> list[ReminderEvent]:
    config = repo.get_vacation_reminder_config()
    if not bool(config.get("enabled")):
        return []
    names = _wechat_query_all_person_names_for_date(repo, target)
    if not include_wecom_unbound and bool(_notification_config_with_env_defaults(repo.get_notification_config()).get("wecom_app_enabled")):
        bound_lookup = _wecom_app_userid_lookup(repo)
        names = [name for name in names if bound_lookup.get(name)]
    tomorrow = target + timedelta(days=1)
    events: list[ReminderEvent] = []
    mode = _normalize_send_content_mode(str(config.get("send_content_mode") or "both"), "both")
    for name in names:
        today_rest = _is_rest_code(_roster_code_for_person(repo, name, target))
        tomorrow_rest = _is_rest_code(_roster_code_for_person(repo, name, tomorrow))
        if not today_rest and tomorrow_rest:
            rest_end = _rest_end_date(repo, name, tomorrow)
            values = {
                "name": name,
                "date": target.isoformat(),
                "rest_start_date": tomorrow.isoformat(),
                "rest_end_date": rest_end.isoformat(),
            }
            events.append(
                ReminderEvent(
                    kind="vacation_start",
                    person_name=name,
                    send_at=datetime.combine(target, _parse_hhmm(_coerce_hhmm(str(config.get("start_reminder_time") or ""), "07:50")), tzinfo=TZ),
                    content=_render_simple_template(_choose_template(config.get("start_message_templates"), str(config.get("start_message_template") or DEFAULT_VACATION_START_TEMPLATE)), values),
                    key_suffix=_rest_range_key(tomorrow, rest_end),
                    send_content_mode=mode,
                )
            )
        if today_rest and not tomorrow_rest:
            rest_start = _rest_start_date(repo, name, target)
            values = {
                "name": name,
                "date": target.isoformat(),
                "rest_start_date": rest_start.isoformat(),
                "rest_end_date": target.isoformat(),
            }
            events.append(
                ReminderEvent(
                    kind="vacation_end",
                    person_name=name,
                    send_at=datetime.combine(target, _parse_hhmm(_coerce_hhmm(str(config.get("end_reminder_time") or ""), "07:50")), tzinfo=TZ),
                    content=_render_simple_template(_choose_template(config.get("end_message_templates"), str(config.get("end_message_template") or DEFAULT_VACATION_END_TEMPLATE)), values),
                    key_suffix=_rest_range_key(rest_start, target),
                    send_content_mode=mode,
                )
            )
    return events


def _choose_template(values: Any, fallback: str) -> str:
    choices = [str(value or "").strip() for value in (values if isinstance(values, list) else []) if str(value or "").strip()]
    if not choices:
        return str(fallback or "").strip()
    return secrets.choice(choices)


def _plan_all_events(repo: DutyRepository, target: date, *, include_wecom_unbound: bool = False):
    assignments: list[ShiftAssignment] = []
    for roster_month in repo.list_roster_months():
        assignments.extend(_assignments_from_grid(roster_month))

    events = []
    message_template = str(repo.get_notification_config().get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
    for person in repo.list_monitored_people(enabled_only=True):
        person_events = plan_reminders_for_day(
            target_date=target,
            assignments=assignments,
            monitored_name=person["name"],
            mention_text="",
            settings=ReminderSettings(
                daily_time=_coerce_hhmm(person["daily_time"], "07:50"),
                before_shift_minutes=person["before_shift_minutes"],
                message_template=message_template,
            ),
            tz=TZ,
        )
        room_id = str(person.get("notification_room_id") or "").strip()
        room_name = str(person.get("notification_room_name") or "").strip()
        mode = _normalize_send_content_mode(str(person.get("send_content_mode") or "both"), "both")
        events.extend(replace(event, target_room_id=room_id, target_room_name=room_name, send_content_mode=mode) for event in person_events)
        if person.get("rest_reminder_enabled"):
            rest_status = _rest_status_for_date(repo, person["name"], target)
            if rest_status:
                content = _render_simple_template(
                    person.get("rest_message_template") or DEFAULT_REST_MESSAGE_TEMPLATE,
                    {"name": person["name"], **rest_status},
                )
                events.append(
                    ReminderEvent(
                        kind="rest",
                        person_name=person["name"],
                        send_at=datetime.combine(target, _parse_hhmm(_coerce_hhmm(person.get("rest_reminder_time") or "08:30", "08:30")), tzinfo=TZ),
                        content=content,
                        key_suffix=_rest_range_key_from_status(rest_status),
                        target_room_id=str(person.get("notification_room_id") or "").strip(),
                        target_room_name=str(person.get("notification_room_name") or "").strip(),
                        send_content_mode=mode,
                    )
                )
    daily_duty = _build_daily_duty_preview(repo, target)
    if daily_duty["enabled"]:
        events.append(
            ReminderEvent(
                kind="daily_duty",
                person_name="今日在岗人员",
                send_at=datetime.fromisoformat(daily_duty["send_at"]),
                content=daily_duty["content"],
                target_room_id=str(daily_duty.get("notification_room_id") or "").strip(),
                target_room_name=str(daily_duty.get("notification_room_name") or "").strip(),
                send_content_mode=_normalize_send_content_mode(str(daily_duty.get("send_content_mode") or "both"), "both"),
            )
        )
    events.extend(_plan_custom_reminder_events(repo, assignments, target))
    events.extend(_plan_vacation_reminder_events(repo, target, include_wecom_unbound=include_wecom_unbound))
    if not include_wecom_unbound and bool(_notification_config_with_env_defaults(repo.get_notification_config()).get("wecom_app_enabled")):
        bound_lookup = _wecom_app_userid_lookup(repo)
        events = [event for event in events if not _wecom_app_unbound_person_target(bound_lookup, event)]
    return sorted(events, key=lambda event: event.send_at)


def _build_system_status(repo: DutyRepository, scheduler_enabled: bool, cjk_font_ready: bool, upload_dir: Path | None = None) -> dict[str, Any]:
    now = datetime.now(TZ)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=TZ).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")
    records_today = _public_send_records(repo, repo.list_send_records_since(today_start))
    failed_records = [record for record in records_today if record["status"] != "success"]
    patrol_config = repo.get_patrol_warning_config()
    patrol_state = repo.get_patrol_warning_state()
    notification_config = _public_notification_config(repo.get_notification_config())
    checks = _build_system_checks(repo, scheduler_enabled=scheduler_enabled, cjk_font_ready=cjk_font_ready, notification_config=notification_config)
    severity_order = {"error": 3, "warning": 2, "ok": 1}
    overall = max(checks, key=lambda item: severity_order.get(str(item.get("status") or ""), 0))["status"] if checks else "ok"
    return {
        "now_beijing": now.isoformat(),
        "timezone": str(TZ),
        "overall_status": overall,
        "checks": checks,
        "scheduler_enabled": scheduler_enabled,
        "webhook_configured": bool(notification_config.get("webhook_configured")),
        "notification_configured": bool(notification_config.get("notification_configured")),
        "notification_sender_type": str(notification_config.get("effective_sender_type") or notification_config.get("sender_type") or "wecom_webhook"),
        "wechat_bridge_enabled": False,
        "cjk_font_ready": cjk_font_ready,
        "roster_month_count": repo.count_roster_months(),
        "monitored_people_count": repo.count_monitored_people(),
        "today_success_count": len([record for record in records_today if record["status"] == "success"]),
        "today_failed_count": len(failed_records),
        "last_error": failed_records[0]["error"] if failed_records else "",
        "next_events": _next_events(repo, now),
        "backup_count": len(_list_database_backups(repo)),
        "upload_storage": _upload_storage_status(upload_dir) if upload_dir is not None else {},
        "patrol_warning_monitor": {
            "enabled": bool(patrol_config.get("enabled")),
            "route_code": str(patrol_config.get("route_code") or ""),
            "last_checked_at": str(patrol_state.get("last_checked_at") or ""),
            "next_check_at": str(patrol_state.get("next_check_at") or ""),
            "backoff_until": str(patrol_state.get("backoff_until") or ""),
            "failure_count": int(patrol_state.get("failure_count") or 0),
            "last_error": _sanitize_wechat_ids_for_display(repo, str(patrol_state.get("last_error") or "")),
            "token_configured": bool(str(patrol_state.get("token") or "").strip()),
            "token_expires_at": str(patrol_state.get("token_expires_at") or ""),
            "last_warning_key": str(patrol_state.get("warning_key") or ""),
        },
    }


def _build_system_checks(
    repo: DutyRepository,
    *,
    scheduler_enabled: bool,
    cjk_font_ready: bool,
    notification_config: dict[str, Any],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(key: str, title: str, status: str, message: str, action: str = "") -> None:
        checks.append({"key": key, "title": title, "status": status, "message": message, "action": action})

    db_status = "ok"
    db_message = "数据库可读写"
    try:
        with repo._connect() as conn:
            conn.execute("CREATE TEMP TABLE IF NOT EXISTS duty_reminder_health_check (id INTEGER)")
            conn.execute("INSERT INTO duty_reminder_health_check (id) VALUES (1)")
            conn.execute("DELETE FROM duty_reminder_health_check")
            journal = conn.execute("PRAGMA journal_mode").fetchone()
            busy = conn.execute("PRAGMA busy_timeout").fetchone()
            db_message = f"可读写，journal={journal[0] if journal else '-'}，busy_timeout={busy[0] if busy else '-'}ms"
    except Exception as exc:
        db_status = "error"
        db_message = f"数据库读写异常：{exc}"
    add("database", "数据库", db_status, db_message, "检查 data 目录挂载和磁盘权限")

    add(
        "scheduler",
        "后台调度",
        "ok" if scheduler_enabled else "warning",
        "已启动" if scheduler_enabled else "未启动，定时提醒不会自动发送",
        "检查服务启动日志和 APScheduler 依赖",
    )
    add(
        "notification",
        "通知通道",
        "ok" if notification_config.get("notification_configured") else "error",
        "已配置" if notification_config.get("notification_configured") else "未配置通知通道，提醒只会生成不会发送",
        "到配置中心设置企业微信自建应用或群机器人",
    )
    add(
        "font",
        "中文字体",
        "ok" if cjk_font_ready else "warning",
        "图片中文字体可用" if cjk_font_ready else "缺少中文字体，生成图片可能乱码或方块",
        "Docker 镜像内安装中文字体",
    )

    rosters = repo.list_roster_months()
    add(
        "roster",
        "排班数据",
        "ok" if rosters else "warning",
        f"已导入 {len(rosters)} 个月排班" if rosters else "尚未导入排班，监控/休息/今日在岗无法按真实数据生成",
        "先导入并确认最新排班",
    )

    monitored = repo.list_monitored_people(enabled_only=True)
    add(
        "monitor_people",
        "监控班提醒",
        "ok" if monitored else "warning",
        f"已启用 {len(monitored)} 人" if monitored else "未启用任何监控班提醒人员",
        "到监控班提醒配置人员",
    )

    if bool(notification_config.get("wecom_app_enabled")):
        bound = _wecom_app_userid_lookup(repo)
        monitored_missing = [str(person.get("name") or "").strip() for person in monitored if not bound.get(str(person.get("name") or "").strip())]
        custom_names = sorted({str(item.get("name") or "").strip() for item in repo.list_custom_reminders(enabled_only=True) if str(item.get("name") or "").strip()})
        custom_missing = [name for name in custom_names if not bound.get(name)]
        public_targets = list(notification_config.get("wecom_app_target_names") or [])
        public_missing = [name for name in public_targets if not bound.get(str(name or "").strip())]
        missing = list(dict.fromkeys([*monitored_missing, *custom_missing, *public_missing]))
        add(
            "wecom_binding",
            "企业微信绑定",
            "ok" if not missing else "warning",
            "所有已配置人员均已绑定" if not missing else f"未绑定 {len(missing)} 人：{'、'.join(missing[:8])}{'等' if len(missing) > 8 else ''}",
            "让对应人员在企业微信自建应用发送“绑定姓名”",
        )

    patrol_config = repo.get_patrol_warning_config()
    patrol_state = repo.get_patrol_warning_state()
    patrol_error = str(patrol_state.get("last_error") or "").strip()
    add(
        "patrol_warning",
        "公路巡查预警",
        "warning" if patrol_config.get("enabled") and patrol_error else ("ok" if patrol_config.get("enabled") else "warning"),
        (f"已启用，最近错误：{_sanitize_wechat_ids_for_display(repo, patrol_error)}" if patrol_error else "已启用")
        if patrol_config.get("enabled")
        else "未启用",
        "检查账号、验证码、代理和轮询状态",
    )
    return checks


def _next_events(repo: DutyRepository, now: datetime, *, days: int = 7, limit: int = 5) -> list[dict[str, str]]:
    events = []
    for offset in range(days):
        target = now.date() + timedelta(days=offset)
        for event in _plan_all_events(repo, target):
            if event.send_at > now:
                events.append(event)
    events = sorted(events, key=lambda event: event.send_at)[:limit]
    return [
        {
            "kind": event.kind,
            "person_name": event.person_name,
            "send_at": event.send_at.isoformat(),
            "content": event.content,
        }
        for event in events
    ]


def _state_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(TZ) if parsed.tzinfo else parsed.replace(tzinfo=TZ)


async def _check_patrol_warning_monitor(repo: DutyRepository) -> None:
    config = repo.get_patrol_warning_config()
    if not config.get("enabled"):
        return
    now = datetime.now(TZ)
    state = repo.get_patrol_warning_state()

    backoff_until = _state_datetime(str(state.get("backoff_until") or ""))
    if backoff_until and now < backoff_until:
        return
    next_check_at = _state_datetime(str(state.get("next_check_at") or ""))
    if next_check_at and now < next_check_at:
        return
    if not next_check_at:
        last_checked_at = _state_datetime(str(state.get("last_checked_at") or ""))
        if last_checked_at and now - last_checked_at < timedelta(minutes=int(config.get("poll_interval_minutes") or 10)):
            return

    webhook_client = _wecom_webhook_client_from_repo(repo)

    route_code = str(config.get("route_code") or "").strip()
    try:
        result = await fetch_latest_warning_result(
            config,
            TZ,
            token=str(state.get("token") or ""),
            token_expires_at=str(state.get("token_expires_at") or ""),
            now=now,
        )
    except PatrolWarningError as exc:
        failure_count = int(state.get("failure_count") or 0) + 1
        retry_at = failure_backoff_until(now, failure_count)
        save_kwargs: dict[str, Any] = {
            "last_checked_at": now.isoformat(),
            "next_check_at": retry_at.isoformat(),
            "failure_count": failure_count,
            "backoff_until": retry_at.isoformat(),
            "last_error": str(exc),
        }
        if exc.is_auth_error:
            save_kwargs["token"] = ""
            save_kwargs["token_expires_at"] = ""
        repo.save_patrol_warning_state(**save_kwargs)
        LOGGER.warning("公路巡查预警监测失败：%s", exc)
        repo.save_send_record(
            kind="patrol_warning_check",
            target=route_code or "公路巡查预警",
            status="failed",
            error=str(exc),
        )
        return
    latest = result.warning
    stats = result.stats
    repo.save_patrol_warning_state(
        last_checked_at=now.isoformat(),
        token=result.token,
        token_expires_at=result.token_expires_at,
        next_check_at=next_poll_time(now, int(config.get("poll_interval_minutes") or 10)).isoformat(),
        failure_count=0,
        backoff_until="",
        last_error="",
    )
    if latest is None:
        LOGGER.info("公路巡查预警未匹配到路线：%s rows=%s", route_code, stats.get("total_rows"))
        return

    state = repo.get_patrol_warning_state()
    is_new_warning = latest.key != str(state.get("warning_key") or "")
    latest_data = latest.as_dict()
    if is_new_warning:
        repo.save_patrol_warning_state(
            warning_key=latest.key,
            warning=latest_data,
            last_start_sent_key="",
            last_end_reminder_slot="",
        )
        state = repo.get_patrol_warning_state()
    elif latest_data != dict(state.get("warning") or {}):
        repo.save_patrol_warning_state(warning=latest_data)
        state = repo.get_patrol_warning_state()

    if str(state.get("last_start_sent_key") or "") != latest.key:
        if not _patrol_warning_should_send_start(latest, config, now=now):
            repo.save_patrol_warning_state(last_start_sent_key=latest.key)
            LOGGER.info(
                "跳过历史公路巡查预警开始提醒：key=%s start=%s create=%s end=%s now=%s",
                latest.key,
                latest.start_time,
                latest.create_time,
                latest.end_time,
                now,
            )
        else:
            if webhook_client is None:
                LOGGER.warning("已检测到公路巡查预警但通知通道不可用：key=%s", latest.key)
                return
            content = _build_patrol_warning_content(latest, config, now=now, mode="start")
            try:
                await _send_patrol_warning_message(
                    repo,
                    webhook_client,
                    kind="patrol_warning_start",
                    target=route_code or latest.route_code or "公路巡查预警",
                    scheduled_at=now.isoformat(),
                    content=content,
                    warning=latest,
                    window_hours=int(config.get("end_reminder_window_hours") or 48),
                    now=now,
                    image_mode="start",
                )
                repo.save_patrol_warning_state(last_start_sent_key=latest.key)
            except Exception as exc:
                LOGGER.exception("公路巡查预警开始提醒发送失败：%s", exc)
                return

    slot = _patrol_warning_due_end_slot(latest, config, now=now)
    if slot is None:
        return
    if webhook_client is None:
        LOGGER.warning("已检测到公路巡查预警结束但通知通道不可用：key=%s slot=%s", latest.key, slot)
        return
    slot_text = slot.isoformat()
    if slot_text == str(repo.get_patrol_warning_state().get("last_end_reminder_slot") or ""):
        return
    content = _build_patrol_warning_content(latest, config, now=now, mode="end")
    try:
        await _send_patrol_warning_message(
            repo,
            webhook_client,
            kind="patrol_warning_end",
            target=route_code or latest.route_code or "公路巡查预警",
            scheduled_at=slot_text,
            content=content,
            warning=latest,
            window_hours=int(config.get("end_reminder_window_hours") or 48),
            now=now,
            image_mode="end",
        )
        repo.save_patrol_warning_state(last_end_reminder_slot=slot_text)
    except Exception as exc:
        LOGGER.exception("公路巡查预警结束后提醒发送失败：%s", exc)


async def _send_patrol_warning_message(
    repo: DutyRepository,
    webhook_client: Any,
    *,
    kind: str,
    target: str,
    scheduled_at: str,
    content: str,
    warning: Any | None = None,
    window_hours: int = 48,
    now: datetime | None = None,
    image_mode: str = "auto",
) -> None:
    try:
        patrol_config = repo.get_patrol_warning_config()
        send_content_mode = _normalize_patrol_send_content_mode(str(patrol_config.get("send_content_mode") or "both"))
        event = ReminderEvent(kind=kind, person_name="", send_at=now or datetime.now(TZ), content=content)
        target_ids = _notification_target_ids_for_event(repo, webhook_client, event) if _is_wecom_app_notify_client(webhook_client) else _patrol_warning_target_room_ids(repo)
        content = _notification_content_for_event(repo, webhook_client, event)
        if send_content_mode in {"both", "image"} and warning is not None:
            await _send_graphic_or_text_image(
                webhook_client,
                title=_event_news_title(event, content),
                text=content,
                image_bytes=render_patrol_warning_image(
                    warning,
                    now=now or datetime.now(TZ),
                    window_hours=window_hours,
                    mode=image_mode,
                ),
                mentions=_notification_true_mentions_for_event(repo, webhook_client, event),
                target_ids=target_ids,
                mode=send_content_mode,
            )
        elif send_content_mode in {"both", "text"}:
            await _notify_send_text(webhook_client, content, _notification_true_mentions_for_event(repo, webhook_client, event), target_ids)
        repo.save_send_record(
            kind=kind,
            target=target,
            scheduled_at=scheduled_at,
            status="success",
            content=content,
            notification_room_id=str(patrol_config.get("notification_room_id") or ""),
            notification_room_name=str(patrol_config.get("notification_room_name") or ""),
        )
    except Exception as exc:
        repo.save_send_record(
            kind=kind,
            target=target,
            scheduled_at=scheduled_at,
            status="failed",
            content=content,
            error=str(exc),
            notification_room_id=str(patrol_config.get("notification_room_id") or ""),
            notification_room_name=str(patrol_config.get("notification_room_name") or ""),
        )
        raise


def _build_patrol_warning_content(warning: Any, config: dict[str, Any], *, now: datetime, mode: str) -> str:
    if mode == "end":
        return build_end_reminder_message(
            warning,
            now=now,
            window_hours=int(config.get("end_reminder_window_hours") or 48),
            mention_all=False,
            template=str(config.get("end_message_template") or DEFAULT_PATROL_WARNING_END_TEMPLATE),
        )
    return build_start_message(
        warning,
        mention_all=False,
        template=str(config.get("start_message_template") or DEFAULT_PATROL_WARNING_START_TEMPLATE),
    )


def _normalize_patrol_send_content_mode(value: str) -> str:
    normalized = str(value or "both").strip().lower()
    return normalized if normalized in {"both", "text", "image"} else "both"


def _normalize_send_content_mode(value: str, default: str = "both") -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in {"both", "text", "image"} else default


def _patrol_send_content_mode(config: dict[str, Any]) -> str:
    return _normalize_patrol_send_content_mode(str(config.get("send_content_mode") or "both"))


def _notification_mention_config(repo: DutyRepository) -> dict[str, str]:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    return {
        "mode": _normalize_notification_mention_mode(str(config.get("mention_mode") or "person")),
        "targets": str(config.get("mention_targets") or "").strip(),
    }


def _split_mention_targets(value: Any) -> list[str]:
    targets: list[str] = []
    for part in re.split(r"[\s,，;；、]+", str(value or "")):
        text = part.strip()
        if text and text not in targets:
            targets.append(text)
    return targets


def _base_reminder_kind(kind: str) -> str:
    value = str(kind or "").strip()
    while value.endswith("_resend"):
        value = value[: -len("_resend")]
    return value


def _person_target_for_event(event: ReminderEvent) -> str:
    if _base_reminder_kind(event.kind) in {"daily", "before_shift", "rest", "custom", "monitor_test", "custom_test", "vacation_start", "vacation_end", "vacation_test"}:
        name = str(event.person_name or "").strip()
        return name if name and name != "测试消息" else ""
    return ""


def _wecom_app_person_target_for_event(event: ReminderEvent) -> str:
    person_target = _person_target_for_event(event)
    if person_target:
        return person_target
    if _base_reminder_kind(event.kind) == "notification_test":
        name = str(event.person_name or "").strip()
        return name if name and name != "测试消息" else ""
    return ""


def _notification_target_names(repo: DutyRepository, event: ReminderEvent | str = "") -> list[str]:
    if isinstance(event, ReminderEvent):
        person_name = str(event.person_name or "").strip()
        person_target = _person_target_for_event(event)
    else:
        person_name = str(event or "").strip()
        person_target = person_name if person_name and person_name != "测试消息" else ""
    mention = _notification_mention_config(repo)
    mode = mention["mode"]
    if mode == "none":
        return []
    if mode == "all":
        return ["所有人"]
    if mode == "person":
        return [person_target or person_name] if (person_target or person_name) and person_name != "测试消息" else []
    if person_target:
        return [person_target]
    names = []
    for target in _split_mention_targets(mention["targets"]):
        if target in {"@all", "@所有人", "所有人"}:
            names.append("所有人")
        else:
            names.append(target.lstrip("@"))
    return list(dict.fromkeys([name for name in names if name]))



def _notification_sender_label(sender: str) -> str:
    value = str(sender or "wecom_webhook").strip()
    if value == "wecom_app":
        return "企业微信自建应用"
    return "企业微信群机器人"


def _shift_from_preview_code(value: str) -> Shift:
    normalized = str(value or "middle").strip().lower()
    return {
        "early": Shift.EARLY,
        "middle": Shift.MIDDLE,
        "night": Shift.NIGHT,
        "早": Shift.EARLY,
        "中": Shift.MIDDLE,
        "晚": Shift.NIGHT,
        "夜": Shift.NIGHT,
    }.get(normalized, Shift.MIDDLE)


def _preview_work_date(now: datetime, shift: Shift) -> date:
    if shift is Shift.EARLY and now.hour >= 12:
        return now.date() + timedelta(days=1)
    return now.date()


def _build_reminder_image_preview_event(repo: DutyRepository, request: ReminderImagePreviewRequest) -> ReminderEvent:
    name = str(request.name or "").strip() or "商邱宏"
    now = datetime.now(TZ)
    shift = _shift_from_preview_code(request.shift_code)
    work_date = _preview_work_date(now, shift)
    send_time = _validate_hhmm(str(request.reminder_time or request.daily_time or "07:50"))
    send_hour, send_minute = [int(part) for part in send_time.split(":", 1)]
    kind = str(request.preview_type or "monitor").strip().lower()
    if kind.startswith("custom"):
        message = _custom_reminder_message_for_name(str(request.message or "").strip() or "需要检查隧道灯", name) or "需要检查隧道灯"
        content = _render_simple_template(
            message,
            {
                "name": name,
                "date": work_date.isoformat(),
                "time_range": f"{shift.start_time:%H:%M}至{shift.end_time:%H:%M}",
                "shift_label": "晚班" if shift is Shift.NIGHT else shift.label,
                "reminder_time": send_time,
            },
        )
        return ReminderEvent(
            kind="custom_test",
            person_name=name,
            send_at=datetime.combine(now.date(), datetime.min.time(), tzinfo=TZ).replace(hour=send_hour, minute=send_minute),
            content=content,
            send_content_mode=_normalize_send_content_mode(request.send_content_mode, "both"),
        )
    if kind.startswith("vacation"):
        rest_start = (now.date() + timedelta(days=1)).isoformat()
        rest_end = (now.date() + timedelta(days=5)).isoformat()
        content = _render_simple_template(
            str(request.message or "").strip() or DEFAULT_VACATION_START_TEMPLATE,
            {
                "name": name,
                "date": now.date().isoformat(),
                "rest_start_date": rest_start,
                "rest_end_date": rest_end,
            },
        )
        return ReminderEvent(
            kind="vacation_test",
            person_name=name,
            send_at=datetime.combine(now.date(), datetime.min.time(), tzinfo=TZ).replace(hour=send_hour, minute=send_minute),
            content=content,
            key_suffix=f"{rest_start}_{rest_end}",
            send_content_mode=_normalize_send_content_mode(request.send_content_mode, "both"),
        )
    if kind.startswith("rest"):
        rest_status = _rest_status_for_date(repo, name, now.date()) or {
            "date": now.date().isoformat(),
            "rest_start_date": now.date().isoformat(),
            "rest_end_date": now.date().isoformat(),
            "rest_status": "今日下午休息",
        }
        content = _render_simple_template(
            str(request.message or "").strip() or DEFAULT_REST_MESSAGE_TEMPLATE,
            {"name": name, **rest_status},
        )
        return ReminderEvent(
            kind="rest_test",
            person_name=name,
            send_at=datetime.combine(now.date(), datetime.min.time(), tzinfo=TZ).replace(hour=send_hour, minute=send_minute),
            content=content,
            key_suffix=_rest_range_key_from_status(rest_status),
            send_content_mode="text",
        )
    template = str(request.message_template or "").strip() or str(repo.get_notification_config().get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
    content = _render_message_template(
        template,
        {
            "name": name,
            "date": work_date.isoformat(),
            "time_range": f"{shift.start_time:%H:%M}至{shift.end_time:%H:%M}",
            "shift_label": "晚班" if shift is Shift.NIGHT else shift.label,
        },
    )
    return ReminderEvent(
        kind="monitor_test",
        person_name=name,
        send_at=datetime.combine(now.date(), datetime.min.time(), tzinfo=TZ).replace(hour=send_hour, minute=send_minute),
        content=content,
        send_content_mode=_normalize_send_content_mode(request.send_content_mode, "both"),
    )

def _notification_true_mentions_for_event(repo: DutyRepository, client: Any, event: ReminderEvent) -> list[str]:
    if _is_personal_wechat_notify_client(client) or _is_wecom_app_notify_client(client):
        return []
    mention = _notification_mention_config(repo)
    mode = mention["mode"]
    if mode == "none":
        return []
    if mode == "all":
        return ["@all"]
    mobile_lookup = _person_mobile_lookup(repo)
    if mode == "person":
        mobile = _mobile_for_event(event, mobile_lookup)
        return [mobile] if mobile else []
    if _person_target_for_event(event):
        mobile = _mobile_for_event(event, mobile_lookup)
        return [mobile] if mobile else []
    mentions = []
    for target in _split_mention_targets(mention["targets"]):
        if target in {"@all", "@所有人", "所有人"}:
            mentions.append("@all")
        elif re.fullmatch(r"1\d{10}", target):
            mentions.append(target)
        else:
            mobile = mobile_lookup.get(target.lstrip("@"), "")
            if mobile:
                mentions.append(mobile)
    return list(dict.fromkeys([item for item in mentions if item]))


def _notification_content_for_event(repo: DutyRepository, client: Any, event: ReminderEvent) -> str:
    content = str(event.content or "")
    if not _is_personal_wechat_notify_client(client):
        return content
    names = _notification_target_names(repo, event)
    prefix = "\n".join(f"@{name}" for name in names if name)
    if not prefix or content.lstrip().startswith("@"):
        return content
    return f"{prefix}\n{content}"


def _should_send_shift_reminder_image(event: ReminderEvent) -> bool:
    return _is_shift_reminder_kind(event.kind)


def _event_send_content_mode(event: ReminderEvent, default: str = "both") -> str:
    return _normalize_send_content_mode(str(getattr(event, "send_content_mode", "") or ""), default)


def _event_can_send_image(event: ReminderEvent) -> bool:
    return _is_shift_reminder_kind(event.kind) or event.kind in {
        "daily_duty",
        "daily_duty_test",
        "vacation_start",
        "vacation_end",
        "vacation_test",
        "custom",
        "custom_test",
        "rest",
    }


def _shift_reminder_intro_content(event: ReminderEvent, *, personal_wechat: bool = False) -> str:
    lines: list[str] = []
    for raw_line in str(event.content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _NEXT_REMINDER_CONTENT_RE.match(line)
        if match:
            shift_label = "晚班" if match.group("shift_label") == "夜班" else match.group("shift_label")
            today = datetime.now(TZ).date()
            try:
                reminder_date = date.fromisoformat(match.group("date"))
            except ValueError:
                reminder_date = None
            if event.kind == "monitor_test" or reminder_date == today:
                day_text = "今天"
            elif reminder_date == today + timedelta(days=1):
                day_text = "明天"
            else:
                day_text = match.group("date")
            person = match.group("name").strip() or event.person_name
            prefix = f"@{person}" if personal_wechat and person else f"{person}"
            lines.append(f"{prefix}{day_text}是你的{shift_label}")
        else:
            lines.append(line)
    return "\n".join(lines) or str(event.content or "").strip() or "监控班提醒"


def _is_shift_reminder_kind(kind: str) -> bool:
    return _base_reminder_kind(kind) in {"daily", "before_shift", "monitor_test"}


def _person_mobile_lookup(repo: DutyRepository) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for person in repo.list_monitored_people():
        mobile = str(person.get("mention_mobile") or "").strip()
        if mobile:
            lookup[str(person.get("name") or "").strip()] = mobile
    for person in repo.list_personnel():
        mobile = str(person.get("mention_mobile") or "").strip()
        if mobile:
            lookup[str(person.get("name") or "").strip()] = mobile
    return {name: mobile for name, mobile in lookup.items() if name}


def _mobile_for_event(event: ReminderEvent, mobile_lookup: dict[str, str]) -> str:
    return str(event.mention_mobile or "").strip() or mobile_lookup.get(event.person_name, "")


async def _send_test_reminder_event(
    repo: DutyRepository,
    notification_client: Any,
    event: ReminderEvent,
    record_kind: str,
) -> dict[str, Any]:
    _raise_if_wecom_app_unbound_person(repo, notification_client, event)
    mentions = _notification_true_mentions_for_event(repo, notification_client, event)
    content = _notification_content_for_event(repo, notification_client, event)
    target = event.person_name or "测试消息"
    target_ids = _notification_target_ids_for_event(repo, notification_client, event)
    sent_content = content
    try:
        mode = _event_send_content_mode(event, "both")
        if event.kind == "daily_duty_test" and hasattr(notification_client, "send_image"):
            await _send_graphic_or_text_image(
                notification_client,
                title=_event_news_title(event, content),
                text=content,
                image_bytes=render_daily_duty_image(_build_daily_duty_preview(repo, event.send_at.date())),
                mentions=mentions,
                target_ids=target_ids,
                mode=mode,
            )
        elif _event_can_send_image(event) and mode in {"both", "image"} and hasattr(notification_client, "send_image"):
            intro_event = ReminderEvent(
                kind=event.kind,
                person_name=event.person_name,
                send_at=event.send_at,
                content=_shift_reminder_intro_content(event, personal_wechat=_is_personal_wechat_notify_client(notification_client)),
            )
            sent_content = _notification_content_for_event(repo, notification_client, intro_event)
            await _send_graphic_or_text_image(
                notification_client,
                title=_event_news_title(event, sent_content),
                text=sent_content,
                image_bytes=render_shift_reminder_image(event),
                mentions=mentions,
                target_ids=target_ids,
                mode=mode,
            )
        else:
            await _notify_send_text(notification_client, content, mentions, target_ids)
        repo.save_send_record(
            kind=record_kind,
            target=target,
            status="success",
            content=content,
            notification_room_id=event.target_room_id,
            notification_room_name=event.target_room_name,
        )
    except WeComError as exc:
        repo.save_send_record(
            kind=record_kind,
            target=target,
            status="failed",
            content=content,
            error=str(exc),
            notification_room_id=event.target_room_id,
            notification_room_name=event.target_room_name,
        )
        raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, str(exc))) from exc
    except Exception as exc:
        error = f"测试发送失败：{exc}"
        repo.save_send_record(
            kind=record_kind,
            target=target,
            status="failed",
            content=content,
            error=error,
            notification_room_id=event.target_room_id,
            notification_room_name=event.target_room_name,
        )
        raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, error)) from exc
    return {"success": True, "content": sent_content, "mentions": mentions}


def _bound_wechat_sender_ids(repo: DutyRepository) -> list[str]:
    ids = []
    for person in repo.list_personnel():
        sender_id = str(person.get("wechat_group_runtime_sender_id") or "").strip()
        if sender_id and sender_id not in ids:
            ids.append(sender_id)
    return ids


def _clean_wechat_member_display_name(name: str, sender_id: str = "") -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    target_id = str(sender_id or "").strip()
    if target_id and target_id in text:
        text = text.replace(target_id, "").strip()
    text = re.sub(r"\s*[·|/\\-]\s*$", "", text).strip()
    text = re.sub(r"\s*[（(]\s*[)）]\s*$", "", text).strip()
    return "" if _looks_like_wechat_runtime_id(text) else text


def _wechat_test_record_target(repo: DutyRepository, sender_id: str, sender_name: str = "") -> str:
    name = _clean_wechat_member_display_name(sender_name, sender_id)
    if name and name != str(sender_id or "").strip():
        return name
    target_id = str(sender_id or "").strip()
    if not target_id:
        return "测试消息"
    for person in repo.list_personnel():
        if target_id == str(person.get("wechat_group_runtime_sender_id") or "").strip():
            label = _clean_wechat_member_display_name(
                str(person.get("wechat_group_member_name") or person.get("name") or "").strip(),
                target_id,
            )
            if label and label != target_id:
                return label
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    rooms = _normalize_feature_channel_rooms(config.get("lightagent_targets"))
    legacy_room_id = str(config.get("lightagent_target") or "").strip()
    if legacy_room_id:
        rooms = _normalize_feature_channel_rooms(rooms + [{"id": legacy_room_id}])
    if wechat_bridge_enabled():
        manager = get_wechat_bridge_manager()
        for room in rooms:
            for member in manager.get_room_members(room["id"], limit=500):
                member_id = str(member.get("runtime_sender_id") or member.get("sender_id") or "").strip()
                if member_id != target_id:
                    continue
                label = str(
                    member.get("display_name")
                    or member.get("sender_nickname")
                    or member.get("name")
                    or member.get("room_alias")
                    or ""
                ).strip()
                label = _clean_wechat_member_display_name(label, target_id)
                return label if label and label != target_id else "测试消息"
    return "测试消息" if target_id.startswith("@") else target_id


def _wechat_room_display_lookup(repo: DutyRepository) -> dict[str, str]:
    lookup: dict[str, str] = {}

    def add_room(room: Any) -> None:
        if not isinstance(room, dict):
            return
        name = str(room.get("name") or room.get("room_name") or room.get("wechat_group_room_name") or room.get("topic") or "").strip()
        ids = [
            room.get("id"),
            room.get("room_id"),
            room.get("stable_room_id"),
            room.get("runtime_room_id"),
            room.get("runtime_id"),
            room.get("wechat_group_room_id"),
        ]
        for value in ids:
            room_id = str(value or "").strip()
            if room_id and name and room_id != name:
                lookup[room_id] = name

    notification = _notification_config_with_env_defaults(repo.get_notification_config())
    for room in _normalize_feature_channel_rooms(notification.get("lightagent_targets")):
        add_room(room)
    legacy_notification = str(notification.get("lightagent_target") or "").strip()
    if legacy_notification:
        add_room({"id": legacy_notification})

    feature = _feature_channel_config_with_env_defaults(repo.get_feature_channel_config())
    for room in _feature_channel_config_rooms(feature):
        add_room(room)

    if wechat_bridge_enabled():
        try:
            manager = get_wechat_bridge_manager()
            for room in manager.status_snapshot().get("rooms") or []:
                add_room(room)
        except Exception:
            pass
    return lookup


def _wechat_member_display_lookup(repo: DutyRepository) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for person in repo.list_personnel():
        ids = [
            str(person.get("wechat_group_runtime_sender_id") or "").strip(),
            str(person.get("wechat_group_member_id") or "").strip(),
        ]
        for sender_id in ids:
            if not sender_id:
                continue
            label = _clean_wechat_member_display_name(
                str(person.get("wechat_group_member_name") or person.get("name") or "").strip(),
                sender_id,
            )
            if label:
                lookup[sender_id] = label
    return lookup


def _wechat_display_lookup(repo: DutyRepository) -> dict[str, str]:
    lookup = _wechat_room_display_lookup(repo)
    lookup.update(_wechat_member_display_lookup(repo))
    return lookup


def _sanitize_wechat_ids_for_display(repo: DutyRepository, text: str) -> str:
    value = str(text or "")
    if not value:
        return ""
    lookup = _wechat_display_lookup(repo)
    for raw_id, label in sorted(lookup.items(), key=lambda item: len(item[0]), reverse=True):
        if raw_id and label:
            value = value.replace(raw_id, label)
    value = re.sub(r"wgr_[A-Za-z0-9_]+", "微信群", value)
    value = re.sub(r"(?<!\\w)@[A-Za-z0-9_-]{16,}", "微信成员", value)
    value = re.sub(r"room@@[A-Za-z0-9_-]+", "微信群", value)
    value = re.sub(r"@@[A-Za-z0-9_-]+", "微信群", value)
    return value


def _wechat_room_record_target(repo: DutyRepository, room_id: str) -> str:
    target_id = str(room_id or "").strip()
    if not target_id:
        return "微信群"
    return _wechat_room_display_lookup(repo).get(target_id) or "微信群"


def _public_send_records(repo: DutyRepository, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_public_send_record(repo, record) for record in records]


def _public_send_record(repo: DutyRepository, record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    target = str(item.get("target") or "").strip()
    if _looks_like_wechat_room_id(target):
        item["target"] = _wechat_room_record_target(repo, target)
    elif _looks_like_wechat_runtime_id(target):
        item["target"] = _wechat_test_record_target(repo, target)
    room_id = str(item.get("notification_room_id") or "").strip()
    if room_id:
        saved_room_name = str(item.get("notification_room_name") or "").strip()
        if re.fullmatch(r"\d+", room_id) and saved_room_name == room_id:
            saved_room_name = ""
        item["notification_room_name"] = (
            _wechat_room_display_lookup(repo).get(room_id)
            or saved_room_name
            or (f"已失效发送目标（{room_id}）" if re.fullmatch(r"\d+", room_id) else room_id)
        )
    for key in ("content", "error"):
        if item.get(key):
            item[key] = _sanitize_wechat_ids_for_display(repo, str(item.get(key) or ""))
    return item


def _lightagent_room_member_sender_ids(repo: DutyRepository) -> list[str]:
    config = _notification_config_with_env_defaults(repo.get_notification_config())
    rooms = _normalize_feature_channel_rooms(config.get("lightagent_targets"))
    legacy_room_id = str(config.get("lightagent_target") or "").strip()
    if legacy_room_id:
        rooms = _normalize_feature_channel_rooms(rooms + [{"id": legacy_room_id}])
    room_ids = [room["id"] for room in rooms if room.get("id")]
    if not room_ids:
        return []
    if wechat_bridge_enabled():
        manager = get_wechat_bridge_manager()
        ids = []
        for room_id in room_ids:
            for member in manager.get_room_members(room_id, limit=500):
                sender_id = str(member.get("runtime_sender_id") or member.get("sender_id") or "").strip()
                if sender_id and sender_id not in ids:
                    ids.append(sender_id)
        return ids or _bound_wechat_sender_ids(repo)
    ids = []
    failed = False
    for room_id in room_ids:
        try:
            data = _lightagent_web_request(
                repo,
                "GET",
                "/api/wechat-group/members",
                params={"stable_room_id": room_id, "limit": "500"},
            )
        except HTTPException:
            failed = True
            continue
        for member in data.get("members") or []:
            sender_id = str(member.get("runtime_sender_id") or member.get("sender_id") or "").strip()
            if sender_id and sender_id not in ids:
                ids.append(sender_id)
    if ids:
        return ids
    if failed:
        return _bound_wechat_sender_ids(repo)
    return _bound_wechat_sender_ids(repo)

def _patrol_warning_mentions_for_client(repo: DutyRepository, config: dict[str, Any], client: Any) -> list[str]:
    event = ReminderEvent(kind="patrol_warning_start", person_name="", send_at=datetime.now(TZ), content="")
    return _notification_true_mentions_for_event(repo, client, event)



def _notification_target_room_ids_for_event(event: ReminderEvent) -> list[str] | None:
    room_id = str(getattr(event, "target_room_id", "") or "").strip()
    return [room_id] if room_id else None


def _notification_target_ids_for_event(repo: DutyRepository, client: Any, event: ReminderEvent) -> list[str] | None:
    if _is_wecom_app_notify_client(client):
        person_target = _wecom_app_person_target_for_event(event)
        if person_target:
            userid = _wecom_app_userid_lookup(repo).get(person_target, "")
            return [userid] if userid else []
        function_key = _wecom_app_function_key_for_event_kind(event.kind)
        if function_key:
            return _wecom_app_tousers_for_function(repo, function_key)
        return None
    return _notification_target_room_ids_for_event(event)


def _wecom_app_unbound_person_target(bound_lookup: dict[str, str], event: ReminderEvent) -> str:
    person_target = _wecom_app_person_target_for_event(event)
    return person_target if person_target and not bound_lookup.get(person_target) else ""


def _wecom_app_unbound_detail(name: str) -> str:
    return f"{name} 还没有绑定企业微信成员，请先让他在企业微信自建应用发送“绑定{name}”。"


def _raise_if_wecom_app_unbound_person(repo: DutyRepository, client: Any, event: ReminderEvent) -> None:
    if not _is_wecom_app_notify_client(client):
        return
    missing_target = _wecom_app_unbound_person_target(_wecom_app_userid_lookup(repo), event)
    if missing_target:
        raise HTTPException(status_code=400, detail=_wecom_app_unbound_detail(missing_target))


def _daily_duty_target_room_ids(repo: DutyRepository) -> list[str] | None:
    room_id = str(repo.get_daily_duty_config().get("notification_room_id") or "").strip()
    return _saved_notification_room_ids(repo, room_id)


def _patrol_warning_target_room_ids(repo: DutyRepository) -> list[str] | None:
    room_id = str(repo.get_patrol_warning_config().get("notification_room_id") or "").strip()
    return _saved_notification_room_ids(repo, room_id)


def _configured_person_target_room_ids(repo: DutyRepository, name: str) -> list[str] | None:
    clean = str(name or "").strip()
    for person in repo.list_monitored_people():
        if str(person.get("name") or "").strip() == clean:
            room_id = str(person.get("notification_room_id") or "").strip()
            return _saved_notification_room_ids(repo, room_id)
    return None


def _record_target_room_ids(repo: DutyRepository, record: dict[str, Any]) -> list[str] | None:
    room_id = str(record.get("notification_room_id") or "").strip()
    return _saved_notification_room_ids(repo, room_id)


def _saved_notification_room_ids(repo: DutyRepository | None, room_id: str) -> list[str] | None:
    clean = str(room_id or "").strip()
    if not clean:
        return None
    if re.fullmatch(r"\d+", clean):
        return None
    if repo is not None:
        configured = _notification_wechat_target_room_ids(repo)
        if configured and clean not in configured:
            return None
    return [clean]


async def _notify_send_text(client: Any, content: str, mentions: list[str] | None = None, target_ids: list[str] | None = None) -> None:
    if target_ids is not None and (_is_personal_wechat_notify_client(client) or _is_wecom_app_notify_client(client)):
        await client.send_text(content, mentions, target_ids=target_ids)
        return
    await client.send_text(content, mentions)


async def _notify_send_image(client: Any, image_bytes: bytes, target_ids: list[str] | None = None) -> None:
    if target_ids is not None and (_is_personal_wechat_notify_client(client) or _is_wecom_app_notify_client(client)):
        await client.send_image(image_bytes, target_ids=target_ids)
        return
    await client.send_image(image_bytes)


async def _notify_send_news(
    client: Any,
    *,
    title: str,
    description: str,
    image_bytes: bytes,
    target_ids: list[str] | None = None,
    url: str = "",
) -> bool:
    if not hasattr(client, "send_news"):
        return False
    if isinstance(client, WeComAppNotifyClient) and not hasattr(client.client, "send_news"):
        return False
    await client.send_news(
        title=_news_text(title, 128),
        description=_news_text(description, 512),
        image_bytes=image_bytes,
        url=url or _notification_news_url(title=title, description=description, image_bytes=image_bytes),
        target_ids=target_ids,
    )
    return True


def _news_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit] or "监控提醒"


def _notification_news_url(*, title: str = "", description: str = "", image_bytes: bytes | None = None) -> str:
    detail_path = ""
    if image_bytes:
        detail_path = _save_notification_news_detail(title, description, image_bytes)
    url = _public_app_url(detail_path or "/")
    return url if url.startswith(("http://", "https://")) else "https://work.weixin.qq.com"


def _save_notification_news_detail(title: str, description: str, image_bytes: bytes) -> str:
    uploads = Path(os.getenv("UPLOAD_DIR", "uploads"))
    uploads.mkdir(parents=True, exist_ok=True)
    filename = f"notification-detail-{uuid.uuid4().hex}.html"
    image_data = base64.b64encode(image_bytes).decode("ascii")
    safe_title = html_lib.escape(_news_text(title, 128))
    safe_description = html_lib.escape(str(description or "").strip())
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{safe_title}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f4f6;color:#111827;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}.page{{max-width:760px;margin:0 auto;padding:14px}}.card{{background:#fff;border-radius:18px;box-shadow:0 8px 24px rgba(15,23,42,.08)}}.header{{padding:18px 18px 10px}}h1{{margin:0;font-size:20px;line-height:1.35}}.desc{{margin:10px 0 0;color:#4b5563;font-size:15px;line-height:1.65;white-space:pre-wrap;word-break:break-word}}img{{display:block;width:100%;height:auto;border-top:1px solid #e5e7eb}}.tip{{padding:10px 18px 16px;color:#9ca3af;font-size:12px}}
</style>
</head>
<body>
<main class="page">
  <article class="card">
    <section class="header">
      <h1>{safe_title}</h1>
      <div class="desc">{safe_description}</div>
    </section>
    <img src="data:image/png;base64,{image_data}" alt="{safe_title}">
    <div class="tip">可上下滑动查看完整内容，双指可放大。</div>
  </article>
</main>
</body>
</html>
"""
    (uploads / filename).write_text(html, encoding="utf-8")
    _cleanup_old_uploads(uploads)
    return f"/notification-detail/{filename}"


def _event_news_title(event: ReminderEvent, content: str) -> str:
    first = next((line.strip() for line in str(content or event.content or "").splitlines() if line.strip()), "")
    labels = {
        "daily_duty": "今日在岗提醒",
        "daily_duty_test": "今日在岗测试",
        "vacation_start": "假期余额提醒",
        "vacation_end": "假期余额不足提醒",
        "vacation_test": "假期余额测试",
        "custom": "自定义提醒",
        "custom_test": "自定义提醒测试",
        "rest": "休息提醒",
    }
    return first or labels.get(str(event.kind or ""), "监控提醒")


async def _send_graphic_or_text_image(
    client: Any,
    *,
    title: str,
    text: str,
    image_bytes: bytes,
    mentions: list[str] | None,
    target_ids: list[str] | None,
    mode: str,
) -> None:
    if mode in {"both", "image"} and await _notify_send_news(
        client,
        title=title,
        description=text,
        image_bytes=image_bytes,
        target_ids=target_ids,
    ):
        return
    sent = False
    if mode in {"both", "text"} and hasattr(client, "send_text"):
        await _notify_send_text(client, text, mentions, target_ids)
        sent = True
    if mode in {"both", "image"} and hasattr(client, "send_image"):
        await _notify_send_image(client, image_bytes, target_ids)
        sent = True
    if not sent:
        raise WeComError("当前通知通道不支持图文发送")


def _is_personal_wechat_notify_client(client: Any) -> bool:
    return isinstance(client, (LightAgentNotifyClient, WechatBridgeNotifyClient)) or bool(
        getattr(client, "is_wechat_bridge", False)
    )


def _is_wecom_app_notify_client(client: Any) -> bool:
    return isinstance(client, WeComAppNotifyClient) or bool(getattr(client, "is_wecom_app_notify", False))


def _tunnel_mechanical_record_date(record: dict[str, Any]) -> date | None:
    for key in ("target", "content", "scheduled_at", "created_at"):
        value = str(record.get(key) or "")
        match = re.search(r"\d{4}-\d{2}-\d{2}", value)
        if not match:
            continue
        try:
            return date.fromisoformat(match.group(0))
        except ValueError:
            continue
    return None


def _tunnel_mechanical_resend_title(kind: str) -> str:
    base = _base_reminder_kind(kind)
    if "modify" in base:
        return "隧道机电修改结果"
    if "query" in base or "result" in base:
        return "隧道机电查询结果"
    return "隧道机电录入结果"


async def _resend_tunnel_mechanical_record(
    repo: DutyRepository,
    client: Any,
    record: dict[str, Any],
    *,
    uploads: Path | None = None,
) -> str:
    kind = str(record.get("kind") or "")
    target = str(record.get("target") or "").strip()
    target_date = _tunnel_mechanical_record_date(record)
    title = _tunnel_mechanical_resend_title(kind)
    upload_dir = uploads or Path(os.getenv("UPLOAD_DIR", "uploads"))
    image_bytes: bytes | None = None
    row_count = 0
    query_error = ""
    if target_date is not None and hasattr(client, "send_image"):
        try:
            result = await _query_tunnel_mechanical_result_image(
                repo,
                TunnelMechanicalResultImageRequest(checkTime=target_date),
                upload_dir,
            )
            row_count = len(result.get("result_rows") or [])
            image_path = _wechat_query_result_image_path({"result_image_url": result.get("result_image_url")}, upload_dir)
            if image_path:
                image_bytes = image_path.read_bytes()
            elif result.get("result_query_error"):
                query_error = str(result.get("result_query_error") or "")
        except Exception as exc:
            query_error = str(exc)
    date_text = target_date.isoformat() if target_date else ""
    description = (
        f"{date_text} {title}补发，共{row_count}条"
        if image_bytes and date_text
        else f"{title}补发：{target or date_text or '未识别日期'}"
    )
    if query_error and not image_bytes:
        description += f"\n重新生成图片失败：{query_error}"
    fake_event = ReminderEvent(kind=kind, person_name="", send_at=datetime.now(TZ), content=description)
    target_ids = _notification_target_ids_for_event(repo, client, fake_event) if _is_wecom_app_notify_client(client) else None
    if image_bytes:
        await _send_graphic_or_text_image(
            client,
            title=title,
            text=description,
            image_bytes=image_bytes,
            mentions=[],
            target_ids=target_ids,
            mode="both",
        )
    else:
        await _notify_send_text(client, description, [], target_ids)
    return description


async def _resend_send_record(repo: DutyRepository, record: dict[str, Any], *, uploads: Path | None = None) -> dict[str, Any]:
    client = _notification_client_from_repo(repo)
    if client is None:
        raise HTTPException(status_code=400, detail="请先配置通知发送通道")

    kind = str(record.get("kind") or "")
    target = str(record.get("target") or "")
    scheduled_at = str(record.get("scheduled_at") or "")
    content = str(record.get("content") or "")
    resend_kind = kind if kind.endswith("_resend") else f"{kind}_resend"
    record_target_ids = _record_target_room_ids(repo, record)
    record_room_id = str(record.get("notification_room_id") or "").strip() if record_target_ids else ""
    record_room_name = str(record.get("notification_room_name") or "").strip() if record_room_id else ""
    try:
        if kind in {"daily_duty", "daily_duty_test", "daily_duty_resend"}:
            preview_date = _date_from_record(record) or _today_in_tz()
            fake_event = ReminderEvent(kind=kind, person_name="今日在岗人员", send_at=datetime.now(TZ), content=content)
            _raise_if_wecom_app_unbound_person(repo, client, fake_event)
            target_ids = _notification_target_ids_for_event(repo, client, fake_event) if _is_wecom_app_notify_client(client) else (record_target_ids or _daily_duty_target_room_ids(repo))
            mode = _normalize_send_content_mode(str(repo.get_daily_duty_config().get("send_content_mode") or "both"), "both")
            await _send_graphic_or_text_image(
                client,
                title=_event_news_title(fake_event, content),
                text=content,
                image_bytes=render_daily_duty_image(_build_daily_duty_preview(repo, preview_date)),
                mentions=_notification_true_mentions_for_event(repo, client, fake_event),
                target_ids=target_ids,
                mode=mode,
            )
        elif kind.startswith("patrol_warning_"):
            fake_event = ReminderEvent(kind=kind, person_name="", send_at=datetime.now(TZ), content=content)
            _raise_if_wecom_app_unbound_person(repo, client, fake_event)
            content = _notification_content_for_event(repo, client, fake_event)
            target_ids = _notification_target_ids_for_event(repo, client, fake_event) if _is_wecom_app_notify_client(client) else (record_target_ids or _patrol_warning_target_room_ids(repo))
            await _notify_send_text(client, content, _notification_true_mentions_for_event(repo, client, fake_event), target_ids)
        elif kind.startswith("tunnel_mechanical"):
            content = await _resend_tunnel_mechanical_record(repo, client, record, uploads=uploads)
        elif (_is_shift_reminder_kind(kind) or kind.startswith(("custom", "vacation", "rest"))) and hasattr(client, "send_image"):
            fake_event = ReminderEvent(
                kind=kind,
                person_name=target,
                send_at=datetime.now(TZ),
                content=content,
            )
            _raise_if_wecom_app_unbound_person(repo, client, fake_event)
            mentions = _notification_true_mentions_for_event(repo, client, fake_event)
            resend_target_ids = _notification_target_ids_for_event(repo, client, fake_event) if _is_wecom_app_notify_client(client) else (record_target_ids or _configured_person_target_room_ids(repo, target))
            mode = "both" if _is_shift_reminder_kind(kind) else "text"
            if kind.startswith("vacation"):
                mode = _normalize_send_content_mode(str(repo.get_vacation_reminder_config().get("send_content_mode") or "both"), "both")
            intro_event = ReminderEvent(
                kind=kind,
                person_name=target,
                send_at=datetime.now(TZ),
                content=_shift_reminder_intro_content(fake_event, personal_wechat=_is_personal_wechat_notify_client(client)),
            )
            intro_content = _notification_content_for_event(repo, client, intro_event)
            await _send_graphic_or_text_image(
                client,
                title=_event_news_title(fake_event, intro_content),
                text=intro_content,
                image_bytes=render_shift_reminder_image(fake_event),
                mentions=mentions,
                target_ids=resend_target_ids,
                mode=mode,
            )
        else:
            fake_event = ReminderEvent(
                kind=kind,
                person_name=target,
                send_at=datetime.now(TZ),
                content=content,
            )
            _raise_if_wecom_app_unbound_person(repo, client, fake_event)
            content = _notification_content_for_event(repo, client, fake_event)
            target_ids = _notification_target_ids_for_event(repo, client, fake_event) if _is_wecom_app_notify_client(client) else (record_target_ids or _configured_person_target_room_ids(repo, target))
            await _notify_send_text(client, content, _notification_true_mentions_for_event(repo, client, fake_event), target_ids)
        repo.save_send_record(
            kind=resend_kind,
            target=target,
            scheduled_at=scheduled_at,
            status="success",
            content=content,
            notification_room_id=record_room_id,
            notification_room_name=record_room_name,
        )
    except HTTPException:
        raise
    except WeComError as exc:
        repo.save_send_record(
            kind=resend_kind,
            target=target,
            scheduled_at=scheduled_at,
            status="failed",
            content=content,
            error=str(exc),
            notification_room_id=record_room_id,
            notification_room_name=record_room_name,
        )
        raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, str(exc))) from exc
    except Exception as exc:
        error = f"补发失败：{exc}"
        repo.save_send_record(
            kind=resend_kind,
            target=target,
            scheduled_at=scheduled_at,
            status="failed",
            content=content,
            error=error,
            notification_room_id=record_room_id,
            notification_room_name=record_room_name,
        )
        raise HTTPException(status_code=502, detail=_sanitize_wechat_ids_for_display(repo, error)) from exc
    return {"success": True}


def _date_from_record(record: dict[str, Any]) -> date | None:
    scheduled_at = str(record.get("scheduled_at") or "")
    if not scheduled_at:
        return None
    try:
        return datetime.fromisoformat(scheduled_at).date()
    except ValueError:
        return None


async def _send_due_reminders(repo: DutyRepository) -> None:
    now = datetime.now(TZ)
    events = _plan_all_events(repo, now.date())
    notification_client = _wecom_webhook_client_from_repo(repo)
    if notification_client is None:
        return

    is_wecom_app_client = _is_wecom_app_notify_client(notification_client)
    wecom_app_bound_lookup = _wecom_app_userid_lookup(repo) if is_wecom_app_client else {}
    for event in events:
        if not (now - REMINDER_SEND_GRACE <= event.send_at <= now):
            continue
        missing_target = _wecom_app_unbound_person_target(wecom_app_bound_lookup, event) if is_wecom_app_client else ""
        if missing_target:
            LOGGER.info("跳过未绑定企业微信成员的提醒：%s %s", event.kind, missing_target)
            continue
        content_hash = hashlib.sha256(event.content.encode("utf-8")).hexdigest()[:12]
        reminder_key = f"{event.person_name}:{event.kind}:{event.send_at.isoformat()}:{event.key_suffix}:{event.target_room_id}:{content_hash}"
        if not repo.mark_sent_once(reminder_key):
            continue
        try:
            target_ids = _notification_target_ids_for_event(repo, notification_client, event)
            mode = _event_send_content_mode(event, "both")
            if event.kind == "daily_duty":
                mentions = _notification_true_mentions_for_event(repo, notification_client, event)
                content = _notification_content_for_event(repo, notification_client, event)
                await _send_graphic_or_text_image(
                    notification_client,
                    title=_event_news_title(event, content),
                    text=content,
                    image_bytes=render_daily_duty_image(_build_daily_duty_preview(repo, now.date())),
                    mentions=mentions,
                    target_ids=target_ids,
                    mode=mode,
                )
            elif _event_can_send_image(event) and mode in {"both", "image"} and hasattr(notification_client, "send_image"):
                mentions = _notification_true_mentions_for_event(repo, notification_client, event)
                intro_event = ReminderEvent(
                    kind=event.kind,
                    person_name=event.person_name,
                    send_at=event.send_at,
                    content=_shift_reminder_intro_content(event, personal_wechat=_is_personal_wechat_notify_client(notification_client)),
                )
                content = _notification_content_for_event(repo, notification_client, intro_event)
                await _send_graphic_or_text_image(
                    notification_client,
                    title=_event_news_title(event, content),
                    text=content,
                    image_bytes=render_shift_reminder_image(event),
                    mentions=mentions,
                    target_ids=target_ids,
                    mode=mode,
                )
            else:
                mentions = _notification_true_mentions_for_event(repo, notification_client, event)
                await _notify_send_text(notification_client, _notification_content_for_event(repo, notification_client, event), mentions, target_ids)
            repo.save_send_record(
                kind=event.kind,
                target=event.person_name,
                scheduled_at=event.send_at.isoformat(),
                status="success",
                content=event.content,
                notification_room_id=event.target_room_id,
                notification_room_name=event.target_room_name,
            )
        except Exception as exc:
            repo.delete_sent_once(reminder_key)
            repo.save_send_record(
                kind=event.kind,
                target=event.person_name,
                scheduled_at=event.send_at.isoformat(),
                status="failed",
                content=event.content,
                error=str(exc),
                notification_room_id=event.target_room_id,
                notification_room_name=event.target_room_name,
            )
            LOGGER.exception("提醒发送失败：%s %s", event.kind, event.person_name)


def _notification_client_from_repo(repo: DutyRepository):
    config = repo.get_notification_config()
    merged = _notification_config_with_env_defaults(config)
    if bool(merged.get("wecom_app_enabled")):
        return _wecom_app_notify_client_from_config(merged, repo)
    return _notification_client_from_config(config)


def _notification_client_from_config(config: dict[str, Any], repo: DutyRepository | None = None):
    config = _notification_config_with_env_defaults(config)
    if bool(config.get("wecom_app_enabled")):
        return _wecom_app_notify_client_from_config(config, repo)
    sender_type = _normalize_notification_sender_type(str(config.get("sender_type") or "wecom_webhook"))
    if sender_type != "wecom_webhook":
        return None
    webhook_url = str(config.get("webhook_url", "")).strip()
    if not webhook_url:
        return None
    return WeComWebhookClient(webhook_url=webhook_url)


def _wecom_webhook_client_from_repo(repo: DutyRepository):
    return _notification_client_from_repo(repo)


def _render_message_template(template: str, values: dict[str, str]) -> str:
    content = template or DEFAULT_MESSAGE_TEMPLATE
    for key, value in values.items():
        content = content.replace("{" + key + "}", value)
    return content


def _wecom_client_from_env() -> WeComClient | None:
    corp_id = os.getenv("WECOM_CORP_ID")
    corp_secret = os.getenv("WECOM_CORP_SECRET")
    agent_id = os.getenv("WECOM_AGENT_ID")
    if not corp_id or not corp_secret or not agent_id:
        return None
    return WeComClient(corp_id=corp_id, corp_secret=corp_secret, agent_id=int(agent_id))


app = create_app()
