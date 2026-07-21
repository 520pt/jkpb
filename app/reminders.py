from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.roster import Shift, ShiftAssignment

DEFAULT_MESSAGE_TEMPLATE = "{name} {date}（{time_range})是你的{shift_label}"


@dataclass(frozen=True)
class ReminderSettings:
    daily_time: str = "07:50"
    before_shift_minutes: int = 10
    message_template: str = DEFAULT_MESSAGE_TEMPLATE


@dataclass(frozen=True)
class ReminderEvent:
    kind: str
    person_name: str
    send_at: datetime
    content: str
    mention_mobile: str = ""
    key_suffix: str = ""


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _anchor_date(assignment: ShiftAssignment) -> date:
    if assignment.shift is Shift.EARLY:
        return assignment.work_date - timedelta(days=1)
    return assignment.work_date


def _shift_start(assignment: ShiftAssignment, tz: ZoneInfo) -> datetime:
    return datetime.combine(assignment.work_date, assignment.shift.start_time, tzinfo=tz)


def render_assignment_message(assignment: ShiftAssignment, template: str = DEFAULT_MESSAGE_TEMPLATE, mention_text: str = "") -> str:
    content = template or DEFAULT_MESSAGE_TEMPLATE
    values = {
        "name": assignment.person_name,
        "date": f"{assignment.work_date:%Y-%m-%d}",
        "time_range": assignment.time_range_text,
        "shift_label": assignment.shift.label,
    }
    for key, value in values.items():
        content = content.replace("{" + key + "}", value)
    if mention_text:
        content += f"\n{mention_text}"
    return content


def plan_reminders_for_day(
    *,
    target_date: date,
    assignments: list[ShiftAssignment],
    monitored_name: str,
    mention_text: str,
    settings: ReminderSettings,
    tz: ZoneInfo,
) -> list[ReminderEvent]:
    events: list[ReminderEvent] = []
    daily_time = _parse_hhmm(settings.daily_time)

    for assignment in assignments:
        if assignment.person_name != monitored_name:
            continue

        anchor = _anchor_date(assignment)
        if anchor == target_date:
            events.append(
                ReminderEvent(
                    kind="daily",
                    person_name=assignment.person_name,
                    send_at=datetime.combine(anchor, daily_time, tzinfo=tz),
                    content=render_assignment_message(assignment, settings.message_template, mention_text),
                )
            )

        before_at = _shift_start(assignment, tz) - timedelta(minutes=settings.before_shift_minutes)
        if before_at.date() == target_date:
            events.append(
                ReminderEvent(
                    kind="before_shift",
                    person_name=assignment.person_name,
                    send_at=before_at,
                    content=render_assignment_message(assignment, settings.message_template, mention_text),
                )
            )

    return sorted(events, key=lambda event: event.send_at)
