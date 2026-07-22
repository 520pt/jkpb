import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from urllib.parse import quote

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from app.patrol_warning import warning_from_dict
from app.storage import DutyRepository
from tests.test_template_parser import _write_synthetic_roster


def test_static_page_uses_synthetic_placeholders(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'data-tab="today">今日提醒' in html
    assert '<section id="todayPage" class="tab-page">' in html
    assert '<section id="reviewPage" class="tab-page hidden">' in html
    assert 'id="personName" list="personnelNameOptions" placeholder="选择或输入姓名"' in html
    assert 'id="customReminderName" list="personnelNameOptions" placeholder="选择或输入姓名"' in html
    assert 'id="driverNameInput" list="personnelNameOptions" placeholder="选择或输入姓名"' in html
    assert 'data-edit-person="${escapeHtml(person.name)}"' in html
    assert 'data-delete-person="${escapeHtml(person.name)}"' in html
    assert 'id="testMobile" placeholder="10000000000"' in html
    assert 'id="mentionMobile" placeholder="10000000000"' in html
    assert 'id="patrolWarningSettings"' in html
    assert 'id="patrolLoginUrl"' in html
    assert 'id="patrolRouteCode" placeholder="S41"' in html
    assert 'id="patrolWarningImageMeta"' in html
    assert 'id="patrolSendContentMode"' in html
    assert '<option value="image">仅图片</option>' in html
    assert "refreshPatrolWarningPanel" in html
    assert "loadTodayReminders" in html
    assert "todayReminderGroupKey" in html
    assert "todayReminderGroupColumn" in html
    assert "left-column" in html
    assert "right-column" in html
    assert "daily-duty-column" in html
    assert "patrol-warning-column" in html
    assert "has-image" in html
    assert 'id="imageViewer"' in html
    assert "openImageViewer" in html
    assert "setupImageViewer" in html
    assert "image-viewer-image" in html
    assert "today-reminder-side" in html
    assert "today-reminder-image-card" in html
    assert "data-today-state-at" in html
    assert "已提醒" in html
    assert "已过预警结束巡查提醒" in html
    assert "其余待发送提醒" in html
    assert "event-collapsed" in html


def test_today_reminders_endpoint_returns_today_plan(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/reminders/today")

    assert response.status_code == 200
    body = response.json()
    assert body["target_date"]
    assert body["now_beijing"]
    assert body["events"]
    assert body["events"][0]["kind"] == "daily_duty"
    assert body["events"][0]["sent_state"] in {"pending", "sent_or_due"}
    assert body["events"][0]["image_url"].startswith("/api/daily-duty-image")
    assert "group_statuses" in body


def test_today_reminders_include_patrol_warning_events(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 8, 0, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_patrol_warning_config(
        enabled=True,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
        route_code="S41",
        end_reminder_interval_hours=6,
        end_reminder_window_hours=48,
    )
    repo.save_patrol_warning_state(
        warning={
            "key": "warning-1",
            "route_code": "S41",
            "route_name": "Route A",
            "warning_level": "3",
            "warning_level_label": "Yellow",
            "warn_type_name": "Rain",
            "start_time": "2026-07-22T01:00:00+08:00",
            "end_time": "2026-07-22T02:00:00+08:00",
            "create_time": "2026-07-22T01:10:00+08:00",
            "start_stake": "K107.000",
            "end_stake": "K137.730",
        }
    )
    client = TestClient(app)

    response = client.get("/api/reminders/today")

    assert response.status_code == 200
    kinds = [event["kind"] for event in response.json()["events"]]
    assert "patrol_warning_start" in kinds
    assert "patrol_warning_end" in kinds
    patrol_events = [event for event in response.json()["events"] if event["kind"].startswith("patrol_warning_")]
    assert patrol_events
    assert all(event["image_url"].startswith("/api/patrol-warning-image") for event in patrol_events)
    assert any("mode=end" in event["image_url"] for event in patrol_events if event["kind"] == "patrol_warning_end")


def test_confirm_roster_prunes_nonexistent_days_for_common_february(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2026,
            "month": 2,
            "source_image_path": "uploads/feb.png",
            "grid": [{"name": "张三", "days": {"28": "中", "29": "晚", "30": "早", "31": "休"}}],
        },
    )

    assert response.status_code == 200
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    roster = repo.get_roster_month(2026, 2)
    assert roster is not None
    assert roster["grid"][0]["days"] == {"28": "中"}


def test_confirm_roster_keeps_february_29_for_leap_year(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2024,
            "month": 2,
            "source_image_path": "uploads/feb-leap.png",
            "grid": [{"name": "张三", "days": {"28": "中", "29": "晚", "30": "早"}}],
        },
    )

    assert response.status_code == 200
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    roster = repo.get_roster_month(2024, 2)
    assert roster is not None
    assert roster["grid"][0]["days"] == {"28": "中", "29": "晚"}


def test_confirm_roster_keeps_day_30_and_prunes_day_31_for_short_month(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2026,
            "month": 4,
            "source_image_path": "uploads/apr.png",
            "grid": [{"name": "张三", "days": {"29": "中", "30": "晚", "31": "早"}}],
        },
    )

    assert response.status_code == 200
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    roster = repo.get_roster_month(2026, 4)
    assert roster is not None
    assert roster["grid"][0]["days"] == {"29": "中", "30": "晚"}


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


