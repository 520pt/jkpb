import asyncio
from datetime import date, datetime

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from app.storage import DutyRepository
from tests.test_template_parser import _write_synthetic_roster


def test_static_page_uses_synthetic_placeholders(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="personName" placeholder="示例甲"' in html
    assert 'id="testMobile" placeholder="10000000000"' in html
    assert 'id="mentionMobile" placeholder="10000000000"' in html


def test_review_busy_overlay_is_hidden_until_import_starts(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="reviewBusyOverlay" class="review-busy-overlay" hidden' in html
    assert ".review-busy-overlay[hidden]" in html
    assert ".review-busy-overlay[hidden] {\n      display: none;" in html


def test_health_check(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_login_protects_pages_and_api_when_configured(tmp_path):
    app = create_app(
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
        start_scheduler=False,
        admin_password="secret",
    )
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    page_response = client.get("/")
    api_response = client.get("/api/rosters")
    assert page_response.status_code == 200
    assert "监控班提醒登录" in page_response.text
    assert 'autocomplete="current-password"' in page_response.text
    assert "www-authenticate" not in page_response.headers
    assert api_response.status_code == 401
    assert "www-authenticate" not in api_response.headers

    bad_login = client.post("/login", data={"username": "admin", "password": "bad"})
    assert bad_login.status_code == 401
    assert "账号或密码不正确" in bad_login.text

    login_response = client.post(
        "/login",
        data={"username": "admin", "password": "secret", "remember": "on"},
        follow_redirects=False,
    )
    assert login_response.status_code == 303
    assert "duty_session=" in login_response.headers["set-cookie"]
    assert "Max-Age=" in login_response.headers["set-cookie"]
    assert client.get("/").status_code == 200
    assert client.get("/api/rosters").status_code == 200

    logout_response = client.get("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert "duty_session=" in logout_response.headers["set-cookie"]
    assert client.get("/api/rosters").status_code == 401


def test_upload_image_returns_review_grid(tmp_path, monkeypatch):
    def fake_extract(path):
        return {
            "year": 2025,
            "month": 9,
            "source_image_path": path,
            "grid": [{"name": "示例甲", "days": {"16": "中"}}],
        }

    monkeypatch.setattr("app.main.extract_roster_image", fake_extract)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post("/api/rosters/upload", files={"file": ("roster.png", b"fake-image", "image/png")})

    assert response.status_code == 200
    body = response.json()
    assert body["year"] == 2025
    assert body["grid"][0]["name"] == "示例甲"
    assert body["source_image_url"].startswith("/api/uploads/")

    image_response = client.get(body["source_image_url"])
    assert image_response.status_code == 200
    assert image_response.content == b"fake-image"


def test_upload_rejects_non_image_and_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 4)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    bad_type = client.post("/api/rosters/upload", files={"file": ("roster.txt", b"fake", "text/plain")})
    too_large = client.post("/api/rosters/upload", files={"file": ("roster.png", b"12345", "image/png")})

    assert bad_type.status_code == 400
    assert too_large.status_code == 413


def test_confirm_roster_and_preview_reminders(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    people_response = client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "wecom_userid": "sqh",
            "mention_text": "@示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    assert people_response.status_code == 200

    confirm_response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "示例甲", "days": {"16": "早"}}],
        },
    )
    assert confirm_response.status_code == 200

    preview_response = client.post("/api/reminders/preview", json={"target_date": "2025-09-15"})

    assert preview_response.status_code == 200
    events = preview_response.json()["events"]
    assert any(event["kind"] == "before_shift" for event in events)
    assert any(event["send_at"] == "2025-09-15T23:50:00+08:00" for event in events)


def test_confirm_roster_rejects_placeholder_names(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "第1行", "days": {"16": "中"}}],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "请先补全所有人员姓名，再确认导入"
    assert client.get("/api/rosters").json()["rosters"] == []


def test_notification_config_and_people_mobile_are_saved(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    config_response = client.post(
        "/api/notification-config",
        json={
            "webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test",
            "message_template": "{name} {date} {shift_label}",
        },
    )
    people_response = client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )

    assert config_response.status_code == 200
    assert config_response.json()["config"]["webhook_url"] == ""
    assert config_response.json()["config"]["webhook_configured"] is True
    assert config_response.json()["config"]["message_template"] == "{name} {date} {shift_label}"
    assert people_response.status_code == 200
    assert people_response.json()["people"][0]["mention_mobile"] == "10000000000"
    assert client.get("/api/notification-config").json()["config"]["webhook_display"] == "已配置"

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    assert repo.get_notification_config()["webhook_url"].endswith("unit-test")


