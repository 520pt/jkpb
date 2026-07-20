from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.reminders import ReminderSettings, plan_reminders_for_day
from app.roster import Shift, ShiftAssignment


TZ = ZoneInfo("Asia/Shanghai")


def test_generates_daily_fixed_reminder_for_monitored_person_on_duty():
    assignment = ShiftAssignment("示例甲", date(2025, 9, 16), Shift.MIDDLE)
    events = plan_reminders_for_day(
        target_date=date(2025, 9, 16),
        assignments=[assignment],
        monitored_name="示例甲",
        mention_text="@示例甲",
        settings=ReminderSettings(daily_time="07:50", before_shift_minutes=10),
        tz=TZ,
    )

    assert events[0].kind == "daily"
    assert events[0].send_at == datetime(2025, 9, 16, 7, 50, tzinfo=TZ)
    assert "示例甲 2025-09-16" in events[0].content


def test_generates_before_shift_reminder_with_configured_minutes():
    assignment = ShiftAssignment("示例甲", date(2025, 9, 16), Shift.MIDDLE)
    events = plan_reminders_for_day(
        target_date=date(2025, 9, 16),
        assignments=[assignment],
        monitored_name="示例甲",
        mention_text="@示例甲",
        settings=ReminderSettings(daily_time="07:50", before_shift_minutes=15),
        tz=TZ,
    )

    before = [event for event in events if event.kind == "before_shift"][0]
    assert before.send_at == datetime(2025, 9, 16, 7, 45, tzinfo=TZ)


def test_early_shift_before_reminder_is_previous_day():
    assignment = ShiftAssignment("示例甲", date(2025, 9, 16), Shift.EARLY)
    events = plan_reminders_for_day(
        target_date=date(2025, 9, 15),
        assignments=[assignment],
        monitored_name="示例甲",
        mention_text="@示例甲",
        settings=ReminderSettings(daily_time="07:50", before_shift_minutes=10),
        tz=TZ,
    )

    before = [event for event in events if event.kind == "before_shift"][0]
    assert before.send_at == datetime(2025, 9, 15, 23, 50, tzinfo=TZ)
    assert "2025-09-16（00:00至08:00)" in before.content


def test_no_events_when_monitored_person_is_not_on_duty():
    assignment = ShiftAssignment("示例丁", date(2025, 9, 16), Shift.NIGHT)
    events = plan_reminders_for_day(
        target_date=date(2025, 9, 16),
        assignments=[assignment],
        monitored_name="示例甲",
        mention_text="@示例甲",
        settings=ReminderSettings(daily_time="07:50", before_shift_minutes=10),
        tz=TZ,
    )

    assert events == []


