from __future__ import annotations

import base64
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx


class PatrolWarningError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_auth_error(self) -> bool:
        return self.status_code in {401, 403}


LEVEL_LABELS = {
    "1": "蓝色预警",
    "2": "黄色预警",
    "3": "橙色预警",
    "4": "红色预警",
}
LEVEL_TEXT_FIELDS = (
    "WarningLevelName",
    "warningLevelName",
    "WarningLevelLabel",
    "warningLevelLabel",
    "WarningLevelText",
    "warningLevelText",
    "WarnLevelName",
    "warnLevelName",
    "WarnLevelText",
    "warnLevelText",
    "LevelName",
    "levelName",
    "LevelText",
    "levelText",
    "WarnTypeName",
    "warnTypeName",
    "WarningTypeName",
    "warningTypeName",
)
DEFAULT_START_MESSAGE_TEMPLATE = (
    "{mention_prefix}请注意监测到 {app_name} 发布 {warning_level_label}\n"
    "路线：{route_text}\n"
    "预警开始时间：{start_time}\n"
    "桩号：{stake_range}"
)
DEFAULT_END_MESSAGE_TEMPLATE = (
    "{mention_prefix}最新{warning_level_label}已结束\n"
    "路线：{route_text}\n"
    "预警开始时间：{start_time}\n"
    "桩号：{stake_range}\n"
    "预警结束时间：{end_time}\n"
    "预警已结束：{elapsed_hours} 小时\n"
    "距离预警结束后{window_hours}小时内{patrol_frequency_clause}，倒计时结束还有 {remaining_hours} 小时。"
)


@dataclass(frozen=True)
class PatrolWarning:
    key: str
    route_code: str
    route_name: str
    warning_level: str
    warning_level_label: str
    warn_type_name: str
    patrol_frequency_text: str
    start_time: datetime | None
    end_time: datetime | None
    create_time: datetime | None
    start_stake: str
    end_stake: str
    raw: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "route_code": self.route_code,
            "route_name": self.route_name,
            "warning_level": self.warning_level,
            "warning_level_label": self.warning_level_label,
            "warn_type_name": self.warn_type_name,
            "patrol_frequency_text": self.patrol_frequency_text,
            "start_time": self.start_time.isoformat() if self.start_time else "",
            "end_time": self.end_time.isoformat() if self.end_time else "",
            "create_time": self.create_time.isoformat() if self.create_time else "",
            "start_stake": self.start_stake,
            "end_stake": self.end_stake,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class FetchWarningResult:
    warning: PatrolWarning | None
    stats: dict[str, int]
    token: str
    token_expires_at: str
    token_reused: bool


def warning_from_dict(value: dict[str, Any], tz: ZoneInfo) -> PatrolWarning | None:
    if not value:
        return None
    start_time = _parse_datetime(value.get("start_time"), tz)
    end_time = _parse_datetime(value.get("end_time"), tz)
    create_time = _parse_datetime(value.get("create_time"), tz)
    raw = dict(value.get("raw") or {})
    saved_level = str(value.get("warning_level") or "").strip()
    saved_label = str(value.get("warning_level_label") or "").strip()
    raw_warning_level = str(_first_value(raw, "WarningLevel", "warningLevel", "Level", "level") or "").strip()
    warning_level = _warning_level_from_text_fields(raw) or raw_warning_level or _warning_level_from_text(saved_label) or saved_level
    warning_level_label = LEVEL_LABELS.get(warning_level) or saved_label or (f"{warning_level}级预警" if warning_level else "预警")
    return PatrolWarning(
        key=str(value.get("key") or ""),
        route_code=str(value.get("route_code") or ""),
        route_name=str(value.get("route_name") or ""),
        warning_level=warning_level,
        warning_level_label=warning_level_label,
        warn_type_name=str(value.get("warn_type_name") or ""),
        patrol_frequency_text=str(value.get("patrol_frequency_text") or _patrol_frequency_text(raw) or ""),
        start_time=start_time,
        end_time=end_time,
        create_time=create_time,
        start_stake=str(value.get("start_stake") or ""),
        end_stake=str(value.get("end_stake") or ""),
        raw=raw,
    )


