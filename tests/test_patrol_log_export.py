from __future__ import annotations

import asyncio
import zipfile
from datetime import date
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest

import app.main as main


class FileClient:
    def __init__(self):
        self.texts = []
        self.files = []

    async def send_text(self, userid, content):
        self.texts.append((userid, content))

    async def send_file(self, userid, filename, content):
        self.files.append((userid, filename, content))


@pytest.fixture(autouse=True)
def clear_pending():
    main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS.clear()
    yield
    main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS.clear()


def message(text, user="luo"):
    return SimpleNamespace(from_user=user, msg_type="text", content=text, event_key="")


def rows():
    return [
        {"id": "a", "patrolType": 1, "maintenanceSectionName": "南景高速", "userNames": "李金雷,罗富耀", "collectTime": "2026-11-15 08:00:00"},
        {"id": "b", "patrolType": 1, "maintenanceSectionName": "南景高速", "userNames": "李金雷,罗富耀", "collectTime": "2026-11-15 09:00:00"},
        {"id": "c", "patrolType": 1, "maintenanceSectionName": "景文高速", "userNames": "李金雷,罗富耀", "collectTime": "2026-11-15 10:00:00"},
        {"id": "d", "patrolType": 3, "maintenanceSectionName": "南景高速", "userNames": "杨伦,罗富耀", "collectTime": "2026-11-15 15:00:00"},
    ]


def test_patrol_history_date_does_not_roll_into_next_year(monkeypatch):
    monkeypatch.setattr(main, "_today_in_tz", lambda: date(2026, 11, 16))
    assert main._patrol_log_date("巡查日志导出") == date(2026, 11, 16)
    assert main._patrol_log_date("11月6日") == date(2026, 11, 6)
    assert main._patrol_log_date("2026-11-15") == date(2026, 11, 15)
    with pytest.raises(main.HTTPException):
        main._patrol_log_date("11月31日")


def test_saved_menu_replaces_rest_but_keeps_legacy_rest_key(tmp_path):
    repo = main.DutyRepository(tmp_path / "duty.db")
    repo.save_wecom_app_menu_config([{"name": "更多查询", "items": [
        {"name": "未来7天", "command": "查询未来7天"},
        {"name": "查询休息", "command": "查询休息"},
    ]}])
    commands = [item["command"] for item in main._wecom_app_menu_groups(repo)[0]["items"]]
    assert "巡查日志导出" in commands and "查询休息" not in commands
    assert "查询未来7天" in commands
    assert main._wecom_app_menu_command("DR_PATROL_LOG_EXPORT", repo) == "巡查日志导出"
    assert main._wecom_app_menu_command("DR_MENU_2_2", None) == "查询休息"


def test_bound_query_requires_own_confirmation_and_sends_categories(tmp_path, monkeypatch):
    repo = main.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀", "杨伦"])
    repo.upsert_personnel_contacts([
        {"name": "罗富耀", "wecom_userid": "luo"},
        {"name": "杨伦", "wecom_userid": "yang"},
    ])
    client = FileClient()
    downloaded = []

    async def query(repo, target, person):
        assert target == date(2026, 11, 15) and person == "罗富耀"
        return rows()

    async def download(repo, target, record_id):
        downloaded.append(record_id)
        return b"original workbook"

    monkeypatch.setattr(main, "_wecom_app_client_from_repo", lambda repo: client)
    monkeypatch.setattr(main, "_query_patrol_log_rows", query)
    monkeypatch.setattr(main, "_download_patrol_log_workbook", download)
    run = lambda text, user="luo": asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message(text, user)))
    run("巡查日志导出2026-11-15")
    assert not client.files and not downloaded
    assert any("共查询到 4 条" in text for _, text in client.texts)
    run("确认导出", "yang")
    assert not client.files
    run("确认导出日常巡查")
    assert downloaded == ["a", "b", "c"]
    assert len(client.files) == 3 and all(user == "luo" for user, _, _ in client.files)
    assert all("2026-11-15_日常巡查" in name for _, name, _ in client.files)
    run("确认导出")
    assert downloaded == ["a", "b", "c", "d"]
    assert not main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS


def test_paginated_results_are_filtered_again_by_exact_person_and_day(monkeypatch):
    pages = [
        [rows()[0], {**rows()[1], "userNames": "罗富耀甲"}],
        [{**rows()[2], "collectTime": "2026-11-14 10:00:00"}, {**rows()[3], "userNames": "杨伦"}],
    ]
    calls = []

    async def api(repo, target, path, *, params=None, payload=None):
        calls.append(params)
        return "https://example.test", {"rows": pages[params["pageNum"] - 1], "total": 4}

    monkeypatch.setattr(main, "_patrol_log_api", api)
    result = asyncio.run(main._query_patrol_log_rows(None, date(2026, 11, 15), "罗富耀"))
    assert [row["id"] for row in result] == ["a"]
    assert len(calls) == 2
    assert all(call["collectTimee"] == call["collectTimes"] == "2026-11-15" for call in calls)
    assert all(call["userNameLike"] == "罗富耀" for call in calls)


