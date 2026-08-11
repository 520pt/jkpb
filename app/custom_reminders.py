from __future__ import annotations


CUSTOM_REMINDER_TIME_RULES: dict[str, tuple[str, str, str, str]] = {
    "early": ("早班", "00:00", "08:00", "07:50"),
    "middle": ("中班", "07:00", "16:00", "07:50"),
    "night": ("晚班", "15:00", "23:59", "21:00"),
}


def _hhmm_minutes(value: str) -> int | None:
    text = str(value or "").strip()
    parts = text.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def custom_reminder_time_rule(shift_code: str) -> tuple[str, str, str, str] | None:
    return CUSTOM_REMINDER_TIME_RULES.get(str(shift_code or "").strip())


def is_custom_reminder_time_allowed(shift_code: str, reminder_time: str) -> bool:
    rule = custom_reminder_time_rule(shift_code)
    if not rule:
        return False
    _, start, end, _ = rule
    value_minutes = _hhmm_minutes(reminder_time)
    start_minutes = _hhmm_minutes(start)
    end_minutes = _hhmm_minutes(end)
    return value_minutes is not None and start_minutes <= value_minutes <= end_minutes


def normalize_custom_reminder_time_for_import(shift_code: str, reminder_time: str) -> str:
    text = str(reminder_time or "").strip()
    rule = custom_reminder_time_rule(shift_code)
    if not rule:
        return text
    if is_custom_reminder_time_allowed(shift_code, text):
        return text
    return rule[3]


def custom_reminder_time_window_text(shift_code: str) -> str:
    rule = custom_reminder_time_rule(shift_code)
    if not rule:
        return "提醒时间不在允许范围内"
    label, start, end, _ = rule
    return f"{label}提醒时间必须在 {start} 至 {end} 之间"