def test_saving_notification_config_with_blank_webhook_preserves_existing_value(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={"webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test"},
    )

    response = client.post("/api/notification-config", json={"webhook_url": "", "message_template": "new {name}"})

    assert response.status_code == 200
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    config = repo.get_notification_config()
    assert config["webhook_url"].endswith("unit-test")
    assert config["message_template"] == "new {name}"


def test_notification_config_test_sends_template_message(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test",
            "message_template": "{name} {date}（{time_range})是你的{shift_label}",
        },
    )

    response = client.post("/api/notification-config/test", json={"test_mobile": "10000000000"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert sent == {
        "webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test",
        "content": "示例甲 2025-09-16（08:00至16:00)是你的中班",
        "mobiles": ["10000000000"],
    }
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "notification_test"
    assert records[0]["status"] == "success"


def test_notification_config_test_returns_json_error_when_send_fails(tmp_path, monkeypatch):
    class FailingWebhookClient:
        def __init__(self, *, webhook_url: str):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            raise RuntimeError("network down")

    monkeypatch.setattr("app.main.WeComWebhookClient", FailingWebhookClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={"webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test"},
    )

    response = client.post("/api/notification-config/test", json={"test_mobile": "10000000000"})

    assert response.status_code == 502
    assert response.json()["detail"] == "测试发送失败：network down"
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "notification_test"
    assert records[0]["status"] == "failed"
    assert records[0]["error"] == "测试发送失败：network down"


def test_reminder_preview_uses_notification_message_template(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/notification-config", json={"message_template": "提醒：{name} {date} {shift_label} {time_range}"})
    client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "示例甲", "days": {"16": "中"}}],
        },
    )

    response = client.post("/api/reminders/preview", json={"target_date": "2025-09-16"})

    assert response.status_code == 200
    assert response.json()["events"][0]["content"] == "提醒：示例甲 2025-09-16 中班 08:00至16:00"


def test_time_fields_reject_invalid_hhmm_values(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    person_response = client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "25:61",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    daily_duty_response = client.post(
        "/api/daily-duty-config",
        json={"enabled": True, "reminder_time": "7:5", "big_driver_names": [], "small_driver_names": []},
    )

    assert person_response.status_code == 422
    assert daily_duty_response.status_code == 422


def test_rest_reminder_distinguishes_rest_transition_statuses(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "rest_reminder_enabled": True,
            "rest_reminder_time": "08:30",
            "rest_message_template": "{name} {rest_status}",
            "enabled": True,
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [
                {"name": "示例甲", "days": {"16": "休", "17": "休", "18": "休", "19": ""}},
                {"name": "示例丁", "days": {"16": "休"}},
            ],
        },
    )

    before_rest_response = client.post("/api/reminders/preview", json={"target_date": "2025-09-15"})
    during_rest_response = client.post("/api/reminders/preview", json={"target_date": "2025-09-16"})
    last_rest_response = client.post("/api/reminders/preview", json={"target_date": "2025-09-18"})

    assert before_rest_response.status_code == 200
    assert during_rest_response.status_code == 200
    assert last_rest_response.status_code == 200
    before_rest_events = before_rest_response.json()["events"]
    during_rest_events = during_rest_response.json()["events"]
    last_rest_events = last_rest_response.json()["events"]
    assert any(event["kind"] == "rest" and event["send_at"] == "2025-09-15T08:30:00+08:00" for event in before_rest_events)
    assert any(event["content"] == "示例甲 今日下午休息" for event in before_rest_events)
    assert any(event["content"] == "示例甲 正在休息到 2025-09-18" for event in during_rest_events)
    assert any(event["content"] == "示例甲 今日下午到岗" for event in last_rest_events)
    assert all(event["person_name"] != "示例丁" for event in before_rest_events)


def test_daily_duty_preview_summarizes_on_duty_people_and_drivers(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [
                {"name": "示例丁", "days": {"16": "早"}},
                {"name": "示例己", "days": {"16": "中"}},
                {"name": "示例甲", "days": {"16": "晚"}},
                {"name": "示例庚", "days": {"16": ""}},
                {"name": "示例丙", "days": {"16": ""}},
                {"name": "示例乙", "days": {"16": ""}},
                {"name": "示例壬", "days": {"16": "休", "17": ""}},
                {"name": "示例戊", "days": {"16": "", "17": "休"}},
                {"name": "示例癸", "days": {"16": "休", "17": "休", "18": ""}},
            ],
        },
    )
    config_response = client.post(
        "/api/daily-duty-config",
        json={
            "enabled": True,
            "reminder_time": "07:20",
            "big_driver_names": ["示例庚"],
            "small_driver_names": ["示例丙"],
        },
    )

    preview_response = client.post("/api/daily-duty-preview", json={"target_date": "2025-09-16"})
    names_response = client.get("/api/personnel")

    assert config_response.status_code == 200
    assert names_response.json()["names"] == sorted(["示例甲", "示例乙", "示例丙", "示例丁", "示例戊", "示例己", "示例庚", "示例癸", "示例壬"])
    assert preview_response.status_code == 200
    body = preview_response.json()
    assert body["send_at"] == "2025-09-16T07:20:00+08:00"
    assert body["content"] == (
        "今日在岗人员\n"
        "监控班：早班：示例丁，中班：示例己，晚班：示例甲\n"
        "驾驶员：大车：示例庚 小车：示例丙\n"
        "备勤人员：示例乙\n"
        "今日下午休息：示例戊\n"
        "正在休息：示例癸\n"
        "今日下午到岗：示例壬"
    )
    assert body["details"]["afternoon_rest"] == "示例戊"
    assert body["details"]["resting"] == "示例癸"
    assert body["details"]["afternoon_return"] == "示例壬"


