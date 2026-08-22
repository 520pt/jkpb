import asyncio
from zoneinfo import ZoneInfo
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

TZ = ZoneInfo("Asia/Shanghai")
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
    assert response.headers["cache-control"] == "no-cache, max-age=0, must-revalidate"
    html = response.text
    assert 'id="mainSubnav"' in html
    assert 'homeTodayReminderCard' in html
    assert 'homeNextReminderCard' in html
    assert 'homeDutySummaryCard' in html
    assert 'homeRecentRecordsCard' in html
    assert '<section id="todayPage" class="tab-page">' in html
    assert '<section id="reviewPage" class="tab-page hidden">' in html
    assert 'id="personName" list="personnelNameOptions" placeholder="选择或输入姓名"' in html
    assert 'id="customReminderName" list="personnelNameOptions" placeholder="选择或输入姓名"' in html
    assert "customReminderTimeRules" in html
    assert 'early: { min: "00:00", max: "08:00", fallback: "07:50", label: "早班" }' in html
    assert '$("customReminderShift").addEventListener("change", applyCustomReminderTimeRule)' in html
    assert 'id="driverNameInput" list="personnelNameOptions" placeholder="选择或输入姓名"' in html
    assert 'data-edit-person="${escapeHtml(person.name)}"' in html
    assert 'data-delete-person="${escapeHtml(person.name)}"' in html
    assert 'id="notificationMentionMode"' in html
    assert 'id="notificationMentionTargets"' in html
    assert 'id="notificationTestPersonName" list="personnelNameOptions" placeholder="示例甲"' in html
    assert 'class="field-grid hidden" id="monitorWechatFields"' in html
    assert 'id="monitorWechatMember"' in html
    assert 'id="monitorWechatMemberId" readonly placeholder="未绑定"' in html
    assert "updateMonitorNotificationFields" in html
    assert "autofillMonitorContactByName" in html
    assert "monitorWechatBindingPayload" in html
    assert "monitorWechatBindingText" in html
    assert "同步${wechatGatewayLabel()}群失败" not in html
    assert "微信交互配置已保存" in html
    assert 'class="wecom-menu-editor"' in html
    assert "wecom-menu-tabs" in html
    assert "data-wecom-menu-tab" in html
    assert "wecom-menu-command-input" in html
    assert 'tunnel_mechanical_wechat: "隧道机电录入"' in html
    assert 'tunnel_mechanical_wechat_modify: "隧道机电修改"' in html
    assert 'tunnel_mechanical_query_wechat: "隧道机电查询"' in html
    assert 'patrol_warning_start_test: "公路巡查预警测试"' in html
    assert 'patrol_warning_end_test: "预警结束巡查测试"' in html
    assert 'patrol_warning_check_resend: "公路巡查预警检查补发"' in html
    assert 'id="patrolWarningSettings"' in html
    assert 'id="patrolLoginUrl"' in html
    assert 'id="patrolRouteCode" placeholder="S41"' in html
    assert 'id="patrolWarningQueryMeta"' in html
    assert "正在等待后台查询" in html
    assert "patrolWarningQueryDueRefreshAt" in html
    assert "下次提醒：正在等待后台发送" in html
    assert "await loadTodayReminders();" in html
    assert 'id="patrolSendContentMode"' in html
    assert '<option value="image">仅图片</option>' in html
    assert 'MAIN_SUBNAV_SCHEMA' in html
    assert '今日提醒' in html
    assert '导入/核对' in html
    assert '隧道机电录入' in html
    assert '橙色预警查询' in html
    assert 'id="settingsOverview"' in html
    assert 'id="exportConfigBtn"' in html
    assert 'id="importConfigBtn"' in html
    assert 'id="createDbBackupBtn"' in html
    assert 'id="databaseBackupsList"' in html
    assert 'id="cleanupUploadsBtn"' in html
    assert 'data-settings-target="personCenterSettings"' in html
    assert 'data-settings-target="interactionCommandSettings"' in html
    assert 'data-settings-target="reminderDiagnosticSettings"' in html
    assert 'id="personnelNamesMultiSelect"' in html
    assert 'id="stationNamesMultiSelect"' in html
    assert 'id="bigDriverNamesMultiSelect"' in html
    assert 'id="smallDriverNamesMultiSelect"' in html
    assert 'id="patrolTeamGroupsEditor"' in html
    assert 'data-patrol-team-action="add"' in html
    assert 'id="bigDriverNamesEditor"' not in html
    assert 'id="smallDriverNamesEditor"' not in html
    assert 'data-subnav-kind="main"' in html
    assert 'data-subnav-kind="settings"' in html
    assert 'data-subnav-kind="scroll"' in html
    assert 'data-subnav-kind="tunnel"' in html
    assert '导入/核对' in html
    assert '已导入排班' in html
    assert '橙色预警查询' in html
    assert '隧道机电录入' in html
    assert '隧道模板' in html
    assert '发送记录' in html
    assert '系统状态' in html
    assert '查询休息' in html
    assert '施工图片' in html
    assert '施工点维护' in html
    assert '企业微信自建应用' in html
    assert '企业微信群机器人' in html
    assert '个人微信群' not in html
    assert '内部微信登录' not in html
    assert '群同步' not in html
    assert '通知接收人' in html
    assert '绑定状态' in html
    assert '配置导出' in html
    assert '配置导入' in html
    assert '数据库备份' in html
    assert '文件清理' in html
    assert 'id="recordStatusFilter"' in html
    assert 'id="recordKindFilter"' in html
    assert 'id="importConfigFile"' in html
    assert 'id="configBackupStatus"' in html
    assert 'function exportConfig' in html
    assert 'function importConfig' in html
    assert '"/api/config/export"' in html
    assert '"/api/config/import"' in html
    assert "交互功能" in html
    assert 'id="tunnelMechanicalPage"' in html
    assert 'id="orangeWarningPage"' in html
    assert 'id="orangeWarningName"' in html
    assert 'id="queryOrangeWarningBtn"' in html
    assert 'duty-reminder:lastActiveMainTab' in html
    assert 'function activateMainTab' in html
    assert 'function renderOrangeWarningRecords' in html
    assert 'function renderOrangeWarningFilteredRecords' in html
    assert 'function renderOrangeWarningStats' in html
    assert 'orange-warning-card-subline' in html
    assert 'orange-warning-card-people' in html
    assert '<div><strong>路线</strong><small>${escapeHtml(record.route_name || "-")}</small></div>' not in html
    assert 'id="orangeWarningHero"' in html
    assert html.index('id="orangeWarningHero"') < html.index('id="orangeWarningName"') < html.index('id="orangeWarningResults"')
    assert 'id="orangeWarningHeroMeta"' in html
    assert 'id="orangeWarningLayout"' in html
    assert 'id="orangeWarningStartDate"' in html
    assert 'id="orangeWarningEndDate"' in html
    assert 'id="clearOrangeWarningDateBtn"' in html
    assert 'id="exportOrangeWarningStatsTableBtn"' in html
    assert 'id="exportOrangeWarningStatsImageBtn"' in html
    assert 'id="orangeWarningStatsTable"' in html
    assert 'id="orangeWarningStatsPagination"' in html
    assert 'id="orangeWarningStatsMeta"' in html
    assert 'function orangeWarningStatsGroups' in html
    assert 'function orangeWarningCanJoinItems' in html
    assert 'current.record && current.record.route_code' in html
    assert 'function orangeWarningStatsRowsFromGroups' in html
    assert 'function orangeWarningStatsRows' in html
    assert 'while (index + 1 < items.length && orangeWarningCanJoinItems(items[index], items[index + 1]))' in html
    assert 'isGroupStart: highlighted && index === 0 && !previousHighlighted' in html
    assert 'isGroupEnd: highlighted && index === (group.records || []).length - 1' in html
    assert 'endTimestamp: orangeWarningRecordEndTimestamp(record)' in html
    assert 'next.timestamp - current.endTimestamp <= windowMs' in html
    assert 'current.date === next.date' not in html
    assert 'function exportOrangeWarningStatsImage' in html
    assert 'function exportOrangeWarningStatsTable' in html
    assert 'function orangeWarningDrawExportGroupBorder' in html
    assert 'orangeWarningImageExportFilename' in html
    assert 'orangeWarningTableExportFilename' in html
    assert 'ctx.fillRect(x + borderWidth, y, Math.max(0, width - borderWidth * 2), borderWidth)' in html
    assert 'orangeWarningDrawExportGroupBorder(ctx, margin, groupY, tableWidth, layout.height, { skipTop: skipTopBorder })' in html
    assert 'return records.length > 0;' in html
    assert '(groups || []).forEach((group, groupIndex) => {' in html
    assert 'const countStyles = [`font-weight:${highlighted ? "700" : "400"}`];' in html
    assert 'if (row.isGroupStart) countStyles.push("border-top:2px solid #111827")' in html
    assert 'return orangeWarningStatsRowsFromGroups(orangeWarningStatsGroups(records));' in html
    assert 'const pageRows = orangeWarningStatsRowsFromGroups(pageRange.records);' in html
    assert 'const pageRows = orangeWarningStatsRows(pageRange.records);' not in html
    assert '巡查记录：${stats.recordCount}条 实际次数：${stats.mergedCount}次' in html
    assert '<tr><th>次数</th><th>日期</th><th>时间</th><th>方向</th><th>桩号</th><th>巡查人</th><th>记录人</th></tr>' in html
    assert '<col class="orange-warning-stats-col-stake" />' in html
    assert '<col class="orange-warning-stats-col-recorder" />' in html
    assert '.orange-warning-stats-table th:nth-child(7)' in html
    assert 'grid-template-columns: minmax(0, 1fr) minmax(620px, 800px)' in html
    assert 'min-width: 760px' in html
    assert 'orange-warning-stats-group-start' in html
    assert 'orange-warning-stats-group-end' in html
    assert 'orange-warning-stats-group-count' in html
    assert 'orange-warning-stats-edge-top' in html
    assert 'orange-warning-stats-edge-right' in html
    assert 'orange-warning-stats-edge-bottom' in html
    assert 'orange-warning-stats-edge-left' in html
    assert 'orange-warning-stats-highlight-cell' not in html
    assert 'row.skipCountCell && cellIndex === 0' not in html
    assert 'border-top: 2px solid #111827' in html
    assert 'border-bottom: 2px solid #111827' in html
    assert 'line-break: auto' in html
    assert 'function orangeWarningPersonNames' in html
    assert 'orange-warning-person-name' in html
    assert 'white-space: nowrap' in html
    assert 'personNames: index >= 4' in html
    assert 'word-break: break-all' in html
    assert '<td${countClass} rowspan="${escapeHtml(row.countRowspan || 1)}">${escapeHtml(row.count)}</td>' in html
    assert 'return records.length > 0;' in html
    assert '连续记录按 1 次计；方向不影响合并' in html
    assert '$("exportOrangeWarningStatsTableBtn").addEventListener("click", exportOrangeWarningStatsTable)' in html
    assert '$("exportOrangeWarningStatsImageBtn").addEventListener("click", exportOrangeWarningStatsImage)' in html

    assert 'skipCountCell: index > 0' in html
    assert '日期 / 时间 / 姓名' not in html
    assert 'orange-warning-layout' in html
    assert 'repeat(auto-fill, minmax(240px, 1fr))' in html
    assert 'grid-template-columns: minmax(0, 1fr) minmax(620px, 800px)' in html
    assert 'table-layout: fixed' in html
    assert 'ORANGE_WARNING_STATS_PAGE_SIZE' in html
    assert 'updateOrangeWarningLayoutHeight' in html
    assert 'id="tunnelEntryPanel"' in html
    assert 'id="tunnelTemplatePanel"' in html
    assert 'data-tunnel-panel-target="tunnelEntryPanel"' in html
    assert 'data-tunnel-panel-target="tunnelTemplatePanel"' in html
    assert "switchTunnelMechanicalPanel" in html
    assert 'id="submitTunnelMechanicalBtn"' in html
    assert 'id="tunnelMechanicalUsername"' in html

    assert 'id="importTunnelMechanicalTemplateBtn"' in html
    assert 'id="exportTunnelMechanicalTemplateBtn"' in html
    assert 'id="tunnelMechanicalTemplateFile"' in html
    assert 'async function exportTunnelMechanicalTemplate' in html
    assert '$("exportTunnelMechanicalTemplateBtn").addEventListener("click", exportTunnelMechanicalTemplate)' in html
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
    assert 'data-settings-target="wechatSimulationSettings"' in html
    assert 'id="featureChannelSettings"' in html
    assert 'id="wechatSimulationSettings"' in html
    assert html.index('data-settings-target="featureChannelSettings"') < html.index('data-settings-target="wechatSimulationSettings"')
    assert 'id="wechatPatrolRecordTriggers"' in html
    assert 'id="wechatPatrolRecordTemplate"' in html
    assert 'id="wechatTunnelTemplateTriggers"' in html
    assert 'id="wechatTunnelModifyTemplateTriggers"' in html
    assert 'id="wechatInteractionMenuPreview"' in html
    assert 'id="wechatInteractionNotificationRooms"' in html
    assert 'id="wechatTunnelTemplate"' in html
    assert 'id="wechatTunnelModifyTemplate"' in html
    assert 'function latestSavedRoster(rosters)' in html
    assert 'state.selectedSavedRoster = latest ? savedRosterKey(latest) : "";' in html
    assert 'saved-today-monitor-cell' in html
    assert 'saved-today-monitor-name' in html
    assert 'saved-today-monitor-row-path' in html
    assert 'saved-today-monitor-col-path' in html
    assert 'saved-today-monitor-join-prev' in html
    assert 'todayMonitorRowIndexes' in html
    assert 'monitorShiftCodes.has' in html
    assert 'id="wechatInteractionTestText"' in html
    assert 'id="wechatInteractionTestStatus"' in html
    assert 'id="refreshWechatQrBtn"' not in html
    assert 'function refreshLightAgentWechatQr' in html
    assert '"/api/lightagent/wechat/refresh-qr"' not in html
    feature_section = html[
        html.index('<section class="settings-panel wechat-interaction-panel" id="featureChannelSettings">') :
        html.index('<section class="settings-panel single" id="wechatSimulationSettings">')
    ]
    simulation_section = html[
        html.index('<section class="settings-panel single" id="wechatSimulationSettings">') :
        html.index('<section class="settings-panel reminder-card-panel" id="monitorSettings">')
    ]
    assert '手动模拟微信发送' not in feature_section
    assert 'id="wechatInteractionTestText"' not in feature_section
    assert 'id="wechatInteractionTestText"' in simulation_section
    assert '<div class="side-block wide">' not in feature_section
    assert feature_section.count('<div class="side-block">') >= 2
    assert ".settings-panel.wechat-interaction-panel" in html
    assert "grid-template-columns: minmax(340px, 1.1fr) minmax(320px, 0.9fr)" in html
    assert "grid-template-columns: minmax(150px, 0.42fr) minmax(220px, 0.58fr)" in html
    assert "min-height: 88px" in html
    assert "grid-template-columns: minmax(300px, 1.08fr) minmax(280px, 0.92fr)" in html
    assert "@media (max-width: 720px)" in html
    assert ".wechat-interaction-editor .item { grid-template-columns: 1fr; }" in html
    assert 'id="featureChannelRoomSelect"' not in html
    assert 'id="addFeatureChannelRoomBtn"' not in html
    assert 'id="featureChannelRoomList"' not in html
    assert "启用微信群功能通道" not in html
    assert "隧道机电查询/录入" not in html
    assert "监控班和值班查询" not in html
    assert "微信群排班导入" not in html
    assert 'id="notificationTargetRoomSelect"' not in html
    assert 'id="addNotificationTargetRoomBtn"' not in html
    assert 'id="notificationTargetRoomList"' not in html
    assert 'id="saveFeatureChannelBtn"' in html
    assert '"/api/wechat-interaction-config"' in html
    assert '"/api/feature-channel-config"' not in html
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
    assert "saved-duty-pair" in html
    assert "明日早班：{tomorrow_early}" in html
    assert "details.tomorrow_early" in html
    assert "has-image" in html
    assert 'id="imageViewer"' in html
    assert "openImageViewer" in html
    assert "setupImageViewer" in html
    assert "image-viewer-image" in html
    assert "today-reminder-side" in html
    assert "today-reminder-image-card" in html
    assert "reminderChannelPreviewHtml" in html
    assert "dailyDutyPreviewMeta" in html
    assert 'id="vacationChannelPreview"' in html
    assert 'id="patrolWarningChannelPreview"' in html
    assert "send_content_mode" in html
    assert "data-today-state-at" in html
    assert "已提醒" in html
    assert "已过预警结束巡查提醒" in html
    assert "其余待发送提醒" in html
    assert "event-collapsed" in html





