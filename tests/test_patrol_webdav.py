import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import quote, unquote
from xml.etree import ElementTree

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.storage import DutyRepository
from tests.test_patrol_log_web import web_client, workbook


@pytest.fixture
def dav_server():
    root = ElementTree.Element("{DAV:}multistatus")
    response = ElementTree.SubElement(root, "{DAV:}response")
    ElementTree.SubElement(response, "{DAV:}href").text = "/dav/"
    propstat = ElementTree.SubElement(response, "{DAV:}propstat")
    ElementTree.SubElement(propstat, "{DAV:}status").text = "HTTP/1.1 200 OK"
    prop = ElementTree.SubElement(propstat, "{DAV:}prop")
    resource = ElementTree.SubElement(prop, "{DAV:}resourcetype")
    ElementTree.SubElement(resource, "{DAV:}collection")
    state = {"files": {}, "directories": {"/dav/"}, "calls": [], "propfind_code": 207, "propfind_body": None}
    authorization = "Basic " + base64.b64encode(b"fixture-user:fixture-password").decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, content=b""):
            self.send_response(status)
            self.send_header("Content-Length", str(len(content)))
            if status == 301:
                self.send_header("Location", "http://redirect.invalid/")
            self.end_headers()
            if content:
                self.wfile.write(content)

        def allowed(self):
            state["calls"].append({"method": self.command, "path": self.path, "depth": self.headers.get("Depth")})
            if self.headers.get("Authorization") != authorization:
                self.reply(401)
                return False
            return True

        def do_PROPFIND(self):
            if not self.allowed():
                return
            if state["propfind_body"] is not None:
                self.reply(state["propfind_code"], state["propfind_body"])
                return
            if self.path not in state["directories"]:
                self.reply(404)
                return
            listing = ElementTree.Element("{DAV:}multistatus")
            for entry in sorted(state["directories"] | set(state["files"])):
                own = entry == self.path
                child = entry.startswith(self.path) and "/" not in entry[len(self.path):].rstrip("/")
                if not own and (self.headers.get("Depth") == "0" or not child):
                    continue
                item = ElementTree.SubElement(listing, "{DAV:}response")
                ElementTree.SubElement(item, "{DAV:}href").text = entry
                propstat = ElementTree.SubElement(item, "{DAV:}propstat")
                ElementTree.SubElement(propstat, "{DAV:}status").text = "HTTP/1.1 200 OK"
                prop = ElementTree.SubElement(propstat, "{DAV:}prop")
                resource = ElementTree.SubElement(prop, "{DAV:}resourcetype")
                if entry in state["directories"]:
                    ElementTree.SubElement(resource, "{DAV:}collection")
            self.reply(state["propfind_code"], ElementTree.tostring(listing))

        def do_MKCOL(self):
            if not self.allowed():
                return
            parent = self.path.rstrip("/").rsplit("/", 1)[0] + "/"
            if self.path in state["directories"]:
                self.reply(405)
            elif parent not in state["directories"]:
                self.reply(409)
            else:
                state["directories"].add(self.path)
                self.reply(201)

        def do_PUT(self):
            if not self.allowed():
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get("If-None-Match") != "*":
                self.reply(428)
            elif self.path in state["files"]:
                self.reply(412)
            elif self.path.rsplit("/", 1)[0] + "/" not in state["directories"]:
                self.reply(409)
            else:
                state["files"][self.path] = {"body": body, "type": self.headers.get("Content-Type")}
                self.reply(201)

        def do_GET(self):
            if not self.allowed():
                return
            value = state["files"].get(self.path)
            self.reply(200, value["body"]) if value else self.reply(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    worker.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/dav", state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.fixture
def configured(web_client, dav_server):
    client, queries, downloads = web_client
    url, state = dav_server
    config = {"base_url": url, "username": "fixture-user", "password": "fixture-password", "directory": ""}
    response = client.post("/api/patrol-logs/webdav/config", json=config)
    assert response.status_code == 200
    assert "fixture-password" not in response.text
    state["calls"].clear()
    return client, state, queries, downloads, config


def test_saved_password_is_not_returned_and_blank_password_is_preserved(configured):
    client, state, _, _, config = configured
    response = client.get("/api/patrol-logs/webdav/config")
    assert response.json()["config"]["password"] == ""
    assert response.json()["config"]["password_configured"] is True
    response = client.post("/api/patrol-logs/webdav/config", json={**config, "password": ""})
    assert response.status_code == 200 and "fixture-password" not in response.text
    state["calls"].clear()
    response = client.post("/api/patrol-logs/webdav/test")
    assert response.status_code == 200 and response.json()["ok"]
    assert state["calls"] == [{"method": "PROPFIND", "path": "/dav/", "depth": "0"}]


@pytest.mark.parametrize("changed", [{"base_url": "https://other.invalid/dav"}, {"username": "other-user"}])
def test_changing_destination_requires_reentering_saved_password(configured, changed):
    client, _, _, _, config = configured
    response = client.post("/api/patrol-logs/webdav/config", json={**config, **changed, "password": ""})
    assert response.status_code == 400
    assert client.get("/api/patrol-logs/webdav/config").json()["config"]["base_url"] == config["base_url"]


@pytest.mark.parametrize("invalid", [
    {"base_url": "file:///tmp/logs"},
    {"base_url": "https://user:do-not-return@example.invalid/dav"},
    {"base_url": "https://example.invalid/dav?token=do-not-return"},
    {"base_url": "https://example.invalid:bad/dav"},
    {"directory": "../outside"},
    {"directory": "parent" + chr(92) + "outside"},
    {"password": "p" * 4097},
])
def test_invalid_configuration_is_rejected_without_disclosing_secrets(configured, invalid):
    client, state, _, _, config = configured
    response = client.post("/api/patrol-logs/webdav/config", json={**config, **invalid})
    assert response.status_code == 400
    assert "do-not-return" not in response.text and "fixture-password" not in response.text
    assert not state["calls"]


@pytest.mark.parametrize("ids", [["a"], ["a", "b"]])
def test_exports_reach_real_http_webdav_and_can_be_read_back(configured, ids):
    client, state, _, downloads, config = configured
    payload = {"target": "2026-11-15", "record_ids": ids}
    preview = client.post("/api/patrol-logs/webdav/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["can_confirm"]
    assert not state["files"] and not downloads
    response = client.post("/api/patrol-logs/webdav/export", json={**payload, "plan_token": preview.json()["plan_token"]})
    assert response.status_code == 200
    result = response.json()
    assert result["records"] == len(ids) and len(state["files"]) == len(ids)
    assert all("2026年/11月/日常巡查/" in path for path in result["paths"])
    with httpx.Client(trust_env=False, auth=("fixture-user", "fixture-password")) as reader:
        contents = [reader.get(config["base_url"] + "/" + quote(path, safe="/")) for path in result["paths"]]
    assert all(response.status_code == 200 for response in contents)
    assert sorted(response.content for response in contents) == sorted(workbook(value) for value in ids)
    assert downloads == ids
    assert all(path.endswith(".xlsx") for path in result["paths"])


def test_reexport_never_overwrites_existing_webdav_file(configured):
    client, state, _, _, _ = configured
    payload = {"target": "2026-11-15", "record_ids": ["a"]}
    preview = client.post("/api/patrol-logs/webdav/preview", json=payload).json()
    assert client.post("/api/patrol-logs/webdav/export", json={**payload, "plan_token": preview["plan_token"]}).status_code == 200
    original = {key: value["body"] for key, value in state["files"].items()}
    response = client.post("/api/patrol-logs/webdav/preview", json=payload)
    assert response.status_code == 200 and not response.json()["can_confirm"]
    assert response.json()["files"][0]["exists"] and not response.json()["plan_token"]
    assert client.post("/api/patrol-logs/webdav/export", json=payload).status_code == 428
    assert {key: value["body"] for key, value in state["files"].items()} == original


def test_invalid_record_is_rejected_before_cloud_or_platform_download(configured):
    client, state, _, downloads, _ = configured
    response = client.post("/api/patrol-logs/webdav/preview", json={"target": "2026-11-15", "record_ids": ["a", "missing"]})
    assert response.status_code == 404 and not state["calls"] and not downloads


@pytest.mark.parametrize("status,body", [(200, b"login-page"), (301, b""), (401, b"")])
def test_non_dav_redirect_and_auth_failures_are_not_reported_as_success(configured, status, body):
    client, state, _, _, _ = configured
    state["propfind_code"], state["propfind_body"] = status, body
    response = client.post("/api/patrol-logs/webdav/test")
    assert response.status_code == 502 and len(state["calls"]) == 1
    assert "fixture-password" not in response.text


def test_webdav_is_optional_and_normal_download_still_works(web_client):
    client, queries, downloads = web_client
    assert client.get("/api/patrol-logs/webdav/config").json()["config"]["base_url"] == ""
    assert client.post("/api/patrol-logs/webdav/test").status_code == 400
    payload = {"target": "2026-11-15", "record_ids": ["a"]}
    assert client.post("/api/patrol-logs/webdav/export", json=payload).status_code == 428
    assert not queries and not downloads
    assert client.post("/api/patrol-logs/export", json=payload).content == workbook("a")


def test_webdav_config_is_included_in_existing_backup_restore(tmp_path):
    source = DutyRepository(tmp_path / "source.sqlite3")
    config = {"base_url": "https://example.invalid/dav", "username": "fixture-user", "password": "fixture-password", "directory": "巡查日志"}
    source.save_patrol_webdav_config(**config)
    destination = DutyRepository(tmp_path / "restored.sqlite3")
    destination.import_config_snapshot(source.export_config_snapshot())
    assert destination.get_patrol_webdav_config() == config


def test_webdav_routes_require_backend_login(tmp_path):
    app = main.create_app(data_dir=tmp_path / "secured-data", upload_dir=tmp_path / "secured-uploads", start_scheduler=False, admin_password="fixture-backend-password")
    with TestClient(app) as client:
        assert client.get("/api/patrol-logs/webdav/config").status_code == 401
        for path in ("config", "test", "export"):
            assert client.post("/api/patrol-logs/webdav/" + path, json={}).status_code == 401
