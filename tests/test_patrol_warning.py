import asyncio
import base64
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app.patrol_warning as patrol_module
from app.main import _patrol_record_group_count
from app.patrol_warning_image import _image_title, _patrol_summary_text, _status_text, render_patrol_warning_image
from app.patrol_record_image import _record_groups
from app.patrol_warning import (
    build_end_reminder_message,
    build_start_message,
    due_end_reminder_slot,
    fetch_patrol_records_by_name_result,
    failure_backoff_until,
    is_token_valid,
    normalize_patrol_record,
    normalize_warning,
    next_poll_time,
    token_cache_expires_at,
    warning_from_dict,
)


TZ = ZoneInfo("Asia/Shanghai")


def test_normalizes_patrol_warning_fields_from_api_row():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "RouteName": "南涧－宁洱",
            "WarningLevel": "3",
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


def test_normalizes_patrol_record_fields_from_api_row():
    record = normalize_patrol_record(
        {
            "Id": "record-1",
            "RouteNumber": "S41",
            "RouteName": "南涧－宁洱",
            "InspectionAllDirection": "2",
            "StartingStake": 106.67,
            "EndStake": 137.42,
            "ResponsiblePerson": "李文杰 张三",
            "Recorder": "陈刚",
            "InspectionAlstime": "2026-07-26T10:44:21+08:00",
            "InspectionAletime": "2026-07-26T11:02:00+08:00",
        },
        TZ,
    )

    assert record is not None
    assert record["id"] == "record-1"
    assert record["route_code"] == "S41"
    assert record["direction"] == "下行"
    assert record["stake_range"] == "106.67 ~ 137.42"
    assert record["responsible_person"] == "李文杰 张三"
    assert record["recorder"] == "陈刚"
    assert record["status"] == "已完成"