def test_custom_reminder_crud_personnel_contact_and_preview(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    confirm_response = client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "商邱宏", "days": {"16": "晚"}}],
        },
    )
    reminder_response = client.post(
        "/api/custom-reminders",
        json={
            "name": "商邱宏",
            "mention_mobile": "10000000000",
            "shift_code": "night",
            "reminder_time": "21:00",
            "message": "{name} 需要关闭隧道灯",
            "enabled": True,
        },
    )
    personnel_response = client.get("/api/personnel")
    preview_response = client.post("/api/reminders/preview", json={"target_date": "2025-09-16"})

    assert confirm_response.status_code == 200
    assert reminder_response.status_code == 200
    assert reminder_response.json()["reminders"][0]["message"] == "{name} 需要关闭隧道灯"
    assert personnel_response.json()["people"] == [{"name": "商邱宏", "mention_mobile": "10000000000"}]
    events = preview_response.json()["events"]
    assert any(
        event["kind"] == "custom"
        and event["person_name"] == "商邱宏"
        and event["send_at"] == "2025-09-16T21:00:00+08:00"
        and event["content"] == "商邱宏 需要关闭隧道灯"
        for event in events
    )

    reminder_id = reminder_response.json()["id"]
    delete_response = client.delete(f"/api/custom-reminders/{reminder_id}")

    assert delete_response.status_code == 200
    assert delete_response.json()["reminders"] == []


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


