from __future__ import annotations

import asyncio
import base64
import calendar
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
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.daily_duty_image import has_cjk_font, render_daily_duty_image
from app.ocr import extract_roster_image, extract_template_roster_image, recheck_template_roster_cells
from app.patrol_warning import (
    PatrolWarningError,
    build_end_reminder_message,
    build_start_message,
    due_end_reminder_slot,
    fetch_latest_warning,
    fetch_latest_warning_result,
    failure_backoff_until,
    next_poll_time,
    warning_from_dict,
)
from app.patrol_warning_image import render_patrol_warning_image
from app.reminders import DEFAULT_MESSAGE_TEMPLATE, ReminderEvent, ReminderSettings, plan_reminders_for_day
from app.roster import Shift, ShiftAssignment, normalize_shift_code
from app.storage import (
    DEFAULT_DAILY_DUTY_TEMPLATE,
    DEFAULT_PATROL_WARNING_END_TEMPLATE,
    DEFAULT_PATROL_WARNING_START_TEMPLATE,
    DEFAULT_REST_MESSAGE_TEMPLATE,
    DutyRepository,
)
from app.tunnel_mechanical_image import render_tunnel_mechanical_result_image
from app.wecom import LightAgentNotifyClient, WeComClient, WeComError, WeComWebhookClient
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
TUNNEL_MECHANICAL_KEEPALIVE_ENABLED = os.getenv("TUNNEL_MECHANICAL_KEEPALIVE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES = max(5, int(os.getenv("TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES", "30") or 30))
TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES = max(5, int(os.getenv("TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES", "30") or 30))


class RosterConfirmRequest(BaseModel):
    year: int
    month: int
    source_image_path: str = ""
    grid: list[dict[str, Any]]
    overwrite: bool = False


class RosterRecheckRequest(BaseModel):
    source_image_path: str
    grid: list[dict[str, Any]]
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
    daily_time: str = "07:50"
    before_shift_minutes: int = Field(default=10, ge=0, le=1440)
    rest_reminder_enabled: bool = False
    rest_reminder_time: str = "08:30"
    rest_message_template: str = DEFAULT_REST_MESSAGE_TEMPLATE
    enabled: bool = True

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
    lightagent_url: str = ""
    lightagent_token: str = ""
    lightagent_target: str = ""
    lightagent_targets: list[LightAgentTargetRequest] = Field(default_factory=list)
    message_template: str = DEFAULT_MESSAGE_TEMPLATE


class NotificationTestRequest(BaseModel):
    test_mobile: str = ""
    test_wechat_member_id: str = ""
    test_wechat_member_name: str = ""


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


class PreviewRequest(BaseModel):
    target_date: date | None = None


class PersonnelContactRequest(BaseModel):
    name: str
    mention_mobile: str = ""
    wechat_group_room_id: str = ""
    wechat_group_room_name: str = ""
    wechat_group_member_id: str = ""
    wechat_group_runtime_sender_id: str = ""
    wechat_group_member_name: str = ""


class PersonnelRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    people: list[PersonnelContactRequest] = Field(default_factory=list)


class CustomReminderRequest(BaseModel):
    id: int | None = None
    name: str
    mention_mobile: str = ""
    wechat_group_room_id: str = ""
    wechat_group_room_name: str = ""
    wechat_group_member_id: str = ""
    wechat_group_runtime_sender_id: str = ""
    wechat_group_member_name: str = ""
    shift_code: str
    reminder_time: str
    message: str
    enabled: bool = True

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


class DailyDutyConfigRequest(BaseModel):
    enabled: bool = True
    reminder_time: str = "07:50"
    big_driver_names: list[str] = []
    small_driver_names: list[str] = []
    message_template: str = DEFAULT_DAILY_DUTY_TEMPLATE

    @field_validator("reminder_time")
    @classmethod
    def validate_reminder_time(cls, value: str) -> str:
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
    end_reminder_interval_hours: int = Field(default=6, ge=1, le=168)
    end_reminder_window_hours: int = Field(default=48, ge=1, le=720)
    mention_all: bool = True
    mention_mobiles: str = ""
    send_content_mode: str = "both"
    start_message_template: str = DEFAULT_PATROL_WARNING_START_TEMPLATE
    end_message_template: str = DEFAULT_PATROL_WARNING_END_TEMPLATE


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


class TunnelMechanicalConfigRequest(BaseModel):
    base_url: str = ""
    username: str = ""
    password: str = ""


class TunnelMechanicalLoginRequest(BaseModel):
    code: str = ""
    uuid: str = ""


class WechatQueryRequest(BaseModel):
    text: str = ""
    room_id: str = ""
    stable_room_id: str = ""
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
    app.state.scheduler_enabled = start_scheduler
    app.state.cjk_font_ready = has_cjk_font()
    app.state.wechat_bridge_enabled = wechat_bridge_enabled()
    app.state.wechat_bridge = get_wechat_bridge_manager() if app.state.wechat_bridge_enabled else None
    if app.state.wechat_bridge:
        app.state.wechat_bridge.set_message_handler(lambda message: _handle_wechat_bridge_message(repo, uploads, message))
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
        return FileResponse(static_dir / "index.html")

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

    @app.get("/api/uploads/{filename}")
    def get_uploaded_image(filename: str):
        safe_name = Path(filename).name
        target = (uploads / safe_name).resolve()
        upload_root = uploads.resolve()
        if upload_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target)

    @app.post("/api/rosters/upload")
    def upload_roster(file: UploadFile = File(...)):
        target = _save_roster_upload(file, uploads)
        try:
            result = extract_roster_image(str(target))
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
            checked = _diff_rechecked_grid(list(request.grid or []), list(parsed.get("grid", [])))

        year = request.year or _today_in_tz().year
        month = request.month or _today_in_tz().month
        checked["grid"] = _sanitize_roster_grid_for_month(list(checked.get("grid", [])), year, month)
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

    @app.get("/api/custom-reminders")
    def list_custom_reminders():
        return {"reminders": repo.list_custom_reminders()}

    @app.post("/api/custom-reminders")
    def save_custom_reminder(request: CustomReminderRequest):
        reminder_id = repo.save_custom_reminder(**request.model_dump())
        return {"success": True, "id": reminder_id, "reminders": repo.list_custom_reminders()}

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
            await notification_client.send_image(render_daily_duty_image(preview))
            repo.save_send_record(
                kind="daily_duty_test",
                target="今日在岗人员",
                scheduled_at=preview["send_at"],
                status="success",
                content=preview["content"],
            )
        except WeComError as exc:
            repo.save_send_record(
                kind="daily_duty_test",
                target="今日在岗人员",
                scheduled_at=preview["send_at"],
                status="failed",
                content=preview["content"],
                error=str(exc),
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            repo.save_send_record(
                kind="daily_duty_test",
                target="今日在岗人员",
                scheduled_at=preview["send_at"],
                status="failed",
                content=preview["content"],
                error=f"测试发送失败：{exc}",
            )
            raise HTTPException(status_code=502, detail=f"测试发送失败：{exc}") from exc
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
        password = request.password if request.password else str(existing.get("password", ""))
        should_reset_state = any(
            str(existing.get(key) or "").strip() != str(getattr(request, key) or "").strip()
            for key in ("login_url", "warning_url", "project_id", "platform", "route_code")
        )
        repo.save_patrol_warning_config(**{**request.model_dump(), "password": password})
        if should_reset_state:
            repo.save_patrol_warning_state(
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
                mentioned_mobile_list=_patrol_warning_mentions(config),
                warning=warning,
                window_hours=int(config.get("end_reminder_window_hours") or 48),
                now=now,
                image_mode=mode,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"发送预警提醒失败：{exc}") from exc
        return {"success": True, "content": content}

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

    @app.get("/api/notification-config")
    def get_notification_config():
        return {"config": _public_notification_config(repo.get_notification_config())}

    @app.post("/api/notification-config")
    def save_notification_config(request: NotificationConfigRequest):
        existing = _notification_config_with_env_defaults(repo.get_notification_config())
        sender_type = request.sender_type.strip() or str(existing.get("sender_type", "wecom_webhook"))
        webhook_url = request.webhook_url.strip() or str(existing.get("webhook_url", "")).strip()
        lightagent_url = request.lightagent_url.strip() or str(existing.get("lightagent_url", "")).strip()
        lightagent_token = request.lightagent_token.strip() or str(existing.get("lightagent_token", "")).strip()
        request_targets = _normalize_feature_channel_rooms(
            [
                {"id": room.id, "name": room.name}
                for room in request.lightagent_targets
            ]
        )
        if request.lightagent_target.strip():
            request_targets = _normalize_feature_channel_rooms(
                request_targets + [{"id": request.lightagent_target.strip()}]
            )
        lightagent_targets = request_targets
        lightagent_target = lightagent_targets[0]["id"] if lightagent_targets else ""
        repo.save_notification_config(
            sender_type=sender_type,
            webhook_url=webhook_url,
            lightagent_url=lightagent_url,
            lightagent_token=lightagent_token,
            lightagent_target=lightagent_target,
            lightagent_targets=lightagent_targets,
            message_template=request.message_template.strip() or DEFAULT_MESSAGE_TEMPLATE,
        )
        lightagent_sync = _sync_lightagent_notification_targets(repo, sender_type, lightagent_targets)
        return {
            "success": True,
            "config": _public_notification_config(repo.get_notification_config()),
            "lightagent_sync": lightagent_sync,
        }

    @app.post("/api/notification-config/test")
    async def test_notification_config(request: NotificationTestRequest):
        config = _notification_config_with_env_defaults(repo.get_notification_config())
        notification_client = _notification_client_from_config(config)
        if notification_client is None:
            raise HTTPException(status_code=400, detail="请先配置通知发送通道")
        content = _render_message_template(
            str(config.get("message_template") or DEFAULT_MESSAGE_TEMPLATE),
            {
                "name": "示例甲",
                "date": "2025-09-16",
                "time_range": "08:00至16:00",
                "shift_label": "中班",
            },
        )
        target = request.test_mobile.strip()
        mentions = [target]
        if _normalize_notification_sender_type(str(config.get("sender_type") or "")) == "lightagent":
            target = request.test_wechat_member_id.strip() or target
            mentions = [request.test_wechat_member_id.strip()] if request.test_wechat_member_id.strip() else []
        record_target = target or "测试消息"
        if _normalize_notification_sender_type(str(config.get("sender_type") or "")) == "lightagent":
            record_target = _wechat_test_record_target(repo, target, request.test_wechat_member_name.strip())
        try:
            await notification_client.send_text(content, mentions)
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
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            repo.save_send_record(
                kind="notification_test",
                target=record_target,
                status="failed",
                content=content,
                error=f"测试发送失败：{exc}",
            )
            raise HTTPException(status_code=502, detail=f"测试发送失败：{exc}") from exc
        return {"success": True, "content": content}

    @app.get("/api/feature-channel-config")
    def get_feature_channel_config():
        return {"config": _public_feature_channel_config(repo.get_feature_channel_config())}

    @app.post("/api/feature-channel-config")
    def save_feature_channel_config(request: FeatureChannelConfigRequest):
        existing = _feature_channel_config_with_env_defaults(repo.get_feature_channel_config())
        lightagent_web_url = request.lightagent_web_url.strip() or str(existing.get("lightagent_web_url", "")).strip()
        lightagent_web_password = request.lightagent_web_password.strip() or str(existing.get("lightagent_web_password", "")).strip()
        rooms = _normalize_feature_channel_rooms(
            [room.model_dump() for room in request.wechat_group_rooms]
            or [
                {
                    "id": request.wechat_group_room_id,
                    "name": request.wechat_group_room_name,
                }
            ]
        )
        repo.save_feature_channel_config(
            enabled=bool(request.enabled),
            lightagent_web_url=lightagent_web_url,
            lightagent_web_password=lightagent_web_password,
            wechat_group_room_id=request.wechat_group_room_id.strip(),
            wechat_group_room_name=request.wechat_group_room_name.strip(),
            wechat_group_rooms=rooms,
            allow_tunnel_mechanical=bool(request.allow_tunnel_mechanical),
            allow_duty_query=bool(request.allow_duty_query),
            allow_roster_import=bool(request.allow_roster_import),
        )
        lightagent_sync = _sync_lightagent_feature_channel_rooms(repo, bool(request.enabled), rooms)
        return {
            "success": True,
            "config": _public_feature_channel_config(repo.get_feature_channel_config()),
            "lightagent_sync": lightagent_sync,
        }

    @app.post("/api/feature-channel-config/test")
    async def test_feature_channel_config():
        config = _feature_channel_config_with_env_defaults(repo.get_feature_channel_config())
        room_id = next(iter(_feature_channel_config_room_ids(config)), "")
        query = WechatQueryRequest(
            text="隧道机电",
            room_id=room_id,
            stable_room_id=room_id,
            sender_id="feature-channel-test",
            runtime_sender_id="feature-channel-test",
            sender_name="功能通道测试",
        )
        result = await _build_wechat_query_response(repo, query, uploads=uploads)
        return {"success": True, "result": result}

    @app.get("/api/lightagent/wechat/status")
    def lightagent_wechat_status():
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
        if app.state.wechat_bridge:
            app.state.wechat_bridge.refresh_rooms()
            return app.state.wechat_bridge.status_snapshot()
        return _lightagent_web_request(repo, "POST", "/api/wechat_group/qrlogin", json_body={"action": "refresh"})

    @app.get("/api/lightagent/wechat/rooms")
    def lightagent_wechat_rooms():
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
        return await _build_wechat_query_response(repo, query, uploads=uploads)

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
        return _build_wechat_roster_import_response(repo, uploads, file, overwrite=overwrite)

    @app.post("/api/wechat-roster/confirm")
    def wechat_roster_confirm(http_request: Request, request: WechatRosterConfirmRequest):
        _require_wechat_query_auth(http_request)
        _require_feature_channel_for_roster_import(
            repo,
            room_id=str(request.room_id or ""),
            stable_room_id=str(request.stable_room_id or ""),
        )
        return _build_wechat_roster_confirm_response(
            repo,
            int(request.year),
            int(request.month),
            list(request.grid or []),
            source_image_path=str(request.source_image_path or ""),
            overwrite=bool(request.overwrite),
        )

    @app.get("/api/send-records")
    def list_send_records(limit: int = 100):
        return {"records": _public_send_records(repo, repo.list_send_records(limit))}

    @app.post("/api/send-records/{record_id}/resend")
    async def resend_send_record(record_id: int):
        record = repo.get_send_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="发送记录不存在")
        return await _resend_send_record(repo, record)

    @app.get("/api/system-status")
    def system_status():
        return _build_system_status(repo, bool(app.state.scheduler_enabled), bool(app.state.cjk_font_ready))

    @app.get("/api/reminders/today")
    def today_reminders():
        today = _today_in_tz()
        return _reminder_events_response(repo, today, now=datetime.now(TZ))

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