def test_patrol_record_groups_pair_adjacent_end_and_start_times():
    records = [
        {
            "id": "r-1",
            "start_time": "2026-07-13T08:01:00+08:00",
            "end_time": "2026-07-13T09:29:00+08:00",
            "direction": "上行",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
        {
            "id": "r-2",
            "start_time": "2026-07-13T09:30:00+08:00",
            "end_time": "2026-07-13T10:02:00+08:00",
            "direction": "下行",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
    ]

    groups = _record_groups(records)

    assert len(groups) == 1
    assert groups[0]["count"] == 1
    assert [record["id"] for record in groups[0]["records"]] == ["r-1", "r-2"]


def test_patrol_record_groups_pair_adjacent_records_across_midnight():
    records = [
        {
            "id": "r-before-midnight",
            "start_time": "2026-07-12T23:57:00+08:00",
            "end_time": "2026-07-13T00:37:00+08:00",
            "direction": "上行",
        },
        {
            "id": "r-after-midnight",
            "start_time": "2026-07-13T00:38:00+08:00",
            "end_time": "2026-07-13T01:13:00+08:00",
            "direction": "下行",
        },
    ]

    groups = _record_groups(records)

    assert len(groups) == 1
    assert groups[0]["count"] == 1
    assert [record["id"] for record in groups[0]["records"]] == [
        "r-before-midnight",
        "r-after-midnight",
    ]
    assert _patrol_record_group_count(records) == 1


def test_patrol_record_groups_handle_multiple_pairs_and_singletons():
    records = [
        {
            "id": "r-1",
            "start_time": "2026-07-13T08:01:00+08:00",
            "end_time": "2026-07-13T08:45:00+08:00",
            "direction": "上行",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
        {
            "id": "r-2",
            "start_time": "2026-07-13T08:45:30+08:00",
            "end_time": "2026-07-13T09:02:00+08:00",
            "direction": "下行",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
        {
            "id": "r-3",
            "start_time": "2026-07-13T10:00:00+08:00",
            "end_time": "2026-07-13T10:30:00+08:00",
            "direction": "双向",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
        {
            "id": "r-4",
            "start_time": "2026-07-13T11:00:00+08:00",
            "end_time": "2026-07-13T11:28:00+08:00",
            "direction": "上行",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
        {
            "id": "r-5",
            "start_time": "2026-07-13T11:28:40+08:00",
            "end_time": "2026-07-13T11:50:00+08:00",
            "direction": "下行",
            "responsible_person": "罗森",
            "recorder": "张三",
        },
    ]

    groups = _record_groups(records)

    assert len(groups) == 3
    assert [group["count"] for group in groups] == [1, 2, 3]
    assert [[record["id"] for record in group["records"]] for group in groups] == [
        ["r-1", "r-2"],
        ["r-3"],
        ["r-4", "r-5"],
    ]
    assert _patrol_record_group_count(records) == 3


def test_patrol_record_query_filters_route_and_name(monkeypatch):
    async def fake_fetch_record_rows(config, token, *, known_keys, limit, use_incremental, tz):
        return [
            {
                "Id": "match-responsible",
                "RouteNumber": "S41",
                "ResponsiblePerson": "李文杰 张三",
                "Recorder": "陈刚",
                "InspectionAlstime": "2026-07-26T10:44:21+08:00",
            },
            {
                "Id": "match-recorder",
                "RouteNumber": "S41",
                "ResponsiblePerson": "罗森",
                "Recorder": "张三",
                "InspectionAlstime": "2026-07-25T10:44:21+08:00",
            },
            {
                "Id": "wrong-route",
                "RouteNumber": "G214",
                "ResponsiblePerson": "张三",
                "Recorder": "陈刚",
                "InspectionAlstime": "2026-07-24T10:44:21+08:00",
            },
        ], 3, True

    monkeypatch.setattr(patrol_module, "_fetch_patrol_record_rows_for_cache", fake_fetch_record_rows)
    result = asyncio.run(
        fetch_patrol_records_by_name_result(
            {"route_code": "S41"},
            TZ,
            name="张三",
            token="cached-token",
            token_expires_at="2026-07-22T22:00:00+08:00",
            now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        )
    )

    assert [record["id"] for record in result.records] == ["match-responsible", "match-recorder", "wrong-route"]
    assert result.stats["total_rows"] == 3
    assert result.stats["loaded_rows"] == 3
    assert result.stats["route_matched_rows"] == 3
    assert result.stats["matched_rows"] == 3
    assert result.stats["cache_used"] == 0


def test_patrol_record_query_reuses_local_cache_and_fetches_incrementally(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []
    remote_pages = [
        [
            {
                "Id": "old-record",
                "RouteNumber": "S41",
                "ResponsiblePerson": "张三",
                "Recorder": "陈刚",
                "InspectionAlstime": "2026-07-25T10:44:21+08:00",
            }
        ],
        [
            {
                "Id": "new-record",
                "RouteNumber": "S41",
                "ResponsiblePerson": "张三",
                "Recorder": "陈刚",
                "InspectionAlstime": "2026-07-26T10:44:21+08:00",
            }
        ],
    ]

    async def fake_fetch_record_rows(config, token, *, known_keys, limit, use_incremental, tz):
        calls.append({"known_keys": set(known_keys), "use_incremental": use_incremental})
        index = min(len(calls) - 1, len(remote_pages) - 1)
        return remote_pages[index], len(remote_pages[index]), not use_incremental

    monkeypatch.setattr(patrol_module, "_fetch_patrol_record_rows_for_cache", fake_fetch_record_rows)
    cache_path = tmp_path / "patrol-warning-records-cache.json"

    first = asyncio.run(
        fetch_patrol_records_by_name_result(
            {"warning_url": "https://example.test/mobile/warninginfo/findPage"},
            TZ,
            name="张三",
            token="cached-token",
            token_expires_at="2026-07-22T22:00:00+08:00",
            now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
            cache_path=cache_path,
        )
    )
    second = asyncio.run(
        fetch_patrol_records_by_name_result(
            {"warning_url": "https://example.test/mobile/warninginfo/findPage"},
            TZ,
            name="张三",
            token="cached-token",
            token_expires_at="2026-07-22T22:00:00+08:00",
            now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
            cache_path=cache_path,
        )
    )

    assert calls[0]["use_incremental"] is False
    assert calls[1]["use_incremental"] is True
    assert calls[1]["known_keys"]
    assert [record["id"] for record in first.records] == ["old-record"]
    assert [record["id"] for record in second.records] == ["new-record", "old-record"]
    assert second.stats["cached_rows"] == 1
    assert second.stats["new_rows"] == 1


def test_patrol_record_query_uses_cache_when_login_fails(tmp_path, monkeypatch):
    async def fake_fetch_record_rows(config, token, *, known_keys, limit, use_incremental, tz):
        return [
            {
                "Id": "cached-record",
                "RouteNumber": "S41",
                "ResponsiblePerson": "张三",
                "Recorder": "陈刚",
                "InspectionAlstime": "2026-07-25T10:44:21+08:00",
            }
        ], 1, True

    async def fake_login(config):
        raise patrol_module.PatrolWarningError("登录失败")

    monkeypatch.setattr(patrol_module, "_fetch_patrol_record_rows_for_cache", fake_fetch_record_rows)
    cache_path = tmp_path / "patrol-warning-records-cache.json"
    asyncio.run(
        fetch_patrol_records_by_name_result(
            {"warning_url": "https://example.test/mobile/warninginfo/findPage"},
            TZ,
            name="张三",
            token="cached-token",
            token_expires_at="2026-07-22T22:00:00+08:00",
            now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
            cache_path=cache_path,
        )
    )
    monkeypatch.setattr(patrol_module, "_login", fake_login)

    result = asyncio.run(
        fetch_patrol_records_by_name_result(
            {"warning_url": "https://example.test/mobile/warninginfo/findPage"},
            TZ,
            name="张三",
            token="",
            token_expires_at="",
            now=datetime.fromisoformat("2026-07-23T08:00:00+08:00"),
            cache_path=cache_path,
        )
    )

    assert [record["id"] for record in result.records] == ["cached-record"]
    assert result.stats["cache_used"] == 1
    assert result.stats["fetched_rows"] == 0


def test_warning_level_prefers_color_text_over_numeric_code():
    warning = normalize_warning(
        {
            "Id": "warning-orange-name",
            "RouteCode": "S41",
            "WarningLevel": "3",
            "WarnTypeName": "暴雨橙色预警",
            "StartTime": "2026-07-21 22:35:01",
        },
        TZ,
    )

    assert warning is not None
    assert warning.warning_level == "3"
    assert warning.warning_level_label == "橙色预警"


def test_warning_level_numeric_code_uses_patrol_platform_order():
    yellow = normalize_warning({"Id": "warning-yellow", "RouteCode": "S41", "WarningLevel": "2"}, TZ)
    orange = normalize_warning({"Id": "warning-orange", "RouteCode": "S41", "WarningLevel": "3"}, TZ)

    assert yellow is not None
    assert yellow.warning_level_label == "黄色预警"
    assert orange is not None
    assert orange.warning_level_label == "橙色预警"


def test_saved_warning_recomputes_level_from_raw_platform_code():
    warning = warning_from_dict(
        {
            "key": "warning-orange",
            "route_code": "S41",
            "warning_level": "3",
            "warning_level_label": "黄色预警",
            "raw": {"WarningLevel": "3", "WarnTypeName": "暴雨预警"},
        },
        TZ,
    )

    assert warning is not None
    assert warning.warning_level == "3"
    assert warning.warning_level_label == "橙色预警"


def test_builds_start_and_end_messages_from_warning_fields():
    warning = normalize_warning(
        {
            "Id": "warning-1",
            "RouteCode": "S41",
            "RouteName": "南涧－宁洱",
            "WarningLevel": "3",
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
            "WarningLevel": "3",
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
            "WarningLevel": "3",
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