def test_settings_redesign_url_aliases_real_main_ui(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/settings-redesign")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, max-age=0, must-revalidate"
    html = response.text
    assert 'id="appShell"' in html
    assert 'id="mainSidebar"' in html
    assert 'data-main-group="home"' in html
    assert 'id="mainSubnav"' in html
    assert "MAIN_SUBNAV_SCHEMA" in html
    assert "renderMainSubnav" in html
    assert "settings-redesign.js" not in html
    assert "首页" in html
    assert "通知" in html


def test_settings_redesign_static_file_redirects_to_main_ui():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "settings-redesign.html").read_text(encoding="utf-8")

    assert "location.replace" in html
    assert "./index.html" in html
    assert "首页概览" not in html
    assert "通知通道" not in html


def test_construction_sites_crud_roundtrip(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    created = client.post("/api/construction-sites", json={"name": "南涧至景东方向K88+730-K88+880上挡墙施工安全检查"})
    assert created.status_code == 200
    site = created.json()["site"]
    assert site["name"].startswith("南涧至景东方向")

    listed = client.get("/api/construction-sites")
    assert listed.status_code == 200
    assert listed.json()["sites"][0]["name"] == site["name"]

    updated = client.put(f"/api/construction-sites/{site['id']}", json={"name": "K88+730-K88+880上挡墙"})
    assert updated.status_code == 200
    assert updated.json()["site"]["name"] == "K88+730-K88+880上挡墙"

    deleted = client.delete(f"/api/construction-sites/{site['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True


def test_config_export_route_returns_complete_snapshot(tmp_path):
    source = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    source.save_notification_config(sender_type="lightagent", webhook_url="", lightagent_token="push-token", mention_mode="custom")
    source.save_daily_duty_config(reminder_time="07:20", big_driver_names=["司机甲"], small_driver_names=["司机乙"])
    source.save_vacation_reminder_config(
        enabled=True,
        start_reminder_time="07:50",
        end_reminder_time="07:55",
        start_message_templates=["开始休息提醒"],
        end_message_templates=["结束休息提醒"],
        send_content_mode="image",
    )
    source.save_feature_channel_config(
        enabled=True,
        lightagent_web_url="http://lightagent:9899",
        lightagent_web_password="web-pass",
        wechat_group_room_id="wgr_notice",
        wechat_group_room_name="通知群",
        wechat_group_rooms=[{"id": "wgr_notice", "name": "通知群"}],
        allow_tunnel_mechanical=True,
        allow_duty_query=False,
        allow_roster_import=True,
    )
    source.save_wechat_interaction_config(
        patrol_record_triggers=["巡查记录"],
        patrol_record_template="查询商邱宏巡查记录 2026-08-01至2026-08-16",
        tunnel_template_triggers=["模板"],
        tunnel_template="隧道机电录入 日期{date} 负责人商邱宏 记录人罗富耀 天气晴",
        tunnel_modify_template_triggers=["修改模板"],
        tunnel_modify_template="隧道机电修改 日期{date} 负责人商邱宏 记录人罗富耀 天气晴",
    )
    source.save_patrol_warning_config(enabled=True, username="patrol-user", password="patrol-pass", route_code="S41")
    source.save_wecom_app_menu_config([
        {"name": "监控在岗", "items": [{"name": "今日在岗", "command": "查询今日在岗"}]}
    ])
    source.add_construction_site("南涧至景东方向K88+730-K88+880上挡墙施工安全检查")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/config/export")
    snapshot = response.json()

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="duty-reminder-config-')
    assert snapshot["format"] == "duty-reminder-config"
    assert snapshot["version"] == 1
    assert snapshot["tables"]["notification_config"][0]["lightagent_token"] == "push-token"
    assert snapshot["tables"]["daily_duty_config"][0]["reminder_time"] == "07:20"
    assert snapshot["tables"]["vacation_reminder_config"][0]["start_reminder_time"] == "07:50"
    assert snapshot["tables"]["feature_channel_config"][0]["lightagent_web_password"] == "web-pass"
    assert snapshot["tables"]["wechat_interaction_config"][0]["patrol_record_template"].startswith("查询商邱宏巡查记录")
    assert snapshot["tables"]["wecom_app_menu_config"][0]["menu_json"]
    assert snapshot["tables"]["construction_sites"][0]["name"].startswith("南涧至景东方向")
    assert snapshot["tables"]["patrol_warning_config"][0]["username"] == "patrol-user"


def test_config_export_route_includes_wechat_bridge_identity(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)

    class FakeBridge:
        def export_identity_snapshot(self):
            return {
                "rooms": {"wgr_notice": {"runtime_room_id": "room@@runtime", "name": "通知群"}},
                "members": {"wgm_member": {"runtime_sender_id": "@member", "name": "示例甲"}},
            }

    app.state.wechat_bridge = FakeBridge()
    client = TestClient(app)

    snapshot = client.get("/api/config/export").json()

    assert snapshot["wechat_bridge_identity"]["rooms"]["wgr_notice"]["runtime_room_id"] == "room@@runtime"
    assert snapshot["wechat_bridge_identity"]["members"]["wgm_member"]["runtime_sender_id"] == "@member"


def test_config_import_route_restores_configuration_and_overwrites_existing_data(tmp_path):
    source = DutyRepository(tmp_path / "source" / "duty-reminder.db")
    source.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="http://lightagent:9899/api/push/send",
        lightagent_token="push-token",
        lightagent_target="wgr_notice",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
        mention_mode="custom",
        mention_targets="示例甲",
        message_template="提醒 {name}",
    )
    source.save_daily_duty_config(reminder_time="07:20", big_driver_names=["司机甲"], small_driver_names=["司机乙"])
    source.save_vacation_reminder_config(
        enabled=True,
        start_reminder_time="07:50",
        end_reminder_time="07:55",
        start_message_templates=["开始休息提醒"],
        end_message_templates=["结束休息提醒"],
        send_content_mode="image",
    )
    source.save_feature_channel_config(
        enabled=True,
        lightagent_web_url="http://lightagent:9899",
        lightagent_web_password="web-pass",
        wechat_group_room_id="wgr_notice",
        wechat_group_room_name="通知群",
        wechat_group_rooms=[{"id": "wgr_notice", "name": "通知群"}],
        allow_tunnel_mechanical=True,
        allow_duty_query=False,
        allow_roster_import=True,
    )
    source.save_wechat_interaction_config(
        patrol_record_triggers=["巡查记录"],
        patrol_record_template="查询商邱宏巡查记录 2026-08-01至2026-08-16",
        tunnel_template_triggers=["模板"],
        tunnel_template="隧道机电录入 日期{date} 负责人商邱宏 记录人罗富耀 天气晴",
        tunnel_modify_template_triggers=["修改模板"],
        tunnel_modify_template="隧道机电修改 日期{date} 负责人商邱宏 记录人罗富耀 天气晴",
    )
    source.save_personnel_contacts([{"name": "示例甲", "mention_mobile": "10000000000"}])
    source.save_monitored_person(name="示例甲", mention_mobile="10000000000", daily_time="07:30")
    source.save_custom_reminder(name="示例甲", mention_mobile="10000000000", shift_code="early", reminder_time="07:10", message="自定义提醒")
    source.save_roster_month(2026, 8, [{"name": "示例甲", "days": {"1": "早"}}], "uploads/a.png")
    source.save_wecom_app_menu_config([
        {"name": "监控在岗", "items": [{"name": "今日在岗", "command": "查询今日在岗"}]}
    ])
    source.add_construction_site("南涧至景东方向K88+730-K88+880上挡墙施工安全检查")
    snapshot = source.export_config_snapshot()
    snapshot["tables"]["custom_reminders"][0]["reminder_time"] = "21:00"

    app = create_app(data_dir=tmp_path / "target" / "data", upload_dir=tmp_path / "target" / "uploads", start_scheduler=False)
    target_repo = DutyRepository(tmp_path / "target" / "data" / "duty-reminder.db")
    target_repo.save_notification_config(sender_type="wecom_webhook", webhook_url="https://old.example.test")
    target_repo.save_personnel_contacts([{"name": "旧姓名", "mention_mobile": "19999999999"}])
    client = TestClient(app)

    response = client.post(
        "/api/config/import",
        files={"file": ("duty-reminder-config.json", json_bytes(snapshot), "application/json")},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    backup = response.json()["backup"]
    assert backup["filename"].startswith("before-config-import-")
    assert (tmp_path / "target" / "data" / "backups" / backup["filename"]).is_file()
    assert target_repo.get_notification_config()["lightagent_token"] == "push-token"
    assert target_repo.get_notification_config()["lightagent_target"] == "wgr_notice"
    assert target_repo.get_daily_duty_config()["reminder_time"] == "07:20"
    assert target_repo.get_vacation_reminder_config()["start_reminder_time"] == "07:50"
    assert target_repo.get_feature_channel_config()["lightagent_web_password"] == "web-pass"
    assert target_repo.get_wechat_interaction_config()["patrol_record_template"].startswith("查询商邱宏巡查记录")
    assert target_repo.list_personnel()[0]["name"] == "示例甲"
    assert target_repo.list_monitored_people()[0]["name"] == "示例甲"
    assert target_repo.list_custom_reminders()[0]["message"] == "自定义提醒"
    assert target_repo.list_custom_reminders()[0]["reminder_time"] == "07:50"
    assert target_repo.get_roster_month(2026, 8)["grid"][0]["days"]["1"] == "早"
    assert target_repo.get_wecom_app_menu_config()[0]["name"] == "监控在岗"
    assert target_repo.list_construction_sites()[0]["name"].startswith("南涧至景东方向")
    assert all(person["name"] != "旧姓名" for person in target_repo.list_personnel())


def test_database_backup_list_create_and_download_routes(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_personnel_names(["示例甲"])

    create_response = client.post("/api/config/backups")

    assert create_response.status_code == 200
    backup = create_response.json()["backup"]
    assert backup["filename"].startswith("manual-backup-")

    list_response = client.get("/api/config/backups")
    backups = list_response.json()["backups"]
    assert backups[0]["filename"] == backup["filename"]
    assert backups[0]["download_url"] == f"/api/config/backups/{backup['filename']}"

    download = client.get(backups[0]["download_url"])
    assert download.status_code == 200
    assert len(download.content) > 0


def test_upload_cleanup_status_and_manual_cleanup_route(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    old_generated = upload_dir / "wechat-query-old.png"
    old_generated.write_bytes(b"old")
    old_regular = upload_dir / "regular.png"
    old_regular.write_bytes(b"old-regular")
    fresh = upload_dir / "wechat-query-fresh.png"
    fresh.write_bytes(b"fresh")
    old_ts = (datetime.now(TZ) - timedelta(days=3)).timestamp()
    os.utime(old_generated, (old_ts, old_ts))
    os.utime(old_regular, (old_ts, old_ts))

    app = create_app(data_dir=tmp_path / "data", upload_dir=upload_dir, start_scheduler=False)
    client = TestClient(app)

    status = client.get("/api/system-status").json()["upload_storage"]
    assert status["expired_generated_count"] == 1
    assert status["expired_regular_count"] == 0

    cleanup = client.post("/api/uploads/cleanup").json()
    assert cleanup["result"]["deleted"] == 1
    assert not old_generated.exists()
    assert old_regular.exists()
    assert fresh.exists()


def test_config_import_route_restores_wechat_bridge_identity(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)

    captured: dict[str, object] = {}

    class FakeBridge:
        def import_identity_snapshot(self, identity):
            captured["identity"] = identity

    app.state.wechat_bridge = FakeBridge()
    client = TestClient(app)
    snapshot = {
        "format": "duty-reminder-config",
        "version": 1,
        "tables": {},
        "wechat_bridge_identity": {
            "rooms": {"wgr_notice": {"runtime_room_id": "room@@runtime", "name": "通知群"}},
            "members": {"wgm_member": {"runtime_sender_id": "@member", "name": "示例甲"}},
        },
    }

    response = client.post(
        "/api/config/import",
        files={"file": ("duty-reminder-config.json", json_bytes(snapshot), "application/json")},
    )

    assert response.status_code == 200
    assert captured["identity"] == snapshot["wechat_bridge_identity"]


def test_refresh_lightagent_wechat_qr_route_uses_manager(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)

    calls: list[str] = []

    class FakeBridge:
        def refresh_login_qr(self):
            calls.append("refresh")

    app.state.wechat_bridge = FakeBridge()
    client = TestClient(app)

    response = client.post("/api/lightagent/wechat/refresh-qr")

    assert response.status_code == 410
    assert calls == []
    assert "已停用" in response.json()["detail"]

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


def test_notification_channel_ui_uses_current_selected_channel():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert "settings-redesign.js" not in html
    assert "function updateNotificationBackendFields()" in html
    assert 'data-notification-sender="wecom_webhook"' in html
    assert 'data-notification-sender="lightagent"' not in html
    assert 'data-notification-sender="wecom_app"' in html
    assert '$("notificationCommonFields").classList.toggle("hidden", isWecomApp);' in html
    assert '$("wecomNotificationFields").classList.toggle("hidden", activePanel !== "wecom_webhook");' in html
    assert 'if ($("lightAgentNotificationFields")) $("lightAgentNotificationFields").classList.add("hidden");' in html
    assert '$("wecomAppNotificationFields").classList.toggle("hidden", !isWecomApp);' in html
    assert "updateMonitorNotificationFields();" in html
    assert "updateCustomReminderNotificationFields();" in html

    notify_targets_section = html[
        html.index('<div id="notificationTargetsPanel"') : html.index('<div id="notificationCommonFields"')
    ]
    wecom_app_section = html[
        html.index('<div id="wecomAppNotificationFields"') : html.index('<p id="notificationStatus"')
    ]
    interaction_section = html[
        html.index('<section class="settings-panel single" id="interactionCommandSettings"')
        : html.index('<section class="settings-panel reminder-card-panel" id="monitorSettings"')
    ]
    person_center_section = html[
        html.index('<section class="settings-panel single" id="personCenterSettings"') : html.index('<section class="settings-panel single" id="driverSettings"')
    ]
    for label in ["公共通知接收人", "今日在岗接收人", "公路预警接收人", "系统测试接收人"]:
        assert label in notify_targets_section
        assert label not in wecom_app_section
    assert 'id="wecomAppTargetNamesPicker"' in notify_targets_section
    assert 'id="wecomAppTargetNamesPicker"' not in wecom_app_section
    assert "<strong>自定义菜单</strong>" not in wecom_app_section
    assert "企业微信绑定状态" not in wecom_app_section
    assert "企业微信绑定状态" in person_center_section
    assert 'id="wecomAppBindingSummary"' not in html
    assert "自定义菜单" in interaction_section
    assert 'id="wecomAppMenuPreview"' in interaction_section
    assert 'id="createWecomAppMenuBtn"' in interaction_section
    assert 'id="saveWecomAppMenuBtn"' in interaction_section
    assert 'id="addWecomAppMenuGroupBtn"' in interaction_section
    assert 'id="restoreWecomAppMenuBtn"' in interaction_section
    assert 'target: "interactionCommandSettings"' in html
    assert 'featureChannelSettings: { group: "mech", view: "mech-modify-template" }' in html
    assert 'featureChannelSettings: { group: "notify", view: "notify-commands-group" }' not in html


def test_settings_nav_is_rendered_from_current_group_schema():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="settingsNavItems"' in html
    assert "const SETTINGS_NAV_SCHEMA =" in html
    assert "首页" in html
    assert "排班与在岗" in html
    assert "提醒中心" in html
    assert "机电施工" in html
    assert "巡查预警" in html
    assert "消息通知" in html
    assert "人员管理" in html
    assert "岗位分组" in html
    assert "记录与工具" in html
    assert "data-settings-groups=" not in html
    assert 'id="constructionSettings"' in html
    assert 'id="refreshConstructionSitesBtn"' in html
    assert 'id="addConstructionSiteBtn"' in html


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
    assert body["preview_image_url"].startswith("/api/uploads/tunnel-mechanical-preview-")
    assert (tmp_path / "uploads" / body["preview_image_url"].rsplit("/", 1)[-1]).exists()
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


def test_httpx_error_message_includes_exception_type_when_message_is_empty():
    request = main_module.httpx.Request("GET", "https://zhyhpt.yciccloud.com/prod-api/code")
    error = main_module.httpx.ConnectError("", request=request)

    message = main_module._httpx_error_message(error)

    assert "ConnectError" in message
    assert "https://zhyhpt.yciccloud.com/prod-api/code" in message


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


def test_today_reminders_keeps_first_patrol_warning_end_event_when_followup_disabled(tmp_path, monkeypatch):
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
    assert kinds.count("patrol_warning_end") == 1


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


def test_ongoing_patrol_warning_is_hidden_after_window(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 8, 17, 20, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_patrol_warning_config(
        enabled=True,
        route_code="S41",
        end_reminder_window_hours=48,
    )
    repo.save_patrol_warning_state(
        warning={
            "key": "warning-old",
            "route_code": "S41",
            "route_name": "南涧－宁洱",
            "warning_level": "3",
            "warning_level_label": "橙色预警",
            "start_time": "2026-07-29T01:45:02+08:00",
            "end_time": "",
            "start_stake": "K106.670",
            "end_stake": "K120.000",
        }
    )
    client = TestClient(app)

    today_response = client.get("/api/reminders/today")
    image_response = client.get("/api/patrol-warning-image")

    assert today_response.status_code == 200
    body = today_response.json()
    assert not any(event["kind"].startswith("patrol_warning_") for event in body["events"])
    assert not any(status["key"] == "patrol_warning" for status in body["group_statuses"])
    assert image_response.status_code == 404


def test_patrol_warning_monitor_marks_old_unsent_warning_handled(tmp_path, monkeypatch):
    sent: list[object] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 8, 17, 20, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent.append(("text", content, mentioned_mobile_list))

        async def send_image(self, image_bytes: bytes):
            sent.append(("image", image_bytes))

    warning = warning_from_dict(
        {
            "key": "warning-old",
            "route_code": "S41",
            "route_name": "南涧－宁洱",
            "warning_level": "3",
            "warning_level_label": "橙色预警",
            "start_time": "2026-07-29T01:45:02+08:00",
            "end_time": "",
            "start_stake": "K106.670",
            "end_stake": "K120.000",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-08-09T01:20:00+08:00",
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
        end_reminder_window_hours=48,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    state = repo.get_patrol_warning_state()
    assert sent == []
    assert state["warning_key"] == "warning-old"
    assert state["last_start_sent_key"] == "warning-old"
    assert repo.list_send_records() == []


def test_patrol_warning_monitor_still_sends_fresh_warning(tmp_path, monkeypatch):
    sent: list[object] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 8, 17, 20, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent.append(("text", content, mentioned_mobile_list))

        async def send_image(self, image_bytes: bytes):
            sent.append(("image", image_bytes))

    warning = warning_from_dict(
        {
            "key": "warning-new",
            "route_code": "S41",
            "route_name": "南涧－宁洱",
            "warning_level": "3",
            "warning_level_label": "橙色预警",
            "start_time": "2026-08-08T17:05:00+08:00",
            "end_time": "",
            "start_stake": "K106.670",
            "end_stake": "K120.000",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-08-09T01:20:00+08:00",
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
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    assert any(item[0] == "text" for item in sent)
    assert repo.get_patrol_warning_state()["last_start_sent_key"] == "warning-new"


def test_patrol_warning_monitor_sends_recent_warning_seen_after_start_window(tmp_path, monkeypatch):
    sent: list[object] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 12, 8, 32, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent.append(("text", content, mentioned_mobile_list))

        async def send_image(self, image_bytes: bytes):
            sent.append(("image", image_bytes))

    warning = warning_from_dict(
        {
            "key": "warning-recent-late",
            "route_code": "S41",
            "route_name": "南涧－宁洱",
            "warning_level": "3",
            "warning_level_label": "橙色预警",
            "start_time": "2026-08-12T03:45:07+08:00",
            "create_time": "2026-08-12T04:55:02+08:00",
            "end_time": "2026-08-12T06:40:17+08:00",
            "start_stake": "K107.000",
            "end_stake": "K121.000",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-08-12T17:00:00+08:00",
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
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "fetch_latest_warning_result", fake_fetch_latest_warning_result)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    assert any(item[0] == "text" for item in sent)
    assert any(item[0] == "image" for item in sent)
    state = repo.get_patrol_warning_state()
    assert state["last_start_sent_key"] == "warning-recent-late"
    assert state["last_end_reminder_slot"] == "2026-08-12T06:40:17+08:00"
    assert [record["kind"] for record in repo.list_send_records()] == ["patrol_warning_end", "patrol_warning_start"]


def test_patrol_warning_monitor_updates_state_without_notification_channel(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 12, 8, 32, tzinfo=tz)

    warning = warning_from_dict(
        {
            "key": "warning-no-channel",
            "route_code": "S41",
            "warning_level": "3",
            "warning_level_label": "橙色预警",
            "start_time": "2026-08-12T08:00:00+08:00",
            "create_time": "2026-08-12T08:05:00+08:00",
        },
        main_module.TZ,
    )

    async def fake_fetch_latest_warning_result(*args, **kwargs):
        return SimpleNamespace(
            warning=warning,
            stats={"total_rows": 1, "matched_rows": 1},
            token="token",
            token_expires_at="2026-08-12T17:00:00+08:00",
            token_reused=False,
        )

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
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
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: None)
    monkeypatch.setattr(main_module, "next_poll_time", lambda now, interval_minutes: now)

    asyncio.run(main_module._check_patrol_warning_monitor(repo))

    state = repo.get_patrol_warning_state()
    assert state["warning_key"] == "warning-no-channel"
    assert state["warning"]["key"] == "warning-no-channel"
    assert state["last_checked_at"]
    assert state["last_start_sent_key"] == ""
    assert repo.list_send_records() == []


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
    assert re.search(r"\.review-busy-overlay\[hidden\]\s*\{\s*display:\s*none;", html)


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


def test_notification_detail_page_is_public_and_embeds_full_image(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("DUTY_REMINDER_PUBLIC_URL", "https://jk.79c.cc")

    url = main_module._notification_news_url(title="图文详情", description="完整提醒内容", image_bytes=b"png-bytes")
    path = "/" + url.split("https://jk.79c.cc/", 1)[1]
    app = create_app(data_dir=tmp_path / "data", upload_dir=uploads, start_scheduler=False, admin_password="secret")
    client = TestClient(app)

    response = client.get(path)

    assert response.status_code == 200
    assert "图文详情" in response.text
    assert "完整提醒内容" in response.text
    assert "data:image/png;base64,cG5nLWJ5dGVz" in response.text
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


def test_roster_upload_corrects_unique_ocr_name_typo_from_existing_roster(tmp_path, monkeypatch):
    def fake_extract(path):
        return {
            "year": 2026,
            "month": 8,
            "source_image_path": path,
            "ocr_status": "template_ok",
            "grid": [
                {"name": "沫春宇", "days": {"1": "早"}},
                {"name": "罗富耀", "days": {"1": "中"}},
            ],
        }

    monkeypatch.setattr("app.main.extract_roster_image", fake_extract)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    app.state.repo.save_roster_month(
        2026,
        7,
        [
            {"name": "沐春宇", "days": {"1": "早"}},
            {"name": "罗富耀", "days": {"1": "中"}},
        ],
        "previous.png",
    )
    body = TestClient(app).post(
        "/api/rosters/upload",
        files={"file": ("roster.png", b"fake-image", "image/png")},
    ).json()

    assert [row["name"] for row in body["grid"]] == ["沐春宇", "罗富耀"]
    assert body["name_corrections"] == [{"before": "沫春宇", "after": "沐春宇"}]


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


def test_cleanup_old_uploads_removes_generated_files_after_one_day_only(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    old_generated = upload_dir / "daily-duty-query-old.png"
    old_detail = upload_dir / "notification-detail-old.html"
    old_original = upload_dir / "uploaded-roster.png"
    fresh_generated = upload_dir / "wechat-query-fresh.png"
    for path in (old_generated, old_detail, old_original, fresh_generated):
        path.write_bytes(b"x")
    old_timestamp = (datetime.now(main_module.TZ) - timedelta(days=2)).timestamp()
    os.utime(old_generated, (old_timestamp, old_timestamp))
    os.utime(old_detail, (old_timestamp, old_timestamp))
    os.utime(old_original, (old_timestamp, old_timestamp))
    monkeypatch.setattr(main_module, "UPLOAD_KEEP_DAYS", 90)
    monkeypatch.setattr(main_module, "GENERATED_UPLOAD_KEEP_DAYS", 1)

    main_module._cleanup_old_uploads(upload_dir)

    assert not old_generated.exists()
    assert not old_detail.exists()
    assert old_original.exists()
    assert fresh_generated.exists()


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


def test_custom_reminder_does_not_preview_without_matching_shift(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2026,
            "month": 8,
            "source_image_path": "uploads/month.png",
            "grid": [{"name": "罗富耀", "days": {"10": "中"}}],
        },
    )
    client.post(
        "/api/custom-reminders",
        json={
            "name": "罗富耀",
            "shift_code": "night",
            "reminder_time": "21:00",
            "message": "@罗富耀\n需要开启隧道灯",
            "enabled": True,
        },
    )

    assert client.get("/api/custom-reminders").json()["reminders"][0]["message"] == "需要开启隧道灯"
    events = client.post("/api/reminders/preview", json={"target_date": "2026-08-10"}).json()["events"]
    assert [event for event in events if event["kind"] == "custom"] == []


def test_custom_reminder_rejects_conflicting_leading_at_target(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/custom-reminders",
        json={
            "name": "商邱宏",
            "shift_code": "night",
            "reminder_time": "21:00",
            "message": "@罗富耀\n需要开启隧道灯",
            "enabled": True,
        },
    )

    assert response.status_code == 422
    assert "提醒文案开头的 @对象必须和姓名一致" in response.json()["detail"]
    assert client.get("/api/custom-reminders").json()["reminders"] == []


def test_custom_reminder_rejects_time_outside_shift_window(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/custom-reminders",
        json={
            "name": "罗熙云",
            "shift_code": "early",
            "reminder_time": "21:00",
            "message": "需要开启隧道灯",
            "enabled": True,
        },
    )

    assert response.status_code == 422
    assert "早班提醒时间必须在 00:00 至 08:00 之间" in str(response.json()["detail"])
    assert client.get("/api/custom-reminders").json()["reminders"] == []


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


def test_notification_config_reports_wecom_app_as_active_sender_when_enabled(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="wecom_webhook",
        webhook_url="https://example.test/cgi-bin/webhook/send?key=old",
        wecom_app_enabled=True,
        wecom_app_corp_id="corp",
        wecom_app_agent_id="1000001",
        wecom_app_secret="secret",
        wecom_app_token="token",
        wecom_app_encoding_aes_key="aes",
    )
    client = TestClient(app)

    public_config = client.get("/api/notification-config").json()["config"]

    assert public_config["sender_type"] == "wecom_app"
    assert public_config["effective_sender_type"] == "wecom_app"
    assert public_config["wecom_app_enabled"] is True
    assert public_config["notification_display"] == "已配置"


def test_lightagent_notification_config_hides_secret_fields_and_tests_send(tmp_path, monkeypatch):
    class FailingLightAgentClient:
        def __init__(self, **kwargs):
            raise AssertionError("旧个人微信群客户端不应再被创建")

    monkeypatch.setattr("app.main.LightAgentNotifyClient", FailingLightAgentClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    save_response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "https://lightagent.test/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_target": "room-1",
            "lightagent_targets": [{"id": "room-1", "name": "通知群"}],
            "message_template": "{name} {date} {shift_label}",
        },
    )
    public_config = client.get("/api/notification-config").json()["config"]
    test_response = client.post("/api/notification-config/test", json={"test_mobile": "10000000000"})

    assert save_response.status_code == 200
    assert public_config["sender_type"] == "wecom_webhook"
    assert public_config["lightagent_url"] == ""
    assert public_config["lightagent_configured"] is False
    assert public_config["lightagent_token_configured"] is False
    assert public_config["lightagent_target"] == ""
    assert public_config["lightagent_targets"] == []
    assert public_config["lightagent_active"] is False
    assert test_response.status_code == 400
    assert "请先配置通知发送通道" in test_response.json()["detail"]

def test_notification_channel_switch_preserves_wecom_but_only_lightagent_sends(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

    class FailingLightAgentClient:
        def __init__(self, **kwargs):
            raise AssertionError("旧个人微信群客户端不应再被创建")

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    monkeypatch.setattr("app.main.LightAgentNotifyClient", FailingLightAgentClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="wecom_webhook",
        webhook_url="https://example.test/cgi-bin/webhook/send?key=old-wecom",
    )
    client = TestClient(app)

    response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "webhook_url": "",
            "lightagent_url": "https://lightagent.test/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_targets": [{"id": "room-1", "name": "通知群"}],
        },
    )
    test_response = client.post("/api/notification-config/test", json={"person_name": "示例甲"})
    config = repo.get_notification_config()

    assert response.status_code == 200
    assert config["sender_type"] == "wecom_webhook"
    assert config["webhook_url"].endswith("old-wecom")
    assert config["lightagent_targets"] == []
    assert response.json()["config"]["lightagent_active"] is False
    assert test_response.status_code == 200
    assert sent["webhook_url"].endswith("old-wecom")
    assert sent["content"].startswith("示例甲")

def test_notification_channel_switch_preserves_lightagent_but_only_wecom_sends(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

    class FailingLightAgentClient:
        def __init__(self, **kwargs):
            raise AssertionError("旧个人微信群客户端不应再被创建")

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    monkeypatch.setattr("app.main.LightAgentNotifyClient", FailingLightAgentClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="https://lightagent.test/api/push/send",
        lightagent_token="push-token",
        lightagent_targets=[{"id": "room-1", "name": "通知群"}],
    )
    client = TestClient(app)

    response = client.post(
        "/api/notification-config",
        json={
            "sender_type": "wecom_webhook",
            "webhook_url": "https://example.test/cgi-bin/webhook/send?key=new-wecom",
            "lightagent_url": "",
            "lightagent_token": "",
            "lightagent_targets": [],
            "mention_mode": "custom",
            "mention_targets": "10000000000",
        },
    )
    test_response = client.post("/api/notification-config/test", json={"test_mobile": "10000000000"})
    config = repo.get_notification_config()

    assert response.status_code == 200
    assert config["sender_type"] == "wecom_webhook"
    assert config["webhook_url"].endswith("new-wecom")
    assert config["lightagent_url"] == ""
    assert config["lightagent_token"] == ""
    assert config["lightagent_targets"] == []
    assert response.json()["config"]["webhook_active"] is True
    assert response.json()["config"]["lightagent_configured"] is False
    assert test_response.status_code == 200
    assert sent["webhook_url"].endswith("new-wecom")
    assert sent["mentions"] == ["10000000000"]

def test_notification_test_failure_sanitizes_wechat_ids(tmp_path, monkeypatch):
    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            raise main_module.WeComError("wgr_notice failed; @member-runtime failed")

    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={"sender_type": "wecom_webhook", "webhook_url": "https://example.test/webhook"},
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

    response = client.post("/api/notification-config/test", json={"person_name": "王路飞"})

    assert response.status_code == 502
    assert response.json()["detail"] == "微信群 failed; 王路飞 failed"

def test_lightagent_notification_config_syncs_target_to_wechat_group_channel(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        raise AssertionError("旧 LightAgent Web 不应再被调用")

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
    assert body["config"]["sender_type"] == "wecom_webhook"
    assert body["config"]["lightagent_targets"] == []
    assert body["lightagent_sync"] == {"success": True, "skipped": True, "reason": "personal_wechat_disabled"}
    assert calls == []

def test_lightagent_notification_config_reports_inactive_stable_target(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        raise AssertionError("旧 LightAgent Web 不应再被调用")

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
    assert body["config"]["sender_type"] == "wecom_webhook"
    assert body["config"]["lightagent_targets"] == []
    assert body["lightagent_sync"] == {"success": True, "skipped": True, "reason": "personal_wechat_disabled"}
    assert calls == []

def test_lightagent_notification_config_requires_connected_wechat_channel(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        raise AssertionError("旧 LightAgent Web 不应再被调用")

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
    assert body["config"]["sender_type"] == "wecom_webhook"
    assert body["config"]["lightagent_targets"] == []
    assert body["lightagent_sync"] == {"success": True, "skipped": True, "reason": "personal_wechat_disabled"}
    assert calls == []

def test_lightagent_notification_config_reports_sync_failure_without_losing_save(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        raise AssertionError("旧 LightAgent Web 不应再被调用")

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
    assert body["config"]["sender_type"] == "wecom_webhook"
    assert body["config"]["lightagent_targets"] == []
    assert body["lightagent_sync"] == {"success": True, "skipped": True, "reason": "personal_wechat_disabled"}
    assert calls == []

def test_lightagent_notification_env_defaults_are_used_for_empty_database(tmp_path, monkeypatch):
    class FailingLightAgentClient:
        def __init__(self, **kwargs):
            raise AssertionError("旧个人微信群客户端不应再被创建")

    monkeypatch.setenv("NOTIFICATION_SENDER_TYPE", "lightagent")
    monkeypatch.setenv("LIGHTAGENT_BASE_URL", "http://lightagent:9899")
    monkeypatch.setenv("LIGHTAGENT_PUSH_TOKEN", "push-token")
    monkeypatch.setenv("LIGHTAGENT_NOTIFY_TARGET", "room-1")
    monkeypatch.setattr("app.main.LightAgentNotifyClient", FailingLightAgentClient)
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

    assert public_config["sender_type"] == "wecom_webhook"
    assert public_config["lightagent_url"] == ""
    assert public_config["lightagent_configured"] is False
    assert public_config["lightagent_token_configured"] is False
    assert public_config["lightagent_target"] == ""
    assert public_config["lightagent_targets"] == []
    assert test_response.status_code == 400

def test_saved_wechat_bridge_notification_channel_is_not_overridden_by_wecom_env(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

    class FailingWechatBridgeClient:
        def __init__(self, **kwargs):
            raise AssertionError("旧内置个人微信桥客户端不应再被创建")

    monkeypatch.setenv("WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("NOTIFICATION_SENDER_TYPE", "wecom_webhook")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.test/cgi-bin/webhook/send?key=env-wecom")
    monkeypatch.setattr("app.main.WeComWebhookClient", FakeWebhookClient)
    monkeypatch.setattr("app.main.WechatBridgeNotifyClient", FailingWechatBridgeClient)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="https://example.test/cgi-bin/webhook/send?key=old-wecom",
        lightagent_target="wgr_notice",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
        message_template="{name}",
    )
    client = TestClient(app)

    public_config = client.get("/api/notification-config").json()["config"]
    test_response = client.post("/api/notification-config/test", json={"person_name": "示例甲"})

    assert public_config["sender_type"] == "wecom_webhook"
    assert public_config["wechat_bridge_enabled"] is False
    assert public_config["lightagent_target"] == ""
    assert test_response.status_code == 200
    assert sent["webhook_url"].endswith("old-wecom")
    assert sent["content"] == "示例甲"

def test_lightagent_wechat_proxy_endpoints(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        return {"status": "success"}

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    responses = [
        client.get("/api/lightagent/wechat/status"),
        client.post("/api/lightagent/wechat/refresh"),
        client.post("/api/lightagent/wechat/refresh-qr"),
        client.get("/api/lightagent/wechat/rooms"),
        client.get("/api/lightagent/wechat/members?room_id=room-1"),
    ]

    assert [response.status_code for response in responses] == [410, 410, 410, 410, 410]
    assert calls == []
    assert all("已停用" in response.json()["detail"] for response in responses)

def test_lightagent_wechat_rooms_marks_stable_room_without_runtime_unsendable(tmp_path, monkeypatch):
    calls: list[dict[str, object]] = []

    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        calls.append({"method": method, "path": path, "params": params, "json_body": json_body})
        return {"status": "success"}

    monkeypatch.setattr(main_module, "_lightagent_web_request", fake_lightagent_web_request)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.get("/api/lightagent/wechat/rooms")

    assert response.status_code == 410
    assert calls == []
    assert "已停用" in response.json()["detail"]

def test_wechat_query_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    response = client.post("/api/wechat-query", json={"text": "查询我的监控", "runtime_sender_id": "@member-1"})

    assert response.status_code == 401


def test_notification_wechat_targets_restrict_wechat_interaction_room(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    app.state.repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "wgr_feature", "name": "功能群"}],
    )

    save_response = client.post(
        "/api/feature-channel-config",
        json={"enabled": True, "wechat_group_room_id": "wgr_feature", "wechat_group_room_name": "功能群"},
    )
    get_response = client.get("/api/feature-channel-config")

    assert save_response.status_code == 410
    assert get_response.status_code == 410
    assert "已停用" in save_response.json()["detail"]
    assert app.state.repo.get_notification_config()["sender_type"] == "wecom_webhook"

def test_feature_channel_config_reports_lightagent_sync_failure_without_losing_save(tmp_path, monkeypatch):
    def fake_lightagent_web_request(repo, method, path, *, params=None, json_body=None):
        raise AssertionError("旧 LightAgent Web 不应再被调用")

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
        },
    )

    assert response.status_code == 410
    assert "已停用" in response.json()["detail"]

def test_feature_channel_legacy_permission_switches_do_not_disable_wechat_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    response = client.post(
        "/api/feature-channel-config",
        json={
            "enabled": True,
            "wechat_group_room_id": "wgr_feature",
            "allow_tunnel_mechanical": False,
            "allow_duty_query": False,
            "allow_roster_import": False,
        },
    )

    assert response.status_code == 410
    assert "已停用" in response.json()["detail"]

def test_feature_channel_legacy_enabled_switch_does_not_disable_wechat_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    response = client.post(
        "/api/feature-channel-config",
        json={
            "enabled": False,
            "wechat_group_room_id": "wgr_feature",
            "allow_tunnel_mechanical": True,
            "allow_duty_query": True,
            "allow_roster_import": True,
        },
    )

    assert response.status_code == 410
    assert "已停用" in response.json()["detail"]

def test_feature_channel_test_uses_notification_room_when_channels_differ(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post("/api/feature-channel-config/test")

    assert response.status_code == 410
    assert "已停用" in response.json()["detail"]

def test_wechat_interaction_config_controls_triggers_and_templates(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 8, 4))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    save_response = client.post(
        "/api/wechat-interaction-config",
        json={
            "patrol_record_triggers": ["记录模板"],
            "patrol_record_template": "查询张三巡查记录 2026-07-01至2026-07-31",
            "tunnel_template_triggers": ["录入模板"],
            "tunnel_template": "自定义录入 日期{date} 负责人罗富耀 记录人张三 天气晴",
            "tunnel_modify_template_triggers": ["修改入口"],
            "tunnel_modify_template": "自定义修改 日期{date} 负责人罗富耀 记录人张三 天气晴 修改日期为{date}",
        },
    )
    template_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "记录模板", "stable_room_id": "wgr_any"},
    )
    old_trigger_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "巡查记录", "stable_room_id": "wgr_any"},
    )
    tunnel_template_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "录入模板", "stable_room_id": "wgr_any"},
    )

    assert save_response.status_code == 200
    config = save_response.json()["config"]
    assert config["patrol_record_triggers"] == ["记录模板"]
    assert config["patrol_record_template"] == "查询商邱宏巡查记录 2026-07-01至2026-07-31"
    assert config["defaults"]["patrol_record_template"] == "查询商邱宏巡查记录 2026-07-01至2026-07-31"
    assert config["tunnel_template"] == "自定义录入 日期{date} 负责人罗富耀 记录人张三 天气晴"
    assert config["tunnel_modify_template"] == "自定义修改 日期{date} 负责人罗富耀 记录人张三 天气晴 修改日期为{date}"
    assert "监控查询菜单" in config["menu_preview"]
    assert template_response.status_code == 200
    assert template_response.json()["reply"] == "查询商邱宏巡查记录 2026-07-01至2026-07-31"
    assert old_trigger_response.status_code == 200
    assert old_trigger_response.json()["query_type"] == "patrol_record"
    assert "没有识别到姓名" in old_trigger_response.json()["reply"]
    assert tunnel_template_response.status_code == 200
    assert tunnel_template_response.json()["query_type"] == "tunnel_mechanical_template"
    assert tunnel_template_response.json()["reply"] == "自定义录入 日期2026-08-04 负责人罗富耀 记录人张三 天气晴"

    test_response = client.post("/api/wechat-interaction-config/test")
    assert test_response.status_code == 200
    test_body = test_response.json()
    assert test_body["success"] is True
    assert len(test_body["results"]) == 3
    assert "修改入口" in test_body["summary"]
    assert "自定义修改 日期2026-08-04" in test_body["summary"]

    legacy_save_response = client.post(
        "/api/wechat-interaction-config",
        json={
            "patrol_record_triggers": ["巡查记录"],
            "patrol_record_template": "查询张三巡查记录 2026-07-01至2026-07-31",
            "tunnel_template_triggers": ["模板"],
            "tunnel_template": "隧道机电录入 日期{date} 负责人罗富耀 记录人张三 天气晴",
            "tunnel_modify_template_triggers": ["修改模板"],
            "tunnel_modify_template": "隧道机电修改 日期{date} 负责人罗富耀 记录人张三 天气晴 修改日期为{date}",
        },
    )
    legacy_config = legacy_save_response.json()["config"]
    assert legacy_config["patrol_record_template"] == "查询商邱宏巡查记录 2026-07-01至2026-07-31"
    assert legacy_config["tunnel_template"] == "隧道机电录入 日期{date} 负责人罗富耀 记录人商邱宏 天气晴"
    assert legacy_config["tunnel_modify_template"] == "隧道机电修改 日期{date} 负责人罗富耀 记录人商邱宏 天气晴 修改日期为{date}"
    assert any(item["query_type"] == "tunnel_modify_template" for item in test_body["results"])

    logs_response = client.get("/api/wechat-interaction-logs?limit=5")
    assert logs_response.status_code == 200
    logs = logs_response.json()["logs"]
    assert logs
    assert logs[0]["command_text"] in {"记录模板", "巡查记录", "模板", "修改模板", "修改入口"}
    assert "reply_preview" in logs[0]


def test_wechat_interaction_config_simulate_reuses_query_chain_and_logs(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_url": "https://lightagent.test/api/push/send",
            "lightagent_token": "push-token",
            "lightagent_targets": [{"id": "room-1", "name": "功能群"}],
        },
    )

    captured: dict[str, object] = {}

    async def fake_build_wechat_query_response(repo_arg, query, *, uploads):
        captured["text"] = query.text
        captured["room_id"] = query.room_id
        captured["stable_room_id"] = query.stable_room_id
        captured["room_name"] = query.room_name
        captured["sender_id"] = query.sender_id
        captured["runtime_sender_id"] = query.runtime_sender_id
        captured["stable_member_id"] = query.stable_member_id
        captured["sender_name"] = query.sender_name
        captured["target_date"] = query.target_date.isoformat() if query.target_date else ""
        return {
            "success": True,
            "query_type": "patrol_record",
            "reply": "查询完成",
            "replies": ["查询完成"],
            "image_url": "/api/uploads/patrol-record-test.png",
        }

    monkeypatch.setattr(main_module, "_build_wechat_query_response", fake_build_wechat_query_response)

    response = client.post(
        "/api/wechat-interaction-config/simulate",
        json={
            "text": "查询商邱宏巡查记录 2026-07-01至2026-07-31",
            "room_id": "room-1",
            "stable_room_id": "room-1",
            "room_name": "功能群",
            "sender_id": "@member-1",
            "runtime_sender_id": "@member-1",
            "stable_member_id": "wgm-1",
            "sender_name": "张三",
            "target_date": "2026-08-04",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["result"]["reply"] == "查询完成"
    assert body["result"]["query_type"] == "patrol_record"
    assert body["image_url"] == "/api/uploads/patrol-record-test.png"
    assert body["image_exists"] is False
    assert body["image_full_url"] == "/api/uploads/patrol-record-test.png"
    assert captured == {
        "text": "查询商邱宏巡查记录 2026-07-01至2026-07-31",
        "room_id": "room-1",
        "stable_room_id": "room-1",
        "room_name": "功能群",
        "sender_id": "@member-1",
        "runtime_sender_id": "@member-1",
        "stable_member_id": "wgm-1",
        "sender_name": "张三",
        "target_date": "2026-08-04",
    }
    logs = client.get("/api/wechat-interaction-logs?limit=1").json()["logs"]
    assert logs and logs[0]["command_text"] == "查询商邱宏巡查记录 2026-07-01至2026-07-31"
    assert logs[0]["room_id"] == "room-1"
    assert logs[0]["room_name"] == "功能群"
    assert logs[0]["sender_id"] == "@member-1"


def test_wechat_interaction_simulate_ignores_non_command_custom_reminder_text(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [{"id": "room-1", "name": "功能群"}],
        },
    )

    response = client.post(
        "/api/wechat-interaction-config/simulate",
        json={
            "text": "@罗富耀\n需要开启隧道灯",
            "room_id": "room-1",
            "stable_room_id": "room-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ignored"] is True
    assert body["query_type"] == "ignored"
    assert body["reply"] == ""
    assert client.get("/api/wechat-interaction-logs?limit=1").json()["logs"] == []


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
    assert "发送“巡查记录”可获取巡查记录查询模板" in body["reply"]
    assert "查询商邱宏巡查记录 2026-07-01至2026-07-31" in body["reply"]
    assert "回复序号即可执行" in body["reply"]
    assert "录入格式：" not in body["reply"]
    assert "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴" not in body["reply"]
    assert body["replies"] == [
        body["reply"],
        "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴",
    ]
    assert body["template"] == "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人商邱宏 天气晴"


def test_wechat_query_ignores_non_command_at_custom_reminder_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "@罗富耀\n需要开启隧道灯", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "success": False,
        "query_type": "ignored",
        "reply": "",
        "replies": [],
        "ignored": True,
    }
    assert client.get("/api/wechat-interaction-logs?limit=1").json()["logs"] == []


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

    direct_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "8", "runtime_sender_id": "@number-menu-member"},
    )
    assert direct_response.status_code == 200
    assert direct_response.json()["query_type"] == "ignored"

    menu_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询", "runtime_sender_id": "@number-menu-member"},
    )
    assert menu_response.status_code == 200
    assert menu_response.json()["query_type"] == "help"

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "8", "runtime_sender_id": "@number-menu-member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_result"
    assert body["checkTime"] == "2026-07-23"

    repeated_response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "8", "runtime_sender_id": "@number-menu-member"},
    )
    assert repeated_response.status_code == 200
    assert repeated_response.json()["query_type"] == "ignored"


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