async def fetch_latest_warning(config: dict[str, Any], tz: ZoneInfo) -> tuple[PatrolWarning | None, dict[str, int]]:
    result = await fetch_latest_warning_result(config, tz)
    return result.warning, result.stats


async def fetch_latest_warning_result(
    config: dict[str, Any],
    tz: ZoneInfo,
    *,
    token: str = "",
    token_expires_at: str = "",
    now: datetime | None = None,
) -> FetchWarningResult:
    now = now or datetime.now(tz)
    current_token = token if is_token_valid(token, token_expires_at, now) else ""
    token_reused = bool(current_token)
    if not current_token:
        current_token = await _login(config)
        token_expires_at = token_cache_expires_at(current_token, now, tz).isoformat()
    try:
        rows = await _fetch_rows(config, current_token)
    except PatrolWarningError as exc:
        if not token_reused or not exc.is_auth_error:
            raise
        current_token = await _login(config)
        token_expires_at = token_cache_expires_at(current_token, now, tz).isoformat()
        token_reused = False
        rows = await _fetch_rows(config, current_token)
    route_code = str(config.get("route_code") or "").strip().upper()
    matches = [warning for row in rows if (warning := normalize_warning(row, tz)) and _route_matches(warning, route_code)]
    matches.sort(key=_warning_sort_key, reverse=True)
    return FetchWarningResult(
        warning=matches[0] if matches else None,
        stats={"total_rows": len(rows), "matched_rows": len(matches)},
        token=current_token,
        token_expires_at=token_expires_at,
        token_reused=token_reused,
    )


def is_token_valid(token: str, expires_at: str, now: datetime) -> bool:
    if not str(token or "").strip():
        return False
    tz = now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else ZoneInfo("Asia/Shanghai")
    parsed = _parse_datetime(expires_at, tz)
    return bool(parsed and parsed > now)


def token_cache_expires_at(token: str, now: datetime, tz: ZoneInfo, *, fallback_hours: int = 8) -> datetime:
    exp = _jwt_exp(token, tz)
    if exp:
        return max(now, exp - timedelta(minutes=5))
    return now + timedelta(hours=max(1, fallback_hours))


def next_poll_time(now: datetime, interval_minutes: int) -> datetime:
    interval = max(1, int(interval_minutes))
    jitter_max = max(1, int(interval * 0.2))
    return now + timedelta(minutes=interval + random.randint(0, jitter_max))


def failure_backoff_until(now: datetime, failure_count: int) -> datetime:
    failures = max(1, int(failure_count))
    delay_minutes = min(60, 5 * (2 ** min(failures - 1, 4)))
    return now + timedelta(minutes=delay_minutes)


def normalize_warning(row: dict[str, Any], tz: ZoneInfo) -> PatrolWarning | None:
    if not isinstance(row, dict):
        return None
    route_code = str(_first_value(row, "RouteCode", "routeCode", "RouteNumber", "routeNumber") or "").strip()
    route_name = str(_first_value(row, "RouteName", "routeName", "SectionName", "Name", "name") or "").strip()
    raw_warning_level = str(_first_value(row, "WarningLevel", "warningLevel", "Level", "level") or "").strip()
    warning_level = _warning_level_from_text_fields(row) or raw_warning_level
    start_time = _parse_datetime(_first_value(row, "StartTime", "startTime", "BeginTime", "beginTime"), tz)
    end_time = _parse_datetime(_first_value(row, "EndTime", "endTime", "FinishTime", "finishTime"), tz)
    create_time = _parse_datetime(_first_value(row, "CreateTime", "createTime", "PublishTime", "publishTime"), tz)
    start_stake = _format_stake(_first_value(row, "StartStake", "startStake", "BeginStake", "beginStake", "StakeStart"))
    end_stake = _format_stake(_first_value(row, "EndStake", "endStake", "FinishStake", "finishStake", "StakeEnd"))
    key = str(_first_value(row, "Id", "id", "WarningInfoId", "warningInfoId") or "").strip()
    if not key:
        key = "|".join(
            [
                route_code,
                raw_warning_level,
                start_time.isoformat() if start_time else "",
                end_time.isoformat() if end_time else "",
                start_stake,
                end_stake,
            ]
        )
    if not route_code and not key:
        return None
    return PatrolWarning(
        key=key,
        route_code=route_code,
        route_name=route_name,
        warning_level=warning_level,
        warning_level_label=LEVEL_LABELS.get(warning_level, f"{warning_level}级预警" if warning_level else "预警"),
        warn_type_name=str(_first_value(row, "WarnTypeName", "warnTypeName", "WarningTypeName", "warningTypeName") or "").strip(),
        patrol_frequency_text=_patrol_frequency_text(row),
        start_time=start_time,
        end_time=end_time,
        create_time=create_time,
        start_stake=start_stake,
        end_stake=end_stake,
        raw=row,
    )


