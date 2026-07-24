import asyncio
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from app.patrol_warning import warning_from_dict
from app.storage import DutyRepository
from tests.test_template_parser import _write_synthetic_roster


TEST_TUNNEL_TEMPLATE = {
    "base_url": "",
    "submit_path": "/prod-api/patrol/deviceCheck/add",
    "list_path": "",
    "people": [{"id": "1001", "name": "张三"}, {"id": "1002", "name": "李四"}],
    "assets": [
        {
            "assetId": "asset-1",
            "assetName": "示例隧道上行",
            "assetCode": "ASSET001",
            "routeCode": "R1",
            "routeName": "示例路线",
            "maintenanceSectionId": "section-1",
            "domainId": "domain-1",
            "deptName": "示例部门",
            "devName": "示例设备",
            "location": "K1+000-K2+000示例隧道",
            "content": "示例检查",
            "result": 1,
            "carLicense": "示例车牌",
            "nums": "1",
        }
    ],
    "defaults": {
        "checkerId": "1001",
        "checker": "张三",
        "recorderId": "1002",
        "recorder": "李四",
        "checkTime": "",
        "weather": "晴",
        "carLicense": "示例车牌",
        "nums": "1",
    },
}


def _import_tunnel_template(client: TestClient, template: dict | None = None):
    return client.post(
        "/api/tunnel-mechanical/templates/import",
        files={"file": ("template.json", json_bytes(template or TEST_TUNNEL_TEMPLATE), "application/json")},
    )


def json_bytes(value: dict) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False).encode("utf-8")


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
    assert 'id="monitorMobileField">@ 手机号 <input id="mentionMobile" placeholder="10000000000"' in html
    assert 'class="field-grid hidden" id="monitorWechatFields"' in html
    assert 'id="monitorWechatMember"' in html
    assert 'id="monitorWechatMemberId" readonly placeholder="未绑定"' in html
    assert "updateMonitorNotificationFields" in html
    assert "autofillMonitorContactByName" in html
    assert "monitorWechatBindingPayload" in html
    assert "monitorWechatBindingText" in html
    assert "同步${wechatGatewayLabel()}群失败" in html
    assert "功能通道已保存，但同步${wechatGatewayLabel()}群失败" in html
    assert 'tunnel_mechanical_wechat: "隧道机电录入"' in html
    assert 'tunnel_mechanical_query_wechat: "隧道机电查询"' in html
    assert 'patrol_warning_start_test: "公路巡查预警测试"' in html
    assert 'patrol_warning_end_test: "预警结束巡查测试"' in html
    assert 'patrol_warning_check_resend: "公路巡查预警检查补发"' in html
    assert 'id="patrolWarningSettings"' in html
    assert 'id="patrolLoginUrl"' in html
    assert 'id="patrolRouteCode" placeholder="S41"' in html
    assert 'id="patrolWarningQueryMeta"' in html
    assert 'id="patrolSendContentMode"' in html
    assert '<option value="image">仅图片</option>' in html
    assert 'data-tab="tunnelMechanical">隧道机电' in html
    assert 'data-tab="settings">配置中心' in html
    assert 'id="settingsOverview"' in html
    assert "微信群交互配置" in html
    assert 'id="tunnelMechanicalPage"' in html
    assert 'id="tunnelEntryPanel"' in html
    assert 'id="tunnelTemplatePanel"' in html
    assert 'data-tunnel-panel-target="tunnelEntryPanel"' in html
    assert 'data-tunnel-panel-target="tunnelTemplatePanel"' in html
    assert "switchTunnelMechanicalPanel" in html
    assert 'id="submitTunnelMechanicalBtn"' in html
    assert 'id="tunnelMechanicalUsername"' in html
    assert 'id="importTunnelMechanicalTemplateBtn"' in html
    assert 'id="tunnelMechanicalTemplateFile"' in html
    assert 'id="queryTunnelMechanicalResultBtn"' in html
    assert 'id="tunnelMechanicalResultDateModal"' in html
    assert 'id="tunnelMechanicalResultDateInput" type="date"' in html
    assert 'id="tunnelMechanicalResultDateConfirmBtn"' in html
    assert '$("queryTunnelMechanicalResultBtn").addEventListener("click", openTunnelMechanicalResultDateModal);' in html
    assert "async function queryTunnelMechanicalResultImage(queryDate)" in html
    assert "checkTime: queryDate || beijingDateInputValue()," in html
    assert 'id="loadTunnelMechanicalCaptchaBtn"' in html
    assert 'id="testTunnelMechanicalLoginBtn"' in html
    assert "tunnel-asset-card" in html
    assert 'data-settings-target="featureChannelSettings"' in html
    assert 'id="featureChannelSettings"' in html
    assert 'id="featureChannelRoomSelect"' in html
    assert 'id="addFeatureChannelRoomBtn"' in html
    assert 'id="featureChannelRoomList"' in html
    assert 'id="notificationTargetRoomSelect"' in html
    assert 'id="addNotificationTargetRoomBtn"' in html
    assert 'id="notificationTargetRoomList"' in html
    assert 'id="saveFeatureChannelBtn"' in html
    assert 'loadTunnelMechanicalTemplates' in html
    assert 'loadTunnelMechanicalConfig' in html
    assert "refreshPatrolWarningPanel" in html
    assert "loadTodayReminders" in html
    assert "todayReminderGroupKey" in html
    assert "todayReminderGroupColumn" in html
    assert "left-column" in html
    assert "right-column" in html
    assert "daily-duty-column" in html
    assert "patrol-warning-column" in html
    assert "明日早班：{tomorrow_early}" in html
    assert "details.tomorrow_early" in html
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


def test_send_record_kind_labels_cover_backend_record_kinds():
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        [
            (root / "app" / "main.py").read_text(encoding="utf-8"),
            (root / "app" / "reminders.py").read_text(encoding="utf-8"),
        ]
    )
    html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    backend_kinds = set(re.findall(r'kind="([^"]+)"', source))
    backend_kinds.update({"patrol_warning_start_test", "patrol_warning_end_test"})
    expected_kinds = backend_kinds | {f"{kind}_resend" for kind in backend_kinds if not kind.endswith("_resend")}
    match = re.search(r"function sendRecordKindLabel\(kind\) \{\s*return \(\{([\s\S]*?)\}\)\[kind\]", html)
    assert match is not None
    frontend_labels = set(re.findall(r"\n\s*([A-Za-z0-9_]+):\s*\"", match.group(1)))

    assert sorted(expected_kinds - frontend_labels) == []


