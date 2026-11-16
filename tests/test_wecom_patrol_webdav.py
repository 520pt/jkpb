import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from xml.etree import ElementTree

import app.main as main
from tests.test_patrol_log_export import FileClient, message, rows
from tests.test_patrol_log_web import workbook
from tests.test_patrol_webdav import dav_server
from tests.test_wecom_app import AES_KEY, CORP_ID, TOKEN, _encrypt, _signature


@pytest.fixture
def patrol(tmp_path, monkeypatch, dav_server):
    main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS.clear()
    app = main.create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads",
                          admin_password="", start_scheduler=False)
    repo = app.state.repo
    repo.upsert_personnel_names(["罗富耀", "杨伦"])
    repo.upsert_personnel_contacts([
        {"name": "罗富耀", "wecom_userid": "luo"},
        {"name": "杨伦", "wecom_userid": "yang"},
    ])
    url, state = dav_server
    repo.save_patrol_webdav_config(base_url=url, username="fixture-user",
                                   password="fixture-password", directory="")
    client = FileClient()
    downloads = []
    current_rows = rows()

    async def query(repo, target, person):
        assert target == date(2026, 11, 15) and person == "罗富耀"
        return list(current_rows)

    async def download(repo, target, record_id):
        downloads.append(record_id)
        return workbook(record_id)

    monkeypatch.setattr(main, "_today_in_tz", lambda: date(2026, 11, 15))
    monkeypatch.setattr(main, "_wecom_app_client_from_repo", lambda repo: client)
    monkeypatch.setattr(main, "_query_patrol_log_rows", query)
    monkeypatch.setattr(main, "_download_patrol_log_workbook", download)

    def run(text, user="luo"):
        asyncio.run(main._handle_wecom_app_message(repo, tmp_path, message(text, user)))

    yield SimpleNamespace(repo=repo, app=app, state=state, client=client, downloads=downloads,
                          current_rows=current_rows, run=run, tmp_path=tmp_path)
    main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS.clear()


def test_new_menu_replaces_saved_today_mechanical_and_keeps_submit(patrol):
    patrol.repo.save_wecom_app_menu_config([{"name": "机电预警", "items": [
        {"name": "今日机电", "command": "查询今日机电"},
        {"name": "机电模板", "command": "模板"},
    ]}])
    menu = main._wecom_app_menu_payload(patrol.repo)["button"][0]["sub_button"]
    assert menu[0]["name"] == "录入今日机电"
    assert menu[1] == {"type": "click", "name": "导出今日巡查日志",
                       "key": "DR_PATROL_LOG_TODAY_EXPORT"}
    assert all(item["name"] != "今日机电" for item in menu)
    assert main._wecom_app_menu_command("DR_TUNNEL_TODAY_QUERY", patrol.repo) == "导出今日巡查日志"


@pytest.mark.parametrize("confirmation", ["确认", "1"])
def test_menu_click_previews_real_paths_and_confirmation_uploads(patrol, confirmation):
    event = SimpleNamespace(from_user="luo", msg_type="event", content="",
                            event_key="DR_PATROL_LOG_TODAY_EXPORT")
    asyncio.run(main._handle_wecom_app_message(patrol.repo, patrol.tmp_path, event))
    pending = main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"]
    prompt = patrol.client.texts[-1][1]
    assert "共查询到 4 条" in prompt and "WebDAV" in prompt
    assert "确认 或 1" in prompt and "待新建目录" in prompt
    assert pending["destination"] == "webdav"
    assert all(item["path"] in prompt for item in pending["plan"]["files"])
    assert not patrol.downloads and not patrol.client.files
    assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])
    patrol.run(confirmation)
    assert len(patrol.downloads) == len(patrol.state["files"]) == 4
    assert not patrol.client.files
    assert sorted(item["body"] for item in patrol.state["files"].values()) == sorted(workbook(row["id"]) for row in rows())
    assert "已导出 4 条巡查日志到 WebDAV" in patrol.client.texts[-1][1]
    assert "luo" not in main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS
    patrol.run("确认导出")
    assert len(patrol.downloads) == 4


def test_cancel_and_other_user_cannot_confirm(patrol):
    patrol.run("导出今日巡查日志")
    patrol.run("确认导出", "yang")
    assert not patrol.downloads and not patrol.state["files"]
    patrol.run("取消")
    patrol.run("确认导出")
    assert not patrol.downloads and not patrol.state["files"]
    assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])


@pytest.mark.parametrize("change", ["expired", "binding_changed"])
def test_stale_confirmation_never_uploads(patrol, change):
    patrol.run("导出今日巡查日志")
    if change == "expired":
        main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"]["expires_at"] = 0
    else:
        patrol.repo.upsert_personnel_contacts([{"name": "罗富耀", "wecom_userid": "other"}])
    patrol.run("1")
    assert not patrol.downloads and not patrol.state["files"]
    assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])


def test_record_change_requires_new_confirmation(patrol):
    patrol.run("导出今日巡查日志")
    patrol.current_rows.pop()
    patrol.run("确认")
    assert not patrol.downloads and not patrol.state["files"]
    assert "重新确认" in patrol.client.texts[-1][1]
    assert "共查询到 3 条" in patrol.client.texts[-1][1]
    patrol.run("1")
    assert len(patrol.state["files"]) == 3