def test_wechat_query_template_shortcut_returns_tunnel_mechanical_template(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 25))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "模板"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_template"
    assert body["reply"] == "隧道机电录入 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴"
    assert body["replies"] == ["隧道机电录入 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴"]
    assert body["template"] == "隧道机电录入 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴"


def test_wechat_query_modify_shortcut_returns_tunnel_mechanical_modify_template(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 25))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    for text in ("修改", "修改模板", "改模板"):
        response = client.post(
            "/api/wechat-query",
            headers={"X-Duty-Query-Token": "unit-token"},
            json={"text": text},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["query_type"] == "tunnel_mechanical_modify_template"
        assert body["reply"] == "隧道机电修改 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴 修改日期为2026-07-25"
        assert body["replies"] == ["隧道机电修改 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴 修改日期为2026-07-25"]
        assert body["template"] == "隧道机电修改 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴 修改日期为2026-07-25"


def test_wechat_query_tunnel_mechanical_shortcut_returns_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 7, 25))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "机电"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_template"
    assert "发送“模板”获取可复制录入模板" in body["reply"]
    assert "修改记录" in body["reply"]
    assert body["replies"][1] == "隧道机电录入 日期2026-07-25 负责人罗富耀 记录人商邱宏 天气晴"


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


def test_wechat_query_patrol_record_template_and_date_range_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["张三"]})
    client.post(
        "/api/patrol-warning-config",
        json={
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/mobile/warninginfo/findPage",
            "username": "station-user",
            "password": "secret",
            "route_code": "S41",
        },
    )
    captured: dict[str, object] = {}

    async def fake_fetch(config, tz, *, name, token, token_expires_at, limit, cache_path=None, known_names=None):
        captured.update({"name": name, "limit": limit, "cache_path": cache_path})
        return SimpleNamespace(
            token="new-token",
            token_expires_at="2026-08-04T22:00:00+08:00",
            records=[
                {"id": "r-1", "start_time": "2026-07-01T08:01:00+08:00", "end_time": "2026-07-01T09:29:00+08:00", "direction": "上行", "responsible_person": "张三", "recorder": "王德刚"},
                {"id": "r-2", "start_time": "2026-07-01T09:30:00+08:00", "end_time": "2026-07-01T10:02:00+08:00", "direction": "下行", "responsible_person": "罗越", "recorder": "张三"},
                {"id": "r-3", "start_time": "2026-08-01T09:00:00+08:00", "end_time": "2026-08-01T10:00:00+08:00", "direction": "双向", "responsible_person": "张三", "recorder": "王德刚"},
            ],
            stats={"matched_rows": 3},
        )

    monkeypatch.setattr(main_module, "fetch_patrol_records_by_name_result", fake_fetch)
    template_response = client.post("/api/wechat-query", headers={"X-Duty-Query-Token": "unit-token"}, json={"text": "巡查记录"})
    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询张三巡查记录 2026-07-01至2026-07-31"},
    )

    assert template_response.status_code == 200
    assert template_response.json()["query_type"] == "patrol_record_template"
    assert template_response.json()["reply"] == "查询商邱宏巡查记录 2026-07-01至2026-07-31"
    assert "巡查记录查询格式" not in template_response.json()["reply"]
    assert "示例：" not in template_response.json()["reply"]
    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "patrol_record"
    assert body["count"] == 2
    assert "实际次数 1 次" in body["reply"]
    assert body["start_date"] == "2026-07-01"
    assert body["end_date"] == "2026-07-31"
    assert body["image_url"].startswith("/api/uploads/patrol-record-")
    assert (tmp_path / "uploads" / body["image_url"].rsplit("/", 1)[-1]).read_bytes().startswith(b"\x89PNG")
    assert captured["name"] == "张三"
    assert captured["limit"] == 5000


