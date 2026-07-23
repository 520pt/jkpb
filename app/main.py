from __future__ import annotations

import asyncio
import base64
import calendar
import hashlib
import hmac
import html as html_lib
import logging
import os
import re
import secrets
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
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
from app.wecom import LightAgentNotifyClient, WeComClient, WeComError, WeComWebhookClient


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


class NotificationConfigRequest(BaseModel):
    sender_type: str = ""
    webhook_url: str = ""
    lightagent_url: str = ""
    lightagent_token: str = ""
    lightagent_target: str = ""
    message_template: str = DEFAULT_MESSAGE_TEMPLATE


class NotificationTestRequest(BaseModel):
    test_mobile: str = ""


class PreviewRequest(BaseModel):
    target_date: date | None = None


class PersonnelContactRequest(BaseModel):
    name: str
    mention_mobile: str = ""


class PersonnelRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    people: list[PersonnelContactRequest] = Field(default_factory=list)


class CustomReminderRequest(BaseModel):
    id: int | None = None
    name: str
    mention_mobile: str = ""
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
        suffix = Path(file.filename or "roster.png").suffix.lower() or ".png"
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise HTTPException(status_code=400, detail="仅支持 jpg、png、webp、bmp 图片")
        if file.content_type and file.content_type.lower() not in ALLOWED_UPLOAD_TYPES:
            raise HTTPException(status_code=400, detail="上传文件类型不是图片")
        target = uploads / f"{uuid.uuid4().hex}{suffix}"
        try:
            _save_upload_file(file, target)
            _cleanup_old_uploads(uploads)
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
            repo.upsert_personnel_contacts([person.model_dump() for person in request.people])
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
        return {
            "config": _public_patrol_warning_config(repo.get_patrol_warning_config()),
            "state": _public_patrol_warning_state(repo.get_patrol_warning_state()),
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
            "state": _public_patrol_warning_state(repo.get_patrol_warning_state()),
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
        return {
            "success": True,
            "stats": stats,
            "latest": latest.as_dict() if latest else None,
        }

    @app.get("/api/patrol-warning-image")
    def patrol_warning_image(mode: str = "auto"):
        warning = warning_from_dict(dict(repo.get_patrol_warning_state().get("warning") or {}), TZ)
        if warning is None:
            raise HTTPException(status_code=404, detail="暂无已监测到的公路巡查预警")
        image = render_patrol_warning_image(
            warning,
            now=datetime.now(TZ),
            window_hours=int(repo.get_patrol_warning_config().get("end_reminder_window_hours") or 48),
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
        if warning is None:
            raise HTTPException(status_code=400, detail="暂无已监测到的预警，请等待后台监测到预警后再发送")
        now = datetime.now(TZ)
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

    @app.get("/api/notification-config")
    def get_notification_config():
        return {"config": _public_notification_config(repo.get_notification_config())}

    @app.post("/api/notification-config")
    def save_notification_config(request: NotificationConfigRequest):
        existing = _notification_config_with_env_defaults(repo.get_notification_config())
        webhook_url = request.webhook_url.strip() or str(existing.get("webhook_url", "")).strip()
        lightagent_url = request.lightagent_url.strip() or str(existing.get("lightagent_url", "")).strip()
        lightagent_token = request.lightagent_token.strip() or str(existing.get("lightagent_token", "")).strip()
        lightagent_target = request.lightagent_target.strip() or str(existing.get("lightagent_target", "")).strip()
        repo.save_notification_config(
            sender_type=request.sender_type.strip() or str(existing.get("sender_type", "wecom_webhook")),
            webhook_url=webhook_url,
            lightagent_url=lightagent_url,
            lightagent_token=lightagent_token,
            lightagent_target=lightagent_target,
            message_template=request.message_template.strip() or DEFAULT_MESSAGE_TEMPLATE,
        )
        return {"success": True, "config": _public_notification_config(repo.get_notification_config())}

    @app.post("/api/notification-config/test")
    async def test_notification_config(request: NotificationTestRequest):
        config = repo.get_notification_config()
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
        try:
            await notification_client.send_text(content, [request.test_mobile.strip()])
            repo.save_send_record(
                kind="notification_test",
                target=request.test_mobile.strip() or "测试消息",
                status="success",
                content=content,
            )
        except WeComError as exc:
            repo.save_send_record(
                kind="notification_test",
                target=request.test_mobile.strip() or "测试消息",
                status="failed",
                content=content,
                error=str(exc),
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            repo.save_send_record(
                kind="notification_test",
                target=request.test_mobile.strip() or "测试消息",
                status="failed",
                content=content,
                error=f"测试发送失败：{exc}",
            )
            raise HTTPException(status_code=502, detail=f"测试发送失败：{exc}") from exc
        return {"success": True, "content": content}

    @app.get("/api/send-records")
    def list_send_records(limit: int = 100):
        return {"records": repo.list_send_records(limit)}

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

    if start_scheduler:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(timezone=TZ)
        scheduler.add_job(_send_due_reminders, "interval", minutes=1, args=[repo], max_instances=1)
        scheduler.add_job(_check_patrol_warning_monitor, "interval", minutes=1, args=[repo], max_instances=1)

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


def _cleanup_old_uploads(uploads: Path) -> None:
    if UPLOAD_KEEP_DAYS <= 0:
        return
    cutoff = datetime.now(TZ).timestamp() - UPLOAD_KEEP_DAYS * 86400
    for path in uploads.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


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


def _env_notification_config_defaults() -> dict[str, str]:
    return {
        "sender_type": os.getenv("NOTIFICATION_SENDER_TYPE", "").strip(),
        "webhook_url": os.getenv("WECOM_WEBHOOK_URL", "").strip(),
        "lightagent_url": (
            os.getenv("LIGHTAGENT_NOTIFY_URL", "").strip()
            or os.getenv("LIGHTAGENT_PUSH_URL", "").strip()
        ),
        "lightagent_token": (
            os.getenv("LIGHTAGENT_NOTIFY_TOKEN", "").strip()
            or os.getenv("LIGHTAGENT_PUSH_TOKEN", "").strip()
        ),
        "lightagent_target": (
            os.getenv("LIGHTAGENT_NOTIFY_TARGET", "").strip()
            or os.getenv("LIGHTAGENT_TARGET", "").strip()
        ),
    }


def _notification_config_with_env_defaults(config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    env_config = _env_notification_config_defaults()
    sender_type = _normalize_notification_sender_type(str(merged.get("sender_type") or "wecom_webhook"))
    has_active_config = (
        bool(str(merged.get("webhook_url", "")).strip())
        if sender_type == "wecom_webhook"
        else bool(str(merged.get("lightagent_url", "")).strip() and str(merged.get("lightagent_target", "")).strip())
    )
    env_sender_type = _normalize_notification_sender_type(env_config["sender_type"]) if env_config["sender_type"] else ""
    has_env_lightagent = bool(env_config["lightagent_url"] or env_config["lightagent_target"])
    if env_sender_type:
        sender_type = env_sender_type
        merged["sender_type"] = sender_type
    elif not has_active_config and has_env_lightagent:
        sender_type = env_sender_type or "lightagent"
        merged["sender_type"] = sender_type

    for key in ("webhook_url", "lightagent_url", "lightagent_token", "lightagent_target"):
        if not str(merged.get(key, "")).strip() and env_config[key]:
            merged[key] = env_config[key]
    return merged


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
    lightagent_target = str(config.get("lightagent_target", "")).strip()
    lightagent_token = str(config.get("lightagent_token", "")).strip()
    sender_type = _normalize_notification_sender_type(str(config.get("sender_type") or "wecom_webhook"))
    active_configured = bool(webhook_url) if sender_type == "wecom_webhook" else bool(lightagent_url and lightagent_target)
    return {
        "sender_type": sender_type,
        "webhook_url": "",
        "webhook_configured": bool(webhook_url),
        "webhook_display": "已配置" if webhook_url else "未配置",
        "lightagent_url": "",
        "lightagent_configured": bool(lightagent_url and lightagent_target),
        "lightagent_display": "已配置" if lightagent_url and lightagent_target else "未配置",
        "lightagent_token_configured": bool(lightagent_token),
        "lightagent_target": lightagent_target,
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


def _public_patrol_warning_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "warning_key": str(state.get("warning_key") or ""),
        "warning": dict(state.get("warning") or {}),
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
        message = "暂无已监测到的公路巡查预警" if warning is None else f"{target:%Y-%m-%d} 没有公路巡查预警提醒"
        statuses.append({"key": "patrol_warning", "message": message})

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
    shift_names = {
        "early": [row["name"] for row in rows if row["code"] == "早"],
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
        send_content_mode = _normalize_patrol_send_content_mode(str(repo.get_patrol_warning_config().get("send_content_mode") or "both"))
        if send_content_mode in {"both", "text"}:
            await webhook_client.send_text(content, mentioned_mobile_list)
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


def _mobile_for_event(event: ReminderEvent, mobile_lookup: dict[str, str]) -> str:
    return event.mention_mobile.strip() or mobile_lookup.get(event.person_name, "")


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
            await client.send_text(content, ["@all"] if "@所有人" in content else [])
        else:
            mobile = _person_mobile_lookup(repo).get(target, target if target != "测试消息" else "")
            await client.send_text(content, [mobile])
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
                await webhook_client.send_text(event.content, [_mobile_for_event(event, mobile_lookup)])
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
        endpoint_url = str(config.get("lightagent_url", "")).strip()
        target = str(config.get("lightagent_target", "")).strip()
        if not endpoint_url or not target:
            return None
        return LightAgentNotifyClient(
            endpoint_url=endpoint_url,
            target=target,
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