def test_tunnel_mechanical_templates_are_empty_until_imported(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    templates_response = client.get("/api/tunnel-mechanical/templates")

    assert templates_response.status_code == 200
    templates = templates_response.json()
    assert templates["base_url"] == ""
    assert templates["assets"] == []
    assert templates["people"] == []
    assert templates["defaults"]["checkerId"] == ""
    assert templates["imported"] is False


def test_tunnel_mechanical_template_import_and_dry_run_payload(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    import_response = _import_tunnel_template(client)
    templates = client.get("/api/tunnel-mechanical/templates").json()

    assert import_response.status_code == 200
    assert templates["base_url"] == ""
    assert len(templates["assets"]) == 1
    assert {"id": "1001", "name": "张三"} in templates["people"]

    asset = templates["assets"][0]
    response = client.post(
        "/api/tunnel-mechanical/submit",
        json={
            "base_url": "",
            "authorization": "Bearer test-token",
            "checkTime": "2026-07-24",
            "weather": "晴",
            "checkerId": "1001",
            "checker": "张三",
            "recorderId": "1002",
            "recorder": "李四",
            "dry_run": True,
            "rows": [asset],
        },
    )

    assert response.status_code == 200
    body = response.json()
    payload = body["submissions"][0]["payload"]
    assert body["success"] is True
    assert body["dry_run"] is True
    assert payload["assetId"] == "asset-1"
    assert payload["checker"] == "张三"
    assert payload["recorder"] == "李四"
    assert payload["checkTime"] == "2026-07-24"
    assert payload["domains"][0]["location"] == "K1+000-K2+000示例隧道"


def test_tunnel_mechanical_submit_rejects_unexpected_host(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)
    asset = client.get("/api/tunnel-mechanical/templates").json()["assets"][0]

    response = client.post(
        "/api/tunnel-mechanical/submit",
        json={
            "base_url": "https://example.com",
            "checkTime": "2026-07-24",
            "weather": "晴",
            "checkerId": "1001",
            "checker": "张三",
            "recorderId": "1002",
            "recorder": "李四",
            "dry_run": False,
            "rows": [asset],
        },
    )

    assert response.status_code == 400


def test_tunnel_mechanical_config_preserves_password_and_hides_it(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo

    response = client.post(
        "/api/tunnel-mechanical/config",
        json={
            "base_url": "",
            "username": "station-user",
            "password": "secret",
        },
    )
    assert response.status_code == 200
    assert response.json()["config"]["password"] == ""
    assert response.json()["config"]["password_configured"] is True

    response = client.post(
        "/api/tunnel-mechanical/config",
        json={
            "base_url": "",
            "username": "station-user",
            "password": "",
        },
    )
    assert response.status_code == 200
    assert response.json()["config"]["password"] == ""
    assert repo.get_tunnel_mechanical_config()["password"] == "secret"


def test_tunnel_mechanical_login_auto_solves_captcha(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(
        base_url="https://example.test",
        username="station-user",
        password="secret",
    )
    captured = {}

    class FakeResponse:
        status_code = 200

        def __init__(self, body):
            self._body = body
            self.text = ""

        def json(self):
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.cookies = main_module.httpx.Cookies()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            captured["captcha_url"] = url
            return FakeResponse({"code": 200, "captchaEnabled": True, "img": "encrypted-img", "uuid": "uuid-1"})

        async def post(self, url, headers=None, json=None):
            captured["login_url"] = url
            captured["login_payload"] = json
            return FakeResponse({"code": 200, "data": {"access_token": "token-1", "expires_in": 7200}})

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main_module, "_tunnel_mechanical_decrypt_text", lambda text: "captcha-image")
    monkeypatch.setattr(main_module, "_solve_tunnel_mechanical_captcha", lambda image: "8")

    state = asyncio.run(main_module._login_tunnel_mechanical(repo, repo.get_tunnel_mechanical_config()))

    assert captured["captcha_url"] == "https://example.test/prod-api/code"
    assert captured["login_url"] == "https://example.test/prod-api/auth/login"
    assert captured["login_payload"]["code"] == "8"
    assert captured["login_payload"]["uuid"] == "uuid-1"
    assert state["access_token"] == "token-1"


def test_tunnel_mechanical_login_retries_when_auto_captcha_is_wrong(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(
        base_url="https://example.test",
        username="station-user",
        password="secret",
    )
    calls = {"captcha": 0, "login": 0}

    async def fake_captcha(base_url):
        calls["captcha"] += 1
        return {"success": True, "captcha_enabled": True, "code": f"code-{calls['captcha']}", "uuid": f"uuid-{calls['captcha']}"}

    class FakeResponse:
        status_code = 200

        def __init__(self, body):
            self._body = body
            self.text = ""

        def json(self):
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.cookies = main_module.httpx.Cookies()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            calls["login"] += 1
            if calls["login"] == 1:
                assert json["code"] == "code-1"
                return FakeResponse({"code": 500, "msg": "验证码错误"})
            assert json["code"] == "code-2"
            return FakeResponse({"code": 200, "data": {"access_token": "token-2", "expires_in": 7200}})

    monkeypatch.setattr(main_module, "_fetch_tunnel_mechanical_captcha", fake_captcha)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)

    state = asyncio.run(main_module._login_tunnel_mechanical(repo, repo.get_tunnel_mechanical_config()))

    assert state["access_token"] == "token-2"
    assert calls == {"captcha": 2, "login": 2}


def test_tunnel_mechanical_keepalive_skips_when_token_is_fresh(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(base_url="https://example.test", username="station-user", password="secret")
    repo.save_tunnel_mechanical_state(
        access_token="cached-token",
        refresh_token="refresh-token",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=2)).isoformat(),
    )
    calls = {"refresh": 0, "login": 0}

    async def fake_refresh(*args, **kwargs):
        calls["refresh"] += 1
        return None

    async def fake_login(*args, **kwargs):
        calls["login"] += 1
        return {}

    monkeypatch.setattr(main_module, "_refresh_tunnel_mechanical_token", fake_refresh)
    monkeypatch.setattr(main_module, "_login_tunnel_mechanical", fake_login)

    asyncio.run(main_module._keepalive_tunnel_mechanical_login(repo))

    assert calls == {"refresh": 0, "login": 0}


def test_tunnel_mechanical_keepalive_refreshes_when_token_near_expiry(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(base_url="https://example.test", username="station-user", password="secret")
    repo.save_tunnel_mechanical_state(
        access_token="old-token",
        refresh_token="refresh-token",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(minutes=5)).isoformat(),
    )
    calls = {"refresh": 0, "login": 0}

    async def fake_refresh(repo_arg, base_url, state):
        calls["refresh"] += 1
        assert base_url == "https://example.test"
        repo_arg.save_tunnel_mechanical_state(
            access_token="fresh-token",
            token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=2)).isoformat(),
            last_error="",
        )
        return repo_arg.get_tunnel_mechanical_state()

    async def fake_login(*args, **kwargs):
        calls["login"] += 1
        return {}

    monkeypatch.setattr(main_module, "_refresh_tunnel_mechanical_token", fake_refresh)
    monkeypatch.setattr(main_module, "_login_tunnel_mechanical", fake_login)

    asyncio.run(main_module._keepalive_tunnel_mechanical_login(repo))

    assert calls == {"refresh": 1, "login": 0}
    assert repo.get_tunnel_mechanical_state()["access_token"] == "fresh-token"


def test_tunnel_mechanical_captcha_fetch_retries_until_solved(monkeypatch):
    calls = {"get": 0, "solve": 0}

    class FakeResponse:
        status_code = 200

        def __init__(self, image):
            self._image = image

        def json(self):
            return {"code": 200, "captchaEnabled": True, "img": self._image, "uuid": f"uuid-{calls['get']}"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None):
            calls["get"] += 1
            return FakeResponse(f"encrypted-{calls['get']}")

    def fake_solve(image):
        calls["solve"] += 1
        if calls["solve"] < 3:
            raise main_module.HTTPException(status_code=422, detail="unreadable")
        return "8"

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main_module, "_tunnel_mechanical_decrypt_text", lambda text: text.replace("encrypted", "image"))
    monkeypatch.setattr(main_module, "_solve_tunnel_mechanical_captcha", fake_solve)

    result = asyncio.run(main_module._fetch_tunnel_mechanical_captcha("https://example.test", solve_attempts=5))

    assert result["code"] == "8"
    assert result["uuid"] == "uuid-3"
    assert calls == {"get": 3, "solve": 3}


def test_tunnel_mechanical_captcha_text_solver_calculates_math():
    assert main_module._solve_tunnel_mechanical_captcha_text("1*8=?") == "8"
    assert main_module._solve_tunnel_mechanical_captcha_text("9 - 4 = ?") == "5"