def test_wechat_bridge_sends_patrol_record_image(tmp_path, monkeypatch):
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    image_path = uploads / "patrol-record-test.png"
    image_path.write_bytes(b"\x89PNG\r\n")
    sent_text: list[str] = []
    sent_image: list[str] = []

    async def fake_build(repo_arg, query, *, uploads):
        return {"success": True, "replies": ["查询完成"], "image_url": "/api/uploads/patrol-record-test.png"}

    class DummyManager:
        def send_text(self, room_id, text, *, mention_ids=None):
            sent_text.append(text)

        def send_image(self, room_id, path):
            sent_image.append(path)

    monkeypatch.setattr(main_module, "_build_wechat_query_response", fake_build)
    monkeypatch.setattr(main_module, "get_wechat_bridge_manager", lambda: DummyManager())
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {"room_id": "room@@runtime", "stable_room_id": "wgr_feature", "text": "@闷葫芦 查询张三巡查记录 2026-07-01至2026-07-31", "is_at": True},
    )
    assert sent_text == ["查询完成"]
    assert sent_image == [str(image_path)]


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
            "text": "@闷葫芦 绑定张三",
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
    main_module._handle_wechat_bridge_message(
        repo,
        uploads,
        {
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "sender_id": "wgm_member",
            "runtime_sender_id": "@member",
            "text": "@罗富耀\n需要开启隧道灯",
            "is_at": True,
        },
    )

    assert calls == [
        "@闷葫芦 查询今日机电",
        "@闷葫芦 绑定张三",
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
                "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人张三 天气晴",
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
        ("wgr_feature", "隧道机电录入 日期2026-07-24 负责人罗富耀 记录人张三 天气晴"),
    ]


