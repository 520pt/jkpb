from datetime import date, time

from app.roster import Shift, ShiftAssignment, normalize_shift_code


def test_normalizes_supported_shift_codes():
    assert normalize_shift_code("早") == Shift.EARLY
    assert normalize_shift_code("中") == Shift.MIDDLE
    assert normalize_shift_code("晚") == Shift.NIGHT
    assert normalize_shift_code("休") is None
    assert normalize_shift_code("出差") is None


def test_night_shift_message_uses_expected_time_range():
    assignment = ShiftAssignment(
        person_name="示例丁",
        work_date=date(2025, 9, 16),
        shift=Shift.NIGHT,
    )

    assert assignment.time_range_text == "16:00至00:00"
    assert assignment.message("@耀二哥") == "示例丁 2025-09-16（16:00至00:00)是你的夜班\n@耀二哥"


def test_shift_start_and_end_times_are_defined():
    assert Shift.EARLY.start_time == time(0, 0)
    assert Shift.EARLY.end_time == time(8, 0)
    assert Shift.MIDDLE.start_time == time(8, 0)
    assert Shift.MIDDLE.end_time == time(16, 0)
    assert Shift.NIGHT.start_time == time(16, 0)
    assert Shift.NIGHT.end_time == time(0, 0)