def _warning_level_from_text_fields(row: dict[str, Any]) -> str:
    for field in LEVEL_TEXT_FIELDS:
        value = _warning_level_from_text(_first_value(row, field))
        if value:
            return value
    return ""


def _warning_level_from_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "红" in text or "red" in text:
        return "4"
    if "橙" in text or "orange" in text:
        return "3"
    if "黄" in text or "yellow" in text:
        return "2"
    if "蓝" in text or "blue" in text:
        return "1"
    return ""


def build_start_message(
    warning: PatrolWarning,
    *,
    app_name: str = "公路巡查APP",
    mention_all: bool = True,
    template: str = "",
) -> str:
    if template:
        return _render_warning_template(
            template,
            warning,
            app_name=app_name,
            mention_all=mention_all,
        )
    prefix = "@所有人\n" if mention_all else ""
    lines = [
        f"{prefix}请注意监测到 {app_name} 发布 {warning.warning_level_label}",
        f"路线：{_route_text(warning)}",
        f"预警开始时间：{_format_datetime(warning.start_time)}",
        f"桩号：{_stake_range(warning)}",
    ]
    if warning.warn_type_name:
        lines.insert(2, f"预警类型：{warning.warn_type_name}")
    return "\n".join(lines)


def build_end_reminder_message(
    warning: PatrolWarning,
    *,
    now: datetime,
    window_hours: int,
    app_name: str = "公路巡查APP",
    mention_all: bool = True,
    template: str = "",
) -> str:
    if template:
        return _render_warning_template(
            template,
            warning,
            now=now,
            window_hours=window_hours,
            app_name=app_name,
            mention_all=mention_all,
        )
    prefix = "@所有人\n" if mention_all else ""
    elapsed_hours = _hours_between(warning.end_time, now)
    deadline = warning.end_time + timedelta(hours=window_hours) if warning.end_time else None
    remaining_hours = max(0, _ceil_hours_between(now, deadline)) if deadline else 0
    lines = [
        f"{prefix}最新{warning.warning_level_label}已结束",
        f"路线：{_route_text(warning)}",
        f"预警开始时间：{_format_datetime(warning.start_time)}",
        f"桩号：{_stake_range(warning)}",
        f"预警结束时间：{_format_datetime(warning.end_time)}",
        f"预警已结束：{elapsed_hours} 小时",
        f"距离预警结束后{window_hours}小时内{_patrol_frequency_clause(warning)}，倒计时结束还有 {remaining_hours} 小时。",
    ]
    if warning.warn_type_name:
        lines.insert(2, f"预警类型：{warning.warn_type_name}")
    return "\n".join(lines)