def test_wechat_bridge_bind_command_replies_success(tmp_path, monkeypatch):
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_personnel_names(["张三"])
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
            "sender_name": "张三微信",
            "text": "@闷葫芦 绑定张三",
            "is_at": True,
        },
    )

    assert sent and sent[0][0] == "wgr_feature"
    assert "绑定成功：张三" in sent[0][1]
    bound = next(person for person in repo.list_personnel() if person["name"] == "张三")
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


def test_wechat_query_triggers_tunnel_mechanical_modify(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    _import_tunnel_template(client)
    captured = {}

    async def fake_modify(repo, request, **kwargs):
        captured["checkTime"] = request.checkTime.isoformat()
        captured["checkerId"] = request.checkerId
        captured["checker"] = request.checker
        captured["recorderId"] = request.recorderId
        captured["recorder"] = request.recorder
        captured["weather"] = request.weather
        captured["newCheckTime"] = request.newCheckTime.isoformat() if request.newCheckTime else ""
        captured["newWeather"] = request.newWeather
        captured["newCheckerId"] = request.newCheckerId
        captured["newChecker"] = request.newChecker
        captured["newRecorderId"] = request.newRecorderId
        captured["newRecorder"] = request.newRecorder
        captured["dry_run"] = request.dry_run
        return {"success": True, "dry_run": request.dry_run, "count": 1, "results": []}

    monkeypatch.setattr(main_module, "_modify_tunnel_mechanical", fake_modify)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "隧道机电修改 日期2026-07-26 负责人张三 记录人李四 天气晴 修改日期为2026-07-25 修改天气为多云 负责人改为李四 记录人改为张三"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "tunnel_mechanical_modify"
    assert body["checkTime"] == "2026-07-26"
    assert body["finalCheckTime"] == "2026-07-25"
    assert body["count"] == 1
    assert captured == {
        "checkTime": "2026-07-26",
        "checkerId": "1001",
        "checker": "张三",
        "recorderId": "1002",
        "recorder": "李四",
        "weather": "晴",
        "newCheckTime": "2026-07-25",
        "newWeather": "多云",
        "newCheckerId": "1002",
        "newChecker": "李四",
        "newRecorderId": "1001",
        "newRecorder": "张三",
        "dry_run": False,
    }
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "tunnel_mechanical_wechat_modify"
    assert records[0]["status"] == "success"


def test_tunnel_mechanical_modify_queries_then_posts_edit_payload(tmp_path, monkeypatch):
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
    _import_tunnel_template(
        client,
        {
            **TEST_TUNNEL_TEMPLATE,
            "base_url": "https://example.test",
            "list_path": "/prod-api/patrol/deviceCheck/list",
            "update_path": "/prod-api/patrol/deviceCheck/edit",
        },
    )
    captured = {"gets": [], "posts": []}

    class FakeResponse:
        def __init__(self, body, status_code=200):
            self._body = body
            self.status_code = status_code
            self.text = str(body)

        def json(self):
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None, params=None):
            captured["gets"].append({"url": url, "headers": headers or {}, "params": params or {}})
            if url == "https://example.test/prod-api/patrol/deviceCheck/get/check-1":
                return FakeResponse(
                    {
                        "code": 200,
                        "data": {
                            "id": "parent-check-1",
                            "assetName": "example tunnel",
                            "assetCode": "ASSET001",
                            "checkTime": "2026-07-26",
                            "weather": "sunny",
                            "checkerId": "1001",
                            "checker": "checker",
                            "recorderId": "1002",
                            "recorder": "recorder",
                            "assetIds": None,
                            "faultRecordList": None,
                            "domains": [
                                {
                                    "id": "check-1",
                                    "checkId": "parent-check-1",
                                    "devName": "device",
                                    "location": "K1+000-K2+000",
                                    "content": "daily check",
                                    "result": 1,
                                    "checkTime": None,
                                    "weather": None,
                                    "checkerId": None,
                                    "checker": None,
                                    "recorderId": None,
                                    "recorder": None,
                                }
                            ],
                        },
                    }
                )
            return FakeResponse(
                {
                    "code": 200,
                    "rows": [
                        {
                            "id": "check-1",
                            "assetName": "示例隧道上行",
                            "assetCode": "ASSET001",
                            "checkTime": "2026-07-26",
                            "weather": "晴",
                            "checkerId": "1001",
                            "checker": "张三",
                            "recorderId": "1002",
                            "recorder": "李四",
                            "domains": [
                                {
                                    "checkId": "check-1",
                                    "devName": "示例设备",
                                    "location": "K1+000-K2+000示例隧道",
                                    "content": "示例检查",
                                    "result": 1,
                                }
                            ],
                        }
                    ],
                }
            )

        async def post(self, url, headers=None, json=None):
            captured["posts"].append({"url": url, "headers": headers or {}, "payload": json})
            return FakeResponse({"code": 200, "msg": "ok"})

        async def put(self, url, headers=None, json=None):
            raise AssertionError("POST edit should succeed before PUT fallback")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)

    request = main_module.TunnelMechanicalModifyRequest(
        base_url="https://example.test",
        checkTime=date(2026, 7, 26),
        weather="晴",
        checkerId="1001",
        checker="张三",
        recorderId="1002",
        recorder="李四",
        newCheckTime=date(2026, 7, 25),
        newWeather="多云",
        newCheckerId="1002",
        newChecker="李四",
        newRecorderId="1001",
        newRecorder="张三",
    )
    result = asyncio.run(main_module._modify_tunnel_mechanical(repo, request))

    assert result["success"] is True
    assert result["count"] == 1
    assert captured["gets"][0]["url"] == "https://example.test/prod-api/patrol/deviceCheck/list"
    assert captured["gets"][0]["headers"]["Authorization"] == "Bearer cached-token"
    assert captured["gets"][0]["headers"]["Cookie"] == "sid=abc"
    assert captured["gets"][0]["params"]["checkTime"] == "2026-07-26"
    assert captured["gets"][1]["url"] == "https://example.test/prod-api/patrol/deviceCheck/get/check-1"
    assert captured["posts"][0]["url"] == "https://example.test/prod-api/patrol/deviceCheck/edit"
    payload = captured["posts"][0]["payload"]
    assert payload["id"] == "parent-check-1"
    assert payload["checkTime"] == "2026-07-25"
    assert payload["weather"] == "多云"
    assert payload["checkerId"] == "1002"
    assert payload["checker"] == "李四"
    assert payload["recorderId"] == "1001"
    assert payload["recorder"] == "张三"
    assert payload["domains"][0]["checkId"] == "parent-check-1"
    assert payload["domains"][0]["checkTime"] is None


def test_tunnel_mechanical_modify_retries_put_when_post_not_supported(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, body, status_code=200):
            self._body = body
            self.status_code = status_code
            self.text = str(body)

        def json(self):
            return self._body

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            calls.append(("POST", url, json))
            return FakeResponse({"code": 500, "msg": "Request method 'POST' not supported"})

        async def put(self, url, headers=None, json=None):
            calls.append(("PUT", url, json))
            return FakeResponse({"code": 200, "msg": "ok"})

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeAsyncClient)

    results = asyncio.run(
        main_module._post_tunnel_mechanical_updates(
            [{"recordId": "check-1", "assetName": "示例隧道", "payload": {"id": "check-1"}}],
            base_url="https://example.test",
            headers={"Authorization": "Bearer token"},
            update_paths=["/prod-api/patrol/deviceCheck/edit"],
        )
    )

    assert results[0]["ok"] is True
    assert results[0]["method"] == "PUT"
    assert calls == [
        ("POST", "https://example.test/prod-api/patrol/deviceCheck/edit", {"id": "check-1"}),
        ("PUT", "https://example.test/prod-api/patrol/deviceCheck/edit", {"id": "check-1"}),
    ]


def test_tunnel_mechanical_modify_builds_domains_from_flat_record():
    request = main_module.TunnelMechanicalModifyRequest(
        checkTime=date(2026, 7, 26),
        weather="sunny",
        checkerId="1001",
        checker="checker",
        recorderId="1002",
        recorder="recorder",
        newCheckTime=date(2026, 7, 25),
    )

    payload = main_module._build_tunnel_mechanical_update_payload(
        request,
        {
            "id": "check-1",
            "assetId": "asset-1",
            "assetName": "tunnel",
            "devName": "device",
            "location": "K1+000-K2+000",
            "content": "daily check",
            "result": 1,
            "describe": None,
            "measures": None,
            "picPaths": None,
            "carLicense": "plate",
            "nums": 1,
            "checkTime": "2026-07-26",
            "weather": "sunny",
            "checkerId": "1001",
            "checker": "checker",
            "recorderId": "1002",
            "recorder": "recorder",
        },
    )

    assert payload["checkTime"] == "2026-07-25"
    assert payload["assetIds"] == []
    assert payload["faultRecordList"] == []
    assert payload["domains"] == [
        {
            "checkId": "check-1",
            "devName": "device",
            "location": "K1+000-K2+000",
            "content": "daily check",
            "result": 1,
            "describe": None,
            "measures": None,
            "picPaths": None,
            "carLicense": "plate",
            "nums": 1,
        }
    ]


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


