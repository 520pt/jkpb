from __future__ import annotations

import asyncio
import base64
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
from app.ocr import extract_roster_image, extract_template_roster_image
from app.reminders import DEFAULT_MESSAGE_TEMPLATE, ReminderEvent, ReminderSettings, plan_reminders_for_day
from app.roster import ShiftAssignment, normalize_shift_code
from app.storage import DEFAULT_DAILY_DUTY_TEMPLATE, DEFAULT_REST_MESSAGE_TEMPLATE, DutyRepository
from app.wecom import WeComClient, WeComError, WeComWebhookClient


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


class MonitoredPersonRequest(BaseModel):
    name: str
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


class NotificationConfigRequest(BaseModel):
    webhook_url: str = ""
    message_template: str = DEFAULT_MESSAGE_TEMPLATE


class NotificationTestRequest(BaseModel):
    test_mobile: str = ""


class PreviewRequest(BaseModel):
    target_date: date | None = None


class PersonnelRequest(BaseModel):
    names: list[str]


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
        parsed = extract_template_roster_image(source_path)
        if parsed is None:
            raise HTTPException(status_code=422, detail="无法从原图重新核对")

        corrected_grid: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        parsed_grid = list(parsed.get("grid", []))
        current_grid = list(request.grid or [])

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

        return {
            "success": True,
            "year": parsed.get("year"),
            "month": parsed.get("month"),
            "source_image_path": str(source_path),
            "source_image_url": f"/api/uploads/{source_path.name}",
            "grid": corrected_grid,
            "issues": issues,
        }

    @app.post("/api/rosters/confirm")
    def confirm_roster(request: RosterConfirmRequest):
        if _has_unconfirmed_roster_names(request.grid):
            raise HTTPException(status_code=422, detail="请先补全所有人员姓名，再确认导入")
        existing = repo.get_roster_month(request.year, request.month)
        if existing and not request.overwrite:
            diffs = _diff_roster_grids(existing.get("grid", []), request.grid)
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "conflict": True,
                    "message": f"{request.year}年{request.month}月排班表已存在",
                    "existing": existing,
                    "incoming": request.model_dump(),
                    "diffs": diffs,
                },
            )
        repo.save_roster_month(request.year, request.month, request.grid, request.source_image_path)
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

    @app.get("/api/personnel")
    def list_personnel():
        return {"names": repo.list_personnel_names()}

    @app.post("/api/personnel")
    def save_personnel(request: PersonnelRequest):
        repo.save_personnel_names(request.names)
        return {"success": True, "names": repo.list_personnel_names()}

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
        webhook_url = str(repo.get_notification_config().get("webhook_url", "")).strip()
        if not webhook_url:
            raise HTTPException(status_code=400, detail="请先配置企业微信群机器人地址")
        target = request.target_date or _today_in_tz()
        preview = _build_daily_duty_preview(repo, target)
        try:
            await WeComWebhookClient(webhook_url=webhook_url).send_image(render_daily_duty_image(preview))
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

    @app.get("/api/notification-config")
    def get_notification_config():
        return {"config": _public_notification_config(repo.get_notification_config())}

    @app.post("/api/notification-config")
    def save_notification_config(request: NotificationConfigRequest):
        existing = repo.get_notification_config()
        webhook_url = request.webhook_url.strip() or str(existing.get("webhook_url", "")).strip()
        repo.save_notification_config(
            webhook_url=webhook_url,
            message_template=request.message_template.strip() or DEFAULT_MESSAGE_TEMPLATE,
        )
        return {"success": True, "config": _public_notification_config(repo.get_notification_config())}

    @app.post("/api/notification-config/test")
    async def test_notification_config(request: NotificationTestRequest):
        config = repo.get_notification_config()
        webhook_url = str(config.get("webhook_url", "")).strip()
        if not webhook_url:
            raise HTTPException(status_code=400, detail="请先配置企业微信群机器人地址")
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
            await WeComWebhookClient(webhook_url=webhook_url).send_text(content, [request.test_mobile.strip()])
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

    @app.post("/api/reminders/preview")
    def preview_reminders(request: PreviewRequest):
        target = request.target_date or _today_in_tz()
        events = _plan_all_events(repo, target)
        return {
            "events": [
                {
                    "kind": event.kind,
                    "person_name": event.person_name,
                    "send_at": event.send_at.isoformat(),
                    "content": event.content,
                }
                for event in events
            ]
        }

    if start_scheduler:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(timezone=TZ)
        scheduler.add_job(_send_due_reminders, "interval", minutes=1, args=[repo], max_instances=1)

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