def _render_warning_template(
    template: str,
    warning: PatrolWarning,
    *,
    now: datetime | None = None,
    window_hours: int = 48,
    app_name: str = "公路巡查APP",
    mention_all: bool = True,
) -> str:
    tzinfo = (
        warning.start_time.tzinfo
        if warning.start_time and warning.start_time.tzinfo
        else ZoneInfo("Asia/Shanghai")
    )
    current = now or datetime.now(tzinfo)
    deadline = warning.end_time + timedelta(hours=window_hours) if warning.end_time else None
    values = {
        "mention_prefix": "@所有人\n" if mention_all else "",
        "app_name": app_name,
        "route_code": warning.route_code or "",
        "route_name": warning.route_name or "",
        "route_text": _route_text(warning),
        "warning_level": warning.warning_level or "",
        "warning_level_label": warning.warning_level_label or "",
        "warn_type_name": warning.warn_type_name or "",
        "patrol_frequency_text": warning.patrol_frequency_text or "",
        "patrol_frequency_clause": _patrol_frequency_clause(warning),
        "start_time": _format_datetime(warning.start_time),
        "end_time": _format_datetime(warning.end_time),
        "create_time": _format_datetime(warning.create_time),
        "start_stake": warning.start_stake or "-",
        "end_stake": warning.end_stake or "-",
        "stake_range": _stake_range(warning),
        "elapsed_hours": str(_hours_between(warning.end_time, current)),
        "window_hours": str(window_hours),
        "remaining_hours": str(max(0, _ceil_hours_between(current, deadline)) if deadline else 0),
        "warning_key": warning.key,
    }
    content = template
    for key, value in values.items():
        content = content.replace("{" + key + "}", str(value))
    return content


def due_end_reminder_slot(
    warning: PatrolWarning,
    *,
    now: datetime,
    interval_hours: int,
    window_hours: int,
) -> datetime | None:
    if not warning.end_time or now < warning.end_time:
        return None
    deadline = warning.end_time + timedelta(hours=window_hours)
    if now > deadline:
        return None
    interval_seconds = max(1, interval_hours) * 3600
    elapsed_seconds = max(0, int((now - warning.end_time).total_seconds()))
    bucket = elapsed_seconds // interval_seconds
    return warning.end_time + timedelta(seconds=bucket * interval_seconds)


def should_poll(last_checked_at: str, now: datetime, interval_minutes: int) -> bool:
    last_checked = _parse_datetime(last_checked_at, now.tzinfo if isinstance(now.tzinfo, ZoneInfo) else ZoneInfo("Asia/Shanghai"))
    if not last_checked:
        return True
    return now - last_checked >= timedelta(minutes=max(1, interval_minutes))


