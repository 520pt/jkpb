from pathlib import Path

from app.storage import DEFAULT_DAILY_DUTY_TEMPLATE, DutyRepository


def _personnel_row(name: str, mention_mobile: str = "", **overrides: str) -> dict[str, str]:
    row = {
        "name": name,
        "mention_mobile": mention_mobile,
        "wechat_group_room_id": "",
        "wechat_group_room_name": "",
        "wechat_group_member_id": "",
        "wechat_group_runtime_sender_id": "",
        "wechat_group_member_name": "",
    }
    row.update(overrides)
    return row


def test_initializes_sqlite_schema(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    tables = repo.table_names()

    assert {
        "roster_months",
        "monitored_people",
        "notification_config",
        "feature_channel_config",
        "personnel_names",
        "custom_reminders",
        "daily_duty_config",
        "patrol_warning_config",
        "patrol_warning_state",
        "tunnel_mechanical_config",
        "tunnel_mechanical_state",
        "tunnel_mechanical_template",
        "sent_reminders",
        "send_records",
    } <= tables


def test_saving_roster_replaces_same_month(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.save_roster_month(2025, 9, [{"name": "示例甲", "days": {"16": "中"}}], "uploads/a.png")
    repo.save_roster_month(2025, 9, [{"name": "示例甲", "days": {"16": "晚"}}], "uploads/b.png")

    roster = repo.get_roster_month(2025, 9)

    assert roster is not None
    assert roster["grid"] == [{"name": "示例甲", "days": {"16": "晚"}}]
    assert roster["source_image_path"] == "uploads/b.png"
    assert repo.count_roster_months() == 1


def test_saving_monitored_people_replaces_existing_name(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.save_monitored_person(
        name="示例甲",
        wecom_userid="shangqiuhong",
        mention_text="@示例甲",
        mention_mobile="10000000000",
        daily_time="07:50",
        before_shift_minutes=10,
        rest_reminder_enabled=True,
        rest_reminder_time="08:30",
        rest_message_template="{name} 今天休息",
        enabled=True,
    )
    repo.save_monitored_person(
        name="示例甲",
        wecom_userid="sqh",
        mention_text="@示例甲",
        mention_mobile="13900139000",
        daily_time="07:40",
        before_shift_minutes=15,
        rest_reminder_enabled=False,
        rest_reminder_time="09:00",
        rest_message_template="{name} 不用到岗",
        enabled=False,
    )

    people = repo.list_monitored_people()

    assert people == [
        {
            "name": "示例甲",
            "wecom_userid": "sqh",
            "mention_text": "@示例甲",
            "mention_mobile": "13900139000",
            "daily_time": "07:40",
            "before_shift_minutes": 15,
            "rest_reminder_enabled": False,
            "rest_reminder_time": "09:00",
            "rest_message_template": "{name} 不用到岗",
            "enabled": False,
        }
    ]


def test_monitored_person_can_be_renamed_and_deleted(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.save_monitored_person(name="示例甲", mention_mobile="10000000000")

    repo.save_monitored_person(
        original_name="示例甲",
        name="示例乙",
        mention_mobile="13900139000",
        daily_time="08:10",
        before_shift_minutes=20,
    )

    assert [person["name"] for person in repo.list_monitored_people()] == ["示例乙"]
    assert repo.list_monitored_people()[0]["mention_mobile"] == "13900139000"
    assert repo.delete_monitored_person("示例乙") is True
    assert repo.delete_monitored_person("示例乙") is False
    assert repo.list_monitored_people() == []


def test_roster_import_syncs_personnel_names(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_roster_month(
        2025,
        9,
        [{"name": "示例甲", "days": {"16": "中"}}, {"name": "示例丁", "days": {"16": "早"}}],
        "uploads/a.png",
    )

    assert repo.list_personnel_names() == sorted(["示例甲", "示例丁"])


def test_personnel_names_preserve_saved_mobile_numbers(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.upsert_personnel_contacts([{"name": "示例甲", "mention_mobile": "10000000000"}])
    repo.save_personnel_names(["示例甲", "示例乙"])

    assert repo.list_personnel() == [
        {"name": "示例乙", "mention_mobile": ""},
        {"name": "示例甲", "mention_mobile": "10000000000"},
    ]


def test_personnel_contacts_save_and_clear_wechat_binding(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_personnel_contacts(
        [
            _personnel_row(
                "Alice",
                "10000000000",
                wechat_group_room_id="room-1",
                wechat_group_room_name="test-room",
                wechat_group_member_id="stable-member-1",
                wechat_group_runtime_sender_id="@member-1",
                wechat_group_member_name="Alice WeChat",
            )
        ]
    )

    assert repo.list_personnel() == [
        {
            "name": "Alice",
            "mention_mobile": "10000000000",
            "wechat_group_room_id": "room-1",
            "wechat_group_room_name": "test-room",
            "wechat_group_member_id": "stable-member-1",
            "wechat_group_runtime_sender_id": "@member-1",
            "wechat_group_member_name": "Alice WeChat",
        }
    ]

    repo.save_personnel_contacts([_personnel_row("Alice", "10000000000")])

    assert repo.list_personnel() == [{"name": "Alice", "mention_mobile": "10000000000"}]


def test_clear_wechat_binding_for_member_preserves_mobile_and_except_name(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.save_personnel_contacts(
        [
            _personnel_row(
                "Alice",
                "10000000000",
                wechat_group_member_id="stable-member-1",
                wechat_group_runtime_sender_id="@member-1",
                wechat_group_member_name="Alice WeChat",
            ),
            _personnel_row(
                "Bob",
                "10000000001",
                wechat_group_member_id="stable-member-1",
                wechat_group_runtime_sender_id="@member-1",
                wechat_group_member_name="Bob WeChat",
            ),
        ]
    )

    repo.clear_wechat_binding_for_member(["stable-member-1", "@member-1"], except_name="Bob")

    assert repo.list_personnel() == [
        {"name": "Alice", "mention_mobile": "10000000000"},
        {
            "name": "Bob",
            "mention_mobile": "10000000001",
            "wechat_group_room_id": "",
            "wechat_group_room_name": "",
            "wechat_group_member_id": "stable-member-1",
            "wechat_group_runtime_sender_id": "@member-1",
            "wechat_group_member_name": "Bob WeChat",
        },
    ]


def test_monitored_people_include_saved_wechat_binding(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_personnel_contacts(
        [
            _personnel_row(
                "示例甲",
                "10000000000",
                wechat_group_room_id="room-1",
                wechat_group_room_name="功能群",
                wechat_group_member_id="stable-member-1",
                wechat_group_runtime_sender_id="@member-1",
                wechat_group_member_name="示例甲微信",
            )
        ]
    )
    repo.save_monitored_person(name="示例甲", mention_mobile="10000000000")

    people = repo.list_monitored_people()

    assert people[0]["wechat_group_room_id"] == "room-1"
    assert people[0]["wechat_group_room_name"] == "功能群"
    assert people[0]["wechat_group_member_id"] == "stable-member-1"
    assert people[0]["wechat_group_runtime_sender_id"] == "@member-1"
    assert people[0]["wechat_group_member_name"] == "示例甲微信"


def test_custom_reminder_roundtrip_updates_personnel_contact(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    reminder_id = repo.save_custom_reminder(
        name="示例甲",
        mention_mobile="10000000000",
        wechat_group_room_id="room-1",
        wechat_group_room_name="通知群",
        wechat_group_member_id="stable-member-1",
        wechat_group_runtime_sender_id="@member-1",
        wechat_group_member_name="示例甲微信",
        shift_code="night",
        reminder_time="21:00",
        message="需要关闭隧道灯",
        enabled=True,
    )

    assert repo.list_custom_reminders() == [
        {
            "id": reminder_id,
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "shift_code": "night",
            "reminder_time": "21:00",
            "message": "需要关闭隧道灯",
            "enabled": True,
            "created_at": repo.list_custom_reminders()[0]["created_at"],
            "updated_at": repo.list_custom_reminders()[0]["updated_at"],
            "wechat_group_room_id": "room-1",
            "wechat_group_room_name": "通知群",
            "wechat_group_member_id": "stable-member-1",
            "wechat_group_runtime_sender_id": "@member-1",
            "wechat_group_member_name": "示例甲微信",
        }
    ]
    assert repo.list_personnel() == [
        {
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "wechat_group_room_id": "room-1",
            "wechat_group_room_name": "通知群",
            "wechat_group_member_id": "stable-member-1",
            "wechat_group_runtime_sender_id": "@member-1",
            "wechat_group_member_name": "示例甲微信",
        }
    ]


def test_daily_duty_config_roundtrip(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_daily_duty_config(
        enabled=True,
        reminder_time="07:20",
        big_driver_names=["示例庚"],
        small_driver_names=["示例丙"],
        message_template="今日 {early} {big_drivers}",
    )

    assert repo.get_daily_duty_config() == {
        "enabled": True,
        "reminder_time": "07:20",
        "big_driver_names": ["示例庚"],
        "small_driver_names": ["示例丙"],
        "message_template": "今日 {early} {big_drivers}",
    }


def test_daily_duty_config_upgrades_legacy_default_template(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    legacy_template = (
        "今日在岗人员\n"
        "监控班：早班：{early}，中班：{middle}，晚班：{night}\n"
        "驾驶员：大车：{big_drivers} 小车：{small_drivers}\n"
        "备勤人员：{standby}"
    )

    repo.save_daily_duty_config(message_template=legacy_template)

    assert repo.get_daily_duty_config()["message_template"] == DEFAULT_DAILY_DUTY_TEMPLATE


def test_patrol_warning_config_and_state_roundtrip(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_patrol_warning_config(
        enabled=True,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
        project_id="project-1",
        platform="2",
        route_code="S41",
        poll_interval_minutes=5,
        rows=3000,
        end_reminder_enabled=False,
        end_reminder_interval_hours=6,
        end_reminder_window_hours=48,
        mention_all=True,
        mention_mobiles="13800138000,13900139000",
        send_content_mode="image",
        start_message_template="start {warning_level_label}",
        end_message_template="end {remaining_hours}",
    )
    repo.save_patrol_warning_state(
        warning_key="warning-1",
        warning={"key": "warning-1", "route_code": "S41"},
        last_checked_at="2026-07-22T14:00:00+08:00",
        last_start_sent_key="warning-1",
        last_end_reminder_slot="2026-07-22T20:00:00+08:00",
        token="cached-token",
        token_expires_at="2026-07-22T22:00:00+08:00",
        next_check_at="2026-07-22T14:11:00+08:00",
        failure_count=2,
        backoff_until="2026-07-22T14:30:00+08:00",
        last_error="HTTP 429",
    )

    assert repo.get_patrol_warning_config()["route_code"] == "S41"
    assert repo.get_patrol_warning_config()["password"] == "secret"
    assert repo.get_patrol_warning_config()["mention_mobiles"] == "13800138000,13900139000"
    assert repo.get_patrol_warning_config()["send_content_mode"] == "image"
    assert repo.get_patrol_warning_config()["end_reminder_enabled"] is False
    assert repo.get_patrol_warning_config()["start_message_template"] == "start {warning_level_label}"
    assert repo.get_patrol_warning_config()["end_message_template"] == "end {remaining_hours}"
    assert repo.get_patrol_warning_state()["warning"] == {"key": "warning-1", "route_code": "S41"}
    assert repo.get_patrol_warning_state()["last_start_sent_key"] == "warning-1"
    assert repo.get_patrol_warning_state()["token"] == "cached-token"
    assert repo.get_patrol_warning_state()["token_expires_at"] == "2026-07-22T22:00:00+08:00"
    assert repo.get_patrol_warning_state()["next_check_at"] == "2026-07-22T14:11:00+08:00"
    assert repo.get_patrol_warning_state()["failure_count"] == 2
    assert repo.get_patrol_warning_state()["backoff_until"] == "2026-07-22T14:30:00+08:00"
    assert repo.get_patrol_warning_state()["last_error"] == "HTTP 429"


def test_tunnel_mechanical_config_and_state_roundtrip(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_tunnel_mechanical_config(
        base_url="https://example.test",
        username="station-user",
        password="secret",
    )
    repo.save_tunnel_mechanical_state(
        access_token="access-token",
        refresh_token="refresh-token",
        cookie_header="sid=abc",
        token_expires_at="2026-07-24T08:00:00+08:00",
        last_login_at="2026-07-23T08:00:00+08:00",
        last_error="",
    )

    assert repo.get_tunnel_mechanical_config() == {
        "base_url": "https://example.test",
        "username": "station-user",
        "password": "secret",
    }
    assert repo.get_tunnel_mechanical_state() == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "cookie_header": "sid=abc",
        "token_expires_at": "2026-07-24T08:00:00+08:00",
        "last_login_at": "2026-07-23T08:00:00+08:00",
        "last_error": "",
    }


def test_tunnel_mechanical_template_roundtrip(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    template = {
        "base_url": "https://example.test",
        "people": [{"id": "1001", "name": "张三"}],
        "assets": [{"assetId": "asset-1", "assetName": "示例资产"}],
        "defaults": {"checkerId": "1001"},
    }

    repo.save_tunnel_mechanical_template(template)

    assert repo.get_tunnel_mechanical_template() == template


def test_saving_notification_config_replaces_existing_value(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.save_notification_config(webhook_url="https://example.test/old", message_template="old {name}")
    repo.save_notification_config(webhook_url="https://example.test/new", message_template="new {name}")

    config = repo.get_notification_config()

    assert config == {
        "sender_type": "wecom_webhook",
        "webhook_url": "https://example.test/new",
        "lightagent_url": "",
        "lightagent_token": "",
        "lightagent_target": "",
        "lightagent_targets": [],
        "message_template": "new {name}",
    }


def test_notification_config_roundtrips_multiple_lightagent_targets(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="http://lightagent:9899/api/push/send",
        lightagent_token="push-token",
        lightagent_target="wgr_notice",
        lightagent_targets=[
            {"id": "wgr_notice", "name": "通知群"},
            {"id": "wgr_second", "name": "第二通知群"},
        ],
    )

    config = repo.get_notification_config()

    assert config["lightagent_target"] == "wgr_notice"
    assert config["lightagent_targets"] == [
        {"id": "wgr_notice", "name": "通知群"},
        {"id": "wgr_second", "name": "第二通知群"},
    ]


def test_feature_channel_config_roundtrip(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_feature_channel_config(
        enabled=False,
        lightagent_web_url="http://lightagent:9899",
        lightagent_web_password="secret",
        wechat_group_room_id="wgr_room",
        wechat_group_room_name="功能群",
        wechat_group_rooms=[
            {"id": "wgr_room", "name": "功能群"},
            {"id": "wgr_second", "name": "第二功能群"},
        ],
        allow_tunnel_mechanical=True,
        allow_duty_query=False,
        allow_roster_import=False,
    )

    assert repo.get_feature_channel_config() == {
        "enabled": False,
        "lightagent_web_url": "http://lightagent:9899",
        "lightagent_web_password": "secret",
        "wechat_group_room_id": "wgr_room",
        "wechat_group_room_name": "功能群",
        "wechat_group_rooms": [
            {"id": "wgr_room", "name": "功能群"},
            {"id": "wgr_second", "name": "第二功能群"},
        ],
        "allow_tunnel_mechanical": True,
        "allow_duty_query": False,
        "allow_roster_import": False,
    }


def test_send_records_roundtrip(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_send_record(
        kind="daily_duty",
        target="今日在岗人员",
        scheduled_at="2026-07-20T07:50:00+08:00",
        status="failed",
        content="今日在岗人员",
        error="network down",
    )

    assert repo.list_send_records() == [
        {
            "id": 1,
            "kind": "daily_duty",
            "target": "今日在岗人员",
            "scheduled_at": "2026-07-20T07:50:00+08:00",
            "status": "failed",
            "content": "今日在岗人员",
            "error": "network down",
            "created_at": repo.list_send_records()[0]["created_at"],
        }
    ]