def _login_page_response(static_dir: Path, *, error: str = "", next_url: str = "/", status_code: int = 200) -> HTMLResponse:
    template = (static_dir / "login.html").read_text(encoding="utf-8")
    error_html = f'<div class="login-error">{html_lib.escape(error)}</div>' if error else ""
    page_html = (
        template.replace("{{error_html}}", error_html)
        .replace("{{next_url}}", html_lib.escape(_safe_next_url(next_url), quote=True))
    )
    return HTMLResponse(page_html, status_code=status_code)


def _public_notification_config(config: dict[str, Any]) -> dict[str, Any]:
    webhook_url = str(config.get("webhook_url", "")).strip()
    return {
        "webhook_url": "",
        "webhook_configured": bool(webhook_url),
        "webhook_display": "已配置" if webhook_url else "未配置",
        "message_template": config.get("message_template") or DEFAULT_MESSAGE_TEMPLATE,
    }


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
    return sorted(events, key=lambda event: event.send_at)


def _build_system_status(repo: DutyRepository, scheduler_enabled: bool, cjk_font_ready: bool) -> dict[str, Any]:
    now = datetime.now(TZ)
    today_start = now.strftime("%Y-%m-%d 00:00:00")
    records_today = repo.list_send_records_since(today_start)
    failed_records = [record for record in records_today if record["status"] != "success"]
    return {
        "now_beijing": now.isoformat(),
        "timezone": str(TZ),
        "scheduler_enabled": scheduler_enabled,
        "webhook_configured": bool(str(repo.get_notification_config().get("webhook_url", "")).strip()),
        "cjk_font_ready": cjk_font_ready,
        "roster_month_count": repo.count_roster_months(),
        "monitored_people_count": repo.count_monitored_people(),
        "today_success_count": len([record for record in records_today if record["status"] == "success"]),
        "today_failed_count": len(failed_records),
        "last_error": failed_records[0]["error"] if failed_records else "",
        "next_events": _next_events(repo, now),
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


async def _resend_send_record(repo: DutyRepository, record: dict[str, Any]) -> dict[str, Any]:
    webhook_url = str(repo.get_notification_config().get("webhook_url", "")).strip()
    if not webhook_url:
        raise HTTPException(status_code=400, detail="请先配置企业微信群机器人地址")

    client = WeComWebhookClient(webhook_url=webhook_url)
    kind = str(record.get("kind") or "")
    target = str(record.get("target") or "")
    scheduled_at = str(record.get("scheduled_at") or "")
    content = str(record.get("content") or "")
    resend_kind = f"{kind}_resend"
    try:
        if kind in {"daily_duty", "daily_duty_test", "daily_duty_resend"}:
            preview_date = _date_from_record(record) or _today_in_tz()
            await client.send_image(render_daily_duty_image(_build_daily_duty_preview(repo, preview_date)))
        else:
            person = next((person for person in repo.list_monitored_people() if person["name"] == target), None)
            mobile = person.get("mention_mobile", "") if person else (target if target != "测试消息" else "")
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
    for event in events:
        if not (now - REMINDER_SEND_GRACE <= event.send_at <= now):
            continue
        reminder_key = f"{event.person_name}:{event.kind}:{event.send_at.isoformat()}"
        if not repo.mark_sent_once(reminder_key):
            continue
        person = people.get(event.person_name)
        try:
            if event.kind == "daily_duty" and webhook_client:
                await webhook_client.send_image(render_daily_duty_image(_build_daily_duty_preview(repo, now.date())))
            elif person and webhook_client:
                await webhook_client.send_text(event.content, [person.get("mention_mobile", "")])
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


def _wecom_webhook_client_from_repo(repo: DutyRepository) -> WeComWebhookClient | None:
    webhook_url = str(repo.get_notification_config().get("webhook_url", "")).strip()
    if not webhook_url:
        return None
    return WeComWebhookClient(webhook_url=webhook_url)


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