async def _login(config: dict[str, Any]) -> str:
    login_url = str(config.get("login_url") or "").strip()
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")
    if not login_url or not username or not password:
        raise PatrolWarningError("请先填写登录接口地址、账号和密码")
    headers = _request_headers(config)
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(login_url, headers=headers, data={"userName": username, "password": password})
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise PatrolWarningError(
            f"公路巡查APP登录请求失败：HTTP {exc.response.status_code}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.HTTPError as exc:
        raise PatrolWarningError(f"公路巡查APP登录请求失败：{exc.__class__.__name__}") from exc
    except ValueError as exc:
        raise PatrolWarningError("公路巡查APP登录响应不是JSON") from exc
    token = (
        _nested_value(data, "Data", "Token")
        or _nested_value(data, "data", "token")
        or _nested_value(data, "data", "Token")
        or data.get("Token")
        or data.get("token")
    )
    if not token:
        message = data.get("Message") or data.get("message") or data.get("msg") or "未返回Token"
        raise PatrolWarningError(f"公路巡查APP登录失败：{message}")
    return str(token)


async def _fetch_rows(config: dict[str, Any], token: str) -> list[dict[str, Any]]:
    warning_url = str(config.get("warning_url") or "").strip()
    if not warning_url:
        raise PatrolWarningError("请先填写预警列表接口地址")
    headers = {**_request_headers(config), "Authorization": f"Bearer${token}"}
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(
                warning_url,
                headers=headers,
                data={"page": 1, "rows": int(config.get("rows") or 5000)},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise PatrolWarningError(
            f"公路巡查APP预警查询失败：HTTP {exc.response.status_code}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.HTTPError as exc:
        raise PatrolWarningError(f"公路巡查APP预警查询失败：{exc.__class__.__name__}") from exc
    except ValueError as exc:
        raise PatrolWarningError("公路巡查APP预警响应不是JSON") from exc
    rows = data.get("rows")
    if rows is None and isinstance(data.get("data"), dict):
        rows = data["data"].get("rows") or data["data"].get("list")
    if rows is None and isinstance(data.get("Data"), dict):
        rows = data["Data"].get("rows") or data["Data"].get("list")
    if rows is None and isinstance(data.get("data"), list):
        rows = data.get("data")
    if rows is None and isinstance(data.get("Data"), list):
        rows = data.get("Data")
    if not isinstance(rows, list):
        raise PatrolWarningError("公路巡查APP预警响应中没有rows列表")
    return [row for row in rows if isinstance(row, dict)]


def _request_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"content-type": "application/x-www-form-urlencoded"}
    platform = str(config.get("platform") or "").strip()
    project_id = str(config.get("project_id") or "").strip()
    if platform:
        headers["platform"] = platform
    if project_id:
        headers["project-id"] = project_id
    return headers


def _route_matches(warning: PatrolWarning, route_code: str) -> bool:
    if not route_code:
        return True
    candidates = [warning.route_code, warning.route_name]
    candidates.extend(str(warning.raw.get(key) or "") for key in ("RouteNumber", "routeNumber", "SectionName", "Name"))
    return any(str(value).strip().upper() == route_code for value in candidates if value)


def _warning_sort_key(warning: PatrolWarning) -> tuple[datetime, str]:
    event_time = warning.start_time or warning.create_time or warning.end_time or datetime.min.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return event_time, warning.key


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _nested_value(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _jwt_exp(token: str, tz: ZoneInfo) -> datetime | None:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    exp = data.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(float(exp), tz)


def _parse_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_datetime(int(text), tz)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(tz) if parsed.tzinfo else parsed.replace(tzinfo=tz)


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"


def _format_stake(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"K{float(value):.3f}"
    except (TypeError, ValueError):
        text = str(value).strip()
        return text if text.upper().startswith("K") else f"K{text}"


def _stake_range(warning: PatrolWarning) -> str:
    if warning.start_stake == "-" and warning.end_stake == "-":
        return "-"
    return f"{warning.start_stake} - {warning.end_stake}"


def _route_text(warning: PatrolWarning) -> str:
    if warning.route_code and warning.route_name:
        return f"{warning.route_code} {warning.route_name}"
    return warning.route_code or warning.route_name or "-"


def _patrol_frequency_text(row: dict[str, Any]) -> str:
    value = _first_value(
        row,
        "PatrolFrequencyText",
        "patrolFrequencyText",
        "预警结束后48小时内应巡查频次",
        "应巡查频次",
        "巡查频次",
        "InspectionFrequencyText",
        "inspectionFrequencyText",
        "FrequencyText",
        "frequencyText",
        "PatrolFrequency",
        "patrolFrequency",
        "InspectionFrequency",
        "inspectionFrequency",
        "Frequency",
        "frequency",
        "PatrolInterval",
        "patrolInterval",
        "InspectionInterval",
        "inspectionInterval",
        "CheckInterval",
        "checkInterval",
        "Interval",
        "interval",
        "PatrolCycle",
        "patrolCycle",
        "InspectionCycle",
        "inspectionCycle",
        "Cycle",
        "cycle",
        "PatrolPeriod",
        "patrolPeriod",
        "InspectionPeriod",
        "inspectionPeriod",
    )
    if value in (None, ""):
        route_type = str(_first_value(row, "PatrolRouteType", "patrolRouteType") or "").strip()
        return {"1": "2小时1次"}.get(route_type, "")
    if isinstance(value, (int, float)) and float(value) > 0:
        return f"{int(float(value)) if float(value).is_integer() else value}小时1次"
    text = str(value).strip()
    if not text:
        return ""
    numeric = re.search(r"\d+(?:\.\d+)?", text)
    if numeric and "小时" not in text and "时" not in text and "次" not in text:
        number = numeric.group(0).rstrip("0").rstrip(".")
        return f"{number}小时1次"
    return text.lstrip("每")


def _patrol_frequency_clause(warning: PatrolWarning) -> str:
    text = str(warning.patrol_frequency_text or "").strip()
    if not text:
        return "按预警要求巡查"
    return text if text.endswith("巡查") else f"{text}都巡查"


def _hours_between(start: datetime | None, end: datetime) -> int:
    if not start:
        return 0
    return max(0, int((end - start).total_seconds() // 3600))


def _ceil_hours_between(start: datetime, end: datetime | None) -> int:
    if not end or end <= start:
        return 0
    seconds = int((end - start).total_seconds())
    return (seconds + 3599) // 3600