def test_tunnel_mechanical_submit_uses_cached_login_state(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(
        base_url="https://example.test",
        username="station-user",
        password="secret",
    )
    repo.save_tunnel_mechanical_state(
        access_token="cached-token",
        cookie_header="sid=abc",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=1)).isoformat(),
    )
    _import_tunnel_template(client)
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"code": 200, "msg": "ok"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    asset = client.get("/api/tunnel-mechanical/templates").json()["assets"][0]

    response = client.post(
        "/api/tunnel-mechanical/submit",
        json={
            "base_url": "https://example.test",
            "checkTime": "2026-07-24",
            "weather": "sunny",
            "checkerId": "8647",
            "checker": "checker",
            "recorderId": "8587",
            "recorder": "recorder",
            "dry_run": False,
            "rows": [asset],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["url"] == "https://example.test/prod-api/patrol/deviceCheck/add"
    assert captured["headers"]["Authorization"] == "Bearer cached-token"
    assert captured["headers"]["Cookie"] == "sid=abc"
    assert captured["payload"]["checker"] == "checker"


def test_tunnel_mechanical_submit_generates_result_image(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(
        base_url="https://example.test",
        username="station-user",
        password="secret",
    )
    repo.save_tunnel_mechanical_state(
        access_token="cached-token",
        cookie_header="sid=abc",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=1)).isoformat(),
    )
    template = {
        **TEST_TUNNEL_TEMPLATE,
        "base_url": "https://example.test",
        "list_path": "/prod-api/patrol/deviceCheck/list",
    }
    _import_tunnel_template(client, template)

    class FakeSubmitResponse:
        status_code = 200

        def json(self):
            return {"code": 200, "msg": "ok"}

    class FakeListResponse:
        status_code = 200

        def json(self):
            return {
                "code": 200,
                "data": {
                    "rows": [
                        {
                            "routeCode": "R1",
                            "assetName": "示例隧道上行",
                            "deptName": "示例部门",
                            "checkTime": "2026-07-24",
                            "weather": "晴",
                            "checker": "张三",
                            "recorder": "李四",
                            "devName": "示例设备",
                            "location": "K1+000-K2+000示例隧道",
                            "content": "示例检查",
                            "result": 1,
                            "carLicense": "示例车牌",
                            "nums": "1",
                        }
                    ]
                },
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeSubmitResponse()

        async def get(self, url, headers=None, params=None):
            assert url == "https://example.test/prod-api/patrol/deviceCheck/list"
            assert params["checkTime"] == "2026-07-24"
            return FakeListResponse()

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    asset = client.get("/api/tunnel-mechanical/templates").json()["assets"][0]

    response = client.post(
        "/api/tunnel-mechanical/submit",
        json={
            "base_url": "https://example.test",
            "checkTime": "2026-07-24",
            "weather": "晴",
            "checkerId": "1001",
            "checker": "张三",
            "recorderId": "1002",
            "recorder": "李四",
            "dry_run": False,
            "rows": [asset],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["result_rows"][0]["resultText"] == "正常"
    assert body["result_image_url"].startswith("/api/uploads/tunnel-mechanical-result-2026-07-24-")
    image_response = client.get(body["result_image_url"])
    assert image_response.status_code == 200
    assert image_response.content.startswith(b"\x89PNG")


def test_tunnel_mechanical_result_image_endpoint_queries_without_submit(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(base_url="https://example.test", username="station-user", password="secret")
    repo.save_tunnel_mechanical_state(
        access_token="cached-token",
        cookie_header="sid=abc",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=1)).isoformat(),
    )
    _import_tunnel_template(
        client,
        {**TEST_TUNNEL_TEMPLATE, "base_url": "https://example.test", "list_path": "/prod-api/patrol/deviceCheck/list"},
    )
    calls = {"get": 0, "post": 0}

    class FakeListResponse:
        status_code = 200

        def json(self):
            return {
                "code": 200,
                "rows": [
                    {
                        "assetName": "示例隧道上行",
                        "checkTime": "2026-07-24",
                        "checker": "张三",
                        "recorder": "李四",
                        "result": 1,
                    }
                ],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None, params=None):
            calls["get"] += 1
            return FakeListResponse()

        async def post(self, url, headers=None, json=None):
            calls["post"] += 1
            raise AssertionError("result image endpoint must not submit records")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    asset = client.get("/api/tunnel-mechanical/templates").json()["assets"][0]

    response = client.post(
        "/api/tunnel-mechanical/result-image",
        json={
            "base_url": "https://example.test",
            "checkTime": "2026-07-24",
            "weather": "晴",
            "checkerId": "1001",
            "checker": "张三",
            "recorderId": "1002",
            "recorder": "李四",
            "dry_run": False,
            "rows": [asset],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["result_image_url"].startswith("/api/uploads/tunnel-mechanical-result-2026-07-24-")
    assert calls == {"get": 1, "post": 0}


def test_tunnel_mechanical_result_image_relogs_in_when_cached_token_expired(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_config(base_url="https://example.test", username="station-user", password="secret")
    repo.save_tunnel_mechanical_state(
        access_token="expired-token",
        cookie_header="sid=old",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=1)).isoformat(),
    )
    _import_tunnel_template(
        client,
        {**TEST_TUNNEL_TEMPLATE, "base_url": "https://example.test", "list_path": "/prod-api/patrol/deviceCheck/list"},
    )
    calls = {"list": 0, "captcha": 0, "login": 0}
    seen_authorizations = []

    class FakeResponse:
        def __init__(self, body, status_code=200):
            self._body = body
            self.status_code = status_code
            self.text = ""

        def json(self):
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.cookies = main_module.httpx.Cookies()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None, params=None):
            if url == "https://example.test/prod-api/code":
                calls["captcha"] += 1
                return FakeResponse({"code": 200, "captchaEnabled": False, "uuid": "uuid-1"})
            assert url == "https://example.test/prod-api/patrol/deviceCheck/list"
            calls["list"] += 1
            seen_authorizations.append(headers.get("Authorization"))
            if calls["list"] == 1:
                return FakeResponse({"code": 401, "msg": "登录状态已过期"})
            return FakeResponse(
                {
                    "code": 200,
                    "rows": [
                        {
                            "assetName": "示例隧道上行",
                            "checkTime": "2026-07-24",
                            "result": 1,
                        }
                    ],
                }
            )

        async def post(self, url, headers=None, json=None):
            assert url == "https://example.test/prod-api/auth/login"
            calls["login"] += 1
            return FakeResponse({"code": 200, "data": {"access_token": "fresh-token", "expires_in": 7200}})

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)
    asset = client.get("/api/tunnel-mechanical/templates").json()["assets"][0]

    response = client.post(
        "/api/tunnel-mechanical/result-image",
        json={
            "base_url": "https://example.test",
            "checkTime": "2026-07-24",
            "weather": "晴",
            "checkerId": "",
            "checker": "",
            "recorderId": "",
            "recorder": "",
            "dry_run": False,
            "rows": [asset],
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert calls == {"list": 2, "captcha": 1, "login": 1}
    assert seen_authorizations == ["Bearer expired-token", "Bearer fresh-token"]


def test_tunnel_mechanical_result_image_queries_by_date_only(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_tunnel_mechanical_state(
        access_token="token-1",
        token_expires_at=(datetime.now(main_module.TZ) + timedelta(hours=1)).isoformat(),
    )
    _import_tunnel_template(
        client,
        {**TEST_TUNNEL_TEMPLATE, "base_url": "https://example.test", "list_path": "/prod-api/patrol/deviceCheck/list"},
    )
    captured_params = []

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "code": 200,
                "rows": [
                    {
                        "assetName": "未勾选隧道",
                        "checkTime": "2026-07-24",
                        "checker": "平台负责人",
                        "recorder": "平台记录人",
                        "result": 1,
                    }
                ],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None, params=None):
            captured_params.append(params)
            return FakeResponse()

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/api/tunnel-mechanical/result-image",
        json={
            "base_url": "https://example.test",
            "checkTime": "2026-07-24",
            "weather": "晴",
            "checkerId": "1001",
            "checker": "张三",
            "recorderId": "1002",
            "recorder": "李四",
            "rows": [],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["result_rows"][0]["assetName"] == "未勾选隧道"
    assert body["result_rows"][0]["checker"] == "平台负责人"
    assert captured_params[0] == {"pageNum": "1", "pageSize": "50", "checkTime": "2026-07-24"}


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


def test_today_reminders_omits_patrol_warning_end_events_when_disabled(tmp_path, monkeypatch):
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
        end_reminder_enabled=False,
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
    assert "patrol_warning_end" not in kinds


def test_expired_patrol_warning_is_hidden_after_window(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 24, 3, 0, tzinfo=tz)

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
            "start_time": "2026-07-21T01:00:00+08:00",
            "end_time": "2026-07-22T02:00:00+08:00",
            "create_time": "2026-07-21T01:10:00+08:00",
            "start_stake": "K107.000",
            "end_stake": "K137.730",
        }
    )
    client = TestClient(app)

    config_response = client.get("/api/patrol-warning-config")
    today_response = client.get("/api/reminders/today")
    image_response = client.get("/api/patrol-warning-image")

    assert config_response.status_code == 200
    assert config_response.json()["state"]["warning"] == {}
    assert today_response.status_code == 200
    today_body = today_response.json()
    assert not any(event["kind"].startswith("patrol_warning_") for event in today_body["events"])
    assert not any(status["key"] == "patrol_warning" for status in today_body["group_statuses"])
    assert image_response.status_code == 404


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


def test_cleanup_old_uploads_removes_expired_files(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    expired = upload_dir / "expired.png"
    fresh = upload_dir / "fresh.png"
    expired.write_bytes(b"old")
    fresh.write_bytes(b"new")
    old_timestamp = (datetime.now(main_module.TZ) - timedelta(days=91)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))
    monkeypatch.setattr(main_module, "UPLOAD_KEEP_DAYS", 90)

    main_module._cleanup_old_uploads(upload_dir)

    assert not expired.exists()
    assert fresh.exists()


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
            "grid": [{"name": "示例甲", "days": {"16": "晚"}}],
        },
    )
    reminder_response = client.post(
        "/api/custom-reminders",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "wechat_group_room_id": "room-1",
            "wechat_group_room_name": "通知群",
            "wechat_group_member_id": "stable-member-1",
            "wechat_group_runtime_sender_id": "@member-1",
            "wechat_group_member_name": "示例甲微信",
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
    assert personnel_response.json()["people"] == [
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
    events = preview_response.json()["events"]
    assert any(
        event["kind"] == "custom"
        and event["person_name"] == "示例甲"
        and event["send_at"] == "2025-09-16T21:00:00+08:00"
        and event["content"] == "示例甲 需要关闭隧道灯"
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


def test_lightagent_notification_config_hides_secret_fields_and_tests_send(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeLightAgentClient:
        def __init__(self, *, endpoint_url: str, target: str = "", targets: list[str] | None = None, token: str = ""):
            sent["endpoint_url"] = endpoint_url
            sent["target"] = target
            sent["targets"] = targets or []
            sent["token"] = token

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

    monkeypatch.setattr("app.main.LightAgentNotifyClient", FakeLightAgentClient)
    monkeypatch.setattr(
        main_module,
        "_lightagent_web_request",
        lambda repo, method, path, *, params=None, json_body=None: {"channels": []} if method == "GET" else {"status": "success"},
    )
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    save_response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "https://lightagent.test/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_target": "room-1",
            "lightagent_targets": [{"id": "room-1", "name": "通知群"}, {"id": "room-2", "name": "第二通知群"}],
            "message_template": "{name} {date} {shift_label}",
        },
    )
    get_response = client.get("/api/notification-config")
    test_response = client.post(
        "/api/notification-config/test",
        json={"test_mobile": "10000000000", "test_wechat_member_id": "@wechat-member-1"},
    )

    assert save_response.status_code == 200
    public_config = get_response.json()["config"]
    assert public_config["sender_type"] == "lightagent"
    assert public_config["lightagent_url"] == "https://lightagent.test/api/push/send"
    assert public_config["lightagent_configured"] is True
    assert public_config["lightagent_token_configured"] is True
    assert public_config["lightagent_target"] == "room-1"
    assert public_config["lightagent_targets"] == [
        {"id": "room-1", "name": "通知群"},
        {"id": "room-2", "name": "第二通知群"},
    ]
    assert test_response.status_code == 200
    assert sent == {
        "endpoint_url": "https://lightagent.test/api/push/send",
        "target": "",
        "targets": ["room-1", "room-2"],
        "token": "push-token",
        "content": "示例甲 2025-09-16 中班",
        "mobiles": ["@wechat-member-1"],
    }


def test_notification_test_failure_sanitizes_wechat_ids(tmp_path, monkeypatch):
    class FakeLightAgentClient:
        def __init__(self, *, endpoint_url: str, target: str = "", targets: list[str] | None = None, token: str = ""):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            raise main_module.WeComError("wgr_notice failed; @member-runtime failed")

    monkeypatch.setattr("app.main.LightAgentNotifyClient", FakeLightAgentClient)
    monkeypatch.setattr(
        main_module,
        "_sync_lightagent_notification_targets",
        lambda repo, sender_type, targets: {"success": True},
    )
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "https://lightagent.test/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_targets": [{"id": "wgr_notice", "name": "通知群"}],
        },
    )
    client.post(
        "/api/personnel",
        json={
            "names": ["王路飞"],
            "people": [
                {
                    "name": "王路飞",
                    "wechat_group_runtime_sender_id": "@member-runtime",
                    "wechat_group_member_name": "王路飞",
                }
            ],
        },
    )

    response = client.post(
        "/api/notification-config/test",
        json={"test_wechat_member_id": "@member-runtime", "test_wechat_member_name": "王路飞"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "通知群 failed; 王路飞 failed"


def test_lightagent_notification_config_syncs_target_to_wechat_group_channel(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        if method == "GET" and path == "/api/channels":
            return {
                "channels": [
                    {
                        "name": "wechat_group",
                        "connected": True,
                        "active": True,
                        "extra": {
                            "stable_selected_room_ids": ["wgr_existing"],
                            "selected_room_ids": ["wgr_existing"],
                        },
                    }
                ]
            }
        if method == "POST" and path == "/api/channels":
            selected = json_body["config"]["wechat_group_stable_room_ids"]
            return {
                "status": "success",
                "restarted": False,
                "extra": {
                    "stable_selected_room_ids": selected,
                    "selected_room_ids": selected,
                },
            }
        raise AssertionError(f"unexpected LightAgent request: {method} {path}")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "http://lightagent:9899/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_targets": [
                {"id": "wgr_notice", "name": "通知群"},
                {"id": "wgr_second", "name": "第二通知群"},
            ],
            "message_template": "{name}",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["lightagent_sync"]["success"] is True
    assert body["lightagent_sync"]["selected_room_ids"] == ["wgr_existing", "wgr_notice", "wgr_second"]
    assert calls[-1] == {
        "method": "POST",
        "path": "/api/channels",
        "params": None,
        "json_body": {
            "action": "save",
            "channel": "wechat_group",
            "config": {"wechat_group_stable_room_ids": ["wgr_existing", "wgr_notice", "wgr_second"]},
        },
    }


def test_lightagent_notification_config_reports_inactive_stable_target(tmp_path, monkeypatch):
    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        if method == "GET" and path == "/api/channels":
            return {
                "channels": [
                    {
                        "name": "wechat_group",
                        "connected": True,
                        "active": True,
                        "extra": {
                            "stable_selected_room_ids": [],
                            "selected_room_ids": [],
                            "rooms": [
                                {"id": "wgr_notice", "stable_room_id": "wgr_notice", "name": "通知群"},
                            ],
                        },
                    }
                ]
            }
        if method == "POST" and path == "/api/channels":
            selected = json_body["config"]["wechat_group_stable_room_ids"]
            return {
                "status": "success",
                "extra": {
                    "stable_selected_room_ids": selected,
                    "selected_room_ids": selected,
                    "rooms": [
                        {"id": "wgr_notice", "stable_room_id": "wgr_notice", "name": "通知群"},
                    ],
                },
            }
        raise AssertionError(f"unexpected LightAgent request: {method} {path}")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "http://lightagent:9899/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_targets": [{"id": "wgr_notice", "name": "通知群"}],
            "message_template": "{name}",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["lightagent_sync"]["success"] is False
    assert body["lightagent_sync"]["inactive_targets"] == ["wgr_notice"]
    assert "当前没有可发送会话" in body["lightagent_sync"]["message"]


def test_lightagent_notification_config_requires_connected_wechat_channel(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        if method == "GET" and path == "/api/channels":
            return {
                "channels": [
                    {
                        "name": "wechat_group",
                        "connected": False,
                        "active": False,
                        "login_status": "qr_ready",
                        "extra": {
                            "stable_selected_room_ids": ["wgr_notice"],
                            "selected_room_ids": ["wgr_notice"],
                        },
                    }
                ]
            }
        raise AssertionError(f"unexpected LightAgent request: {method} {path}")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "http://lightagent:9899/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_targets": [{"id": "wgr_notice", "name": "通知群"}],
            "message_template": "{name}",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["lightagent_sync"]["success"] is False
    assert body["lightagent_sync"]["login_status"] == "qr_ready"
    assert "未登录或未连接" in body["lightagent_sync"]["message"]
    assert [call["method"] for call in calls] == ["GET"]


def test_lightagent_notification_config_reports_sync_failure_without_losing_save(tmp_path, monkeypatch):
    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        raise RuntimeError("room service unavailable")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "http://lightagent:9899/api/push/send",
            "lightagent_target": "wgr_notice",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["config"]["lightagent_target"] == "wgr_notice"
    assert body["lightagent_sync"] == {
        "success": False,
        "target": "wgr_notice",
        "targets": ["wgr_notice"],
        "source": "notification",
        "message": "room service unavailable",
    }


def test_lightagent_notification_env_defaults_are_used_for_empty_database(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeLightAgentClient:
        def __init__(self, *, endpoint_url: str, target: str = "", targets: list[str] | None = None, token: str = ""):
            sent["endpoint_url"] = endpoint_url
            sent["target"] = target
            sent["targets"] = targets or []
            sent["token"] = token

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

    monkeypatch.setenv("NOTIFICATION_SENDER_TYPE", "lightagent")
    monkeypatch.setenv("LIGHTAGENT_BASE_URL", "http://lightagent:9899")
    monkeypatch.setenv("LIGHTAGENT_PUSH_TOKEN", "push-token")
    monkeypatch.setenv("LIGHTAGENT_NOTIFY_TARGET", "room-1")
    monkeypatch.setattr("app.main.LightAgentNotifyClient", FakeLightAgentClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="http://old-lightagent:9899/api/push/send",
        lightagent_token="old-token",
        lightagent_target="old-room",
    )
    client = TestClient(app)

    public_config = client.get("/api/notification-config").json()["config"]
    test_response = client.post("/api/notification-config/test", json={"test_mobile": "10000000000"})

    assert public_config["sender_type"] == "lightagent"
    assert public_config["lightagent_url"] == "http://old-lightagent:9899/api/push/send"
    assert public_config["lightagent_configured"] is True
    assert public_config["lightagent_token_configured"] is True
    assert public_config["lightagent_target"] == "old-room"
    assert public_config["lightagent_targets"] == [{"id": "old-room", "name": ""}]
    assert test_response.status_code == 200
    assert sent["endpoint_url"] == "http://old-lightagent:9899/api/push/send"
    assert sent["target"] == ""
    assert sent["targets"] == ["old-room"]
    assert sent["token"] == "old-token"


def test_saved_wechat_bridge_notification_channel_is_not_overridden_by_wecom_env(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FailingWebhookClient:
        def __init__(self, *, webhook_url: str):
            raise AssertionError("企业微信机器人不应在个人微信群通道下生效")

    class FakeWechatBridgeClient:
        is_wechat_bridge = True

        def __init__(self, *, targets: list[str]):
            sent["targets"] = targets

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

    monkeypatch.setenv("WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("NOTIFICATION_SENDER_TYPE", "wecom_webhook")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.test/cgi-bin/webhook/send?key=env-wecom")
    monkeypatch.setattr("app.main.WeComWebhookClient", FailingWebhookClient)
    monkeypatch.setattr("app.main.WechatBridgeNotifyClient", FakeWechatBridgeClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="https://example.test/cgi-bin/webhook/send?key=old-wecom",
        lightagent_url="",
        lightagent_token="",
        lightagent_target="wgr_notice",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
        message_template="{name}",
    )
    client = TestClient(app)

    public_config = client.get("/api/notification-config").json()["config"]
    test_response = client.post("/api/notification-config/test", json={"test_wechat_member_id": "@member-runtime"})

    assert public_config["sender_type"] == "lightagent"
    assert public_config["lightagent_target"] == "wgr_notice"
    assert test_response.status_code == 200
    assert sent == {
        "targets": ["wgr_notice"],
        "content": "示例甲",
        "mentions": ["@member-runtime"],
    }


def test_lightagent_wechat_proxy_endpoints(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        if method == "GET" and path == "/api/wechat_group/qrlogin":
            return {"status": "success", "login_status": "connected"}
        if method == "POST" and path == "/api/wechat_group/qrlogin":
            return {"status": "success", "login_status": "waiting"}
        if method == "GET" and path == "/api/channels":
            return {
                "channels": [
                    {
                        "name": "wechat_group",
                        "connected": True,
                        "login_status": "connected",
                        "extra": {
                            "rooms": [{"id": "room-1", "name": "test-room"}],
                            "selected_room_ids": ["room-1"],
                            "selected_room_names": ["test-room"],
                        },
                    }
                ]
            }
        if method == "GET" and path == "/api/wechat-group/members":
            return {
                "status": "success",
                "members": [
                    {"runtime_sender_id": "@member-1", "sender_nickname": "Alice"},
                    {"id": "@member-2", "nickName": "Bob"},
                    {"sender_id": "@member-3", "sender_nickname": "@member-3"},
                ],
            }
        raise AssertionError(f"unexpected LightAgent request: {method} {path}")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    status_response = client.get("/api/lightagent/wechat/status")
    refresh_response = client.post("/api/lightagent/wechat/refresh")
    rooms_response = client.get("/api/lightagent/wechat/rooms")
    members_response = client.get("/api/lightagent/wechat/members?room_id=room-1")

    assert status_response.json()["login_status"] == "connected"
    assert refresh_response.json()["login_status"] == "waiting"
    assert rooms_response.json() == {
        "status": "success",
        "connected": True,
        "login_status": "connected",
        "rooms": [
            {
                "id": "room-1",
                "name": "test-room",
                "stable_room_id": "",
                "runtime_room_id": "room-1",
                "sendable": True,
            }
        ],
        "sendable_room_count": 1,
        "selected_room_ids": ["room-1"],
        "selected_room_names": ["test-room"],
    }
    assert members_response.json()["members"] == [
        {
            "runtime_sender_id": "@member-1",
            "sender_nickname": "Alice",
            "sender_id": "@member-1",
            "display_name": "Alice",
            "is_raw_id_name": False,
        },
        {
            "id": "@member-2",
            "nickName": "Bob",
            "runtime_sender_id": "@member-2",
            "sender_id": "@member-2",
            "display_name": "Bob",
            "sender_nickname": "Bob",
            "is_raw_id_name": False,
        },
        {
            "sender_id": "@member-3",
            "sender_nickname": "@member-3",
            "runtime_sender_id": "@member-3",
            "display_name": "@member-3",
            "is_raw_id_name": True,
        },
    ]
    assert calls[-1] == {
        "method": "GET",
        "path": "/api/wechat-group/members",
        "params": {"stable_room_id": "room-1", "limit": "500"},
        "json_body": None,
    }


def test_lightagent_wechat_rooms_marks_stable_room_without_runtime_unsendable(tmp_path, monkeypatch):
    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        if method == "GET" and path == "/api/channels":
            return {
                "channels": [
                    {
                        "name": "wechat_group",
                        "connected": True,
                        "login_status": "connected",
                        "extra": {
                            "rooms": [
                                {"id": "wgr_inactive", "stable_room_id": "wgr_inactive", "name": "历史群"},
                                {
                                    "id": "wgr_active",
                                    "stable_room_id": "wgr_active",
                                    "runtime_room_id": "room@@active",
                                    "name": "当前群",
                                },
                            ],
                        },
                    }
                ]
            }
        raise AssertionError(f"unexpected LightAgent request: {method} {path}")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/lightagent/wechat/rooms")

    assert response.status_code == 200
    rooms = response.json()["rooms"]
    assert rooms[0]["id"] == "wgr_inactive"
    assert rooms[0]["runtime_room_id"] == ""
    assert rooms[0]["sendable"] is False
    assert "群内发言" in rooms[0]["sendable_reason"]
    assert rooms[1]["id"] == "wgr_active"
    assert rooms[1]["runtime_room_id"] == "room@@active"
    assert rooms[1]["sendable"] is True


def test_wechat_query_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    response = client.post("/api/wechat-query", json={"text": "查询我的监控", "runtime_sender_id": "@member-1"})

    assert response.status_code == 401


def test_feature_channel_config_hides_password_and_restricts_wechat_room(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        if method == "GET" and path == "/api/channels":
            return {
                "channels": [
                    {
                        "name": "wechat_group",
                        "connected": True,
                        "active": True,
                        "extra": {
                            "stable_selected_room_ids": ["wgr_notice"],
                            "selected_room_ids": ["wgr_notice"],
                        },
                    }
                ]
            }
        if method == "POST" and path == "/api/channels":
            selected = json_body["config"]["wechat_group_stable_room_ids"]
            return {
                "status": "success",
                "extra": {
                    "stable_selected_room_ids": selected,
                    "selected_room_ids": selected,
                },
            }
        raise AssertionError(f"unexpected LightAgent request: {method} {path}")

    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    save_response = client.post(
        "/api/feature-channel-config",
        json={
            "enabled": True,
            "lightagent_web_url": "http://lightagent.test",
            "lightagent_web_password": "secret",
            "wechat_group_room_id": "wgr_feature",
            "wechat_group_room_name": "功能群",
            "wechat_group_rooms": [
                {"id": "wgr_feature", "name": "功能群"},
                {"id": "wgr_second", "name": "第二功能群"},
            ],
            "allow_tunnel_mechanical": True,
            "allow_duty_query": True,
            "allow_roster_import": True,
        },
    )
    get_response = client.get("/api/feature-channel-config")
    wrong_room = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电", "stable_room_id": "wgr_other", "room_id": "room@@other"},
    )
    second_room = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电", "stable_room_id": "wgr_second", "room_id": "room@@second"},
    )

    assert save_response.status_code == 200
    assert save_response.json()["lightagent_sync"]["success"] is True
    assert save_response.json()["lightagent_sync"]["selected_room_ids"] == ["wgr_notice", "wgr_feature", "wgr_second"]
    assert calls[-1]["json_body"] == {
        "action": "save",
        "channel": "wechat_group",
        "config": {"wechat_group_stable_room_ids": ["wgr_notice", "wgr_feature", "wgr_second"]},
    }
    config = get_response.json()["config"]
    assert config["lightagent_web_url"] == "http://lightagent.test"
    assert config["lightagent_web_password_configured"] is True
    assert config["wechat_group_room_id"] == "wgr_feature"
    assert [room["id"] for room in config["wechat_group_rooms"]] == ["wgr_feature", "wgr_second"]
    assert second_room.status_code == 200
    assert wrong_room.status_code == 403
    assert "功能群" in wrong_room.json()["detail"]


def test_feature_channel_config_reports_lightagent_sync_failure_without_losing_save(tmp_path, monkeypatch):
    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        raise RuntimeError("LightAgent offline")

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/feature-channel-config",
        json={
            "enabled": True,
            "wechat_group_room_id": "wgr_feature",
            "wechat_group_room_name": "功能群",
            "wechat_group_rooms": [{"id": "wgr_feature", "name": "功能群"}],
            "allow_tunnel_mechanical": True,
            "allow_duty_query": True,
            "allow_roster_import": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["config"]["wechat_group_room_id"] == "wgr_feature"
    assert body["lightagent_sync"] == {
        "success": False,
        "target": "wgr_feature",
        "targets": ["wgr_feature"],
        "source": "feature_channel",
        "message": "LightAgent offline",
    }


def test_feature_channel_can_disable_tunnel_mechanical_query(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/feature-channel-config",
        json={
            "enabled": True,
            "wechat_group_room_id": "wgr_feature",
            "allow_tunnel_mechanical": False,
            "allow_duty_query": True,
            "allow_roster_import": True,
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电", "stable_room_id": "wgr_feature"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "该功能未在功能通道启用"


def test_feature_channel_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/feature-channel-config",
        json={
            "enabled": False,
            "wechat_group_room_id": "wgr_feature",
            "allow_tunnel_mechanical": True,
            "allow_duty_query": True,
            "allow_roster_import": True,
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "闅ч亾鏈虹數", "stable_room_id": "wgr_feature"},
    )

    assert response.status_code == 403


def test_wechat_query_help_returns_numbered_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 24))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "help"
    assert "监控查询菜单" in body["reply"]
    assert "1. 查询我的监控" in body["reply"]
    assert "7. 查询我的绑定" in body["reply"]
    assert "9. 查询2026-07-24机电" in body["reply"]
    assert "回复序号即可执行" in body["reply"]
    assert "录入格式：隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴" in body["reply"]


def test_wechat_query_numbered_menu_selection_runs_command(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 23))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    async def fake_query(repo, request, uploads):
        return {
            "success": True,
            "result_rows": [{"assetName": "示例隧道上行"}],
            "result_image_url": f"/api/uploads/result-{request.checkTime.isoformat()}.png",
        }

    monkeypatch.setattr(main_module, "_query_tunnel_mechanical_result_image", fake_query)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "8"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_result"
    assert body["checkTime"] == "2026-07-23"


def test_wechat_query_tunnel_mechanical_returns_fill_template(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 23))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    async def fail_submit(repo, request, **kwargs):
        raise AssertionError("template request must not submit")

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fail_submit)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "@登录账号 隧道机电"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_template"
    assert "隧道机电功能" in body["reply"]
    assert "查询今日机电" in body["reply"]
    assert body["template"] == "隧道机电录入 日期2026-07-23 负责人罗富耀 记录人商邱宏 天气晴"
    assert body["replies"][-1] == "隧道机电录入 日期2026-07-23 负责人罗富耀 记录人商邱宏 天气晴"
    assert "当前模板资产：1 条" in body["reply"]


def test_wechat_query_tunnel_mechanical_format_command_sends_copyable_template_separately(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 24))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    async def fail_submit(repo, request, **kwargs):
        raise AssertionError("format request must not submit")

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fail_submit)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电录入格式"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_template"
    assert body["replies"][0].startswith("隧道机电功能")
    assert body["replies"][1] == "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴"


def test_wechat_query_tunnel_mechanical_accepts_bot_name_starting_with_at(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 23))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    async def fail_submit(repo, request, **kwargs):
        raise AssertionError("template request must not submit")

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fail_submit)

    for text in ("@@\u2005隧道机电", "@@隧道机电\u2005隧道机电"):
        response = client.post(
            "/api/wechat-query",
            headers={"X-Duty-Query-Token": "unit-token"},
            json={"text": text},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["query_type"] == "tunnel_mechanical_template"
        assert "隧道机电功能" in body["reply"]


def test_wechat_query_tunnel_mechanical_result_sends_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 23))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)
    captured = []

    async def fake_query(repo, request, uploads):
        captured.append(request.checkTime.isoformat())
        return {
            "success": True,
            "result_rows": [{"assetName": "示例隧道上行"}],
            "result_image_url": f"/api/uploads/result-{request.checkTime.isoformat()}.png",
        }

    monkeypatch.setattr(main_module, "_query_tunnel_mechanical_result_image", fake_query)

    today_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询今日机电"},
    )
    date_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询2026-07-22机电"},
    )

    assert today_response.status_code == 200
    assert date_response.status_code == 200
    today_body = today_response.json()
    date_body = date_response.json()
    assert today_body["success"] is True
    assert today_body["query_type"] == "tunnel_mechanical_result"
    assert today_body["checkTime"] == "2026-07-23"
    assert today_body["image_url"] == "/api/uploads/result-2026-07-23.png"
    assert today_body["image_full_url"] == "/api/uploads/result-2026-07-23.png"
    assert "图片已生成，正在发送" in today_body["reply"]
    assert date_body["checkTime"] == "2026-07-22"
    assert date_body["image_url"] == "/api/uploads/result-2026-07-22.png"
    assert captured == ["2026-07-23", "2026-07-22"]