def test_wechat_today_reminder_summary_does_not_repeat_same_time_monitor_reminder(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["Alice"]})
    client.post("/api/people", json={"name": "Alice", "daily_time": "07:50", "before_shift_minutes": 10, "enabled": True})
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "grid": [{"name": "Alice", "days": {"16": "晚", "17": "早"}}],
        },
    )

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询今日提醒", "runtime_sender_id": "@missing-member", "target_date": "2025-09-16"},
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "07:50每日提醒、15:50班前提醒、23:50班前提醒" in reply
    assert "07:50每日提醒、07:50每日提醒" not in reply


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
            "names": ["张三"],
            "people": [
                {
                    "name": "张三",
                    "wechat_group_member_id": "wgm_stable_member",
                    "wechat_group_member_name": "张三微信",
                }
            ],
        },
    )
    client.post("/api/people", json={"name": "张三", "daily_time": "07:40", "before_shift_minutes": 10, "enabled": True})

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询我的绑定", "sender_id": "wgm_stable_member"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "binding"
    assert body["person_name"] == "张三"


def test_wechat_binding_query_is_not_treated_as_bind_command(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/personnel",
        json={
            "names": ["张三"],
            "people": [
                {
                    "name": "张三",
                    "wechat_group_runtime_sender_id": "@runtime-member",
                    "wechat_group_member_name": "张三微信",
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
    assert body["person_name"] == "张三"


def test_wechat_query_can_bind_current_sender_to_person_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post("/api/personnel", json={"names": ["旧人员", "张三"]})
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
            "text": "绑定张三",
            "room_id": "room@@runtime",
            "stable_room_id": "wgr_feature",
            "room_name": "功能群",
            "sender_id": "wgm_stable_member",
            "stable_member_id": "wgm_stable_member",
            "runtime_sender_id": "@runtime-member",
            "sender_name": "张三微信",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "binding_update"
    assert body["person_name"] == "张三"
    people = DutyRepository(tmp_path / "data" / "duty-reminder.db").list_personnel()
    assert next(person for person in people if person["name"] == "旧人员") == {"name": "旧人员", "mention_mobile": ""}
    bound = next(person for person in people if person["name"] == "张三")
    assert bound["wechat_group_room_id"] == "wgr_feature"
    assert bound["wechat_group_room_name"] == "功能群"
    assert bound["wechat_group_member_id"] == "wgm_stable_member"
    assert bound["wechat_group_runtime_sender_id"] == "@runtime-member"
    assert bound["wechat_group_member_name"] == "张三微信"


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
    assert "2025-09-15" in body["reply"]
    assert "请注意" in body["reply"]
    assert body["image_url"].startswith("/api/uploads/wechat-query-")
    image_response = client.get(body["image_url"])
    assert image_response.status_code == 200
    assert image_response.content.startswith(b"\x89PNG")


def test_wechat_query_template_remains_text_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "模板", "runtime_sender_id": "@member-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "tunnel_mechanical_template"
    assert "image_url" not in body


def test_wechat_query_today_duty_returns_daily_duty_image(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2025, 9, 16))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={"text": "查询今日在岗"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["query_type"] == "daily_duty_query"
    assert body["target_date"] == "2025-09-16"
    assert body["image_url"].startswith("/api/uploads/daily-duty-query-")
    image_response = client.get(body["image_url"])
    assert image_response.status_code == 200
    assert image_response.content.startswith(b"\x89PNG")


def test_wechat_query_rest_uses_bound_person_and_summarizes_ranges(tmp_path, monkeypatch):
    monkeypatch.setenv("DUTY_REMINDER_QUERY_TOKEN", "unit-token")
    monkeypatch.setattr(main_module, "_today_in_tz", lambda: date(2026, 8, 16))
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_roster_month(
        2026,
        8,
        [
            {"name": "商邱宏", "days": {"11": "休", "12": "休", "13": "休", "25": "休", "26": "休"}},
            {"name": "罗富耀", "days": {"17": "休"}},
        ],
        "",
    )
    repo.upsert_personnel_contacts([{"name": "商邱宏", "wecom_userid": "u-shang"}])

    response = client.post(
        "/api/wechat-query",
        headers={"X-Duty-Query-Token": "unit-token"},
        json={
            "text": "查询休息",
            "channel": "wecom_app",
            "sender_id": "wecom_user:u-shang",
            "runtime_sender_id": "wecom_user:u-shang",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "rest_query"
    assert "商邱宏 本月休息共5天，分2次休息" in body["reply"]
    assert "已经休息3天，本月休息还剩2天" in body["reply"]
    assert "距离第二次休息还剩9天" in body["reply"]
    assert body["image_url"].startswith("/api/uploads/wechat-query-")


def test_vacation_reminder_plans_start_and_end_events(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_roster_month(
        2026,
        8,
        [{"name": "罗富耀", "days": {"16": "", "17": "休", "18": "休"}}],
        "",
    )
    repo.save_vacation_reminder_config(enabled=True, start_reminder_time="07:50", end_reminder_time="07:55")

    start_events = main_module._plan_all_events(repo, date(2026, 8, 16))
    end_events = main_module._plan_all_events(repo, date(2026, 8, 18))

    assert any(event.kind == "vacation_start" and event.person_name == "罗富耀" and event.send_at.strftime("%H:%M") == "07:50" for event in start_events)
    assert any(event.kind == "vacation_end" and event.person_name == "罗富耀" and event.send_at.strftime("%H:%M") == "07:55" for event in end_events)


def test_vacation_reminder_test_uses_template_library_randomly(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeNotificationClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list or []

    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_notification_config(sender_type="wecom_webhook", webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    monkeypatch.setattr(main_module, "_notification_client_from_repo", lambda repo: FakeNotificationClient())
    monkeypatch.setattr(main_module.secrets, "choice", lambda items: items[-1])
    client = TestClient(app)

    response = client.post(
        "/api/vacation-reminder-config/test",
        json={
            "enabled": True,
            "start_reminder_time": "07:50",
            "end_reminder_time": "07:55",
            "start_message_templates": ["第一条", "第二条"],
            "end_message_templates": ["结束第一条", "结束第二条"],
            "send_content_mode": "text",
        },
    )

    assert response.status_code == 200
    assert response.json()["content"] == "第二条"
    assert sent["content"] == "第二条"


def test_wecom_app_vacation_test_rejects_unbound_person_without_record(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.upsert_personnel_names(["刘显坤"])
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id="ww-test",
        wecom_app_agent_id="1000002",
        wecom_app_secret="secret",
        wecom_app_token="token",
        wecom_app_encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    )

    response = client.post("/api/vacation-reminder-config/test", json={"enabled": True})

    assert response.status_code == 400
    assert "刘显坤 还没有绑定企业微信成员" in response.json()["detail"]
    assert client.get("/api/send-records").json()["records"] == []


def test_wecom_app_person_tests_reject_unbound_people_without_record(tmp_path, monkeypatch):
    class FakeWeComAppClient:
        is_wecom_app_notify = True

        async def send_text(self, *args, **kwargs):
            raise AssertionError("未绑定人员不应该发送文字")

        async def send_image(self, *args, **kwargs):
            raise AssertionError("未绑定人员不应该发送图片")

    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    fake_client = FakeWeComAppClient()
    monkeypatch.setattr(main_module, "_notification_client_from_config", lambda config: fake_client)
    monkeypatch.setattr(main_module, "_notification_client_from_repo", lambda repo: fake_client)

    monitor_response = client.post(
        "/api/people/test",
        json={
            "name": "未绑定监控",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )
    custom_response = client.post(
        "/api/custom-reminders/test",
        json={
            "name": "未绑定自定义",
            "shift_code": "night",
            "reminder_time": "21:00",
            "message": "关闭隧道灯",
            "enabled": True,
        },
    )
    notification_response = client.post("/api/notification-config/test", json={"person_name": "未绑定测试"})

    assert monitor_response.status_code == 400
    assert "未绑定监控 还没有绑定企业微信成员" in monitor_response.json()["detail"]
    assert custom_response.status_code == 400
    assert "未绑定自定义 还没有绑定企业微信成员" in custom_response.json()["detail"]
    assert notification_response.status_code == 400
    assert "未绑定测试 还没有绑定企业微信成员" in notification_response.json()["detail"]
    assert client.get("/api/send-records").json()["records"] == []


def test_wecom_app_planned_person_reminders_skip_unbound_people(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.save_notification_config(webhook_url="", wecom_app_enabled=True)
    repo.upsert_personnel_names(["已绑定", "未绑定"])
    repo.upsert_personnel_contacts([{"name": "已绑定", "wecom_userid": "bound-user"}])
    repo.save_daily_duty_config(enabled=True, reminder_time="07:50")
    for name in ["已绑定", "未绑定"]:
        repo.save_monitored_person(
            name=name,
            daily_time="07:40",
            before_shift_minutes=5,
            rest_reminder_enabled=True,
            rest_reminder_time="08:30",
            enabled=True,
        )
        repo.save_custom_reminder(
            name=name,
            shift_code="middle",
            reminder_time="08:00",
            message="自定义提醒",
            enabled=True,
        )
    repo.save_vacation_reminder_config(enabled=True, start_reminder_time="07:50", end_reminder_time="07:55")
    repo.save_roster_month(
        2026,
        8,
        [
            {"name": "已绑定", "days": {"16": "中", "17": "休", "18": "中"}},
            {"name": "未绑定", "days": {"16": "中", "17": "休", "18": "中"}},
        ],
        "",
    )

    events = main_module._plan_all_events(repo, date(2026, 8, 16))
    rest_day_events = main_module._plan_all_events(repo, date(2026, 8, 17))

    person_events = [event for event in events if event.person_name in {"已绑定", "未绑定"}]
    assert {event.kind for event in person_events if event.person_name == "已绑定"} >= {"daily", "before_shift", "custom", "vacation_start"}
    assert not any(event.person_name == "未绑定" for event in person_events)
    assert any(event.kind == "daily_duty" for event in events)
    rest_person_events = [event for event in rest_day_events if event.person_name in {"已绑定", "未绑定"}]
    assert {event.kind for event in rest_person_events if event.person_name == "已绑定"} >= {"rest", "vacation_end"}
    assert not any(event.person_name == "未绑定" for event in rest_person_events)


def test_wecom_app_public_notification_targets_can_be_limited_to_selected_bound_people(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.upsert_personnel_contacts([
        {"name": "罗熙云", "wecom_userid": "luoxiyun"},
        {"name": "罗富耀", "wecom_userid": "luofuyao"},
        {"name": "未绑定"},
    ])
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id="ww-test",
        wecom_app_agent_id="1000002",
        wecom_app_secret="secret",
        wecom_app_token="token",
        wecom_app_encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        wecom_app_target_names=["罗熙云", "未绑定"],
    )

    config = client.get("/api/notification-config").json()["config"]

    assert config["wecom_app_target_names"] == ["罗熙云", "未绑定"]
    assert main_module._wecom_app_default_tousers(repo) == ["luoxiyun"]


def test_wecom_app_public_notification_targets_can_be_split_by_function(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    repo: DutyRepository = app.state.repo
    repo.upsert_personnel_contacts([
        {"name": "罗熙云", "wecom_userid": "luoxiyun"},
        {"name": "罗富耀", "wecom_userid": "luofuyao"},
        {"name": "商邱宏", "wecom_userid": "shangqiuhong"},
    ])
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id="ww-test",
        wecom_app_agent_id="1000002",
        wecom_app_secret="secret",
        wecom_app_token="token",
        wecom_app_encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        wecom_app_target_names=["罗熙云"],
        wecom_app_function_target_names={
            "daily_duty": ["罗富耀"],
            "patrol_warning": ["商邱宏", "未绑定"],
        },
    )

    daily_event = main_module.ReminderEvent(kind="daily_duty", person_name="今日在岗人员", send_at=datetime.now(), content="")
    patrol_event = main_module.ReminderEvent(kind="patrol_warning_start", person_name="", send_at=datetime.now(), content="")
    custom_event = main_module.ReminderEvent(kind="other_public", person_name="", send_at=datetime.now(), content="")

    assert main_module._notification_target_ids_for_event(repo, main_module.WeComAppNotifyClient.__new__(main_module.WeComAppNotifyClient), daily_event) == ["luofuyao"]
    assert main_module._notification_target_ids_for_event(repo, main_module.WeComAppNotifyClient.__new__(main_module.WeComAppNotifyClient), patrol_event) == ["shangqiuhong"]
    assert main_module._notification_target_ids_for_event(repo, main_module.WeComAppNotifyClient.__new__(main_module.WeComAppNotifyClient), custom_event) is None


def test_people_center_summarizes_bindings_and_reminders(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.upsert_personnel_contacts([
        {
            "name": "商邱宏",
            "wecom_userid": "shangqiuhong",
            "wechat_group_member_name": "商邱宏微信",
            "mention_mobile": "10000000000",
            "tunnel_mechanical_partner": "罗富耀",
        }
    ])
    repo.save_monitored_person(name="商邱宏", daily_time="07:50", before_shift_minutes=10, rest_reminder_enabled=True)
    repo.save_custom_reminder(name="商邱宏", shift_code="middle", reminder_time="07:50", message="消毒")

    body = client.get("/api/people-center").json()
    person = next(item for item in body["people"] if item["name"] == "商邱宏")

    assert person["wecom_bound"] is True
    assert person["wechat_group_bound"] is True
    assert person["monitor_enabled"] is True
    assert person["custom_reminder_count"] == 1
    assert person["tunnel_mechanical_partner"] == "罗富耀"


def test_personnel_delete_removes_related_config_and_hides_roster_name(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.upsert_personnel_contacts([
        {
            "name": "张三",
            "wecom_userid": "zs-user",
            "mention_mobile": "13800000000",
        }
    ])
    repo.save_monitored_person(name="张三", wecom_userid="zs-user", daily_time="07:50")
    repo.save_custom_reminder(name="张三", shift_code="early", reminder_time="07:50", message="开启隧道灯")
    repo.save_roster_month(2026, 8, [{"name": "张三", "days": {"1": "早"}}], "uploads/roster.png")

    delete_response = client.delete("/api/personnel/%E5%BC%A0%E4%B8%89")
    assert delete_response.status_code == 200
    assert "张三" not in client.get("/api/personnel").json()["names"]
    assert all(person["name"] != "张三" for person in client.get("/api/people").json()["people"])
    assert all(item["name"] != "张三" for item in client.get("/api/custom-reminders").json()["reminders"])
    assert all(item["name"] != "张三" for item in client.get("/api/people-center").json()["people"])

    client.post("/api/personnel", json={"names": ["张三"], "people": [{"name": "张三", "wecom_userid": "zs-user"}]})
    assert "张三" in client.get("/api/personnel").json()["names"]
    assert any(person["name"] == "张三" for person in client.get("/api/people-center").json()["people"])

    rename_response = client.put("/api/personnel/%E5%BC%A0%E4%B8%89", json={"name": "张三甲"})
    assert rename_response.status_code == 200
    assert "张三甲" in client.get("/api/personnel").json()["names"]
    assert all(person["name"] != "张三" for person in client.get("/api/people-center").json()["people"])
    assert any(person["name"] == "张三甲" for person in client.get("/api/people-center").json()["people"])


def test_interaction_commands_catalog_marks_current_menu_items(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_wecom_app_menu_config([
        {"name": "监控在岗", "items": [{"name": "今日在岗", "command": "查询今日在岗"}]},
    ])

    commands = client.get("/api/interaction-commands").json()["commands"]
    today = next(item for item in commands if item["command"] == "查询今日在岗")
    rest = next(item for item in commands if item["command"] == "查询休息")
    construction_image = next(item for item in commands if item["command"] == "施工图片")
    construction_site = next(item for item in commands if item["command"] == "施工点维护")
    orange = next(item for item in commands if item["command"] == "橙色预警巡查记录查询")

    assert today["menu_available"] is True
    assert today["in_current_menu"] is True
    assert rest["feature"] == "休息统计"
    assert construction_image["feature"] == "施工影像 Word"
    assert construction_site["feature"] == "施工点维护"
    assert orange["feature"] == "橙色预警巡查记录"
    assert any(item["bind_required"] for item in commands)


def test_reminder_diagnostics_explains_generated_skipped_and_not_generated(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_notification_config(
        webhook_url="",
        wecom_app_enabled=True,
        wecom_app_corp_id="ww-test",
        wecom_app_agent_id="1000002",
        wecom_app_secret="secret",
        wecom_app_token="token",
        wecom_app_encoding_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    )
    repo.upsert_personnel_contacts([{"name": "已绑定", "wecom_userid": "bound-user"}, {"name": "未绑定"}])
    repo.save_monitored_person(name="已绑定", daily_time="07:50", before_shift_minutes=10, enabled=True)
    repo.save_monitored_person(name="未绑定", daily_time="07:50", before_shift_minutes=10, enabled=True)
    repo.save_custom_reminder(name="未绑定", shift_code="middle", reminder_time="07:50", message="中班事项")
    repo.save_custom_reminder(name="已绑定", shift_code="night", reminder_time="21:00", message="晚班事项")
    repo.save_roster_month(
        2026,
        8,
        [
            {"name": "已绑定", "days": {"16": "中"}},
            {"name": "未绑定", "days": {"16": "中"}},
        ],
        "uploads/month.png",
    )

    body = client.get("/api/reminders/diagnostics?target_date=2026-08-16").json()
    items = body["items"]

    assert any(item["person_name"] == "已绑定" and item["status"] in {"pending", "due"} for item in items)
    assert any(item["person_name"] == "未绑定" and item["status"] == "skipped" for item in items)
    assert any(item["person_name"] == "已绑定" and item["status"] == "not_generated" and "不是晚班" in item["reason"] for item in items)


def test_wecom_app_resend_rejects_unbound_person_without_new_record(tmp_path, monkeypatch):
    class FakeWeComAppClient:
        is_wecom_app_notify = True

        async def send_text(self, *args, **kwargs):
            raise AssertionError("未绑定补发不应该发送文字")

        async def send_image(self, *args, **kwargs):
            raise AssertionError("未绑定补发不应该发送图片")

    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_send_record(
        kind="daily",
        target="未绑定",
        scheduled_at="2026-08-16T07:50:00+08:00",
        status="failed",
        content="补发内容",
    )
    monkeypatch.setattr(main_module, "_notification_client_from_repo", lambda repo: FakeWeComAppClient())
    record_id = client.get("/api/send-records").json()["records"][0]["id"]

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 400
    assert "未绑定 还没有绑定企业微信成员" in response.json()["detail"]
    records = client.get("/api/send-records").json()["records"]
    assert len(records) == 1
    assert records[0]["kind"] == "daily"


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
        json={
            "webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test",
            "mention_mode": "custom",
            "mention_targets": "10000000000",
        },
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


def test_patrol_warning_config_reschedules_countdown_when_interval_changes(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_patrol_warning_config(
        enabled=True,
        poll_interval_minutes=10,
        login_url="https://example.test/login",
        warning_url="https://example.test/warninginfo/findPage",
        username="station-user",
        password="secret",
    )
    repo.save_patrol_warning_state(
        last_checked_at="2026-07-22T08:00:00+08:00",
        next_check_at="2026-07-22T08:12:00+08:00",
        failure_count=2,
        backoff_until="2026-07-22T08:20:00+08:00",
        last_error="HTTP 429",
    )
    monkeypatch.setattr(
        main_module,
        "next_poll_time",
        lambda now, interval_minutes: now + timedelta(minutes=interval_minutes),
    )

    response = client.post(
        "/api/patrol-warning-config",
        json={
            "enabled": True,
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/warninginfo/findPage",
            "username": "station-user",
            "password": "",
            "poll_interval_minutes": 2,
        },
    )

    assert response.status_code == 200
    state = repo.get_patrol_warning_state()
    assert state["next_check_at"]
    assert state["next_check_at"] != "2026-07-22T08:12:00+08:00"
    assert state["backoff_until"] == ""
    assert state["failure_count"] == 0
    assert state["last_error"] == ""

    initial_state = repo.get_patrol_warning_state()
    assert initial_state["next_check_at"]

    disabled_response = client.post(
        "/api/patrol-warning-config",
        json={
            "enabled": False,
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/warninginfo/findPage",
            "username": "station-user",
            "password": "",
            "poll_interval_minutes": 2,
        },
    )

    assert disabled_response.status_code == 200
    state = repo.get_patrol_warning_state()
    assert state["next_check_at"] == ""
    assert state["backoff_until"] == ""


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


def test_patrol_warning_orange_records_query_uses_saved_config_and_token_cache(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_patrol_records_by_name_result(config, tz, *, name, token, token_expires_at, limit, cache_path=None, known_names=None):
        captured.update(
            {
                "config": config,
                "name": name,
                "token": token,
                "token_expires_at": token_expires_at,
                "limit": limit,
                "cache_path": cache_path,
            }
        )
        return SimpleNamespace(
            token="new-token",
            token_expires_at="2026-07-22T22:00:00+08:00",
            records=[
                {
                    "id": "record-1",
                    "route_code": "S41",
                    "route_name": "南涧－宁洱",
                    "responsible_person": "张三",
                    "recorder": "陈刚",
                }
            ],
            stats={"total_rows": 1, "loaded_rows": 1, "route_matched_rows": 1, "matched_rows": 1},
        )

    monkeypatch.setattr(main_module, "fetch_patrol_records_by_name_result", fake_fetch_patrol_records_by_name_result)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/patrol-warning-config",
        json={
            "login_url": "https://example.test/login",
            "warning_url": "https://example.test/mobile/warninginfo/findPage",
            "username": "station-user",
            "password": "secret",
            "route_code": "S41",
        },
    )
    app.state.repo.save_patrol_warning_state(token="cached-token", token_expires_at="2026-07-22T21:00:00+08:00")

    response = client.get("/api/patrol-warning/orange-records", params={"name": "张三", "limit": 1000})

    assert response.status_code == 200
    body = response.json()
    assert body["records"][0]["responsible_person"] == "张三"
    assert body["stats"]["matched_rows"] == 1
    assert captured["config"]["password"] == "secret"
    assert captured["name"] == "张三"
    assert captured["token"] == "cached-token"
    assert captured["limit"] == 1000
    assert str(captured["cache_path"]).endswith("patrol-warning-records-cache.json")
    assert client.get("/api/patrol-warning-config").json()["state"]["token_configured"] is True

    captured.clear()
    response = client.get("/api/patrol-warning/orange-records", params={"name": "张三"})

    assert response.status_code == 200
    assert captured["limit"] == 5000


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


def test_patrol_warning_monitor_still_sends_first_end_reminder_when_followup_disabled(tmp_path, monkeypatch):
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

    assert any(item[0] == "text" for item in sent)
    assert any(item[0] == "image" for item in sent)
    assert repo.get_patrol_warning_state()["last_end_reminder_slot"] == "2026-07-22T02:00:00+08:00"
    records = repo.list_send_records()
    assert len(records) == 1
    assert records[0]["kind"] == "patrol_warning_end"


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
    repo.save_notification_config(
        webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test",
        mention_mode="custom",
        mention_targets="13800138000, 13900139000",
    )
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
            "mention_mode": "custom",
            "mention_targets": "10000000000",
            "message_template": "{name} {date}（{time_range})是你的{shift_label}",
        },
    )

    response = client.post("/api/notification-config/test", json={"person_name": "示例甲"})

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
        json={
            "webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test",
            "mention_mode": "custom",
            "mention_targets": "10000000000",
        },
    )

    response = client.post("/api/notification-config/test", json={"person_name": "示例甲"})

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

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

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
        json={"person_name": "王路飞"},
    )

    assert response.status_code == 200
    assert sent["mentions"] == []
    assert sent["content"].startswith("@王路飞\n")
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "notification_test"
    assert records[0]["target"] == "王路飞"


def test_monitor_person_test_sends_current_form_with_wechat_member(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

    monkeypatch.setattr("app.main._notification_client_from_config", lambda config: FakeWechatClient())
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [{"id": "wgr_notice", "name": "notice"}],
        },
    )

    response = client.post(
        "/api/people/test",
        json={
            "name": "Alice",
            "mention_mobile": "",
            "wechat_group_member_id": "wgm_stable_member",
            "wechat_group_runtime_sender_id": "@runtime-member",
            "daily_time": "07:50",
            "before_shift_minutes": 10,
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["content"] == "@Alice今天是你的中班"
    assert sent["mentions"] == []
    assert sent["content"] == "@Alice今天是你的中班"
    assert isinstance(sent["image_bytes"], bytes)
    assert sent["image_bytes"].startswith(b"\x89PNG")
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "monitor_test"
    assert records[0]["target"] == "Alice"
    assert records[0]["status"] == "success"


def test_custom_reminder_test_sends_current_form_to_webhook(tmp_path, monkeypatch):
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
            "mention_mode": "custom",
            "mention_targets": "10000000000",
        },
    )

    response = client.post(
        "/api/custom-reminders/test",
        json={
            "name": "Bob",
            "mention_mobile": "10000000000",
            "shift_code": "night",
            "reminder_time": "21:00",
            "message": "请处理 {name} {shift_label} {reminder_time}",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert sent == {
        "webhook_url": "https://example.test/cgi-bin/webhook/send?key=unit-test",
        "content": "请处理 Bob 夜班 21:00",
        "mobiles": ["10000000000"],
    }
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "custom_test"
    assert records[0]["target"] == "Bob"
    assert records[0]["status"] == "success"


def test_personal_wechat_patrol_warning_uses_true_all_mention(tmp_path):
    class FakeWechatClient:
        is_wechat_bridge = True

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="", mention_mode="all")
    repo.save_patrol_warning_config(mention_all=True)

    mentions = main_module._patrol_warning_mentions_for_client(
        repo,
        repo.get_patrol_warning_config(),
        FakeWechatClient(),
    )

    assert mentions == []


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

    assert record["target"] == "微信群"
    assert record["error"] == "微信群: target room is not active; 微信群: target room is not active; 王路飞 failed"

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


def test_rest_reminder_preview_uses_rest_specific_branch(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 15, 8, 0, tzinfo=tz)

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
                {"name": "示例甲", "days": {"16": "休"}},
            ],
        },
    )

    channel_response = client.post(
        "/api/reminder-channel-preview",
        json={
            "preview_type": "rest",
            "name": "示例甲",
            "reminder_time": "08:30",
            "message": "{name} {rest_status}",
        },
    )
    image_response = client.post(
        "/api/reminder-image-preview",
        json={
            "preview_type": "rest",
            "name": "示例甲",
            "reminder_time": "08:30",
            "message": "{name} {rest_status}",
        },
    )

    assert channel_response.status_code == 200
    body = channel_response.json()
    assert body["title"] == "示例甲 今日下午休息"
    assert body["mode"] == "text"
    assert body["content"] == "示例甲 今日下午休息"
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content.startswith(b"\x89PNG")


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
            "send_content_mode": "image",
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
        "巡查班：无\n"
        "站管：无\n"
        "办公室：无\n"
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


def test_daily_duty_preview_uses_next_calendar_day_across_month(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2026,
            "month": 7,
            "source_image_path": "uploads/july.png",
            "grid": [
                {"name": "七月早班", "days": {"31": "早"}},
                {"name": "明日休息人", "days": {"31": ""}},
                {"name": "月末休息人", "days": {"31": "休"}},
            ],
        },
    )
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2026,
            "month": 8,
            "source_image_path": "uploads/august.png",
            "grid": [
                {"name": "八月早班", "days": {"1": "早"}},
                {"name": "明日休息人", "days": {"1": "休"}},
                {"name": "月末休息人", "days": {"1": "休"}},
            ],
        },
    )

    response = client.post("/api/daily-duty-preview", json={"target_date": "2026-07-31"})

    assert response.status_code == 200
    body = response.json()
    assert body["details"]["early"] == "七月早班"
    assert body["details"]["tomorrow_early"] == "八月早班"
    assert body["details"]["afternoon_rest"] == "明日休息人"
    assert body["details"]["resting"] == "月末休息人"
    assert body["details"]["afternoon_return"] == "无"
    assert "明日早班：八月早班" in body["content"]
    assert "今日下午休息：明日休息人" in body["content"]


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
            "send_content_mode": "image",
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


def test_daily_duty_preview_keeps_monitor_shift_people_out_of_patrol_list(tmp_path):
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/rosters/confirm",
        json={
            "year": 2025,
            "month": 9,
            "source_image_path": "uploads/month.png",
            "grid": [
                {"name": "商邱宏", "days": {"16": "早"}},
                {"name": "罗富耀", "days": {"16": "中"}},
                {"name": "王德刚", "days": {"16": "巡"}},
                {"name": "沐春宇", "days": {"16": "备"}},
            ],
        },
    )
    client.post(
        "/api/daily-duty-config",
        json={
            "enabled": True,
            "reminder_time": "07:20",
            "patrol_team_names": ["商邱宏", "罗富耀", "王德刚", "沐春宇"],
            "send_content_mode": "image",
        },
    )

    response = client.post("/api/daily-duty-preview", json={"target_date": "2025-09-16"})

    assert response.status_code == 200
    body = response.json()
    assert body["details"]["early"] == "商邱宏"
    assert body["details"]["middle"] == "罗富耀"
    assert body["details"]["patrol"] == "王德刚"
    assert body["details"]["standby"] == "沐春宇"
    assert "巡查班：王德刚" in body["content"]
    assert "监控班：今日早班：商邱宏，明日早班：无，中班：罗富耀，晚班：无" in body["content"]
    assert "备勤人员：沐春宇" in body["content"]


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
            "send_content_mode": "image",
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


def test_due_monitored_reminder_uses_saved_wechat_member_for_personal_wechat(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 7, 50, 25, tzinfo=tz)

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mentions"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "wgr_notice", "name": "notice"}],
    )
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(
        2025,
        9,
        [{"name": "Alice", "days": {"16": "\u4e2d"}}],
        "uploads/month.png",
    )
    repo.save_monitored_person(
        name="Alice",
        mention_mobile="",
        wechat_group_room_id="wgr_notice",
        wechat_group_member_id="wgm_stable_member",
        wechat_group_runtime_sender_id="@old-runtime-member",
        wechat_group_member_name="Alice",
        daily_time="07:50",
        before_shift_minutes=5,
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["mentions"] == []
    assert sent["content"] == "@Alice今天是你的中班"
    assert isinstance(sent["image_bytes"], bytes)
    assert sent["image_bytes"].startswith(b"\x89PNG")
    records = repo.list_send_records()
    assert records[0]["kind"] == "daily"
    assert records[0]["target"] == "Alice"
    assert records[0]["status"] == "success"


def test_due_reminder_retry_after_send_failure(tmp_path, monkeypatch):
    sent: list[str] = []
    images: list[bytes] = []

    class FrozenDateTime(datetime):
        current = datetime(2025, 9, 16, 7, 50, 5, tzinfo=TZ)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    class FlakyWebhookClient:
        def __init__(self):
            self.calls = 0

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            self.calls += 1
            sent.append(content)
            if self.calls == 1:
                raise RuntimeError("network down")

        async def send_image(self, image_bytes: bytes):
            images.append(image_bytes)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(
        2025,
        9,
        [{"name": "示例甲", "days": {"16": "中"}}],
        "uploads/month.png",
    )
    repo.save_monitored_person(
        name="示例甲",
        mention_mobile="10000000000",
        daily_time="07:50",
        before_shift_minutes=0,
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    flaky_client = FlakyWebhookClient()
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: flaky_client)

    asyncio.run(main_module._send_due_reminders(repo))
    assert len(sent) == 1
    assert repo.list_send_records()[0]["status"] == "failed"

    FrozenDateTime.current = datetime(2025, 9, 16, 7, 50, 5, tzinfo=TZ)
    asyncio.run(main_module._send_due_reminders(repo))

    records = repo.list_send_records()
    assert len(sent) == 2
    assert len(images) == 1
    assert images[0].startswith(b"\x89PNG")
    assert records[0]["status"] == "success"
    assert records[1]["status"] == "failed"


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
        send_content_mode="text",
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


def test_due_custom_reminder_does_not_send_when_person_lacks_matching_shift(tmp_path, monkeypatch):
    sent: list[str] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 10, 21, 0, 25, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent.append(content)

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("不应该发送图片")

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test")
    repo.save_roster_month(2026, 8, [{"name": "罗富耀", "days": {"10": "中"}}], "uploads/month.png")
    repo.save_custom_reminder(
        name="罗富耀",
        shift_code="night",
        reminder_time="21:00",
        message="@罗富耀\n需要开启隧道灯",
        send_content_mode="text",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent == []
    assert repo.list_send_records() == []


def test_due_custom_reminder_sends_early_shift_at_configured_morning_time(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 7, 50, 25, tzinfo=tz)

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
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
    )
    repo.save_roster_month(2026, 8, [{"name": "罗熙云", "days": {"11": "早"}}], "uploads/month.png")
    repo.save_custom_reminder(
        name="罗熙云",
        shift_code="early",
        reminder_time="07:50",
        message="开启隧道灯",
        send_content_mode="text",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "@罗熙云\n开启隧道灯"
    assert sent["mentions"] == []
    records = repo.list_send_records()
    assert records[0]["kind"] == "custom"
    assert records[0]["target"] == "罗熙云"
    assert records[0]["scheduled_at"] == "2026-08-11T07:50:00+08:00"


def test_personal_wechat_custom_reminder_uses_actual_person_not_fixed_mention_target(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 10, 21, 0, 25, tzinfo=tz)

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
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
        mention_mode="custom",
        mention_targets="罗富耀",
    )
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(
        2026,
        8,
        [{"name": "商邱宏", "days": {"10": "晚"}}, {"name": "罗富耀", "days": {"10": "中"}}],
        "uploads/month.png",
    )
    repo.save_custom_reminder(
        name="商邱宏",
        shift_code="night",
        reminder_time="21:00",
        message="关闭隧道灯",
        send_content_mode="text",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "@商邱宏\n关闭隧道灯"
    assert sent["mentions"] == []
    assert repo.list_send_records()[0]["target"] == "商邱宏"


def test_wecom_custom_reminder_uses_actual_person_mobile_not_fixed_mention_target(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 10, 21, 0, 25, tzinfo=tz)

    class FakeWebhookClient:
        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("自定义提醒不应该发送图片")

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        webhook_url="https://example.test/cgi-bin/webhook/send?key=unit-test",
        mention_mode="custom",
        mention_targets="10000000000",
    )
    repo.save_roster_month(2026, 8, [{"name": "商邱宏", "days": {"10": "晚"}}], "uploads/month.png")
    repo.save_custom_reminder(
        name="商邱宏",
        mention_mobile="19900000000",
        shift_code="night",
        reminder_time="21:00",
        message="关闭隧道灯",
        send_content_mode="text",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakeWebhookClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "关闭隧道灯"
    assert sent["mobiles"] == ["19900000000"]


def test_three_people_custom_reminders_follow_actual_roster_shift_not_fixed_target(tmp_path, monkeypatch):
    sent: list[str] = []

    class FrozenDateTime(datetime):
        current = datetime(2026, 8, 11, 7, 50, 25, tzinfo=TZ)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    class FakePersonalWechatClient:
        is_wechat_bridge = True

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent.append(content)

        async def send_image(self, image_bytes: bytes):
            raise AssertionError("自定义提醒不应该发送图片")

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_url="https://lightagent.test/api/push/send",
        lightagent_targets=[{"id": "wgr_notice", "name": "通知群"}],
        mention_mode="custom",
        mention_targets="罗富耀",
    )
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(
        2026,
        8,
        [
            {"name": "商邱宏", "days": {"11": "晚"}},
            {"name": "罗富耀", "days": {"11": "中"}},
            {"name": "罗熙云", "days": {"11": "早"}},
        ],
        "uploads/month.png",
    )
    for name in ["商邱宏", "罗富耀", "罗熙云"]:
        repo.save_custom_reminder(
            name=name,
            shift_code="early",
            reminder_time="07:50",
            message="开启隧道灯",
            send_content_mode="text",
            enabled=True,
        )
        repo.save_custom_reminder(
            name=name,
            shift_code="night",
            reminder_time="21:00",
            message="关闭隧道灯",
            send_content_mode="text",
            enabled=True,
        )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))
    FrozenDateTime.current = datetime(2026, 8, 11, 21, 0, 25, tzinfo=TZ)
    asyncio.run(main_module._send_due_reminders(repo))

    assert sent == ["@罗熙云\n开启隧道灯", "@商邱宏\n关闭隧道灯"]
    records = repo.list_send_records()
    assert [record["target"] for record in records] == ["商邱宏", "罗熙云"]
    assert {record["content"] for record in records} == {"开启隧道灯", "关闭隧道灯"}


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
        send_content_mode="text",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent["content"] == "@示例甲\n需要关闭隧道灯"
    assert sent["mentions"] == []
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
        send_content_mode="text",
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
    assert body["checks"]
    assert any(check["key"] == "database" for check in body["checks"])


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
    assert "微信群" in body["last_error"]
    assert "@member-runtime" not in body["last_error"]

def test_send_records_can_be_filtered_by_status_kind_target_and_today_failed(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 16, 9, 0, tzinfo=tz)

    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo: DutyRepository = app.state.repo
    repo.save_send_record(kind="custom", target="商邱宏", status="failed", error="失败")
    repo.save_send_record(kind="daily", target="罗富耀", status="success")

    failed = client.get("/api/send-records?status=failed").json()["records"]
    custom = client.get("/api/send-records?kind=custom").json()["records"]
    target = client.get("/api/send-records?target=商邱宏").json()["records"]
    today_failed = client.get("/api/send-records?today_failed=true").json()["records"]

    assert [item["target"] for item in failed] == ["商邱宏"]
    assert [item["kind"] for item in custom] == ["custom"]
    assert [item["target"] for item in target] == ["商邱宏"]
    assert [item["target"] for item in today_failed] == ["商邱宏"]


def test_send_records_target_filter_uses_display_names(tmp_path):
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={"sender_type": "lightagent", "lightagent_targets": [{"id": "wgr_notice", "name": "通知群"}]},
    )
    repo: DutyRepository = app.state.repo
    repo.save_send_record(kind="daily_duty", target="wgr_notice", status="success")

    records = client.get("/api/send-records?target=微信群").json()["records"]

    assert [item["target"] for item in records] == ["微信群"]

def test_resend_failed_text_record_sends_again_and_records_result(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, *, webhook_url: str):
            sent["webhook_url"] = webhook_url

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            sent["content"] = content
            sent["mobiles"] = mentioned_mobile_list

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

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
    assert sent["image_bytes"].startswith(b"\x89PNG")
    records = client.get("/api/send-records").json()["records"]
    assert records[0]["kind"] == "daily_resend"
    assert records[0]["status"] == "success"


def test_resend_record_does_not_append_duplicate_resend_suffix(tmp_path, monkeypatch):
    sent: dict[str, object] = {}

    class FakeWebhookClient:
        def __init__(self, webhook_url: str):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None):
            pass

        async def send_image(self, image_bytes: bytes):
            sent["image_bytes"] = image_bytes

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
    assert sent["image_bytes"].startswith(b"\x89PNG")
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
    assert response.json()["detail"] == "微信群 failed; 王路飞 failed"


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



def test_due_monitored_reminder_routes_to_configured_wechat_room(tmp_path, monkeypatch):
    sent: list[tuple[str, list[str] | None]] = []
    images: list[list[str] | None] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 7, 50, 20, tzinfo=tz)

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            sent.append((content, target_ids))

        async def send_image(self, image_bytes: bytes, *, target_ids: list[str] | None = None):
            images.append(target_ids)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "room-1", "name": "一群"}, {"id": "room-2", "name": "二群"}],
    )
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(2025, 9, [{"name": "沐春宇", "days": {"16": "中"}}], "uploads/month.png")
    repo.save_monitored_person(
        name="沐春宇",
        daily_time="07:50",
        before_shift_minutes=5,
        notification_room_id="room-2",
        notification_room_name="二群",
        enabled=True,
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent and sent[0][1] == ["room-2"]
    assert images == [["room-2"]]


def test_due_monitored_reminder_without_room_still_broadcasts_default_targets(tmp_path, monkeypatch):
    sent: list[list[str] | None] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 7, 50, 20, tzinfo=tz)

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            sent.append(target_ids)

        async def send_image(self, image_bytes: bytes, *, target_ids: list[str] | None = None):
            sent.append(target_ids)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "room-1", "name": "一群"}, {"id": "room-2", "name": "二群"}],
    )
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(2025, 9, [{"name": "商邱宏", "days": {"16": "中"}}], "uploads/month.png")
    repo.save_monitored_person(name="商邱宏", daily_time="07:50", before_shift_minutes=5, enabled=True)
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent and all(target_ids is None for target_ids in sent)


