from pathlib import Path

from app.storage import DEFAULT_DAILY_DUTY_TEMPLATE, DutyRepository


def test_initializes_sqlite_schema(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    tables = repo.table_names()

    assert {
        "roster_months",
        "monitored_people",
        "notification_config",
        "personnel_names",
        "daily_duty_config",
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


def test_roster_import_syncs_personnel_names(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")

    repo.save_roster_month(
        2025,
        9,
        [{"name": "示例甲", "days": {"16": "中"}}, {"name": "示例丁", "days": {"16": "早"}}],
        "uploads/a.png",
    )

    assert repo.list_personnel_names() == sorted(["示例甲", "示例丁"])


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


def test_saving_notification_config_replaces_existing_value(tmp_path: Path):
    repo = DutyRepository(tmp_path / "duty.db")
    repo.save_notification_config(webhook_url="https://example.test/old", message_template="old {name}")
    repo.save_notification_config(webhook_url="https://example.test/new", message_template="new {name}")

    config = repo.get_notification_config()

    assert config == {"webhook_url": "https://example.test/new", "message_template": "new {name}"}


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