def test_wechat_bridge_group_command_requires_at_mention(tmp_path, monkeypatch):
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    calls = []

    async def fake_build_wechat_query_response(repo_arg, query, *, uploads):
        calls.append(query.text)
        return {"success": True, "reply": ""}

    class DummyManager:
        def send_text(self, room_id, text, *, mention_ids=None):
            raise AssertionError("empty reply should not send")

        def send_image(self, room_id, path):
            raise AssertionError("no image should not send")

    monkeypatch.setattr(main_module, "_build_wechat_query_response", fake_build_wechat_query_response)
    monkeypatch.setattr(main_module, "get_wechat_bridge_manager", lambda: DummyManager())

    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "查询今日机电",
            "is_at": False,
        },
    )
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "@闷葫芦 查询今日机电",
            "is_at": True,
        },
    )
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "@闷葫芦 8",
            "is_at": True,
        },
    )
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "stable_member_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "@闷葫芦 绑定商邱宏",
            "is_at": True,
        },
    )
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "查询今日机电@闷葫芦\u2005",
            "is_at": True,
        },
    )
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "查询@闷葫芦\u2005",
            "is_at": True,
        },
    )

    assert calls == [
        "@闷葫芦 查询今日机电",
        "@闷葫芦 8",
        "@闷葫芦 绑定商邱宏",
        "查询今日机电@闷葫芦",
        "查询@闷葫芦",
    ]