def _cleanup_old_uploads(uploads: Path) -> None:
    if UPLOAD_KEEP_DAYS <= 0:
        return
    cutoff = datetime.now(TZ).timestamp() - UPLOAD_KEEP_DAYS * 86400
    for path in uploads.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


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
    return normalized if normalized in {"wecom_webhook", "lightagent"} else "wecom_webhook"


def _env_notification_config_defaults() -> dict[str, Any]:
    lightagent_base_url = os.getenv("LIGHTAGENT_BASE_URL", "").strip().rstrip("/")
    lightagent_push_url = f"{lightagent_base_url}/api/push/send" if lightagent_base_url else ""
    env_target_ids = _split_env_list(
        os.getenv("LIGHTAGENT_NOTIFY_TARGETS", "").strip()
        or os.getenv("LIGHTAGENT_TARGETS", "").strip()
    )
    env_target_names = _split_env_list(os.getenv("LIGHTAGENT_NOTIFY_TARGET_NAMES", ""))
    env_targets = _normalize_feature_channel_rooms(
        [
            {"id": target_id, "name": env_target_names[index] if index < len(env_target_names) else ""}
            for index, target_id in enumerate(env_target_ids)
        ]
    )
    single_target = (
        os.getenv("LIGHTAGENT_NOTIFY_TARGET", "").strip()
        or os.getenv("LIGHTAGENT_TARGET", "").strip()
    )
    if single_target:
        env_targets = _normalize_feature_channel_rooms(env_targets + [{"id": single_target}])
    return {
        "sender_type": os.getenv("NOTIFICATION_SENDER_TYPE", "").strip(),
        "webhook_url": os.getenv("WECOM_WEBHOOK_URL", "").strip(),
        "lightagent_url": (
            os.getenv("LIGHTAGENT_NOTIFY_URL", "").strip()
            or os.getenv("LIGHTAGENT_PUSH_URL", "").strip()
            or lightagent_push_url
        ),
        "lightagent_token": (
            os.getenv("LIGHTAGENT_NOTIFY_TOKEN", "").strip()
            or os.getenv("LIGHTAGENT_PUSH_TOKEN", "").strip()
        ),
        "lightagent_target": single_target,
        "lightagent_targets": env_targets,
    }


