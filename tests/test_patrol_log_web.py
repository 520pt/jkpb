from datetime import date
from io import BytesIO
import zipfile

from fastapi.testclient import TestClient
import pytest

import app.main as main
from tests.test_patrol_log_export import rows


def workbook(record_id):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in (("[Content_Types].xml", "fixture"), ("xl/workbook.xml", "fixture"), ("record.txt", record_id)):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), value)
    return buffer.getvalue()


@pytest.fixture
def web_client(tmp_path, monkeypatch):
    queries = []
    downloads = []

    async def query(repo, target, person_name):
        queries.append((target, person_name))
        if target != date(2026, 11, 15):
            return []
        return [{**row, "private_marker": "not-public"} for row in rows()]

    async def download(repo, target, record_id):
        downloads.append(record_id)
        return workbook(record_id)

    monkeypatch.setattr(main, "_query_patrol_log_rows", query)
    monkeypatch.setattr(main, "_download_patrol_log_workbook", download)
    app = main.create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False, admin_password="")
    with TestClient(app) as client:
        yield client, queries, downloads


def test_list_filters_category_and_uses_shared_person_query(web_client):
    client, queries, downloads = web_client
    response = client.get("/api/patrol-logs", params={"target": "2026-11-15", "person_name": " 罗富耀 ", "patrol_type": "1"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["rows"]] == ["a", "b", "c"]
    assert response.json()["total"] == 3
    assert queries == [(date(2026, 11, 15), "罗富耀")]
    assert not downloads and "not-public" not in response.text


@pytest.mark.parametrize("params,status", [
    ({"target": "2026-11-31"}, 422),
    ({"target": "2026-11-15", "patrol_type": "unknown"}, 400),
])
def test_invalid_filters_do_not_query_platform(web_client, params, status):
    client, queries, downloads = web_client
    assert client.get("/api/patrol-logs", params=params).status_code == status
    assert not queries and not downloads


def test_empty_day_returns_empty_list(web_client):
    client, _, downloads = web_client
    response = client.get("/api/patrol-logs", params={"target": "2026-11-16"})
    assert response.status_code == 200 and response.json()["rows"] == []
    assert not downloads


def test_single_export_downloads_original_excel(web_client):
    client, _, downloads = web_client
    response = client.post("/api/patrol-logs/export", json={"target": "2026-11-15", "record_ids": ["a"]})
    assert response.status_code == 200 and response.content == workbook("a")
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert response.headers["cache-control"] == "no-store"
    assert downloads == ["a"]


def test_multiple_export_is_zip_and_deduplicates_selected_ids(web_client):
    client, _, downloads = web_client
    response = client.post("/api/patrol-logs/export", json={"target": "2026-11-15", "record_ids": ["a", "b", "a"]})
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert archive.testzip() is None
        assert all("/" not in name for name in archive.namelist())
        assert sorted(archive.read(name) for name in archive.namelist()) == sorted([workbook("a"), workbook("b")])
    assert downloads == ["a", "b"]


@pytest.mark.parametrize("extra,ids", [
    ({}, ["a", "missing"]),
    ({"patrol_type": "1"}, ["d"]),
    ({"target": "2026-11-14"}, ["a"]),
])
def test_export_revalidates_every_record_before_any_download(web_client, extra, ids):
    client, _, downloads = web_client
    response = client.post("/api/patrol-logs/export", json={"target": "2026-11-15", "record_ids": ids, **extra})
    assert response.status_code == 404 and not downloads


def test_export_requires_nonempty_selection(web_client):
    client, queries, downloads = web_client
    response = client.post("/api/patrol-logs/export", json={"target": "2026-11-15", "record_ids": []})
    assert response.status_code == 422 and not queries and not downloads


def test_patrol_web_routes_require_admin_login(tmp_path):
    app = main.create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False, admin_password="unit-test-password")
    with TestClient(app) as client:
        assert client.get("/api/patrol-logs", params={"target": "2026-11-15"}).status_code == 401
        assert client.post("/api/patrol-logs/export", json={"target": "2026-11-15", "record_ids": ["a"]}).status_code == 401
        assert client.post("/api/patrol-logs/export-status", json={"target": "2026-11-15", "record_ids": ["a"]}).status_code == 401


def test_export_history_check_and_successful_download(web_client):
    client, _, downloads = web_client
    payload = {"target": "2026-11-15", "record_ids": ["a", "b"]}
    before = client.post("/api/patrol-logs/export-status", json=payload)
    assert before.status_code == 200 and before.headers["cache-control"] == "no-store"
    assert before.json()["exported"] == []
    assert [row["id"] for row in before.json()["unexported"]] == ["a", "b"]
    assert not downloads
    assert client.post("/api/patrol-logs/export", json={**payload, "record_ids": ["a"]}).status_code == 200
    after = client.post("/api/patrol-logs/export-status", json=payload).json()
    assert [row["id"] for row in after["exported"]] == ["a"]
    assert after["exported"][0]["exported_at"] and after["exported"][0]["filename"].endswith(".xlsx")
    assert [row["id"] for row in after["unexported"]] == ["b"]
    assert client.post("/api/patrol-logs/export", json=payload).status_code == 200
    assert len(client.post("/api/patrol-logs/export-status", json=payload).json()["exported"]) == 2
    assert downloads == ["a", "a", "b"]


@pytest.mark.parametrize("extra", [{"target": "2026-11-14"}, {"patrol_type": "3"}, {"record_ids": ["missing"]}])
def test_export_history_revalidates_selection(web_client, extra):
    client, _, downloads = web_client
    response = client.post("/api/patrol-logs/export-status", json={"target": "2026-11-15", "record_ids": ["a"], **extra})
    assert response.status_code == 404 and not downloads


def test_failed_batch_does_not_record_partial_exports(web_client, monkeypatch):
    client, _, _ = web_client

    async def download(repo, target, record_id):
        if record_id == "b":
            raise main.HTTPException(502, "fixture failure")
        return workbook(record_id)

    monkeypatch.setattr(main, "_download_patrol_log_workbook", download)
    payload = {"target": "2026-11-15", "record_ids": ["a", "b"]}
    assert client.post("/api/patrol-logs/export", json=payload).status_code == 502
    assert client.post("/api/patrol-logs/export-status", json=payload).json()["exported"] == []


def test_export_history_survives_repository_restart_and_is_date_scoped(tmp_path):
    path = tmp_path / "history.db"
    repo = main.DutyRepository(path)
    repo.record_patrol_log_exports("2026-11-15", [{"id": "a", "filename": "日志.xlsx"}])
    restarted = main.DutyRepository(path)
    assert restarted.get_patrol_log_export_history("2026-11-15", ["a"])["a"]["filename"] == "日志.xlsx"
    assert restarted.get_patrol_log_export_history("2026-11-16", ["a"]) == {}