def test_wechat_bridge_sends_multiple_text_replies(tmp_path, monkeypatch):
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    sent: list[tuple[str, str]] = []

    async def fake_build_wechat_query_response(repo_arg, query, *, uploads):
        return {
            "success": True,
            "replies": [
                "隧道机电功能",
                "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴",
            ],
            "reply": "fallback should not be sent when replies exist",
        }

    class DummyManager:
        def send_text(self, room_id, text, *, mention_ids=None):
            sent.append((room_id, text))

        def send_image(self, room_id, path):
            raise AssertionError("no image should not send")

    monkeypatch.setattr(main_module, "_build_wechat_query_response", fake_build_wechat_query_response)
    monkeypatch.setattr(main_module, "get_wechat_bridge_manager", lambda: DummyManager())

    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "@闷葫芦 隧道机电录入格式",
            "is_at": True,
        },
    )

    assert sent == [
        ("wgr_feature", "隧道机电功能"),
        ("wgr_feature", "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴"),
    ]


def test_wechat_bridge_bind_command_replies_success(tmp_path, monkeypatch):
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_personnel_names(["商邱宏"])
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    sent: list[tuple[str, str]] = []

    class DummyManager:
        def send_text(self, room_id, text, *, mention_ids=None):
            sent.append((room_id, text))

        def send_image(self, room_id, path):
            raise AssertionError("no image should not send")

    monkeypatch.setattr(main_module, "get_wechat_bridge_manager", lambda: DummyManager())

    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "room_name": "功能群",
            "sender_id": "wgm_stable_member",
            "stable_member_id": "wgm_stable_member",
            "runtime_sender_id": "@runtime-member",
            "sender_name": "商邱宏微信",
            "text": "@闷葫芦 绑定商邱宏",
            "is_at": True,
        },
    )

    assert sent and sent[0][0] == "wgr_feature"
    assert "绑定成功：商邱宏" in sent[0][1]
    bound = next(person for person in repo.list_personnel() if person["name"] == "商邱宏")
    assert bound["wechat_group_member_id"] == "wgm_stable_member"
    assert bound["wechat_group_runtime_sender_id"] == "@runtime-member"