def test_daily_duty_and_patrol_config_room_fields_roundtrip(tmp_path):
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_daily_duty_config(notification_room_id="room-1", notification_room_name="一群")
    repo.save_patrol_warning_config(notification_room_id="room-2", notification_room_name="二群")

    assert repo.get_daily_duty_config()["notification_room_id"] == "room-1"
    assert repo.get_daily_duty_config()["notification_room_name"] == "一群"
    assert repo.get_patrol_warning_config()["notification_room_id"] == "room-2"
    assert repo.get_patrol_warning_config()["notification_room_name"] == "二群"



def test_due_custom_reminder_routes_to_configured_wechat_room(tmp_path, monkeypatch):
    sent: list[list[str] | None] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 7, 50, 20, tzinfo=tz)

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            sent.append(target_ids)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "room-1", "name": "一群"}, {"id": "room-2", "name": "二群"}],
    )
    repo.save_daily_duty_config(enabled=False)
    repo.save_roster_month(2025, 9, [{"name": "沐春宇", "days": {"16": "早"}}], "uploads/month.png")
    repo.save_custom_reminder(
        name="沐春宇",
        shift_code="early",
        reminder_time="07:50",
        message="开启隧道灯",
        notification_room_id="room-2",
        notification_room_name="二群",
    )
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent == [["room-2"]]


