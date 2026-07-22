import base64
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app.patrol_warning as patrol_module
from app.patrol_warning_image import _image_title, _patrol_summary_text, _status_text, render_patrol_warning_image
from app.patrol_warning import (
    build_end_reminder_message,
    build_start_message,
    due_end_reminder_slot,
    failure_backoff_until,
    is_token_valid,
    normalize_warning,
    next_poll_time,
    token_cache_expires_at,
)


TZ = ZoneInfo("Asia/Shanghai")


def test_normalizes_patrol_warning_fields_from_api_row():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "RouteName": "南涧－宁洱",
            "WarningLevel": "2",
            "WarnTypeName": "暴雨预警",
            "PatrolRouteType": 1,
            "StartStake": 107.0,
            "EndStake": 137.73,
            "StartTime": 1784644501000,
            "EndTime": 1784655303000,
        },
        TZ,
    )

    assert warning is not None
    assert warning.key == "warning-1"
    assert warning.route_code == "S41"
    assert warning.warning_level_label == "橙色预警"
    assert warning.patrol_frequency_text == "2小时1次"
    assert warning.start_stake == "K107.000"
    assert warning.end_stake == "K137.730"
    assert warning.start_time.isoformat() == "2026-07-21T22:35:01+08:00"
    assert warning.end_time.isoformat() == "2026-07-22T01:35:03+08:00"


def test_builds_start_and_end_messages_from_warning_fields():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "RouteName": "南涧－宁洱",
            "WarningLevel": "2",
            "PatrolRouteType": 1,
            "StartStake": 107,
            "EndStake": 137.73,
            "StartTime": 1784644501000,
            "EndTime": 1784655303000,
        },
        TZ,
    )
    now = datetime.fromisoformat("2026-07-22T07:40:00+08:00")

    start_message = build_start_message(warning)
    end_message = build_end_reminder_message(warning, now=now, window_hours=48)

    assert "@所有人" in start_message
    assert "橙色预警" in start_message
    assert "K107.000 - K137.730" in start_message
    assert "最新橙色预警已结束" in end_message
    assert "预警结束时间：2026-07-22 01:35:03" in end_message
    assert "预警已结束：6 小时" in end_message
    assert "距离预警结束后48小时内2小时1次都巡查，倒计时结束还有 42 小时。" in end_message


def test_builds_patrol_warning_messages_from_custom_templates():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "RouteName": "南涧－宁洱",
            "WarningLevel": "2",
            "WarnTypeName": "暴雨预警",
            "PatrolFrequencyText": "2小时1次",
            "StartStake": 107,
            "EndStake": 137.73,
            "StartTime": 1784644501000,
            "EndTime": 1784655303000,
        },
        TZ,
    )
    now = datetime.fromisoformat("2026-07-22T07:40:00+08:00")

    start_message = build_start_message(
        warning,
        mention_all=False,
        template="{app_name}|{warning_level_label}|{route_text}|{warn_type_name}|{start_time}|{stake_range}",
    )
    end_message = build_end_reminder_message(
        warning,
        now=now,
        window_hours=48,
        mention_all=True,
        template="{mention_prefix}{end_time}|{elapsed_hours}|{remaining_hours}|{patrol_frequency_text}|{patrol_frequency_clause}",
    )

    assert start_message == "公路巡查APP|橙色预警|S41 南涧－宁洱|暴雨预警|2026-07-21 22:35:01|K107.000 - K137.730"
    assert end_message == "@所有人\n2026-07-22 01:35:03|6|42|2小时1次|2小时1次都巡查"


def test_due_end_reminder_slot_advances_by_configured_interval():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "WarningLevel": "3",
            "EndTime": "2026-07-22T01:00:00+08:00",
        },
        TZ,
    )

    assert due_end_reminder_slot(
        warning,
        now=datetime.fromisoformat("2026-07-22T00:59:00+08:00"),
        interval_hours=6,
        window_hours=48,
    ) is None
    assert due_end_reminder_slot(
        warning,
        now=datetime.fromisoformat("2026-07-22T07:05:00+08:00"),
        interval_hours=6,
        window_hours=48,
    ).isoformat() == "2026-07-22T07:00:00+08:00"


def test_patrol_warning_image_status_distinguishes_warning_and_patrol_window():
    end_time = datetime.fromisoformat("2026-07-22T01:00:00+08:00")

    assert _status_text(end_time, datetime.fromisoformat("2026-07-22T00:59:00+08:00"), 48) == "\u9884\u8b66\u672a\u7ed3\u675f"
    assert _status_text(end_time, datetime.fromisoformat("2026-07-22T07:00:00+08:00"), 48) == "\u9884\u8b66\u5df2\u7ed3\u675f"
    assert _status_text(end_time, datetime.fromisoformat("2026-07-24T01:00:00+08:00"), 48) == "\u5de1\u67e5\u7ed3\u675f"


def test_patrol_warning_end_image_uses_distinct_title_and_frequency():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "RouteName": "南涧－宁洱",
            "WarningLevel": "2",
            "PatrolRouteType": 1,
            "StartStake": 107,
            "EndStake": 137.73,
            "StartTime": 1784644501000,
            "EndTime": 1784655303000,
        },
        TZ,
    )
    now = datetime.fromisoformat("2026-07-22T19:40:00+08:00")

    assert _image_title(warning, "end") == "最新橙色预警已结束"
    assert _patrol_summary_text(warning, 48, "end") == "预警结束后 48 小时内2小时1次都巡查"
    assert render_patrol_warning_image(warning, now=now, window_hours=48, mode="end").startswith(b"\x89PNG")


def test_token_cache_uses_jwt_exp_with_safety_margin():
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    exp = int((now + timedelta(hours=2)).timestamp())
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode("utf-8")).rstrip(b"=").decode("ascii")
    token = f"header.{payload}.signature"

    expires_at = token_cache_expires_at(token, now, TZ)

    assert expires_at.isoformat() == "2026-07-22T09:55:00+08:00"
    assert is_token_valid(token, expires_at.isoformat(), now) is True
    assert is_token_valid(token, "2026-07-22T07:59:59+08:00", now) is False


def test_poll_jitter_and_backoff_helpers(monkeypatch):
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    monkeypatch.setattr(patrol_module.random, "randint", lambda start, end: end)

    assert next_poll_time(now, 10).isoformat() == "2026-07-22T08:12:00+08:00"
    assert failure_backoff_until(now, 1).isoformat() == "2026-07-22T08:05:00+08:00"
    assert failure_backoff_until(now, 4).isoformat() == "2026-07-22T08:40:00+08:00"
    assert failure_backoff_until(now, 8).isoformat() == "2026-07-22T09:00:00+08:00"