def test_wechat_query_triggers_tunnel_mechanical_submit(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)
    captured = {}

    async def fake_submit(repo, request, **kwargs):
        captured["checkTime"] = request.checkTime.isoformat()
        captured["checkerId"] = request.checkerId
        captured["checker"] = request.checker
        captured["recorderId"] = request.recorderId
        captured["recorder"] = request.recorder
        captured["weather"] = request.weather
        captured["dry_run"] = request.dry_run
        captured["row_count"] = len(request.rows)
        return {"success": True, "dry_run": request.dry_run, "results": [], "result_image_url": "/api/uploads/result.png"}

    monkeypatch.setattr(main_module, "_submit_tunnel_mechanical", fake_submit)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电录入 日期2026-07-24 负责人张三 记录人李四 天气晴"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical"
    assert body["checkTime"] == "2026-07-24"
    assert body["checkerId"] == "1001"
    assert body["recorderId"] == "1002"
    assert body["weather"] == "晴"
    assert body["count"] == 1
    assert body["image_url"] == "/api/uploads/result.png"
    assert body["image_full_url"] == "/api/uploads/result.png"
    assert "/api/uploads/result.png" not in body["reply"]
    assert captured == {
        "checkTime": "2026-07-24",
        "checkerId": "1001",
        "checker": "张三",
        "recorderId": "1002",
        "recorder": "李四",
        "weather": "晴",
        "dry_run": False,
        "row_count": 1,
    }
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "tunnel_mechanical_wechat"
    assert records[0]["status"] == "success"