def test_changed_directory_requires_new_confirmation(patrol):
    patrol.run("导出今日巡查日志")
    patrol.state["directories"].add("/dav/changed/")
    config = patrol.repo.get_patrol_webdav_config()
    patrol.repo.save_patrol_webdav_config(**{**config, "directory": "changed"})
    patrol.run("确认")
    assert not patrol.downloads and not patrol.state["files"]
    assert "保存根目录：/changed" in patrol.client.texts[-1][1]
    patrol.run("1")
    assert len(patrol.state["files"]) == 4
    assert all(path.startswith("/dav/changed/") for path in patrol.state["files"])


def test_existing_files_are_reported_and_never_overwritten(patrol):
    patrol.run("导出今日巡查日志")
    patrol.run("确认")
    original = dict(patrol.state["files"])
    downloads = list(patrol.downloads)
    patrol.run("导出今日巡查日志")
    assert "已存在，不覆盖" in patrol.client.texts[-1][1]
    patrol.run("1")
    assert patrol.state["files"] == original and patrol.downloads == downloads


def test_download_failure_does_not_create_directories(patrol, monkeypatch):
    async def fail(*args):
        raise main.HTTPException(502, "fixture download failure")

    patrol.run("导出今日巡查日志")
    monkeypatch.setattr(main, "_download_patrol_log_workbook", fail)
    patrol.run("1")
    assert not patrol.state["files"]
    assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])


def test_long_confirmation_paths_are_sent_without_truncation(patrol):
    patrol.current_rows[:] = [{**rows()[0], "id": str(index), "maintenanceSectionName": "长路径巡查测试路段" * 8} for index in range(12)]
    patrol.run("导出今日巡查日志")
    messages = [text for _, text in patrol.client.texts]
    prompt = "".join(messages)
    plan = main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS["luo"]["plan"]
    assert all(len(text.encode("utf-8")) <= 2000 for text in messages)
    assert all(item["path"] in prompt for item in plan["files"])
    assert "确认 或 1" in prompt and "共查询到 12 条" in prompt
    assert not patrol.downloads and not patrol.state["files"]


def test_cancellation_during_final_directory_check_prevents_upload(patrol, monkeypatch):
    patrol.run("导出今日巡查日志")
    original = main.plan_patrol_webdav
    checks = 0

    async def cancel_during_check(*args):
        nonlocal checks
        plan = await original(*args)
        checks += 1
        if checks == 2:
            main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS.pop("luo", None)
        return plan

    monkeypatch.setattr(main, "plan_patrol_webdav", cancel_during_check)
    patrol.run("确认")
    assert not patrol.state["files"]
    assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])
    assert "未上传 WebDAV" in patrol.client.texts[-1][1]


def test_switching_to_another_menu_clears_patrol_confirmation(patrol):
    patrol.run("导出今日巡查日志")
    patrol.run("普通消息")
    assert "luo" not in main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS
    assert not patrol.downloads and not patrol.state["files"]


@pytest.mark.parametrize("confirmation", ["确认", "1"])
def test_encrypted_callback_click_then_confirmation_uploads(patrol, confirmation):
    patrol.repo.save_notification_config(
        webhook_url="", wecom_app_enabled=True, wecom_app_corp_id=CORP_ID,
        wecom_app_agent_id="1000002", wecom_app_secret="fixture-app-secret",
        wecom_app_token=TOKEN, wecom_app_encoding_aes_key=AES_KEY,
    )

    def callback(client, kind, value):
        plain = ElementTree.Element("xml")
        fields = {"ToUserName": CORP_ID, "FromUserName": "luo",
                  "CreateTime": "123", "MsgType": kind, "AgentID": "1000002"}
        if kind == "event":
            fields.update(Event="click", EventKey=value)
        else:
            fields.update(Content=value, MsgId="901")
        for key, content in fields.items():
            ElementTree.SubElement(plain, key).text = content
        encrypted = _encrypt(ElementTree.tostring(plain, encoding="unicode"))
        envelope = ElementTree.Element("xml")
        ElementTree.SubElement(envelope, "Encrypt").text = encrypted
        return client.post(
            "/api/wecom-app/callback",
            params={"msg_signature": _signature(encrypted), "timestamp": "123", "nonce": "nonce"},
            content=ElementTree.tostring(envelope, encoding="utf-8"),
        )

    with TestClient(patrol.app) as client:
        clicked = callback(client, "event", "DR_PATROL_LOG_TODAY_EXPORT")
        assert clicked.status_code == 200 and clicked.text == "success"
        assert "luo" in main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS
        assert not patrol.downloads and not patrol.state["files"]
        assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])
        confirmed = callback(client, "text", confirmation)
        assert confirmed.status_code == 200 and confirmed.text == "success"
    assert len(patrol.state["files"]) == 4
    assert not patrol.client.files
    assert "luo" not in main.WECOM_APP_PENDING_PATROL_LOG_EXPORTS


def test_missing_saved_directory_does_not_download_or_write(patrol):
    config = patrol.repo.get_patrol_webdav_config()
    patrol.repo.save_patrol_webdav_config(**{**config, "directory": "missing"})
    patrol.run("导出今日巡查日志")
    assert "WebDAV" in patrol.client.texts[-1][1]
    assert "目录不存在" in patrol.client.texts[-1][1]
    assert not patrol.downloads and not patrol.state["files"]
    assert all(call["method"] == "PROPFIND" for call in patrol.state["calls"])