def test_daily_duty_preview_defaults_to_beijing_today(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 0, 30, tzinfo=tz)

    monkeypatch.setattr("app.main.datetime", FixedDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "示例丁", "days": {"16": "早"}}],
        },
    )

    response = client.post("/api/daily-duty-preview", json={})

    assert response.status_code == 200
    assert response.json()["send_at"] == "2025-09-16T07:50:00+08:00"


def test_daily_duty_image_endpoint_returns_backend_png(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "示例丁", "days": {"16": "早"}}],
        },
    )

    response = client.get("/api/daily-duty-image?target_date=2025-09-16")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_daily_duty_preview_excludes_resting_driver(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [
                {"name": "示例庚", "days": {"16": "休息", "17": ""}},
                {"name": "示例丙", "days": {"16": ""}},
                {"name": "示例乙", "days": {"16": ""}},
            ],
        },
    )
    client.post(
        "/api/daily-duty-config",
        json={
            "enabled": True,
            "reminder_time": "07:20",
            "big_driver_names": ["示例庚"],
            "small_driver_names": ["示例丙"],
        },
    )

    response = client.post("/api/daily-duty-preview", json={"target_date": "2025-09-16"})

    assert response.status_code == 200
    body = response.json()
    assert body["details"]["big_drivers"] == "无"
    assert body["details"]["small_drivers"] == "示例丙"
    assert body["details"]["standby"] == "示例乙"
    assert body["details"]["afternoon_return"] == "示例庚"
    assert "大车：无" in body["content"]
    assert "今日下午到岗：示例庚" in body["content"]


def test_daily_duty_test_sends_preview_image_to_webhook(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            raise AssertionError("今日在岗人员不应该再发送文字")

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [
                {"name": "示例丁", "days": {"16": "早"}},
                {"name": "示例己", "days": {"16": "中"}},
                {"name": "示例甲", "days": {"16": "晚"}},
                {"name": "示例庚", "days": {"16": ""}},
                {"name": "示例丙", "days": {"16": ""}},
                {"name": "示例乙", "days": {"16": ""}},
            ],
        },
    )
    client.post(
        "/api/notification-config",
        json={"webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test"},
    )
    client.post(
        "/api/daily-duty-config",
        json={
            "enabled": True,
            "reminder_time": "07:20",
            "big_driver_names": ["示例庚"],
            "small_driver_names": ["示例丙"],
        },
    )

    response = client.post("/api/daily-duty-config/test", json={"target_date": "2025-09-16"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["details"]["big_drivers"] == "示例庚"
    assert sent["webhook_url"] == "https://example.test/cgi-bin/webhook/send?key=unit-test"
    assert isinstance(sent["image_bytes"], bytes)
    assert sent["image_bytes"].startswith(b"\x89PNG")
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "daily_duty_test"
    assert records[0]["status"] == "success"


def test_due_reminder_sends_recently_overdue_daily_duty_event(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 20, 7, 50, 32, tzinfo=tz)

    class FakeWebhookClient:
        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_daily_duty_config(enabled=True, reminder_time="07:50")
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert isinstance(sent["image_bytes"], bytes)
    assert sent["image_bytes"].startswith(b"\x89PNG")
    records = repo.list_send_records()
    assert records[0]["kind"] == "daily_duty"
    assert records[0]["status"] == "success"


def test_list_confirmed_rosters_after_import(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    confirm_response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "示例甲", "days": {"16": "晚"}}],
        },
    )
    assert confirm_response.status_code == 200

    response = client.get("/api/rosters")

    assert response.status_code == 200
    body = response.json()
    assert body["rosters"][0]["year"] == 2025
    assert body["rosters"][0]["month"] == 9
    assert body["rosters"][0]["grid"] == [{"name": "示例甲", "days": {"16": "晚"}}]