def test_wechat_query_tunnel_mechanical_missing_person_returns_help(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电录入 日期2026-07-24 天气晴"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["query_type"] == "tunnel_mechanical"
    assert "负责人/检查人" in body["reply"]
    assert "记录人" in body["reply"]


def test_wechat_roster_import_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/wechat-roster/import",
        files={"file": ("roster.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 401


def test_wechat_roster_import_auto_confirms_with_internal_token_when_admin_password_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")

    def fake_extract(path):
        return {
            "year": 2025,
            "month": 9,
            "source_image_path": str(path),
            "ocr_status": "template_ok",
            "grid": [{"name": "示例甲", "days": {"16": "中"}}],
        }

    monkeypatch.setattr("app.main.extract_roster_image", fake_extract)
    app = create_app(
        data_dir=tmp_path / "data",
        upload_dir=tmp_path / "uploads",
        start_scheduler=False,
        admin_password="admin-secret",
    )
    client = TestClient(app)

    response = client.post(
        "/api/wechat-roster/import",
        headers={"X-Duty-Query-Token": "unit-token"},
        files={"file": ("roster.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["import_status"] == "imported"
    assert "已导入 2025年9月排班表" in body["reply"]
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    roster = repo.get_roster_month(2025, 9)
    assert roster is not None
    assert roster["grid"] == [{"name": "示例甲", "days": {"16": "中"}}]


def test_wechat_roster_import_conflict_can_be_confirmed_with_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")

    def fake_extract(path):
        return {
            "year": 2025,
            "month": 9,
            "source_image_path": str(path),
            "ocr_status": "template_ok",
            "grid": [{"name": "示例甲", "days": {"16": "晚"}}],
        }

    monkeypatch.setattr("app.main.extract_roster_image", fake_extract)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={"year": 2025, "month": 9, "grid": [{"name": "示例甲", "days": {"16": "中"}}]},
    )

    import_response = client.post(
        "/api/wechat-roster/import",
        headers={"X-Duty-Query-Token": "unit-token"},
        files={"file": ("roster.png", b"fake-image", "image/png")},
    )

    assert import_response.status_code == 200
    import_body = import_response.json()
    assert import_body["success"] is False
    assert import_body["import_status"] == "conflict"
    assert "覆盖导入" in import_body["reply"]

    confirm_response = client.post(
        "/api/wechat-roster/confirm",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={
            "year": import_body["year"],
            "month": import_body["month"],
            "source_image_path": import_body["source_image_path"],
            "grid": import_body["grid"],
            "overwrite": True,
        },
    )

    assert confirm_response.status_code == 200
    confirm_body = confirm_response.json()
    assert confirm_body["success"] is True
    assert confirm_body["import_status"] == "imported_overwrite"
    assert "已覆盖导入 2025年9月排班表" in confirm_body["reply"]
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    assert repo.get_roster_month(2025, 9)["grid"] == [{"name": "示例甲", "days": {"16": "晚"}}]


def test_wechat_query_returns_bound_person_monitor_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice"],
            "people": [
                {
                    "name": "Alice",
                    "wechat_group_room_id": "room-1",
                    "wechat_group_member_id": "stable-member-1",
                    "wechat_group_runtime_sender_id": "@member-1",
                    "wechat_group_member_name": "Alice WeChat",
                }
            ],
        },
    )
    client.post(
        "/api/people",
        json={
            "name": "Alice",
            "daily_time": "07:40",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [{"name": "Alice", "days": {"16": "中"}}],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={
            "text": "查询我的监控",
            "runtime_sender_id": "@member-1",
            "stable_member_id": "stable-member-1",
            "target_date": "2025-09-16",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["person_name"] == "Alice"
    assert body["target_date"] == "2025-09-16"
    assert "Alice 2025-09-16" in body["reply"]
    assert "排班：中班 08:00至16:00" in body["reply"]
    assert "每日提醒" not in body["reply"]


def test_wechat_query_my_monitor_returns_near_seven_day_roster(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [{"name": "Alice", "days": {"15": "早", "16": "中", "17": "晚", "18": "休"}}],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询我的监控", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "monitor_range"
    assert body["person_name"] == "Alice"
    assert body["start_date"] == "2025-09-15"
    assert body["days"] == 7
    assert "今天 2025-09-15" in body["reply"]
    assert "明天 2025-09-16" in body["reply"]
    assert "后天 2025-09-17" in body["reply"]
    assert "早班 00:00至08:00" in body["reply"]
    assert "中班 08:00至16:00" in body["reply"]
    assert "夜班 16:00至00:00" in body["reply"]
    assert "提醒" not in body["reply"]


def test_wechat_monitor_commands_do_not_return_reminder_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice", "Bob", "Cindy"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post("/api/people", json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})
    client.post("/api/people", json={"name": "Bob", "daily_time": "08:10", "before_shift_minutes": 5, "enabled": True})
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [
                {"name": "Alice", "days": {"15": "早", "16": "中", "17": "晚", "18": "休"}},
                {"name": "Bob", "days": {"15": "中", "16": "晚", "17": "早"}},
                {"name": "Cindy", "days": {"15": "晚", "16": "早", "17": "中"}},
            ],
        },
    )

    cases = [
        ("查询我的监控", "monitor_range"),
        ("我的监控", "monitor_range"),
        ("查询明日监控", "monitor_all"),
        ("查询本周监控", "monitor_all_range"),
        ("查询未来7天", "monitor_all_range"),
        ("查询未来7天监控", "monitor_all_range"),
        ("查询罗熙云监控", "monitor"),
    ]
    client.post("/api/personnel", json={"names": ["Alice", "Bob", "Cindy", "罗熙云"]})
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "overwrite": True,
            "grid": [
                {"name": "Alice", "days": {"15": "早", "16": "中", "17": "晚", "18": "休"}},
                {"name": "Bob", "days": {"15": "中", "16": "晚", "17": "早"}},
                {"name": "Cindy", "days": {"15": "晚", "16": "早", "17": "中"}},
                {"name": "罗熙云", "days": {"15": "中"}},
            ],
        },
    )

    for text, query_type in cases:
        response = client.post(
            "/api/wechat-query",
            headers={"X-Duty-Query-Token": "unit-token"},
            json={"text": text, "runtime_sender_id": "@member-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True, text
        assert body["query_type"] == query_type, text
        assert "监控排班" in body["reply"], text
        assert "计划提醒" not in body["reply"], text
        assert "每日提醒" not in body["reply"], text
        assert "班前提醒" not in body["reply"], text


def test_wechat_reminder_commands_are_the_only_ones_returning_reminder_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post("/api/people", json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})
    client.post(
        "/api/rosters/confirm",
        json={"year": 2025, "month": 9, "grid": [{"name": "Alice", "days": {"15": "早", "16": "中"}}]},
    )

    for text in ("查询今日提醒", "查询我的提醒", "查询下次提醒"):
        response = client.post(
            "/api/wechat-query",
            headers={"X-Duty-Query-Token": "unit-token"},
            json={"text": text, "runtime_sender_id": "@member-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True, text
        assert body["query_type"] in {"reminder_all", "reminder", "next_reminder"}, text
        assert "提醒" in body["reply"], text


def test_wechat_query_returns_named_person_monitor_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["罗熙云"]})
    client.post(
        "/api/people",
        json={
            "name": "罗熙云",
            "daily_time": "07:40",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [{"name": "罗熙云", "days": {"16": "中"}}],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询罗熙云监控", "target_date": "2025-09-16"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "monitor"
    assert body["person_name"] == "罗熙云"
    assert body["target_date"] == "2025-09-16"
    assert "罗熙云 2025-09-16" in body["reply"]
    assert "中班 08:00至16:00" in body["reply"]


def test_wechat_query_reports_unbound_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/wechat-query",
        headers={"Authorization": "Bearer unit-token"},
        json={"text": "查询我的监控", "runtime_sender_id": "@missing-member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["query_type"] == "unbound"
    assert "还没有识别到“我”对应的人员" in body["reply"]
    assert "@missing-member" not in body["reply"]


def test_wechat_query_allows_unbound_group_member_to_query_all_today_reminders(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["Alice", "Bob"]})
    client.post("/api/people", json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})
    client.post("/api/people", json={"name": "Bob", "daily_time": "08:10", "before_shift_minutes": 5, "enabled": True})
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [{"name": "Alice", "days": {"16": "中"}}, {"name": "Bob", "days": {"16": "早"}}],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询今日提醒", "runtime_sender_id": "@missing-member", "target_date": "2025-09-16"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "reminder_all"
    assert body["target_date"] == "2025-09-16"
    assert "Alice" in body["reply"]
    assert "Bob" in body["reply"]
    assert "还没有识别到" not in body["reply"]


def test_wechat_query_tomorrow_monitor_returns_all_shift_summary_even_when_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice", "Bob", "Cindy"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [
                {"name": "Alice", "days": {"16": "早"}},
                {"name": "Bob", "days": {"16": "中"}},
                {"name": "Cindy", "days": {"16": "晚"}},
            ],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询明日监控@闷葫芦\u2005", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "monitor_all"
    assert body["target_date"] == "2025-09-16"
    assert "明天 2025-09-16" in body["reply"]
    assert "早班：Alice" in body["reply"]
    assert "中班：Bob" in body["reply"]
    assert "晚班：Cindy" in body["reply"]
    assert "提醒" not in body["reply"]


def test_wechat_query_allows_unbound_group_member_to_query_all_range(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["Alice"]})
    client.post("/api/people", json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})
    client.post(
        "/api/rosters/confirm",
        json={"year": 2025, "month": 9, "grid": [{"name": "Alice", "days": {"15": "早", "16": "中"}}]},
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询未来3天", "runtime_sender_id": "@missing-member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "monitor_all_range"
    assert body["start_date"] == "2025-09-15"
    assert body["days"] == 3
    assert "监控排班" in body["reply"]
    assert "早班：Alice" in body["reply"]
    assert "中班：Alice" in body["reply"]


def test_wechat_query_allows_unbound_group_member_to_query_all_next_reminders(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 15, 7, 0, tzinfo=tz)

    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["Alice"]})
    client.post("/api/people", json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})
    client.post(
        "/api/rosters/confirm",
        json={"year": 2025, "month": 9, "grid": [{"name": "Alice", "days": {"15": "中"}}]},
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询下次提醒", "runtime_sender_id": "@missing-member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "next_reminder_all"
    assert "全员下次提醒" in body["reply"]
    assert "Alice" in body["reply"]


def test_wechat_query_matches_saved_stable_member_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["商邱宏"],
            "people": [
                {
                    "name": "商邱宏",
                    "wechat_group_member_id": "wgm_stable_member",
                    "wechat_group_member_name": "商邱宏微信",
                }
            ],
        },
    )
    client.post("/api/people", json={"name": "商邱宏", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询我的绑定", "sender_id": "wgm_stable_member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "binding"
    assert body["person_name"] == "商邱宏"


def test_wechat_binding_query_is_not_treated_as_bind_command(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["商邱宏"],
            "people": [
                {
                    "name": "商邱宏",
                    "wechat_group_runtime_sender_id": "@runtime-member",
                    "wechat_group_member_name": "商邱宏微信",
                }
            ],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "绑定查询", "runtime_sender_id": "@runtime-member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "binding"
    assert body["person_name"] == "商邱宏"


def test_wechat_query_can_bind_current_sender_to_person_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["旧人员", "商邱宏"]})
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_personnel_contacts(
        [
            {
                "name": "旧人员",
                "wechat_group_member_id": "wgm_stable_member",
                "wechat_group_runtime_sender_id": "@runtime-member",
                "wechat_group_member_name": "旧微信名",
            }
        ]
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={
            "text": "绑定商邱宏",
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "room_name": "功能群",
            "sender_id": "wgm_stable_member",
            "stable_member_id": "wgm_stable_member",
            "runtime_sender_id": "@runtime-member",
            "sender_name": "商邱宏微信",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "binding_update"
    assert body["person_name"] == "商邱宏"
    people = DutyRepository(tmp_path / "data" / "duty-reminder.db").list_personnel()
    assert next(person for person in people if person["name"] == "旧人员") == {"name": "旧人员", "mention_mobile": ""}
    bound = next(person for person in people if person["name"] == "商邱宏")
    assert bound["wechat_group_room_id"] == "wgr_feature"
    assert bound["wechat_group_room_name"] == "功能群"
    assert bound["wechat_group_member_id"] == "wgm_stable_member"
    assert bound["wechat_group_runtime_sender_id"] == "@runtime-member"
    assert bound["wechat_group_member_name"] == "商邱宏微信"


def test_wechat_query_accepts_natural_date_shift_question(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post(
        "/api/people",
        json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True},
    )
    client.post(
        "/api/rosters/confirm",
        json={"year": 2025, "month": 9, "grid": [{"name": "Alice", "days": {"16": "中"}}]},
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "我9月16日什么班", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "monitor"
    assert body["target_date"] == "2025-09-16"
    assert "中班 08:00至16:00" in body["reply"]


def test_wechat_query_returns_future_range_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 15))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post(
        "/api/people",
        json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True},
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [{"name": "Alice", "days": {"15": "早", "16": "中", "17": "休"}}],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询未来3天", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "monitor_all_range"
    assert body["start_date"] == "2025-09-15"
    assert body["days"] == 3
    assert "今天 2025-09-15" in body["reply"]
    assert "明天 2025-09-16" in body["reply"]
    assert "后天 2025-09-17" in body["reply"]
    assert "休息" in body["reply"]


def test_wechat_query_returns_next_reminder(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 9, 15, 6, 0, tzinfo=tz)

    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["Alice"],
            "people": [{"name": "Alice", "wechat_group_runtime_sender_id": "@member-1"}],
        },
    )
    client.post(
        "/api/people",
        json={"name": "Alice", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True},
    )
    client.post(
        "/api/rosters/confirm",
        json={"year": 2025, "month": 9, "grid": [{"name": "Alice", "days": {"15": "中"}}]},
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "下次提醒", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "next_reminder"
    assert "Alice 下次提醒" in body["reply"]
    assert "07:40" in body["reply"]


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


def test_monitored_person_roundtrips_wechat_binding(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/people",
        json={
            "name": "示例甲",
            "mention_mobile": "10000000000",
            "wechat_group_room_id": "room-1",
            "wechat_group_room_name": "功能群",
            "wechat_group_member_id": "stable-member-1",
            "wechat_group_runtime_sender_id": "@member-1",
            "wechat_group_member_name": "示例甲微信 · @member-1",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )

    assert response.status_code == 200
    person = response.json()["people"][0]
    assert person["wechat_group_room_id"] == "room-1"
    assert person["wechat_group_room_name"] == "功能群"
    assert person["wechat_group_member_id"] == "stable-member-1"
    assert person["wechat_group_runtime_sender_id"] == "@member-1"
    assert person["wechat_group_member_name"] == "示例甲微信 · @member-1"


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


def test_patrol_warning_monitor_skips_end_reminder_when_disabled(tmp_path, monkeypatch):
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
            "warning_level": "3",
            "warning_level_label": "Orange",
            "start_time": "2026-07-22T01:00:00+08:00",
            "end_time": "2026-07-22T02:00:00+08:00",
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
        end_reminder_enabled=False,
        end_reminder_interval_hours=6,
        end_reminder_window_hours=48,
    )
    repo.save_patrol_warning_state(
        warning_key="warning-1",
        warning=warning.as_dict(),
        last_start_sent_key="warning-1",
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    assert sent == []
    assert repo.get_patrol_warning_state()["last_end_reminder_slot"] == ""


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


def test_personal_wechat_notification_test_records_member_name(tmp_path, monkeypatch):
    sent = {}

    class FakeWechatClient:
        is_wechat_bridge = True

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

    monkeypatch.setattr("app.main._notification_client_from_config", lambda config: FakeWechatClient())
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [{"id": "wgr_notice", "name": "通知群"}],
        },
    )

    response = client.post(
        "/api/notification-config/test",
        json={"test_wechat_member_id": "@member-runtime", "test_wechat_member_name": "王路飞 · @member-runtime"},
    )

    assert response.status_code == 200
    assert sent["mentions"] == ["@member-runtime"]
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "notification_test"
    assert records[0]["target"] == "王路飞"