def _notification_config_with_env_defaults(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    env_config = _env_notification_config_defaults()
    sender_type = _normalize_notification_sender_type(str(merged.get("sender_type") or "wecom_webhook"))
    lightagent_targets = _normalize_feature_channel_rooms(merged.get("lightagent_targets"))
    legacy_target = str(merged.get("lightagent_target", "")).strip()
    if legacy_target:
        lightagent_targets = _normalize_feature_channel_rooms(lightagent_targets + [{"id": legacy_target}])
    has_active_config = (
        bool(str(merged.get("webhook_url", "")).strip())
        if sender_type == "wecom_webhook"
        else bool(str(merged.get("lightagent_url", "")).strip() and lightagent_targets)
    )
    env_sender_type = _normalize_notification_sender_type(env_config["sender_type"]) if env_config["sender_type"] else ""
    has_env_lightagent = bool(env_config["lightagent_url"] or env_config["lightagent_targets"])
    if env_sender_type:
        sender_type = env_sender_type
        merged["sender_type"] = sender_type
    elif not has_active_config and has_env_lightagent:
        sender_type = env_sender_type or "lightagent"
        merged["sender_type"] = sender_type

    for key in ("webhook_url", "lightagent_url", "lightagent_token", "lightagent_target"):
        if env_config[key] and (env_sender_type or not str(merged.get(key, "")).strip()):
            merged[key] = env_config[key]
    if env_config["lightagent_targets"] and (
        env_sender_type or not _normalize_feature_channel_rooms(merged.get("lightagent_targets"))
    ):
        merged["lightagent_targets"] = env_config["lightagent_targets"]
        merged["lightagent_target"] = env_config["lightagent_targets"][0]["id"]
    else:
        merged["lightagent_targets"] = lightagent_targets
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
        merged[key] = _feature_channel_bool(merged.get(key), default=True)
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
        "enabled": bool(config.get("enabled", True)),
        "wechat_bridge_enabled": wechat_bridge_enabled(),
        "lightagent_web_url": str(config.get("lightagent_web_url") or ""),
        "lightagent_web_password_configured": bool(str(config.get("lightagent_web_password") or "").strip()),
        "wechat_group_room_id": str(primary_room.get("id") or ""),
        "wechat_group_room_name": str(primary_room.get("name") or ""),
        "wechat_group_rooms": rooms,
        "allow_tunnel_mechanical": bool(config.get("allow_tunnel_mechanical", True)),
        "allow_duty_query": bool(config.get("allow_duty_query", True)),
        "allow_roster_import": bool(config.get("allow_roster_import", True)),
        "configured": bool(rooms),
    }


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
    if _normalize_notification_sender_type(sender_type) != "lightagent":
        return {"success": True, "skipped": True, "reason": "not_lightagent"}
    room_ids = [str(room.get("id") or "").strip() for room in targets or [] if str(room.get("id") or "").strip()]
    if not room_ids:
        return {"success": True, "skipped": True, "reason": "empty_targets"}
    return _sync_lightagent_wechat_group_targets(repo, room_ids, source="notification")


def _sync_lightagent_feature_channel_rooms(
    repo: DutyRepository,
    enabled: bool,
    rooms: list[dict[str, str]],
) -> dict[str, Any]:
    if not enabled:
        return {"success": True, "skipped": True, "reason": "feature_channel_disabled"}
    room_ids = [str(room.get("id") or "").strip() for room in rooms or [] if str(room.get("id") or "").strip()]
    if not room_ids:
        return {"success": True, "skipped": True, "reason": "empty_rooms"}
    return _sync_lightagent_wechat_group_targets(repo, room_ids, source="feature_channel")


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
                "message": "LightAgent 已响应，但未确认目标群已进入个人微信群选中列表",
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


def _require_feature_channel_for_wechat_query(
    repo: DutyRepository,
    query: WechatQueryRequest,
    permission_key: str,
) -> None:
    config = _feature_channel_config_with_env_defaults(repo.get_feature_channel_config())
    if not bool(config.get("enabled", True)):
        raise HTTPException(status_code=403, detail="功能通道未启用")
    if not bool(config.get(permission_key, True)):
        raise HTTPException(status_code=403, detail="该功能未在功能通道启用")
    configured_room_ids = _feature_channel_config_room_ids(config)
    if configured_room_ids and not (configured_room_ids & _feature_channel_query_room_ids(query)):
        room_name = _feature_channel_config_room_label(config) or "未命名功能群"
        raise HTTPException(status_code=403, detail=f"当前微信群不是功能通道：{room_name}")


def _require_feature_channel_for_roster_import(
    repo: DutyRepository,
    room_id: str = "",
    stable_room_id: str = "",
) -> None:
    config = _feature_channel_config_with_env_defaults(repo.get_feature_channel_config())
    if not bool(config.get("enabled", True)):
        raise HTTPException(status_code=403, detail="功能通道未启用")
    if not bool(config.get("allow_roster_import", True)):
        raise HTTPException(status_code=403, detail="排班导入未在功能通道启用")
    configured_room_ids = _feature_channel_config_room_ids(config)
    supplied = {str(room_id or "").strip(), str(stable_room_id or "").strip()} - {""}
    if configured_room_ids and not (configured_room_ids & supplied):
        room_name = _feature_channel_config_room_label(config) or "未命名功能群"
        raise HTTPException(status_code=403, detail=f"当前微信群不是功能通道：{room_name}")


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
        source_image_url=str(result.get("source_image_url") or ""),
    )


