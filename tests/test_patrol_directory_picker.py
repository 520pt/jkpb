import json
import os
import shutil
import subprocess
import sys

import pytest

from tests.test_frontend_mobile import ROOT, PLAYWRIGHT_DIR, _wait_for_http


@pytest.mark.skipif(not shutil.which("node") or not PLAYWRIGHT_DIR.exists(), reason="playwright unavailable")
def test_directory_picker_opens_current_path_and_keeps_cancelled_selection(tmp_path):
    env = os.environ.copy()
    env.update({"DATA_DIR": str(tmp_path / "data"), "UPLOAD_DIR": str(tmp_path / "uploads"), "ADMIN_PASSWORD": "", "PYTHONUTF8": "1", "TUNNEL_MECHANICAL_KEEPALIVE_ENABLED": "false"})
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18084"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_for_http("http://127.0.0.1:18084/health")
        prefix = "const { chromium } = require(" + json.dumps(PLAYWRIGHT_DIR.as_posix()) + ");"
        script = prefix + """
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [390, 1280]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const page = await context.newPage();
      const paths = [];
      let simulateMissingDirectory = false;
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/patrol-logs/webdav/config', route => route.fulfill({ json: { config: { base_url: 'https://webdav.example.test/dav', username: '', password: '', password_configured: true, directory: '巡查记录' } } }));
      await page.route('**/api/patrol-logs/webdav/directories', route => {
        const path = route.request().postDataJSON().directory;
        paths.push(path);
        if (simulateMissingDirectory && path) {
          return route.fulfill({ status: 404, json: { detail: 'WebDAV 目录不存在，请重新选择实际保存位置。' } });
        }
        const child = path === '巡查记录' ? '巡查记录/2026年' : null;
        return route.fulfill({ json: { path, directories: child ? [{ name: '2026年', path: child, is_directory: true }] : [], files: [] } });
      });
      await page.goto('http://127.0.0.1:18084/lufei', { waitUntil: 'networkidle' });
      if (width === 390) {
        await page.click('.mobile-tabbar [data-mobile-group="more"]');
        await page.click('#mobileMoreSheet [data-mobile-more-group="mech"]');
      } else await page.click('#mainSidebar [data-main-group="mech"]');
      await page.click('#mainSubnav [data-subnav-view="mech-patrol-logs"]');
      await page.waitForFunction(() => !document.getElementById('patrolWebDAVBrowseBtn').disabled);
      if (await page.locator('#patrolWebDAVBrowseBtn').textContent() !== '选择目录') throw Error('Incorrect directory button label');
      if (!(await page.locator('#patrolWebDAVBrowseBtn').evaluate(el => el.parentElement.contains(document.getElementById('patrolWebDAVDirectoryLabel'))))) throw Error('Directory button is not beside the saved path');
      await page.click('#patrolWebDAVBrowseBtn');
      await page.waitForFunction(() => !document.getElementById('patrolWebDAVChooseBtn').disabled);
      if (paths[0] !== '巡查记录') throw Error('Picker did not open saved directory');
      if (!(await page.textContent('#patrolWebDAVBrowserPath')).includes('/巡查记录')) throw Error('Displayed path does not match current directory');
      await page.click('#patrolWebDAVBrowserCancelBtn');
      if (await page.inputValue('#patrolWebDAVDirectory') !== '巡查记录') throw Error('Cancel changed the saved directory');
      await page.click('#patrolWebDAVBrowseBtn');
      await page.waitForFunction(() => !document.getElementById('patrolWebDAVChooseBtn').disabled);
      await page.click('#patrolWebDAVDirectoryList [data-webdav-path="巡查记录/2026年"]');
      await page.waitForFunction(() => document.getElementById('patrolWebDAVBrowserPath').textContent === '/巡查记录/2026年' && !document.getElementById('patrolWebDAVChooseBtn').disabled);
      await page.click('#patrolWebDAVChooseBtn');
      if (await page.inputValue('#patrolWebDAVDirectory') !== '巡查记录/2026年') throw Error('Directory selection was not applied');
      simulateMissingDirectory = true;
      await page.click('#patrolWebDAVBrowseBtn');
      await page.waitForFunction(() => document.getElementById('patrolWebDAVBrowserStatus').textContent.includes('读取失败'));
      if (!(await page.isDisabled('#patrolWebDAVChooseBtn'))) throw Error('Missing directory can still be selected');
      if (await page.isDisabled('#patrolWebDAVParentBtn')) throw Error('Missing directory prevents returning to parent');
      if (await page.textContent('#patrolWebDAVBrowserPath') !== '/巡查记录/2026年') throw Error('Failed listing displays the wrong directory');
      await page.click('#patrolWebDAVParentBtn');
      await page.waitForFunction(() => document.getElementById('patrolWebDAVBrowserPath').textContent === '/巡查记录' && document.getElementById('patrolWebDAVBrowserStatus').textContent.includes('读取失败'));
      await page.click('#patrolWebDAVParentBtn');
      await page.waitForFunction(() => document.getElementById('patrolWebDAVBrowserPath').textContent.includes('根目录') && !document.getElementById('patrolWebDAVChooseBtn').disabled);
      await page.click('#patrolWebDAVBrowserCancelBtn');
      if (await page.inputValue('#patrolWebDAVDirectory') !== '巡查记录/2026年') throw Error('Recovery changed the selected directory without confirmation');
      simulateMissingDirectory = false;
      await page.click('#patrolWebDAVBrowseBtn');
      await page.waitForFunction(() => !document.getElementById('patrolWebDAVChooseBtn').disabled);
      if (paths.at(-1) !== '巡查记录/2026年') throw Error('Reopened picker did not use newly selected directory');
      await page.click('#patrolWebDAVParentBtn');
      await page.waitForFunction(() => document.getElementById('patrolWebDAVBrowserPath').textContent === '/巡查记录' && !document.getElementById('patrolWebDAVChooseBtn').disabled);
      await page.click('#patrolWebDAVParentBtn');
      await page.waitForFunction(() => document.getElementById('patrolWebDAVBrowserPath').textContent.includes('根目录') && !document.getElementById('patrolWebDAVChooseBtn').disabled);
      await page.click('#patrolWebDAVChooseBtn');
      await page.click('#patrolWebDAVBrowseBtn');
      await page.waitForFunction(() => !document.getElementById('patrolWebDAVChooseBtn').disabled);
      if (paths.at(-1) !== '') throw Error('Selected WebDAV root could not be reopened');
      if (errors.length) throw Error(errors.join('; '));
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        check = tmp_path / "directory_picker_check.js"
        check.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", str(check)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