def test_original_download_does_not_forward_authentication_to_signed_url(monkeypatch):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "fixture")
        archive.writestr("xl/workbook.xml", "fixture")
    content = buffer.getvalue()
    requests = []

    async def context(repo, request, *, force_login=False):
        return "https://example.test", {"Authorization": "Bearer unit-test"}, {}

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/prod-api/patrol/inspectionLogs/getPreviewUrl"
            assert request.content == b'{"ids":["a"]}'
            return httpx.Response(200, json={"code": 200, "data": {"previewUrl": "https://example.test/preview/a.xlsx?signature=test"}})
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(200, content=content)

    monkeypatch.setattr(main, "_tunnel_mechanical_request_context", context)
    monkeypatch.setattr(main, "tunnel_async_httpx_client", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    result = asyncio.run(main._download_patrol_log_workbook(None, date(2026, 11, 15), "a"))
    assert result == content and len(requests) == 2


def test_original_download_rejects_foreign_preview_origin(monkeypatch):
    async def api(*args, **kwargs):
        return "https://example.test", {"data": {"previewUrl": "https://other.test/a.xlsx"}}

    monkeypatch.setattr(main, "_patrol_log_api", api)
    with pytest.raises(main.HTTPException):
        asyncio.run(main._download_patrol_log_workbook(None, date(2026, 11, 15), "a"))


@pytest.mark.parametrize("state", ["expired", "binding_changed", "unbound"])
def test_stale_or_unbound_confirmation_never_downloads(tmp_path, monkeypatch, state):
    repo = main.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀", "杨伦"])
    if state != "unbound":
        repo.upsert_personnel_contacts([{"name": "杨伦" if state == "binding_changed" else "罗富耀", "wecom_userid": "luo"}])
    client = FileClient()
    main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"] = {
        "person_name": "罗富耀", "date": "2026-11-15", "rows": rows(),
        "sent_ids": set(), "expires_at": 0 if state == "expired" else float("inf"),
    }

    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid confirmation must not query or download platform records")

    monkeypatch.setattr(main, "_wecom_app_client_from_repo", lambda repo: client)
    monkeypatch.setattr(main, "_query_patrol_log_rows", forbidden)
    monkeypatch.setattr(main, "_download_patrol_log_workbook", forbidden)
    asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message("确认导出")))
    assert not client.files
    assert "luo" not in main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS
    assert client.texts and all(userid == "luo" for userid, _ in client.texts)


def test_failed_file_send_retries_only_unacknowledged_files(tmp_path, monkeypatch):
    repo = main.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀"])
    repo.upsert_personnel_contacts([{"name": "罗富耀", "wecom_userid": "luo"}])
    client = FileClient()
    send_attempts = []
    failure_pending = True

    async def query(*args):
        return rows()[:2]

    async def download(repo, target, record_id):
        return record_id.encode()

    async def send_file(userid, filename, content):
        nonlocal failure_pending
        send_attempts.append(content)
        if content == b"b" and failure_pending:
            failure_pending = False
            raise main.WeComError("unit-test file send rejected")
        client.files.append((userid, filename, content))

    client.send_file = send_file
    monkeypatch.setattr(main, "_wecom_app_client_from_repo", lambda repo: client)
    monkeypatch.setattr(main, "_query_patrol_log_rows", query)
    monkeypatch.setattr(main, "_download_patrol_log_workbook", download)
    asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message("巡查日志导出2026-11-15")))
    asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message("确认导出")))
    assert main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"]["sent_ids"] == {"a"}
    assert not main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"]["sending"]
    asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message("确认导出")))
    assert send_attempts == [b"a", b"b", b"b"]
    assert [content for _, _, content in client.files] == [b"a", b"b"]
    assert not main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS


def test_platform_record_change_requires_another_confirmation(tmp_path, monkeypatch):
    repo = main.DutyRepository(tmp_path / "duty.db")
    repo.upsert_personnel_names(["罗富耀"])
    repo.upsert_personnel_contacts([{"name": "罗富耀", "wecom_userid": "luo"}])
    client = FileClient()
    current_rows = rows()[:1]

    async def query(*args):
        return list(current_rows)

    async def forbidden(*args, **kwargs):
        pytest.fail("Changed platform records must be confirmed again before downloading")

    monkeypatch.setattr(main, "_wecom_app_client_from_repo", lambda repo: client)
    monkeypatch.setattr(main, "_query_patrol_log_rows", query)
    monkeypatch.setattr(main, "_download_patrol_log_workbook", forbidden)
    asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message("巡查日志导出2026-11-15")))
    current_rows.append(rows()[1])
    asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message("确认导出")))
    assert not client.files
    assert any("平台记录有变化" in content for _, content in client.texts)
    assert len(main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"]["rows"]) == 2


def test_patrol_api_reuses_shared_login_once_after_auth_expiry(monkeypatch):
    login_attempts = []
    requests = []

    async def context(repo, request, *, force_login=False):
        login_attempts.append(force_login)
        return "https://example.test", {"Authorization": "Bearer refreshed" if force_login else "Bearer old"}, {}

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"code": 401, "msg": "Unauthorized"})
        assert request.headers["Authorization"] == "Bearer refreshed"
        return httpx.Response(200, json={"code": 200, "total": 0, "rows": []})

    monkeypatch.setattr(main, "_tunnel_mechanical_request_context", context)
    monkeypatch.setattr(main, "tunnel_async_httpx_client", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))
    result = asyncio.run(main._query_patrol_log_rows(None, date(2026, 11, 15), "罗富耀"))
    assert result == [] and login_attempts == [False, True]
    assert len(requests) == 2