def _build_wechat_roster_confirm_response(
    repo: DutyRepository,
    year: int,
    month: int,
    grid: list[dict[str, Any]],
    *,
    source_image_path: str = "",
    overwrite: bool = False,
    ocr_status: str = "",
    source_image_url: str = "",
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
        return {
            "success": False,
            "import_status": "needs_names",
            "year": year,
            "month": month,
            "people_count": len(sanitized_grid),
            "source_image_path": source_image_path,
            "source_image_url": source_image_url,
            "grid": sanitized_grid,
            "reply": (
                f"已识别 {year}年{month}月排班表，共 {len(sanitized_grid)} 行，"
                "但姓名没有识别完整，暂不自动导入。请到 duty-reminder 网页端上传校对后确认。"
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
            "diffs": diffs[:50],
            "reply": (
                f"{year}年{month}月排班表已存在{preview}。\n"
                "5 分钟内回复“覆盖导入”可替换现有排班；回复“取消导入”放弃。"
            ),
        }
    repo.save_roster_month(year, month, sanitized_grid, source_image_path)
    return {
        "success": True,
        "import_status": "imported_overwrite" if existing and overwrite else "imported",
        "ocr_status": ocr_status,
        "year": year,
        "month": month,
        "people_count": len(sanitized_grid),
        "source_image_path": source_image_path,
        "source_image_url": source_image_url,
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
    text = _normalize_wechat_query_text(query.text)
    if _is_tunnel_mechanical_wechat_request(text):
        _require_feature_channel_for_wechat_query(repo, query, "allow_tunnel_mechanical")
    else:
        _require_feature_channel_for_wechat_query(repo, query, "allow_duty_query")
    tunnel_response = await _build_tunnel_mechanical_wechat_response(repo, query, text, uploads=uploads)
    if tunnel_response is not None:
        return tunnel_response
    if _is_wechat_query_help(text):
        return {"success": True, "reply": _wechat_query_help_text(), "query_type": "help"}
    person = _person_for_wechat_query(repo, query)
    if _is_wechat_binding_query(text):
        if not person:
            return _wechat_query_unbound_response(query)
        return {
            "success": True,
            "query_type": "binding",
            "person_name": person["name"],
            "reply": (
                f"已绑定：{person['name']}\n"
                f"微信成员：{_clean_wechat_member_display_name(str(person.get('wechat_group_member_name') or query.sender_name or ''), str(person.get('wechat_group_runtime_sender_id') or query.runtime_sender_id or query.sender_id or '')) or '已绑定'}"
            ),
        }
    if _is_wechat_next_reminder_query(text):
        if not person:
            return _wechat_query_unbound_response(query)
        return _build_person_next_reminder_query_response(repo, str(person["name"]))
    if not _is_wechat_monitor_query(text):
        return {"success": False, "reply": _wechat_query_help_text(), "query_type": "unknown"}
    if not person:
        return _wechat_query_unbound_response(query)
    start, days = _wechat_query_range(text, query.target_date)
    if days > 1:
        return _build_person_monitor_range_query_response(repo, str(person["name"]), start, days)
    target = start
    return _build_person_monitor_query_response(repo, str(person["name"]), target)


def _handle_wechat_bridge_message(repo: DutyRepository, uploads: Path, message: dict[str, Any]) -> None:
    if message.get("my_msg"):
        return
    if not bool(message.get("is_at")):
        return
    text = str(message.get("text") or "").strip()
    if not text:
        return
    normalized = _normalize_wechat_query_text(text)
    if not _looks_like_duty_wechat_command(normalized):
        return
    LOGGER.warning(
        "内置微信桥收到功能命令：room=%s sender=%s text=%s",
        message.get("stable_room_id") or message.get("room_id") or "",
        message.get("sender_name") or message.get("sender_id") or "",
        text,
    )
    manager = get_wechat_bridge_manager()
    query = WechatQueryRequest(
        text=text,
        room_id=str(message.get("room_id") or ""),
        stable_room_id=str(message.get("stable_room_id") or ""),
        sender_id=str(message.get("sender_id") or ""),
        runtime_sender_id=str(message.get("runtime_sender_id") or ""),
        sender_name=str(message.get("sender_name") or ""),
    )
    try:
        result = asyncio.run(_build_wechat_query_response(repo, query, uploads=uploads))
    except HTTPException as exc:
        result = {"success": False, "reply": str(exc.detail)}
    except Exception as exc:
        LOGGER.exception("内置微信桥处理群消息失败")
        result = {"success": False, "reply": f"查询失败：{exc}"}
    reply = str(result.get("reply") or "").strip()
    room_id = str(message.get("stable_room_id") or message.get("room_id") or "").strip()
    if not room_id:
        return
    image_path = _wechat_query_result_image_path(result, uploads)
    try:
        if reply:
            manager.send_text(room_id, reply)
        if image_path:
            manager.send_image(room_id, str(image_path))
    except Exception:
        LOGGER.exception("内置微信桥发送查询回复失败")


def _looks_like_duty_wechat_command(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return any(
        checker(value)
        for checker in (
            _is_tunnel_mechanical_wechat_request,
            _is_wechat_query_help,
            _is_wechat_binding_query,
            _is_wechat_next_reminder_query,
            _is_wechat_monitor_query,
        )
    )


def _wechat_query_result_image_path(result: dict[str, Any], uploads: Path) -> Path | None:
    image_url = str(result.get("image_url") or result.get("result_image_url") or "").strip()
    if not image_url.startswith("/api/uploads/"):
        return None
    filename = Path(image_url).name
    path = uploads / filename
    return path if path.exists() else None


async def _build_tunnel_mechanical_wechat_response(
    repo: DutyRepository,
    query: WechatQueryRequest,
    text: str,
    *,
    uploads: Path | None = None,
) -> dict[str, Any] | None:
    if not _is_tunnel_mechanical_wechat_request(text):
        return None
    template = _public_tunnel_mechanical_template(repo.get_tunnel_mechanical_template())
    if _is_tunnel_mechanical_wechat_result_query_command(text):
        return await _build_tunnel_mechanical_wechat_result_query_response(repo, query, text, template, uploads=uploads)
    if not _is_tunnel_mechanical_wechat_submit_command(text):
        return {
            "success": True,
            "query_type": "tunnel_mechanical_template",
            "reply": _tunnel_mechanical_wechat_template_reply(template, query.target_date),
        }
    if not template["assets"] or not template["people"]:
        return {
            "success": False,
            "query_type": "tunnel_mechanical",
            "reply": "还没有导入隧道机电模板，请先在页面点击“导入模板”。",
        }
    params = _parse_tunnel_mechanical_wechat_params(text, template["people"], query.target_date)
    missing = []
    if not params.get("checker"):
        missing.append("负责人/检查人")
    if not params.get("recorder"):
        missing.append("记录人")
    if missing:
        return {
            "success": False,
            "query_type": "tunnel_mechanical",
            "reply": (
                "隧道机电录入参数不完整：缺少" + "、".join(missing) + "。\n"
                "示例：隧道机电录入 日期2026-07-24 负责人张三 记录人李四 天气晴"
            ),
        }
    dry_run = "预览" in text
    config = repo.get_tunnel_mechanical_config()
    request = TunnelMechanicalSubmitRequest(
        base_url=str(config.get("base_url") or "") or str(template.get("base_url") or ""),
        checkTime=params["checkTime"],
        weather=str(params.get("weather") or ""),
        checkerId=str(params["checker"]["id"]),
        checker=str(params["checker"]["name"]),
        recorderId=str(params["recorder"]["id"]),
        recorder=str(params["recorder"]["name"]),
        rows=[TunnelMechanicalAssetRequest(**asset) for asset in template["assets"]],
        dry_run=dry_run,
    )
    try:
        result = await _submit_tunnel_mechanical(repo, request, result_upload_dir=uploads)
    except HTTPException as exc:
        detail = str(exc.detail)
        repo.save_send_record(
            kind="tunnel_mechanical_wechat",
            target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
            status="failed",
            content=str(query.text or ""),
            error=detail,
        )
        return {"success": False, "query_type": "tunnel_mechanical", "reply": f"隧道机电录入失败：{detail}"}
    except Exception as exc:
        repo.save_send_record(
            kind="tunnel_mechanical_wechat",
            target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
            status="failed",
            content=str(query.text or ""),
            error=str(exc),
        )
        return {"success": False, "query_type": "tunnel_mechanical", "reply": f"隧道机电录入失败：{exc}"}
    selected_count = len([row for row in request.rows if row.enabled])
    result_image_url = _public_app_url(str(result.get("result_image_url") or ""))
    repo.save_send_record(
        kind="tunnel_mechanical_wechat",
        target=f"{request.checkTime.isoformat()} {request.checker}/{request.recorder}",
        status="success" if result.get("success") else "failed",
        content=str(query.text or ""),
        error="" if result.get("success") else "平台返回部分记录失败",
    )
    return {
        "success": bool(result.get("success")),
        "query_type": "tunnel_mechanical",
        "dry_run": dry_run,
        "status": "preview" if dry_run else ("success" if result.get("success") else "failed"),
        "checkTime": request.checkTime.isoformat(),
        "checkerId": request.checkerId,
        "checker": request.checker,
        "recorderId": request.recorderId,
        "recorder": request.recorder,
        "weather": request.weather,
        "count": selected_count,
        "reply": (
            f"隧道机电{'预览' if dry_run else '录入'}完成：{request.checkTime.isoformat()}，"
            f"负责人{request.checker}，记录人{request.recorder}，天气{request.weather}，共{selected_count}条。"
            + (f"\n查询结果生成失败：{result.get('result_query_error')}" if result.get("result_query_error") else "")
            if result.get("success")
            else "隧道机电录入未全部成功，请到页面查看提交结果。"
        ),
        "image_url": result.get("result_image_url") or "",
        "image_full_url": result_image_url,
        "result": result,
    }


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
        repo.save_send_record(
            kind="tunnel_mechanical_query_wechat",
            target=target_date.isoformat(),
            status="failed",
            content=str(query.text or ""),
            error=detail,
        )
        return {"success": False, "query_type": "tunnel_mechanical_result", "reply": f"隧道机电查询失败：{detail}"}
    except Exception as exc:
        repo.save_send_record(
            kind="tunnel_mechanical_query_wechat",
            target=target_date.isoformat(),
            status="failed",
            content=str(query.text or ""),
            error=str(exc),
        )
        return {"success": False, "query_type": "tunnel_mechanical_result", "reply": f"隧道机电查询失败：{exc}"}
    result_image_url = str(result.get("result_image_url") or "")
    image_full_url = _public_app_url(result_image_url)
    success = bool(result.get("success"))
    repo.save_send_record(
        kind="tunnel_mechanical_query_wechat",
        target=target_date.isoformat(),
        status="success" if success else "failed",
        content=str(query.text or ""),
        error="" if success else str(result.get("result_query_error") or "未生成查询结果图片"),
    )
    row_count = len(result.get("result_rows") or [])
    return {
        "success": success,
        "query_type": "tunnel_mechanical_result",
        "checkTime": target_date.isoformat(),
        "count": row_count,
        "reply": (
            f"已查询 {target_date.isoformat()} 隧道机电结果，共 {row_count} 条，图片已生成，正在发送。"
            if success
            else f"隧道机电查询失败：{result.get('result_query_error') or '未生成查询结果图片'}"
        ),
        "image_url": result_image_url,
        "image_full_url": image_full_url,
        "result": result,
    }


def _is_tunnel_mechanical_wechat_request(text: str) -> bool:
    return "隧道机电" in text or "机电日常检查" in text or ("机电" in text and any(keyword in text for keyword in ("查询", "查", "今日", "今天", "昨日", "昨天", "明日", "明天")))


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


def _is_tunnel_mechanical_wechat_result_query_command(text: str) -> bool:
    return _is_tunnel_mechanical_wechat_request(text) and any(keyword in text for keyword in ("查询", "查"))


def _tunnel_mechanical_wechat_template_reply(template: dict[str, Any], target_date: date | None = None) -> str:
    defaults = template.get("defaults") if isinstance(template.get("defaults"), dict) else {}
    check_time = (target_date or _today_in_tz()).isoformat()
    checker = str(defaults.get("checker") or "").strip() or "张三"
    recorder = str(defaults.get("recorder") or "").strip() or "李四"
    weather = str(defaults.get("weather") or "").strip() or "晴"
    asset_count = len(template.get("assets") or [])
    people = [str(person.get("name") or "").strip() for person in (template.get("people") or []) if person.get("name")]
    people_line = f"\n可用人员：{'、'.join(people[:20])}" if people else ""
    asset_line = f"\n当前模板资产：{asset_count} 条" if asset_count else "\n当前还没有导入隧道模板，请先在页面导入模板。"
    return (
        "隧道机电功能\n"
        "查询结果图：\n"
        f"查询今日机电\n"
        f"查询{check_time}机电\n\n"
        "录入记录：复制下面一行，把负责人、记录人、天气改好后发送：\n"
        f"隧道机电录入 日期{check_time} 负责人{checker} 记录人{recorder} 天气{weather}\n"
        f"只想预览请求，把“录入”改成“预览”。\n"
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


def _normalize_wechat_query_text(text: str) -> str:
    value = _strip_leading_wechat_mentions(str(text or ""))
    return re.sub(r"\s+", "", value).strip("，,。.!！?？：:")


def _is_wechat_query_help(text: str) -> bool:
    if text in {"帮助", "查询帮助", "监控帮助", "提醒帮助"}:
        return True
    return "帮助" in text and any(keyword in text for keyword in ("查询", "监控", "提醒", "绑定"))


def _is_wechat_binding_query(text: str) -> bool:
    return text in {"查询我的绑定", "我的绑定", "查我的绑定", "绑定查询", "我绑定了吗", "我的微信绑定"}


def _is_wechat_next_reminder_query(text: str) -> bool:
    return text in {"查询下次提醒", "下次提醒", "我的下次提醒", "最近提醒", "下一次提醒", "我下次什么时候提醒"}


def _is_wechat_monitor_query(text: str) -> bool:
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
    }:
        return True
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[日号]?|\d{1,2}/\d{1,2}", text):
        return any(keyword in text for keyword in ("查询", "监控", "排班", "提醒", "值班", "什么班", "上班吗"))
    if re.search(r"(?:未来|接下来|最近)(?:\d{1,2}|[一二两三四五六七八九十]+)天", text):
        return True
    return any(
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


def _wechat_query_help_text() -> str:
    return (
        "监控查询菜单：\n"
        "1. 查询我的监控\n"
        "2. 查询今日提醒\n"
        "3. 查询明日监控\n"
        "4. 查询本周监控\n"
        "5. 查询未来7天\n"
        "6. 查询下次提醒\n"
        "7. 查询我的绑定\n"
        "8. 查询今日机电\n"
        "9. 查询2026-07-24机电\n"
        "10. 隧道机电录入 日期2026-07-24 负责人张三 记录人李四 天气晴\n"
        "回复序号即可执行，菜单 3 分钟内有效。\n"
        "也可以问：我今天什么班、明天我上班吗、查询7月24日监控。\n"
        "说明：普通群成员只能查询自己，需要先在 duty-reminder 设置里绑定微信成员。"
    )


def _wechat_query_unbound_response(query: WechatQueryRequest) -> dict[str, Any]:
    sender_name = _clean_wechat_member_display_name(str(query.sender_name or ""), str(query.runtime_sender_id or query.sender_id or ""))
    suffix = f"\n当前微信成员：{sender_name}" if sender_name else ""
    return {
        "success": False,
        "query_type": "unbound",
        "reply": "还没有找到你的微信成员绑定。请先在 duty-reminder 设置 -> 通知发送 -> 微信群通知里同步成员并保存绑定。" + suffix,
    }


def _person_for_wechat_query(repo: DutyRepository, query: WechatQueryRequest) -> dict[str, str] | None:
    runtime_ids = {
        str(query.runtime_sender_id or "").strip(),
        str(query.sender_id or "").strip(),
    }
    stable_ids = {str(query.stable_member_id or "").strip()}
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


def _build_person_monitor_query_response(repo: DutyRepository, person_name: str, target: date) -> dict[str, Any]:
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
        "query_type": "monitor",
        "person_name": person_name,
        "target_date": target.isoformat(),
        "reply": "\n".join(lines),
    }


def _build_person_monitor_range_query_response(repo: DutyRepository, person_name: str, start: date, days: int) -> dict[str, Any]:
    monitored = next((person for person in repo.list_monitored_people() if person["name"] == person_name), None)
    lines = [
        f"{person_name} {start:%Y-%m-%d} 起 {days} 天监控汇总",
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
        lines.append(
            f"- {target:%m-%d} {_wechat_query_weekday(target)}：{_person_roster_status_text(repo, person_name, target)}；{event_times}"
        )
    return {
        "success": True,
        "query_type": "monitor_range",
        "person_name": person_name,
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
        for event in upcoming[:5]:
            content = str(event.content or "").splitlines()[0]
            lines.append(f"- {event.send_at:%m-%d %H:%M} {_wechat_query_event_label(event.kind)}：{content}")
        reply = "\n".join(lines)
    return {
        "success": True,
        "query_type": "next_reminder",
        "person_name": person_name,
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
    return "在岗/备勤（未标早中晚班）"


def _wechat_query_event_label(kind: str) -> str:
    return {
        "daily": "每日提醒",
        "before_shift": "班前提醒",
        "rest": "休息提醒",
        "custom": "自定义提醒",
    }.get(kind, kind)


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
    lightagent_url = str(config.get("lightagent_url", "")).strip()
    lightagent_targets = _normalize_feature_channel_rooms(config.get("lightagent_targets"))
    lightagent_target = str(config.get("lightagent_target", "")).strip()
    if lightagent_target:
        lightagent_targets = _normalize_feature_channel_rooms(lightagent_targets + [{"id": lightagent_target}])
    lightagent_target = lightagent_targets[0]["id"] if lightagent_targets else ""
    lightagent_token = str(config.get("lightagent_token", "")).strip()
    sender_type = _normalize_notification_sender_type(str(config.get("sender_type") or "wecom_webhook"))
    active_configured = (
        bool(webhook_url)
        if sender_type == "wecom_webhook"
        else bool(lightagent_targets and (lightagent_url or wechat_bridge_enabled()))
    )
    return {
        "sender_type": sender_type,
        "wechat_bridge_enabled": wechat_bridge_enabled(),
        "webhook_url": "",
        "webhook_configured": bool(webhook_url),
        "webhook_display": "已配置" if webhook_url else "未配置",
        "lightagent_url": lightagent_url,
        "lightagent_configured": bool(lightagent_targets and (lightagent_url or wechat_bridge_enabled())),
        "lightagent_display": "已配置" if lightagent_targets and (lightagent_url or wechat_bridge_enabled()) else "未配置",
        "lightagent_token_configured": bool(lightagent_token),
        "lightagent_target": lightagent_target,
        "lightagent_targets": lightagent_targets,
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
        "end_reminder_interval_hours": int(config.get("end_reminder_interval_hours") or 6),
        "end_reminder_window_hours": int(config.get("end_reminder_window_hours") or 48),
        "mention_all": bool(config.get("mention_all", True)),
        "mention_mobiles": str(config.get("mention_mobiles") or ""),
        "send_content_mode": _patrol_send_content_mode(config),
        "start_message_template": str(config.get("start_message_template") or DEFAULT_PATROL_WARNING_START_TEMPLATE),
        "end_message_template": str(config.get("end_message_template") or DEFAULT_PATROL_WARNING_END_TEMPLATE),
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


async def _fetch_tunnel_mechanical_captcha(base_url: str, *, solve_attempts: int = 5) -> dict[str, Any]:
    base_url = _tunnel_mechanical_base_url(base_url)
    attempts = max(1, int(solve_attempts or 1))
    last_result: dict[str, Any] | None = None
    for _ in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
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
            raise HTTPException(status_code=502, detail=f"获取智慧养护验证码失败：{exc}") from exc
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
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
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
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
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
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest,
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
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
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
        return {"success": True, "dry_run": True, "submissions": submissions}

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
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest,
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
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest,
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
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest,
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
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
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
            rows = _normalize_tunnel_mechanical_result_rows(_extract_tunnel_mechanical_rows(body))
            filtered = _filter_tunnel_mechanical_result_rows(rows, request)
            if filtered:
                return filtered
            if rows:
                unmatched_rows = rows
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
    request: TunnelMechanicalSubmitRequest | TunnelMechanicalResultImageRequest,
) -> list[dict[str, Any]]:
    date_text = request.checkTime.isoformat()
    filtered = []
    for row in rows:
        row_date = str(row.get("checkTime") or "")
        if row_date and row_date != date_text:
            continue
        filtered.append(row)
    return filtered


def _reminder_events_response(repo: DutyRepository, target: date, *, now: datetime) -> dict[str, Any]:
    events = [*_plan_all_events(repo, target), *_plan_patrol_warning_display_events(repo, target, now=now)]
    events = sorted(events, key=lambda event: event.send_at)
    return {
        "target_date": target.isoformat(),
        "now_beijing": now.isoformat(),
        "group_statuses": _today_reminder_group_statuses(repo, target, events),
        "events": [
            {
                "kind": event.kind,
                "person_name": event.person_name,
                "send_at": event.send_at.isoformat(),
                "content": event.content,
                "sent_state": "sent_or_due" if event.send_at <= now else "pending",
                **_today_reminder_event_media(repo, event, target),
            }
            for event in events
        ],
    }


def _today_reminder_event_media(repo: DutyRepository, event: ReminderEvent, target: date) -> dict[str, str]:
    if event.kind == "daily_duty":
        return {
            "image_url": f"/api/daily-duty-image?target_date={target.isoformat()}",
            "image_alt": "今日在岗提醒图片",
        }
    if event.kind in {"patrol_warning_start", "patrol_warning_end"}:
        send_content_mode = _patrol_send_content_mode(repo.get_patrol_warning_config())
        if send_content_mode in {"both", "image"}:
            mode = "end" if event.kind == "patrol_warning_end" else "start"
            return {
                "image_url": f"/api/patrol-warning-image?mode={mode}&t={event.send_at.timestamp()}",
                "image_alt": "公路巡查预警图片",
            }
    return {}


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
    start_at = warning.create_time or warning.start_time
    if start_at and start_at.date() == target:
        events.append(
            ReminderEvent(
                kind="patrol_warning_start",
                person_name=target_name,
                send_at=start_at,
                content=_build_patrol_warning_content(warning, config, now=now, mode="start"),
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
                    )
                )
            if slot.date() > target:
                break
            slot += timedelta(hours=interval_hours)
    return events


def _patrol_warning_in_display_window(warning: Any, config: dict[str, Any], *, now: datetime) -> bool:
    if warning is None:
        return False
    end_time = getattr(warning, "end_time", None)
    if not end_time:
        return True
    window_hours = max(1, int((config or {}).get("end_reminder_window_hours") or 48))
    return now <= end_time + timedelta(hours=window_hours)


def _today_reminder_group_statuses(repo: DutyRepository, target: date, events: list[ReminderEvent]) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    event_kinds = {event.kind for event in events}

    monitored_count = len(repo.list_monitored_people(enabled_only=True))
    has_monitor_events = bool(event_kinds & {"daily", "before_shift", "rest"})
    if monitored_count == 0:
        statuses.append({"key": "monitor", "message": "未配置监控班提醒人员"})
    elif not has_monitor_events:
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
        statuses.append({"key": "custom", "message": "今日没有匹配到自定义提醒"})

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
        rest_end_date = _rest_end_date(repo, person_name, target)
        return {
            "date": f"{target:%Y-%m-%d}",
            "rest_start_date": f"{target:%Y-%m-%d}",
            "rest_end_date": f"{rest_end_date:%Y-%m-%d}",
            "rest_status": f"正在休息到 {rest_end_date:%Y-%m-%d}",
        }
    if today_is_rest:
        return {
            "date": f"{target:%Y-%m-%d}",
            "rest_start_date": f"{target:%Y-%m-%d}",
            "rest_end_date": f"{target:%Y-%m-%d}",
            "rest_status": "今日下午到岗",
        }
    return None


def _rest_end_date(repo: DutyRepository, person_name: str, start: date) -> date:
    current = start
    while _is_rest_code(_roster_code_for_person(repo, person_name, current + timedelta(days=1))):
        current += timedelta(days=1)
    return current


def _build_daily_duty_preview(repo: DutyRepository, target: date) -> dict[str, Any]:
    config = repo.get_daily_duty_config()
    rows = _roster_rows_for_date(repo, target)
    tomorrow_rows = _roster_rows_for_date(repo, target + timedelta(days=1))
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
    on_duty_names = [row["name"] for row in rows if _is_on_duty_code(row["code"])]
    big_drivers = [name for name in on_duty_names if name in big_driver_set]
    small_drivers = [name for name in on_duty_names if name in small_driver_set]
    excluded = (
        set(shift_names["early"])
        | set(shift_names["middle"])
        | set(shift_names["night"])
        | set(big_drivers)
        | set(small_drivers)
        | set(afternoon_rest)
        | set(resting)
        | set(afternoon_return)
    )
    standby = [name for name in on_duty_names if name not in excluded]
    values = {
        "early": _join_names(shift_names["early"]),
        "tomorrow_early": _join_names(shift_names["tomorrow_early"]),
        "middle": _join_names(shift_names["middle"]),
        "night": _join_names(shift_names["night"]),
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
    }


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


def _diff_rechecked_grid(current_grid: list[dict[str, Any]], parsed_grid: list[dict[str, Any]]) -> dict[str, Any]:
    corrected_grid: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row_index, parsed_row in enumerate(parsed_grid):
        current_row = current_grid[row_index] if row_index < len(current_grid) else {}
        parsed_days = dict(parsed_row.get("days", {}))
        parsed_boxes = dict(parsed_row.get("boxes", {}))
        current_days = dict(current_row.get("days", {}))
        for day, parsed_value in parsed_days.items():
            current_value = str(current_days.get(day, ""))
            if current_value != str(parsed_value):
                issues.append(
                    {
                        "row": row_index,
                        "day": day,
                        "before": current_value,
                        "after": parsed_value,
                        "box": parsed_boxes.get(day),
                    }
                )
        corrected_grid.append(
            {
                **parsed_row,
                "name": str(current_row.get("name") or parsed_row.get("name") or ""),
                "days": parsed_days,
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
            content = _render_simple_template(str(reminder.get("message") or ""), values)
            events.append(
                ReminderEvent(
                    kind="custom",
                    person_name=name,
                    send_at=datetime.combine(target, _parse_hhmm(reminder_time), tzinfo=TZ),
                    content=content,
                    mention_mobile=str(reminder.get("mention_mobile") or "").strip(),
                    key_suffix=str(reminder.get("id") or ""),
                )
            )
    return events


def _plan_all_events(repo: DutyRepository, target: date):
    assignments: list[ShiftAssignment] = []
    for roster_month in repo.list_roster_months():
        assignments.extend(_assignments_from_grid(roster_month))

    events = []
    message_template = str(repo.get_notification_config().get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
    for person in repo.list_monitored_people(enabled_only=True):
        events.extend(
            plan_reminders_for_day(
                target_date=target,
                assignments=assignments,
                monitored_name=person["name"],
                mention_text=person["mention_text"],
                settings=ReminderSettings(
                    daily_time=_coerce_hhmm(person["daily_time"], "07:50"),
                    before_shift_minutes=person["before_shift_minutes"],
                    message_template=message_template,
                ),
                tz=TZ,
            )
        )
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
            )
        )
    events.extend(_plan_custom_reminder_events(repo, assignments, target))
    return sorted(events, key=lambda event: event.send_at)


def _build_system_status(repo: DutyRepository, scheduler_enabled: bool, cjk_font_ready: bool) -> dict[str, Any]:
    now = datetime.now(TZ)
    today_start = now.strftime("%Y-%m-%d 00:00:00")
    records_today = repo.list_send_records_since(today_start)
    failed_records = [record for record in records_today if record["status"] != "success"]
    patrol_config = repo.get_patrol_warning_config()
    patrol_state = repo.get_patrol_warning_state()
    notification_config = _public_notification_config(repo.get_notification_config())
    return {
        "now_beijing": now.isoformat(),
        "timezone": str(TZ),
        "scheduler_enabled": scheduler_enabled,
        "webhook_configured": bool(notification_config.get("webhook_configured")),
        "notification_configured": bool(notification_config.get("notification_configured")),
        "notification_sender_type": str(notification_config.get("sender_type") or "wecom_webhook"),
        "wechat_bridge_enabled": wechat_bridge_enabled(),
        "cjk_font_ready": cjk_font_ready,
        "roster_month_count": repo.count_roster_months(),
        "monitored_people_count": repo.count_monitored_people(),
        "today_success_count": len([record for record in records_today if record["status"] == "success"]),
        "today_failed_count": len(failed_records),
        "last_error": failed_records[0]["error"] if failed_records else "",
        "next_events": _next_events(repo, now),
        "patrol_warning_monitor": {
            "enabled": bool(patrol_config.get("enabled")),
            "route_code": str(patrol_config.get("route_code") or ""),
            "last_checked_at": str(patrol_state.get("last_checked_at") or ""),
            "next_check_at": str(patrol_state.get("next_check_at") or ""),
            "backoff_until": str(patrol_state.get("backoff_until") or ""),
            "failure_count": int(patrol_state.get("failure_count") or 0),
            "last_error": str(patrol_state.get("last_error") or ""),
            "token_configured": bool(str(patrol_state.get("token") or "").strip()),
            "token_expires_at": str(patrol_state.get("token_expires_at") or ""),
            "last_warning_key": str(patrol_state.get("warning_key") or ""),
        },
    }


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
    if webhook_client is None:
        return

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
        content = _build_patrol_warning_content(latest, config, now=now, mode="start")
        try:
            await _send_patrol_warning_message(
                repo,
                webhook_client,
                kind="patrol_warning_start",
                target=route_code or latest.route_code or "公路巡查预警",
                scheduled_at=now.isoformat(),
                content=content,
                mentioned_mobile_list=_patrol_warning_mentions(config),
                warning=latest,
                window_hours=int(config.get("end_reminder_window_hours") or 48),
                now=now,
                image_mode="start",
            )
            repo.save_patrol_warning_state(last_start_sent_key=latest.key)
        except Exception as exc:
            LOGGER.exception("公路巡查预警开始提醒发送失败：%s", exc)
            return

    slot = due_end_reminder_slot(
        latest,
        now=now,
        interval_hours=int(config.get("end_reminder_interval_hours") or 6),
        window_hours=int(config.get("end_reminder_window_hours") or 48),
    )
    if slot is None:
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
            mentioned_mobile_list=_patrol_warning_mentions(config),
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
    webhook_client: WeComWebhookClient,
    *,
    kind: str,
    target: str,
    scheduled_at: str,
    content: str,
    mentioned_mobile_list: list[str],
    warning: Any | None = None,
    window_hours: int = 48,
    now: datetime | None = None,
    image_mode: str = "auto",
) -> None:
    try:
        patrol_config = repo.get_patrol_warning_config()
        send_content_mode = _normalize_patrol_send_content_mode(str(patrol_config.get("send_content_mode") or "both"))
        if send_content_mode in {"both", "text"}:
            await webhook_client.send_text(content, _patrol_warning_mentions_for_client(repo, patrol_config, webhook_client))
        if send_content_mode in {"both", "image"} and warning is not None:
            await webhook_client.send_image(
                render_patrol_warning_image(
                    warning,
                    now=now or datetime.now(TZ),
                    window_hours=window_hours,
                    mode=image_mode,
                )
            )
        repo.save_send_record(
            kind=kind,
            target=target,
            scheduled_at=scheduled_at,
            status="success",
            content=content,
        )
    except Exception as exc:
        repo.save_send_record(
            kind=kind,
            target=target,
            scheduled_at=scheduled_at,
            status="failed",
            content=content,
            error=str(exc),
        )
        raise


def _build_patrol_warning_content(warning: Any, config: dict[str, Any], *, now: datetime, mode: str) -> str:
    mention_all = bool(config.get("mention_all", True))
    if mode == "end":
        return build_end_reminder_message(
            warning,
            now=now,
            window_hours=int(config.get("end_reminder_window_hours") or 48),
            mention_all=mention_all,
            template=str(config.get("end_message_template") or DEFAULT_PATROL_WARNING_END_TEMPLATE),
        )
    return build_start_message(
        warning,
        mention_all=mention_all,
        template=str(config.get("start_message_template") or DEFAULT_PATROL_WARNING_START_TEMPLATE),
    )


def _normalize_patrol_send_content_mode(value: str) -> str:
    normalized = str(value or "both").strip().lower()
    return normalized if normalized in {"both", "text", "image"} else "both"


def _patrol_send_content_mode(config: dict[str, Any]) -> str:
    return _normalize_patrol_send_content_mode(str(config.get("send_content_mode") or "both"))


def _patrol_warning_mentions(config: dict[str, Any]) -> list[str]:
    if bool(config.get("mention_all", True)):
        return ["@all"]
    text = str(config.get("mention_mobiles") or "")
    return [part for part in re.split(r"[\s,，;；]+", text) if part]


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


def _person_wechat_sender_lookup(repo: DutyRepository) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for person in repo.list_personnel():
        sender_id = str(person.get("wechat_group_runtime_sender_id") or "").strip()
        if sender_id:
            lookup[str(person.get("name") or "").strip()] = sender_id
    return {name: sender_id for name, sender_id in lookup.items() if name}


def _mobile_for_event(event: ReminderEvent, mobile_lookup: dict[str, str]) -> str:
    return event.mention_mobile.strip() or mobile_lookup.get(event.person_name, "")


def _mentions_for_event(client: Any, event: ReminderEvent, mobile_lookup: dict[str, str], wechat_lookup: dict[str, str]) -> list[str]:
    if _is_personal_wechat_notify_client(client):
        sender_id = wechat_lookup.get(event.person_name, "")
        return [sender_id] if sender_id else []
    mobile = _mobile_for_event(event, mobile_lookup)
    return [mobile] if mobile else []


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
    if _is_personal_wechat_notify_client(client):
        if bool(config.get("mention_all", True)):
            return _lightagent_room_member_sender_ids(repo)
        names_or_ids = [part for part in re.split(r"[\s,，;；]+", str(config.get("mention_mobiles") or "")) if part]
        lookup = _person_wechat_sender_lookup(repo)
        mentions = []
        for item in names_or_ids:
            sender_id = item if item.startswith("@") else lookup.get(item, "")
            if sender_id and sender_id not in mentions:
                mentions.append(sender_id)
        return mentions
    return _patrol_warning_mentions(config)


def _is_personal_wechat_notify_client(client: Any) -> bool:
    return isinstance(client, (LightAgentNotifyClient, WechatBridgeNotifyClient)) or bool(
        getattr(client, "is_wechat_bridge", False)
    )


async def _resend_send_record(repo: DutyRepository, record: dict[str, Any]) -> dict[str, Any]:
    client = _notification_client_from_repo(repo)
    if client is None:
        raise HTTPException(status_code=400, detail="请先配置通知发送通道")

    kind = str(record.get("kind") or "")
    target = str(record.get("target") or "")
    scheduled_at = str(record.get("scheduled_at") or "")
    content = str(record.get("content") or "")
    resend_kind = f"{kind}_resend"
    try:
        if kind in {"daily_duty", "daily_duty_test", "daily_duty_resend"}:
            preview_date = _date_from_record(record) or _today_in_tz()
            await client.send_image(render_daily_duty_image(_build_daily_duty_preview(repo, preview_date)))
        elif kind.startswith("patrol_warning_"):
            await client.send_text(content, _patrol_warning_mentions_for_client(repo, repo.get_patrol_warning_config(), client))
        else:
            mobile_lookup = _person_mobile_lookup(repo)
            fake_event = ReminderEvent(
                kind=kind,
                person_name=target,
                send_at=datetime.now(TZ),
                content=content,
                mention_mobile="" if target in mobile_lookup or target == "测试消息" else target,
            )
            await client.send_text(
                content,
                _mentions_for_event(client, fake_event, mobile_lookup, _person_wechat_sender_lookup(repo)),
            )
        repo.save_send_record(
            kind=resend_kind,
            target=target,
            scheduled_at=scheduled_at,
            status="success",
            content=content,
        )
    except WeComError as exc:
        repo.save_send_record(
            kind=resend_kind,
            target=target,
            scheduled_at=scheduled_at,
            status="failed",
            content=content,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        error = f"补发失败：{exc}"
        repo.save_send_record(
            kind=resend_kind,
            target=target,
            scheduled_at=scheduled_at,
            status="failed",
            content=content,
            error=error,
        )
        raise HTTPException(status_code=502, detail=error) from exc
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
    webhook_client = _wecom_webhook_client_from_repo(repo)
    app_client = None if webhook_client else _wecom_client_from_env()
    if webhook_client is None and app_client is None:
        return

    people = {person["name"]: person for person in repo.list_monitored_people(enabled_only=True)}
    mobile_lookup = _person_mobile_lookup(repo)
    wechat_lookup = _person_wechat_sender_lookup(repo)
    for event in events:
        if not (now - REMINDER_SEND_GRACE <= event.send_at <= now):
            continue
        person = people.get(event.person_name)
        can_send = event.kind == "daily_duty" and webhook_client
        can_send = bool(can_send or webhook_client or (person and app_client))
        if not can_send:
            continue
        content_hash = hashlib.sha256(event.content.encode("utf-8")).hexdigest()[:12]
        reminder_key = f"{event.person_name}:{event.kind}:{event.send_at.isoformat()}:{event.key_suffix}:{content_hash}"
        if not repo.mark_sent_once(reminder_key):
            continue
        try:
            if event.kind == "daily_duty" and webhook_client:
                await webhook_client.send_image(render_daily_duty_image(_build_daily_duty_preview(repo, now.date())))
            elif webhook_client:
                await webhook_client.send_text(event.content, _mentions_for_event(webhook_client, event, mobile_lookup, wechat_lookup))
            elif person and app_client:
                await app_client.send_text(person["wecom_userid"], event.content)
            else:
                continue
            repo.save_send_record(
                kind=event.kind,
                target=event.person_name,
                scheduled_at=event.send_at.isoformat(),
                status="success",
                content=event.content,
            )
        except Exception as exc:
            repo.save_send_record(
                kind=event.kind,
                target=event.person_name,
                scheduled_at=event.send_at.isoformat(),
                status="failed",
                content=event.content,
                error=str(exc),
            )
            LOGGER.exception("提醒发送失败：%s %s", event.kind, event.person_name)


def _notification_client_from_repo(repo: DutyRepository):
    return _notification_client_from_config(repo.get_notification_config())


def _notification_client_from_config(config: dict[str, Any]):
    config = _notification_config_with_env_defaults(config)
    sender_type = _normalize_notification_sender_type(str(config.get("sender_type") or "wecom_webhook"))
    if sender_type == "lightagent":
        targets = _normalize_feature_channel_rooms(config.get("lightagent_targets"))
        legacy_target = str(config.get("lightagent_target", "")).strip()
        if legacy_target:
            targets = _normalize_feature_channel_rooms(targets + [{"id": legacy_target}])
        target_ids = [room["id"] for room in targets if room.get("id")]
        if not target_ids:
            return None
        if wechat_bridge_enabled():
            return WechatBridgeNotifyClient(targets=target_ids)
        endpoint_url = str(config.get("lightagent_url", "")).strip()
        if not endpoint_url:
            return None
        return LightAgentNotifyClient(
            endpoint_url=endpoint_url,
            targets=target_ids,
            token=str(config.get("lightagent_token") or ""),
        )

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


app = create_app(start_scheduler=os.getenv("ENABLE_SCHEDULER", "false").lower() == "true")
