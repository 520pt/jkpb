import base64
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest

from tests.test_frontend_mobile import ROOT, PLAYWRIGHT_DIR, _wait_for_http
from tests.test_patrol_log_web import workbook
from tests.test_patrol_webdav import dav_server


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
@pytest.mark.skipif(not PLAYWRIGHT_DIR.exists(), reason="playwright not installed")
def test_patrol_page_real_clicks_and_downloads_on_mobile_and_desktop(tmp_path, dav_server):
    dav_url, dav_state = dav_server
    fixtures = [
        {
            "id": record_id, "date": "2026-11-15", "category": "日常巡查",
            "section": section, "person_names": "示例甲",
            "start_time": "2026-11-15 09:00:00", "end_time": "2026-11-15 10:00:00",
        }
        for record_id, section in (("a", "南景高速"), ("b", "景文高速"))
    ]
    fixtures[0]["person_names"] += " " + chr(60) + 'img src=x onerror="window.__patrolXss=1">'
    batch = BytesIO()
    with zipfile.ZipFile(batch, "w") as archive:
        archive.writestr("a.xlsx", workbook("a"))
        archive.writestr("b.xlsx", workbook("b"))
    env = os.environ.copy()
    env.update({
        "DATA_DIR": str(tmp_path / "data"), "UPLOAD_DIR": str(tmp_path / "uploads"),
        "ADMIN_PASSWORD": "", "PYTHONUTF8": "1",
        "TUNNEL_MECHANICAL_KEEPALIVE_ENABLED": "false",
    })
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18083"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_http("http://127.0.0.1:18083/health")
        prefix = [
            "const fs = require('node:fs');",
            "const path = require('node:path');",
            "const { chromium } = require(" + json.dumps(PLAYWRIGHT_DIR.as_posix()) + ");",
            "const fixtures = " + json.dumps(fixtures) + ";",
            "const output = " + json.dumps(tmp_path.as_posix()) + ";",
            "const today = " + json.dumps(datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()) + ";",
            "const single = Buffer.from(" + json.dumps(base64.b64encode(workbook("b")).decode()) + ", 'base64');",
            "const batch = Buffer.from(" + json.dumps(base64.b64encode(batch.getvalue()).decode()) + ", 'base64');",
        ]
        prefix.append("const davURL = " + json.dumps(dav_url) + ";")
        script = chr(10).join(prefix) + """
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1280]) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, acceptDownloads: true });
      const page = await context.newPage();
      const errors = [];
      const exports = [];
      const cloudExports = [];
      const historyChecks = [];
      const exportedIds = new Set();
      let historyFailure = false;
      await page.route("**/api/patrol-logs/webdav/preview", route => route.fulfill({ json: {
        base_directory: "", records: 2, can_confirm: true, plan_token: "fixture-plan",
        folders: [{ path: "2026年/11月/日常巡查", exists: false }],
        files: fixtures.map(row => ({ id: row.id, path: "2026年/11月/日常巡查/" + row.id + ".xlsx", exists: false })),
      } }));
      await page.route("**/api/patrol-logs/webdav/export", async (route) => {
        const payload = route.request().postDataJSON();
        if (payload.target !== "2026-11-15" || payload.person_name !== "示例甲" || payload.patrol_type !== "1") throw new Error("WebDAV 未使用查询筛选快照");
        cloudExports.push(payload.record_ids);
        await route.fulfill({ json: { ok: true, records: payload.record_ids.length, path: "巡查日志/测试日志.zip" } });
      });
      page.on("pageerror", (error) => errors.push(error.message));
      await page.route("**/api/patrol-logs?*", async (route) => {
        const params = new URL(route.request().url()).searchParams;
        if (params.get("person_name") !== "示例甲" || params.get("patrol_type") !== "1") {
          throw new Error("查询没有传递人员或巡查类型");
        }
        const date = params.get("target");
        if (date === "2026-11-14") {
          await route.fulfill({ status: 502, json: { detail: "平台暂不可用" } });
        } else {
          const rows = date === "2026-11-13" ? [] : fixtures;
          await route.fulfill({ json: { date, rows, total: rows.length } });
        }
      });
      await page.route("**/api/patrol-logs/export", async (route) => {
        const payload = route.request().postDataJSON();
        if (payload.target !== "2026-11-15" || payload.person_name !== "示例甲" || payload.patrol_type !== "1") {
          throw new Error("导出没有使用已查询的日期、人员和类型");
        }
        exports.push(payload.record_ids);
        payload.record_ids.forEach(id => exportedIds.add(id));
        const one = payload.record_ids.length === 1;
        const filename = one ? "测试巡查日志.xlsx" : "测试巡查日志.zip";
        await route.fulfill({
          headers: {
            "Content-Type": one ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "application/zip",
            "Content-Disposition": "attachment; filename*=UTF-8''" + encodeURIComponent(filename),
          },
          body: one ? single : batch,
        });
      });
      await page.route("**/api/patrol-logs/export-status", async (route) => {
        const payload = route.request().postDataJSON();
        historyChecks.push(payload.record_ids);
        if (historyFailure) return route.fulfill({ status: 502, json: { detail: "历史检查暂不可用" } });
        if (payload.target !== "2026-11-15" || payload.person_name !== "示例甲" || payload.patrol_type !== "1") {
          throw new Error("导出历史检查没有使用已查询的日期、人员和类型");
        }
        await route.fulfill({ json: {
          total: payload.record_ids.length,
          exported: payload.record_ids.filter(id => exportedIds.has(id)).map(id => ({ id, filename: id + ".xlsx" })),
          unexported: payload.record_ids.filter(id => !exportedIds.has(id)).map(id => ({ id, filename: id + ".xlsx" })),
        } });
      });
      await page.goto("http://127.0.0.1:18083/lufei", { waitUntil: "networkidle" });
      if (width === 390) {
        await page.click('.mobile-tabbar [data-mobile-group="more"]');
        await page.click('#mobileMoreSheet [data-mobile-more-group="mech"]');
      } else {
        await page.click('#mainSidebar [data-main-group="mech"]');
      }
      await page.click('#mainSubnav [data-subnav-view="mech-patrol-logs"]');
      await page.waitForSelector("#patrolLogExportPanel", { state: "visible" });
      await page.waitForFunction(() => !document.getElementById("patrolWebDAVSaveBtn").disabled);
      if (await page.locator(".tunnel-subnav").isVisible()) throw new Error("巡查页仍显示无关隧道模板状态");
      if (await page.locator("#tunnelEntryPanel").isVisible() || await page.locator("#tunnelTemplatePanel").isVisible()) throw new Error("巡查页混入机电录入或模板内容");
      const gap = await page.evaluate(() => document.getElementById("patrolWebDAVForm").getBoundingClientRect().top - document.getElementById("mainSubnav").getBoundingClientRect().bottom);
      if (gap > 80) throw new Error("巡查页顶部仍有大段无效空白：" + gap);
      await page.fill("#patrolWebDAVUrl", davURL);
      await page.fill("#patrolWebDAVUsername", "fixture-user");
      await page.fill("#patrolWebDAVPassword", "fixture-password");
      await page.click("#patrolWebDAVBrowseBtn");
      await page.waitForFunction(() => !document.getElementById("patrolWebDAVChooseBtn").disabled);
      await page.click("#patrolWebDAVChooseBtn");
      await page.click("#patrolWebDAVSaveBtn");
      await page.waitForFunction(() => document.getElementById("patrolWebDAVStatus").textContent.includes("已保存"));
      if (await page.inputValue("#patrolWebDAVPassword")) throw new Error("保存后密码未清空");
      await page.click("#patrolWebDAVTestBtn");
      await page.waitForFunction(() => document.getElementById("patrolWebDAVStatus").textContent.includes("连接成功"));
      await page.click('#mainSubnav [data-subnav-view="mech-entry"]');
      await page.waitForSelector("#tunnelEntryPanel", { state: "visible" });
      await page.click('#mainSubnav [data-subnav-view="mech-patrol-logs"]');
      await page.waitForSelector("#patrolLogExportPanel", { state: "visible" });
      if (await page.locator(".tunnel-subnav").isVisible()) throw new Error("切换回来后隧道状态再次混入巡查页");
      if (!(await page.isDisabled("#patrolLogWebDAVBtn"))) throw new Error("未查询日志时可以上传");
      if (await page.inputValue("#patrolLogDate") !== today) throw new Error("默认日期不是北京时间当天");
      await page.fill("#patrolLogDate", "2026-11-15");
      await page.fill("#patrolLogPerson", "示例甲");
      await page.selectOption("#patrolLogType", "1");
      await page.click("#patrolLogQueryBtn");
      await page.waitForFunction(() => document.querySelectorAll("#patrolLogRows [data-patrol-log-select]").length === 2);
      await page.waitForFunction(() => !document.getElementById("patrolLogExportBtn").disabled);
      if (!(await page.textContent("#patrolLogStatus")).includes("共 2 条")) throw new Error("查询结果统计错误");
      if (await page.locator("#patrolLogResults img").count() || await page.evaluate(() => !!window.__patrolXss)) {
        throw new Error("平台字段未按纯文本渲染");
      }
      const layout = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
      if (layout.page > layout.viewport + 2) throw new Error("巡查日志表格撑宽页面");
      await page.click("#patrolLogWebDAVBtn");
      await page.waitForSelector("#patrolWebDAVPreviewDialog[open]");
      await page.click("#patrolWebDAVPreviewCancelBtn");
      if (cloudExports.length) throw new Error("取消后仍上传 WebDAV");
      await page.click("#patrolLogWebDAVBtn");
      await page.waitForSelector("#patrolWebDAVPreviewDialog[open]");
      await page.click("#patrolWebDAVConfirmBtn");
      await page.waitForFunction(() => document.getElementById("patrolLogStatus").textContent.includes("已保存 2 条"));
      if (JSON.stringify(cloudExports[0]) !== JSON.stringify(["a", "b"])) throw new Error("WebDAV 选中记录错误");
      page.once("dialog", (dialog) => dialog.dismiss());
      await page.click("#patrolLogExportBtn");
      await page.waitForTimeout(60);
      if (exports.length || historyChecks.length) throw new Error("取消确认后仍发起检查或导出");
      page.once("dialog", (dialog) => dialog.accept());
      let downloadPromise = page.waitForEvent("download");
      await page.click("#patrolLogExportBtn");
      let download = await downloadPromise;
      const batchPath = path.join(output, "batch-" + width + ".zip");
      await download.saveAs(batchPath);
      if (download.suggestedFilename() !== "测试巡查日志.zip" || !fs.readFileSync(batchPath).equals(batch)) {
        throw new Error("批量下载文件名或文件内容错误");
      }
      if (JSON.stringify(exports[0]) !== JSON.stringify(["a", "b"])) throw new Error("批量导出记录不正确");
      await page.waitForFunction(() => document.getElementById("patrolLogQueryForm").getAttribute("aria-busy") !== "true");
      await page.uncheck("#patrolLogSelectAll");
      if (!(await page.isDisabled("#patrolLogExportBtn"))) throw new Error("未选择记录时仍可导出");
      await page.check('#patrolLogRows [data-patrol-log-select="b"]');
      const duplicatePrompts = [];
      const cancelDuplicate = async dialog => {
        duplicatePrompts.push(dialog.message());
        if (duplicatePrompts.length === 1) await dialog.accept();
        else await dialog.dismiss();
      };
      page.on("dialog", cancelDuplicate);
      await page.click("#patrolLogExportBtn");
      await page.waitForFunction(() => document.getElementById("patrolLogStatus").textContent.includes("未发起下载"));
      page.off("dialog", cancelDuplicate);
      if (exports.length !== 1 || duplicatePrompts.length !== 2 || !duplicatePrompts[1].includes("已经导出过")) throw Error("重复导出取消流程错误");
      const acceptAll = dialog => dialog.accept();
      page.on("dialog", acceptAll);
      downloadPromise = page.waitForEvent("download");
      await page.click("#patrolLogExportBtn");
      download = await downloadPromise;
      const singlePath = path.join(output, "single-" + width + ".xlsx");
      await download.saveAs(singlePath);
      if (download.suggestedFilename() !== "测试巡查日志.xlsx" || !fs.readFileSync(singlePath).equals(single)) {
        throw new Error("单条下载文件名或文件内容错误");
      }
      if (JSON.stringify(exports[1]) !== JSON.stringify(["b"])) throw new Error("勾选记录未正确传递");
      await page.waitForFunction(() => document.getElementById("patrolLogQueryForm").getAttribute("aria-busy") !== "true");
      historyFailure = true;
      await page.click('#patrolLogRows [data-patrol-log-download="a"]');
      await page.waitForFunction(() => document.getElementById("patrolLogStatus").textContent.includes("历史检查暂不可用"));
      if (exports.length !== 2 || JSON.stringify(historyChecks.at(-1)) !== JSON.stringify(["a"])) throw Error("单条下载未检查历史或检查失败后仍下载");
      historyFailure = false;
      page.off("dialog", acceptAll);
      await page.fill("#patrolLogDate", "2026-11-14");
      if (!(await page.isDisabled("#patrolLogExportBtn"))) throw new Error("修改筛选后仍可导出旧结果");
      await page.click("#patrolLogQueryBtn");
      await page.waitForFunction(() => document.getElementById("patrolLogStatus").textContent.includes("平台暂不可用"));
      if (await page.locator("#patrolLogRows [data-patrol-log-select]").count()) throw new Error("查询失败后残留旧结果");
      await page.fill("#patrolLogDate", "2026-11-13");
      await page.click("#patrolLogQueryBtn");
      await page.waitForFunction(() => document.getElementById("patrolLogStatus").textContent.includes("未查询到"));
      if (!(await page.isDisabled("#patrolLogExportBtn"))) throw new Error("空结果仍可导出");
      if (errors.length) throw new Error(errors.join("; "));
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
"""
        check = tmp_path / "patrol_frontend_check.js"
        check.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(check)], cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", timeout=150, check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert any(item["method"] == "PROPFIND" for item in dav_state["calls"]), "浏览器连接测试没有到达实际 HTTP WebDAV 服务"
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