def test_due_daily_duty_routes_to_configured_wechat_room(tmp_path, monkeypatch):
    sent_images: list[list[str] | None] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 9, 16, 7, 50, 20, tzinfo=tz)

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_image(self, image_bytes: bytes, *, target_ids: list[str] | None = None):
            sent_images.append(target_ids)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "room-1", "name": "一群"}, {"id": "room-2", "name": "二群"}],
    )
    repo.save_daily_duty_config(enabled=True, reminder_time="07:50", notification_room_id="room-1", notification_room_name="一群", send_content_mode="image")
    monkeypatch.setattr(main_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(main_module, "_wecom_webhook_client_from_repo", lambda repo: FakePersonalWechatClient())

    asyncio.run(main_module._send_due_reminders(repo))

    assert sent_images == [["room-1"]]


def test_patrol_warning_message_routes_to_configured_wechat_room(tmp_path):
    sent: list[list[str] | None] = []

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            sent.append(target_ids)

    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_notification_config(
        sender_type="lightagent",
        webhook_url="",
        lightagent_targets=[{"id": "room-1", "name": "一群"}, {"id": "room-2", "name": "二群"}],
    )
    repo.save_patrol_warning_config(send_content_mode="text", notification_room_id="room-1", notification_room_name="一群")

    asyncio.run(main_module._send_patrol_warning_message(
        repo,
        FakePersonalWechatClient(),
        kind="patrol_warning_start_test",
        target="S41",
        scheduled_at="2025-09-16T07:50:00+08:00",
        content="预警测试",
    ))

    assert sent == [["room-1"]]



def test_resend_record_uses_original_notification_room(tmp_path, monkeypatch):
    sent: list[list[str] | None] = []

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            sent.append(target_ids)

    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [{"id": "room-1", "name": "一群"}, {"id": "room-2", "name": "二群"}],
        },
    )
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(
        kind="custom",
        target="沐春宇",
        status="failed",
        content="开启隧道灯",
        notification_room_id="room-2",
        notification_room_name="二群",
    )
    record_id = repo.list_send_records()[0]["id"]
    monkeypatch.setattr(main_module, "_notification_client_from_repo", lambda repo: FakePersonalWechatClient())

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert sent == [["room-2"]]
    resend = repo.list_send_records()[0]
    assert resend["notification_room_id"] == "room-2"


def test_resend_ignores_invalid_numeric_notification_room(tmp_path, monkeypatch):
    sent: list[list[str] | None] = []

    class FakePersonalWechatClient(main_module.WechatBridgeNotifyClient):
        def __init__(self):
            pass

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            sent.append(target_ids)

    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    client.post(
        "/api/notification-config",
        json={
            "sender_type": "lightagent",
            "lightagent_targets": [{"id": "room-actual", "name": "测试群"}],
        },
    )
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(
        kind="custom",
        target="商邱宏",
        status="failed",
        content="补发内容",
        notification_room_id="1",
        notification_room_name="1",
    )
    record_id = repo.list_send_records()[0]["id"]
    monkeypatch.setattr(main_module, "_notification_client_from_repo", lambda repo: FakePersonalWechatClient())

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 200
    assert sent == [None]
    resend = repo.list_send_records()[0]
    assert resend["notification_room_id"] == ""


def test_resend_tunnel_mechanical_confirmation_record_sends_result_not_digit(tmp_path, monkeypatch):
    sent_news: list[dict[str, object]] = []

    class FakeWeComAppNotifyClient:
        is_wecom_app_notify = True

        async def send_text(self, content: str, mentioned_mobile_list: list[str] | None = None, *, target_ids: list[str] | None = None):
            raise AssertionError("隧道机电补发不应该把确认数字当文字发送")

        async def send_image(self, image_bytes: bytes, *, target_ids: list[str] | None = None):
            raise AssertionError("自建应用应优先发送图文")

        async def send_news(self, *, title: str, description: str, image_bytes: bytes, url: str, target_ids: list[str] | None = None):
            sent_news.append({"title": title, "description": description, "target_ids": target_ids, "image_bytes": image_bytes})

    async def fake_query_tunnel_result(repo, request, upload_dir):
        upload_dir.mkdir(parents=True, exist_ok=True)
        image_path = upload_dir / "tunnel-result.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return {"success": True, "result_rows": [{"assetName": "照明"}], "result_image_url": "/api/uploads/tunnel-result.png"}

    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    client = TestClient(app)
    repo = DutyRepository(tmp_path / "data" / "duty-reminder.db")
    repo.save_send_record(
        kind="tunnel_mechanical_wechat",
        target="2026-08-16 罗富耀/商邱宏",
        status="success",
        content="1",
    )
    record_id = repo.list_send_records()[0]["id"]
    monkeypatch.setattr(main_module, "_notification_client_from_repo", lambda repo: FakeWeComAppNotifyClient())
    monkeypatch.setattr(main_module, "_query_tunnel_mechanical_result_image", fake_query_tunnel_result)

    response = client.post(f"/api/send-records/{record_id}/resend")

    assert response.status_code == 200
    assert sent_news
    assert sent_news[0]["title"] == "隧道机电录入结果"
    assert sent_news[0]["description"] != "1"
    assert "2026-08-16" in str(sent_news[0]["description"])
    resend = repo.list_send_records()[0]
    assert resend["kind"] == "tunnel_mechanical_wechat_resend"
    assert resend["content"] != "1"