def test_confirm_same_month_requires_overwrite_and_returns_diffs(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    first = {
        "year": 2025,
        "month": 9,
        "source_image_path": "uploads/old.png",
        "grid": [{"name": "示例甲", "days": {"16": "中"}}],
    }
    replacement = {
        "year": 2025,
        "month": 9,
        "source_image_path": "uploads/new.png",
        "grid": [{"name": "示例甲", "days": {"16": "晚"}}],
    }
    assert client.post("/api/rosters/confirm", json=first).status_code == 200

    conflict_response = client.post("/api/rosters/confirm", json=replacement)

    assert conflict_response.status_code == 409
    conflict = conflict_response.json()
    assert conflict["success"] is False
    assert conflict["conflict"] is True
    assert conflict["diffs"] == [{"row": 0, "name": "示例甲", "day": "16", "before": "中", "after": "晚"}]
    assert client.get("/api/rosters").json()["rosters"][0]["source_image_path"] == "uploads/old.png"

    overwrite_response = client.post("/api/rosters/confirm", json={**replacement, "overwrite": True})

    assert overwrite_response.status_code == 200
    assert client.get("/api/rosters").json()["rosters"][0]["source_image_path"] == "uploads/new.png"


def test_roster_versions_can_restore_previous_import(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    first = {
        "year": 2025,
        "month": 9,
        "source_image_path": "uploads/old.png",
        "grid": [{"name": "示例甲", "days": {"16": "中"}}],
        "overwrite": True,
    }
    second = {
        "year": 2025,
        "month": 9,
        "source_image_path": "uploads/new.png",
        "grid": [{"name": "示例甲", "days": {"16": "晚"}}],
        "overwrite": True,
    }
    assert client.post("/api/rosters/confirm", json=first).status_code == 200
    assert client.post("/api/rosters/confirm", json=second).status_code == 200

    versions_response = client.get("/api/rosters/2025/9/versions")

    assert versions_response.status_code == 200
    versions = versions_response.json()["versions"]
    assert [version["source_image_path"] for version in versions[:2]] == ["uploads/new.png", "uploads/old.png"]

    restore_response = client.post(f"/api/rosters/2025/9/versions/{versions[1]['id']}/restore")

    assert restore_response.status_code == 200
    current = client.get("/api/rosters").json()["rosters"][0]
    assert current["source_image_path"] == "uploads/old.png"
    assert current["grid"] == [{"name": "示例甲", "days": {"16": "中"}}]


def test_system_status_reports_runtime_and_next_events(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 20, 7, 40, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", FixedDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2026,
            "month": 7,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "示例甲", "days": {"20": "中"}}],
        },
    )

    response = client.get("/api/system-status")

    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Asia/Shanghai"
    assert body["now_beijing"].startswith("2026-07-20T07:40:00")
    assert body["scheduler_enabled"] is False
    assert body["webhook_configured"] is False
    assert body["roster_month_count"] == 1
    assert body["monitored_people_count"] == 1
    assert body["next_events"][0]["send_at"] == "2026-07-20T07:50:00+08:00"


def test_resend_failed_text_record_sends_again_and_records_result(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("文字补发不应该发送图片")

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/notification-config", json={"webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test"})
    client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(
        kind="daily",
        target="示例甲",
        scheduled_at="2025-09-16T07:50:00+08:00",
        status="failed",
        content="补发内容",
        error="network down",
    )
    record_id = client.get("/api/send-records").json()["records"][0]["id"]

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert sent["content"] == "补发内容"
    assert sent["mobiles"] == ["10000000000"]
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "daily_resend"
    assert records[0]["status"] == "success"


def test_recheck_roster_corrects_mismatched_cells_from_source_image(tmp_path):
    upload_dir = tmp_path / "uploads"
    image_path = upload_dir / "roster.png"
    upload_dir.mkdir()
    _write_synthetic_roster(image_path)
    app = create_app(data_dir=tmp_path / "data", upload_dir=upload_dir, start_scheduler=False)
    client = TestClient(app)

    upload_response = client.post(
        "/api/rosters/upload",
        files={"file": ("roster.png", image_path.read_bytes(), "image/png")},
    )
    grid = upload_response.json()["grid"]
    grid[0]["days"]["5"] = "中"

    response = client.post(
        "/api/rosters/recheck",
        json={"source_image_path": upload_response.json()["source_image_path"], "grid": grid},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["grid"][0]["days"]["5"] == "晚"
    assert body["grid"][0]["boxes"]["5"] == {"x": 257, "y": 120, "width": 24, "height": 33}
    assert body["issues"] == [
        {
            "row": 0,
            "day": "5",
            "before": "中",
            "after": "晚",
            "box": {"x": 257, "y": 120, "width": 24, "height": 33},
        }
    ]