def test_monitored_person_can_be_updated_and_deleted(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    create_response = client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    update_response = client.post(
        "/api/people",
        json={
            "original_name": "示例甲",
            "name": "示例乙",
            "mention_mobile": "13900139000",
            "daily_time": "08:10",
            "before_shift_minutes": 20,
            "enabled": True,
        },
    )
    delete_response = client.delete(f"/api/people/{quote('示例乙')}")

    assert create_response.status_code == 200
    assert update_response.status_code == 200
    assert [person["name"] for person in update_response.json()["people"]] == ["示例乙"]
    assert update_response.json()["people"][0]["mention_mobile"] == "13900139000"
    assert update_response.json()["people"][0]["daily_time"] == "08:10"
    assert delete_response.status_code == 200
    assert delete_response.json()["people"] == []


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


def test_patrol_warning_config_preserves_password_and_hides_it(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    create_response = client.post(
        "/api/patrol-warning-config",
        json={
            "enabled": True,
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/warninginfo/findPage",
            "username": "station-user",
            "password": "secret",
            "project_id": "project-1",
            "platform": "2",
            "route_code": "S41",
        },
    )
    update_response = client.post(
        "/api/patrol-warning-config",
        json={
            "enabled": True,
            "login_url": "https://example.test/login2",
            "warning_url": "https://example.test/warninginfo/findPage",
            "username": "station-user",
            "password": "",
            "project_id": "project-1",
            "platform": "2",
            "route_code": "S41",
        },
    )
    get_response = client.get("/api/patrol-warning-config")

    assert create_response.status_code == 200
    assert update_response.status_code == 200
    public_config = get_response.json()["config"]
    assert public_config["password"] == ""
    assert public_config["password_configured"] is True
    assert public_config["login_url"] == "https://example.test/login2"
    assert public_config["send_content_mode"] == "both"
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    assert repo.get_patrol_warning_config()["password"] == "secret"


def test_patrol_warning_state_hides_cached_token(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_patrol_warning_state(
        token="cached-token",
        token_expires_at="2026-07-22T22:00:00+08:00",
        next_check_at="2026-07-22T14:11:00+08:00",
        failure_count=1,
        backoff_until="2026-07-22T14:05:00+08:00",
        last_error="HTTP 429",
    )

    response = client.get("/api/patrol-warning-config")

    assert response.status_code == 200
    state = response.json()["state"]
    assert "token" not in state
    assert state["token_configured"] is True
    assert state["token_expires_at"] == "2026-07-22T22:00:00+08:00"
    assert state["next_check_at"] == "2026-07-22T14:11:00+08:00"
    assert state["failure_count"] == 1
    assert state["last_error"] == "HTTP 429"


def test_patrol_warning_image_preview_endpoint_returns_png(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/patrol-warning-image-preview",
        json={
            "window_hours": 48,
            "warning": {
                "key": "warning-1",
                "route_code": "S41",
                "route_name": "南涧－宁洱",
                "warning_level": "2",
                "warning_level_label": "橙色预警",
                "start_time": "2026-07-22T08:00:00+08:00",
                "end_time": "2026-07-22T10:00:00+08:00",
                "start_stake": "K107.000",
                "end_stake": "K137.730",
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_patrol_warning_config_test_uses_saved_password(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class FakeWarning:
        def as_dict(self):
            return {"key": "warning-1", "route_code": "S41", "warning_level_label": "橙色预警"}

    async def fake_fetch_latest_warning(config, tz):
        captured["config"] = config
        return FakeWarning(), {"total_rows": 2, "matched_rows": 1}

    monkeypatch.setattr(main_module, "fetch_latest_warning", fake_fetch_latest_warning)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/patrol-warning-config",
        json={
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/warninginfo/findPage",
            "username": "station-user",
            "password": "secret",
            "route_code": "S41",
        },
    )

    response = client.post(
        "/api/patrol-warning-config/test",
        json={
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/warninginfo/findPage",
            "username": "station-user",
            "password": "",
            "route_code": "S41",
        },
    )

    assert response.status_code == 200
    assert response.json()["latest"]["warning_level_label"] == "橙色预警"
    assert captured["config"]["password"] == "secret"
    state = client.get("/api/patrol-warning-config").json()["state"]
    assert state["warning"]["key"] == "warning-1"
    assert state["warning_key"] == ""


def test_patrol_warning_monitor_backs_off_after_fetch_failure(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 8, 0, tzinfo=tz)

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        raise main_module.PatrolWarningError("HTTP 429", status_code=429)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_patrol_warning_config(
        enabled=True,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
        route_code="S41",
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: object())

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    state = repo.get_patrol_warning_state()
    assert state["last_checked_at"] == "2026-07-22T08:00:00+08:00"
    assert state["next_check_at"] == "2026-07-22T08:05:00+08:00"
    assert state["backoff_until"] == "2026-07-22T08:05:00+08:00"
    assert state["failure_count"] == 1
    assert state["last_error"] == "HTTP 429"
    records = repo.list_send_records()
    assert records[0]["kind"] == "patrol_warning_check"
    assert records[0]["status"] == "failed"


def test_patrol_warning_monitor_refreshes_same_warning_without_resending(tmp_path, monkeypatch):
    sent: list[object] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 8, 0, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent.append(("text", content, mentioned_mobile_list))

        async def send_image(self, image_bytes: bytes):
            sent.append(("image", image_bytes))

    warning = warning_from_dict(
        {
            "key": "warning-1",
            "route_code": "S41",
            "route_name": "Route A",
            "warning_level": "2",
            "warning_level_label": "Orange",
            "start_time": "2026-07-22T07:00:00+08:00",
            "end_time": "2026-07-22T10:00:00+08:00",
            "start_stake": "K107.000",
            "end_stake": "K137.730",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-07-22T18:00:00+08:00",
            token_reused=False,
        )

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_patrol_warning_config(
        enabled=True,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
        route_code="S41",
    )
    repo.save_patrol_warning_state(
        warning_key="warning-1",
        warning={
            "key": "warning-1",
            "route_code": "S41",
            "route_name": "Route A",
            "warning_level": "2",
            "warning_level_label": "Orange",
            "start_time": "2026-07-22T07:00:00+08:00",
            "end_time": "",
            "start_stake": "K107.000",
            "end_stake": "",
        },
        last_start_sent_key="warning-1",
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    state = repo.get_patrol_warning_state()
    assert state["warning"]["end_time"] == "2026-07-22T10:00:00+08:00"
    assert state["warning"]["end_stake"] == "K137.730"
    assert state["last_start_sent_key"] == "warning-1"
    assert sent == []


def test_patrol_warning_monitor_uses_specific_mentions_and_template(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 8, 0, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

    warning = warning_from_dict(
        {
            "key": "warning-1",
            "route_code": "S41",
            "route_name": "南涧－宁洱",
            "warning_level": "2",
            "warning_level_label": "橙色预警",
            "start_time": "2026-07-22T08:00:00+08:00",
            "end_time": "2026-07-22T10:00:00+08:00",
            "start_stake": "K107.000",
            "end_stake": "K137.730",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-07-22T18:00:00+08:00",
            token_reused=False,
        )

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_patrol_warning_config(
        enabled=True,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
        route_code="S41",
        mention_all=False,
        mention_mobiles="13800138000, 13900139000",
        start_message_template="指定模板：{warning_level_label} {stake_range}",
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    assert sent["content"] == "指定模板：橙色预警 K107.000 - K137.730"
    assert sent["mobiles"] == ["13800138000", "13900139000"]
    assert sent["image_bytes"].startswith(b"\x89PNG")


def test_patrol_warning_send_content_mode_image_only_skips_text(tmp_path, monkeypatch):
    sent: dict[str, object] = {"text_count": 0, "image_count": 0}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 22, 8, 0, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["text_count"] = int(sent["text_count"]) + 1

        async def send_image(self, image_bytes: bytes):
            sent["image_count"] = int(sent["image_count"]) + 1
            sent["image_bytes"] = image_bytes

    warning = warning_from_dict(
        {
            "key": "warning-1",
            "route_code": "S41",
            "warning_level": "2",
            "warning_level_label": "橙色预警",
            "start_time": "2026-07-22T08:00:00+08:00",
            "end_time": "2026-07-22T10:00:00+08:00",
            "start_stake": "K107.000",
            "end_stake": "K137.730",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-07-22T18:00:00+08:00",
            token_reused=False,
        )

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_patrol_warning_config(
        enabled=True,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
        route_code="S41",
        send_content_mode="image",
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    assert sent["text_count"] == 0
    assert sent["image_count"] == 1
    assert sent["image_bytes"].startswith(b"\x89PNG")


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


def test_due_custom_reminder_sends_with_saved_personnel_mobile(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 21, 0, 25, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("自定义提醒不应该发送图片")

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_roster_month(
        2025,
        9,
        [{"name": "商邱宏", "days": {"16": "晚"}}],
        "uploads/month.png",
    )
    repo.save_custom_reminder(
        name="商邱宏",
        mention_mobile="10000000000",
        shift_code="night",
        reminder_time="21:00",
        message="需要关闭隧道灯",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "需要关闭隧道灯"
    assert sent["mobiles"] == ["10000000000"]
    records = repo.list_send_records()
    assert records[0]["kind"] == "custom"
    assert records[0]["target"] == "商邱宏"
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