def test_personal_wechat_patrol_warning_uses_true_all_mention(tmp_path):
    class FakeWechatClient:
        is_wechat_bridge = True

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_patrol_warning_config(mention_all=True)

    mentions = main_module._patrol_warning_mentions_for_client(
        repo,
        repo.get_patrol_warning_config(),
        FakeWechatClient(),
    )

    assert mentions == ["@all"]


def test_send_records_display_wechat_runtime_id_as_member_name(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["王路飞"],
            "people": [
                {
                    "name": "王路飞",
                    "wechat_group_runtime_sender_id": "@member-runtime",
                    "wechat_group_member_name": "王路飞 · @member-runtime",
                }
            ],
        },
    )
    repo = DutyRepository(data_dir / "duty-reminder.db")
    repo.save_send_record(kind="notification_test", target="@member-runtime", status="success")

    records = client.get("/api/send-records").json()["records"]

    assert records[0]["target"] == "王路飞"


def test_send_records_display_wechat_room_ids_as_room_names(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [
                {"id": "wgr_notice", "name": "通知群"},
                {"id": "wgr_second", "name": "第二通知群"},
            ],
        },
    )
    client.post(
        "/api/personnel",
        json={
            "names": ["王路飞"],
            "people": [
                {
                    "name": "王路飞",
                    "wechat_group_runtime_sender_id": "@member-runtime",
                    "wechat_group_member_name": "王路飞",
                }
            ],
        },
    )
    repo = DutyRepository(data_dir / "duty-reminder.db")
    repo.save_send_record(
        kind="daily_duty_test",
        target="wgr_notice",
        status="failed",
        error="wgr_notice: target room is not active; wgr_second: target room is not active; @member-runtime failed",
    )

    record = client.get("/api/send-records").json()["records"][0]

    assert record["target"] == "通知群"
    assert "通知群" in record["error"]
    assert "第二通知群" in record["error"]
    assert "王路飞" in record["error"]
    assert "wgr_" not in record["target"]
    assert "wgr_" not in record["error"]
    assert "@member-runtime" not in record["error"]


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
                {"name": "示例辛", "days": {"17": "早"}},
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
    assert names_response.json()["names"] == sorted(["示例甲", "示例乙", "示例丙", "示例丁", "示例戊", "示例己", "示例庚", "示例癸", "示例壬", "示例辛"])
    assert preview_response.status_code == 200
    body = preview_response.json()
    assert body["send_at"] == "2025-09-16T07:20:00+08:00"
    assert body["content"] == (
        "今日在岗人员\n"
        "监控班：今日早班：示例丁，明日早班：示例辛，中班：示例己，晚班：示例甲\n"
        "驾驶员：大车：示例庚 小车：示例丙\n"
        "备勤人员：示例乙\n"
        "今日下午休息：示例戊\n"
        "正在休息：示例癸\n"
        "今日下午到岗：示例壬"
    )
    assert body["details"]["early"] == "示例丁"
    assert body["details"]["tomorrow_early"] == "示例辛"
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
        [{"name": "示例甲", "days": {"16": "晚"}}],
        "uploads/month.png",
    )
    repo.save_custom_reminder(
        name="示例甲",
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
    assert records[0]["target"] == "示例甲"
    assert records[0]["status"] == "success"


def test_due_custom_reminder_sends_with_saved_wechat_member(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 21, 0, 25, tzinfo=tz)

    class FakePersonalWechatClient:
        is_wechat_bridge = True

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("自定义提醒不应该发送图片")

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="https://lightagent.test/api/push/send",
        lightagent_token="push-token",
        lightagent_target="wgr_notice",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
    )
    repo.save_roster_month(
        2025,
        9,
        [{"name": "示例甲", "days": {"16": "晚"}}],
        "uploads/month.png",
    )
    repo.save_custom_reminder(
        name="示例甲",
        mention_mobile="",
        wechat_group_room_id="wgr_notice",
        wechat_group_room_name="通知群",
        wechat_group_member_id="stable-member-1",
        wechat_group_runtime_sender_id="@member-runtime",
        wechat_group_member_name="示例甲微信",
        shift_code="night",
        reminder_time="21:00",
        message="需要关闭隧道灯",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "需要关闭隧道灯"
    assert sent["mentions"] == ["@member-runtime"]
    records = repo.list_send_records()
    assert records[0]["kind"] == "custom"
    assert records[0]["status"] == "success"


def test_due_custom_reminder_without_wechat_binding_adds_visible_at_name(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 21, 0, 25, tzinfo=tz)

    class FakePersonalWechatClient:
        is_wechat_bridge = True

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("自定义提醒不应该发送图片")

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="https://lightagent.test/api/push/send",
        lightagent_token="push-token",
        lightagent_target="wgr_notice",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
    )
    repo.save_roster_month(2025, 9, [{"name": "示例甲", "days": {"16": "晚"}}], "uploads/month.png")
    repo.save_custom_reminder(
        name="示例甲",
        mention_mobile="",
        shift_code="night",
        reminder_time="21:00",
        message="需要关闭隧道灯",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "@示例甲\n需要关闭隧道灯"
    assert sent["mentions"] == []


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


def test_system_status_counts_sqlite_utc_records_for_beijing_today(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 20, 7, 40, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", FixedDateTime)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(kind="custom", target="示例甲", status="success")
    with repo._connect() as conn:
        conn.execute("UPDATE send_records SET created_at = ? WHERE id = 1", ("2026-07-19 16:30:00",))

    body = main_module._build_system_status(repo, scheduler_enabled=False, cjk_font_ready=True)

    assert body["today_success_count"] == 1
    assert body["today_failed_count"] == 0


def test_system_status_sanitizes_wechat_ids_in_errors(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 24, 17, 37, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [
                {"id": "wgr_notice", "name": "通知群"},
                {"id": "wgr_second", "name": "第二通知群"},
            ],
        },
    )
    client.post(
        "/api/personnel",
        json={
            "names": ["王路飞"],
            "people": [
                {
                    "name": "王路飞",
                    "wechat_group_runtime_sender_id": "@member-runtime",
                    "wechat_group_member_name": "王路飞",
                }
            ],
        },
    )
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(
        kind="daily_duty_test",
        target="wgr_notice",
        status="failed",
        error="wgr_notice failed; wgr_second failed; @member-runtime failed",
    )
    repo.save_patrol_warning_state(last_error="wgr_notice patrol error")

    body = client.get("/api/system-status").json()

    assert body["today_failed_count"] == 1
    assert "通知群" in body["last_error"]
    assert "第二通知群" in body["last_error"]
    assert "王路飞" in body["last_error"]
    assert "wgr_" not in body["last_error"]
    assert "@member-runtime" not in body["last_error"]
    assert body["patrol_warning_monitor"]["last_error"] == "通知群 patrol error"


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


def test_resend_record_does_not_append_duplicate_resend_suffix(tmp_path, monkeypatch):
    class FakeWebhookClient:
        def __init__(self, webhook_url: str):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            pass

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("文字补发不应该发送图片")

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/notification-config", json={"webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test"})
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(
        kind="daily_resend",
        target="示例甲",
        scheduled_at="2025-09-16T07:50:00+08:00",
        status="failed",
        content="补发内容",
        error="network down",
    )
    record_id = client.get("/api/send-records").json()["records"][0]["id"]

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 200
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "daily_resend"
    assert records[0]["kind"] != "daily_resend_resend"


def test_resend_failure_sanitizes_wechat_ids(tmp_path, monkeypatch):
    class FakeWechatClient:
        is_wechat_bridge = True

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            raise main_module.WeComError("wgr_notice failed; @member-runtime failed")

        async def send_image(self, image_bytes: bytes):
            raise main_module.WeComError("wgr_notice failed")

    monkeypatch.setattr("app.main._notification_client_from_config", lambda config: FakeWechatClient())
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="https://lightagent.test/api/push/send",
        lightagent_token="push-token",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
    )
    repo.save_personnel_names(["王路飞"])
    repo.save_personnel_contacts(
        [
            {
                "name": "王路飞",
                "wechat_group_runtime_sender_id": "@member-runtime",
                "wechat_group_member_name": "王路飞",
            }
        ]
    )
    repo.save_send_record(
        kind="daily",
        target="王路飞",
        scheduled_at="2025-09-16T07:50:00+08:00",
        status="failed",
        content="补发内容",
        error="network down",
    )
    record_id = client.get("/api/send-records").json()["records"][0]["id"]

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 502
    assert response.json()["detail"] == "通知群 failed; 王路飞 failed"


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
