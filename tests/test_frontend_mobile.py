import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_DIR = ROOT / "node_modules" / "playwright"

EXPECTED_SUBNAV = {
    "home": ["排班查看", "今日提醒", "下次提醒", "今日在岗摘要", "最近发送情况", "配置总览"],
    "schedule": ["导入/核对", "已导入排班", "今日在岗"],
    "reminder": ["监控班提醒", "自定义提醒", "休息提醒", "查询休息", "假期余额提醒"],
    "mech": ["隧道机电录入", "隧道模板", "修改模板", "施工图片", "施工点维护"],
    "warning": ["公路巡查预警", "橙色预警查询", "预警提醒"],
    "notify": ["通知通道", "通知接收人", "交互菜单", "发送测试", "手动模拟发送"],
    "people": ["人员名单", "岗位分组", "绑定状态"],
    "records": ["发送记录", "提醒诊断", "配置与维护", "系统状态"],
}


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2):  # nosec - local test server only
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.3)
    raise RuntimeError(f"等待服务启动失败：{last_error}")


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
@pytest.mark.skipif(not PLAYWRIGHT_DIR.exists(), reason="playwright not installed")
def test_frontend_mobile_navigation_real_clicks(tmp_path):
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["UPLOAD_DIR"] = str(tmp_path / "uploads")
    env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18080"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_http("http://127.0.0.1:18080/health")
        script = f"""
const {{ chromium }} = require({json.dumps(str(PLAYWRIGHT_DIR.as_posix()))});

(async() => {{
  const browser = await chromium.launch({{ headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 390, height: 844 }} }});
  await page.goto('http://127.0.0.1:18080/', {{ waitUntil: 'networkidle' }});

  const sidebarDisplay = await page.$eval('#mainSidebar', (el) => getComputedStyle(el).display);
  const tabbarDisplay = await page.$eval('.mobile-tabbar', (el) => getComputedStyle(el).display);
  if (sidebarDisplay !== 'none' || tabbarDisplay !== 'flex') {{
    throw new Error(`mobile app nav mismatch: sidebar=${{sidebarDisplay}}, tabbar=${{tabbarDisplay}}`);
  }}

  const groups = Object.keys({json.dumps(EXPECTED_SUBNAV)});
  for (const group of groups) {{
    const mainButton = await page.$(`.mobile-tabbar [data-mobile-group="${{group}}"]`);
    if (mainButton) {{
      await mainButton.click();
    }} else {{
      await page.click('.mobile-tabbar [data-mobile-group="more"]');
      await page.click(`#mobileMoreSheet [data-mobile-more-group="${{group}}"]`);
    }}
    await page.waitForTimeout(100);
    const labels = await page.$$eval('#appShell > nav.tabs .tab-button', (els) => els.map((el) => (el.innerText || el.textContent || '').trim()));
    const expected = {json.dumps(EXPECTED_SUBNAV)}[group];
    if (JSON.stringify(labels) !== JSON.stringify(expected)) {{
      throw new Error(`subnav mismatch for ${{group}}: got ${{JSON.stringify(labels)}}, expected ${{JSON.stringify(expected)}}`);
    }}
    if (labels.some((label) => !label)) {{
      throw new Error(`empty label found for ${{group}}`);
    }}
  }}

  await browser.close();
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        js_path = tmp_path / "mobile_nav_check.js"
        js_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(js_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
@pytest.mark.skipif(not PLAYWRIGHT_DIR.exists(), reason="playwright not installed")
def test_frontend_desktop_navigation_real_clicks_all_subpages(tmp_path):
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["UPLOAD_DIR"] = str(tmp_path / "uploads")
    env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18081"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_http("http://127.0.0.1:18081/health")
        script = f"""
const {{ chromium }} = require({json.dumps(str(PLAYWRIGHT_DIR.as_posix()))});

(async() => {{
  const expected = {json.dumps(EXPECTED_SUBNAV, ensure_ascii=False)};
  const browser = await chromium.launch({{ headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 1440, height: 900 }} }});
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${{error.message}}`));
  page.on('console', (message) => {{
    if (message.type() === 'error') errors.push(`console: ${{message.text()}}`);
  }});
  await page.goto('http://127.0.0.1:18081/', {{ waitUntil: 'networkidle' }});

  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  if (theme !== 'light') {{
    throw new Error(`desktop default theme should be light, got ${{theme}}`);
  }}
  const themeButtonLabel = await page.$eval('#themeToggleBtn', (el) => (el.innerText || el.textContent || '').trim());
  if (themeButtonLabel !== '切换到暗色') {{
    throw new Error(`theme button should default to dark-toggle label, got ${{themeButtonLabel}}`);
  }}
  await page.evaluate(() => {{
    localStorage.setItem('duty-reminder:theme', 'dark');
    localStorage.removeItem('duty-reminder:theme-user-set');
  }});
  await page.reload({{ waitUntil: 'networkidle' }});
  const themeAfterLegacyDark = await page.evaluate(() => document.documentElement.dataset.theme);
  if (themeAfterLegacyDark !== 'light') {{
    throw new Error(`legacy dark cache should not override default light, got ${{themeAfterLegacyDark}}`);
  }}
  await page.evaluate(() => {{
    localStorage.setItem('duty-reminder:theme', 'dark');
    localStorage.setItem('duty-reminder:theme-user-set', '1');
  }});
  await page.reload({{ waitUntil: 'networkidle' }});
  const themeAfterSavedDark = await page.evaluate(() => document.documentElement.dataset.theme);
  if (themeAfterSavedDark !== 'dark') {{
    throw new Error(`explicit dark choice should persist, got ${{themeAfterSavedDark}}`);
  }}
  await page.evaluate(() => {{
    localStorage.setItem('duty-reminder:theme', 'light');
    localStorage.setItem('duty-reminder:theme-user-set', '1');
  }});
  await page.reload({{ waitUntil: 'networkidle' }});
  const themeAfterBackToLight = await page.evaluate(() => document.documentElement.dataset.theme);
  if (themeAfterBackToLight !== 'light') {{
    throw new Error(`theme toggle should switch back to light, got ${{themeAfterBackToLight}}`);
  }}
  const sidebarDisplay = await page.$eval('#mainSidebar', (el) => getComputedStyle(el).display);
  const tabbarDisplay = await page.$eval('.mobile-tabbar', (el) => getComputedStyle(el).display);
  if (sidebarDisplay === 'none' || tabbarDisplay !== 'none') {{
    throw new Error(`desktop nav mismatch: sidebar=${{sidebarDisplay}}, tabbar=${{tabbarDisplay}}`);
  }}

  for (const [group, expectedLabels] of Object.entries(expected)) {{
    await page.click(`#mainSidebar [data-main-group="${{group}}"]`);
    await page.waitForTimeout(100);
    const labels = await page.$$eval('#appShell > nav.tabs .tab-button:not([hidden])', (els) => els.map((el) => (el.innerText || el.textContent || '').trim()));
    if (JSON.stringify(labels) !== JSON.stringify(expectedLabels)) {{
      throw new Error(`desktop subnav mismatch for ${{group}}: got ${{JSON.stringify(labels)}}, expected ${{JSON.stringify(expectedLabels)}}`);
    }}
    const activeSidebarGroup = await page.$eval('#mainSidebar button.active', (el) => el.getAttribute('data-main-group'));
    if (activeSidebarGroup !== group) {{
      throw new Error(`active sidebar mismatch: got ${{activeSidebarGroup}}, expected ${{group}}`);
    }}
    for (const label of expectedLabels) {{
      await page.locator('#appShell > nav.tabs .tab-button:not([hidden])').filter({{ hasText: label }}).click();
      await page.waitForTimeout(120);
      const activeTab = await page.$eval('#appShell > nav.tabs .tab-button.active', (el) => (el.innerText || el.textContent || '').trim());
      if (activeTab !== label) {{
        throw new Error(`active tab mismatch for ${{group}}/${{label}}: got ${{activeTab}}`);
      }}
      const visiblePages = await page.$$eval('#appShell > main .tab-page', (nodes) => nodes
        .filter((node) => getComputedStyle(node).display !== 'none' && getComputedStyle(node).visibility !== 'hidden' && node.offsetParent !== null)
        .map((node) => node.id));
      if (visiblePages.length !== 1) {{
        throw new Error(`visible tab-page mismatch for ${{group}}/${{label}}: ${{JSON.stringify(visiblePages)}}`);
      }}
      const gap = await page.evaluate(() => {{
        const tabs = document.querySelector('#appShell > nav.tabs');
        const pagePanel = document.querySelector('#appShell > main .tab-page:not([hidden])');
        if (!tabs || !pagePanel) return 999;
        return Math.round(pagePanel.getBoundingClientRect().top - tabs.getBoundingClientRect().bottom);
      }});
      if (gap > 2) {{
        throw new Error(`card tabs detached for ${{group}}/${{label}}: gap=${{gap}}`);
      }}
      const visiblePageSelector = `#${{visiblePages[0]}}`;
      const visibleText = await page.$eval(visiblePageSelector, (el) => (el.innerText || el.textContent || '').trim());
      for (const phrase of ['个人微信群', '内部微信登录', '群同步', 'LightAgent']) {{
        if (visibleText.includes(phrase)) {{
          throw new Error(`旧入口文字不应可见：${{group}}/${{label}} -> ${{phrase}}`);
        }}
      }}
      const visible = async (selector) => page.$eval(selector, (el) => {{
        const style = getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
      }}).catch(() => false);
      if (group === 'home' && label === '今日提醒') {{
        const layout = await page.$eval('#todayRemindersList', (el) => {{
          const columns = Array.from(el.querySelectorAll('.today-reminder-column')).map((column) => ({{
            className: column.className,
            titles: Array.from(column.querySelectorAll('.today-reminder-group h3 span')).map((node) => (node.textContent || '').trim()),
            width: Math.round(column.getBoundingClientRect().width),
          }}));
          const firstItem = el.querySelector('.today-reminder-item');
          return {{
            columns,
            childCount: el.children.length,
            firstItem: firstItem ? {{
              hasContentBlock: Boolean(firstItem.querySelector('.today-reminder-content')),
              hasSummary: Boolean(firstItem.querySelector('.today-reminder-summary')),
            }} : null,
          }};
        }});
        if (layout.columns.length !== 2) {{
          throw new Error(`今日提醒不应再是单列布局：${{JSON.stringify(layout)}}`);
        }}
        if (!layout.columns[0].titles.length || !layout.columns[1].titles.length) {{
          throw new Error(`今日提醒左右两栏都应有内容：${{JSON.stringify(layout)}}`);
        }}
        if (!layout.firstItem || layout.firstItem.hasContentBlock || !layout.firstItem.hasSummary) {{
          throw new Error(`今日提醒卡片内不应左右重复正文：${{JSON.stringify(layout)}}`);
        }}
      }}
      if (group === 'notify' && label === '通知通道') {{
        if (await visible('#wecomAppMenuPreview')) {{
          throw new Error('通知通道不应显示自定义菜单编辑器');
        }}
        for (const phrase of ['公共通知接收人', '企业微信绑定状态', '自定义菜单']) {{
          if (visibleText.includes(phrase)) {{
            throw new Error(`通知通道不应显示${{phrase}}`);
          }}
        }}
      }}
      if (group === 'notify' && label === '通知接收人') {{
        await page.click('[data-notification-sender="wecom_app"]');
        await page.waitForTimeout(80);
        const receiverText = await page.$eval(visiblePageSelector, (el) => (el.innerText || el.textContent || '').trim());
        if (!receiverText.includes('公共通知接收人') || !receiverText.includes('今日在岗接收人')) {{
          throw new Error('通知接收人页必须显示公共/今日在岗接收人配置');
        }}
        if (receiverText.includes('企业微信绑定状态') || receiverText.includes('自定义菜单')) {{
          throw new Error('通知接收人页不应混入绑定状态或自定义菜单');
        }}
      }}
      if (group === 'notify' && label === '交互菜单') {{
        if (!(await visible('#wecomAppMenuPreview')) || !(await visible('#createWecomAppMenuBtn'))) {{
          throw new Error('交互菜单页必须显示自定义菜单编辑器和创建按钮');
        }}
        if (visibleText.includes('CorpID') || visibleText.includes('公共通知接收人') || visibleText.includes('企业微信绑定状态')) {{
          throw new Error('交互菜单页不应混入自建应用基础配置、接收人或绑定状态');
        }}
      }}
      if (group === 'reminder' && label === '休息提醒') {{
        const reminderTitle = await page.$eval('#monitorSettingsTitle', (el) => (el.innerText || el.textContent || '').trim());
        const reminderListTitle = await page.$eval('#monitorListTitle', (el) => (el.innerText || el.textContent || '').trim());
        if (reminderTitle !== '休息提醒') {{
          throw new Error(`休息提醒页标题不正确：${{reminderTitle}}`);
        }}
        if (reminderListTitle !== '已配置休息提醒') {{
          throw new Error(`休息提醒列表标题不正确：${{reminderListTitle}}`);
        }}
      }}
      if (group === 'reminder' && label === '假期余额提醒') {{
        const vacationTitle = await page.$eval('#vacationReminderTitle', (el) => (el.innerText || el.textContent || '').trim());
        const vacationPreviewTitle = await page.$eval('#vacationImagePreviewPanel .reminder-preview-head strong', (el) => (el.innerText || el.textContent || '').trim());
        const visibleBlocks = await page.$$eval('#vacationReminderSettings .side-block', (nodes) => nodes
          .filter((node) => getComputedStyle(node).display !== 'none' && node.offsetParent !== null)
          .map((node) => (node.querySelector('h3')?.innerText || '').trim()));
        if (vacationTitle !== '假期余额提醒') {{
          throw new Error(`假期余额提醒标题不正确：${{vacationTitle}}`);
        }}
        if (vacationPreviewTitle !== '图片效果预览') {{
          throw new Error(`假期预览标题不正确：${{vacationPreviewTitle}}`);
        }}
        if (JSON.stringify(visibleBlocks) !== JSON.stringify(['假期余额提醒', '图片效果预览'])) {{
          throw new Error(`假期余额提醒不应再是单列：${{JSON.stringify(visibleBlocks)}}`);
        }}
      }}
      if (group === 'mech' && label === '修改模板') {{
        if (!(await visible('#saveFeatureChannelBtn'))) {{
          throw new Error('修改模板页必须显示保存按钮');
        }}
        if (await visible('#featureChannelMenuPreviewField')) {{
          throw new Error('修改模板页不应显示交互菜单预览');
        }}
      }}
      if (group === 'mech' && label === '隧道机电录入') {{
        const previewTitle = await page.$eval('#tunnelMechanicalChannelPreview', (el) => {{
          const title = el.closest('.reminder-image-preview-panel')?.querySelector('.reminder-preview-head strong');
          return (title && (title.innerText || title.textContent) || '').trim();
        }});
        if (previewTitle !== '图文效果预览') {{
          throw new Error(`隧道机电页图文预览标题不正确：${{previewTitle}}`);
        }}
        if (!(await visible('#tunnelMechanicalChannelPreview'))) {{
          throw new Error('隧道机电页缺少图文效果预览容器');
        }}
      }}
      if (group === 'mech' && label === '施工图片') {{
        const previewTitle = await page.$eval('#constructionImageChannelPreview', (el) => {{
          const title = el.closest('.reminder-image-preview-panel')?.querySelector('.reminder-preview-head strong');
          return (title && (title.innerText || title.textContent) || '').trim();
        }});
        if (previewTitle !== '图文效果预览') {{
          throw new Error(`施工图片页图文预览标题不正确：${{previewTitle}}`);
        }}
        if (!(await visible('#constructionImageChannelPreview'))) {{
          throw new Error('施工图片页缺少图文效果预览容器');
        }}
      }}
      if (group === 'records' && label === '配置与维护') {{
        const cardTitles = await page.$$eval('#settingsOverview .maintenance-card', (cards) => cards
          .filter((card) => getComputedStyle(card).display !== 'none' && card.offsetParent !== null)
          .map((card) => (card.querySelector('h3')?.innerText || '').trim()));
        const expectedCards = ['配置导出', '配置导入', '数据库备份', '文件清理'];
        if (JSON.stringify(cardTitles) !== JSON.stringify(expectedCards)) {{
          throw new Error(`配置与维护卡片不完整：${{JSON.stringify(cardTitles)}}`);
        }}
        const looseHeadings = await page.$$eval('#settingsOverview > div > h3', (nodes) => nodes
          .filter((node) => getComputedStyle(node).display !== 'none' && node.offsetParent !== null)
          .map((node) => (node.innerText || '').trim()));
        if (looseHeadings.length) {{
          throw new Error(`配置与维护不应再有散落标题：${{JSON.stringify(looseHeadings)}}`);
        }}
        for (const selector of ['#exportConfigBtn', '#importConfigBtn', '#createDbBackupBtn', '#refreshDbBackupsBtn', '#cleanupUploadsBtnInline']) {{
          if (!(await visible(selector))) {{
            throw new Error(`配置与维护缺少按钮：${{selector}}`);
          }}
        }}
      }}
      if (group === 'records' && label === '发送记录') {{
        const statusFilter = await page.$eval('#recordStatusFilter', (el) => {{
          const options = Array.from(el.options).map((option) => ({{
            value: option.value,
            label: option.textContent.trim(),
          }}));
          return {{
            value: el.value,
            options,
          }};
        }});
        if (statusFilter.value !== 'failed') {{
          throw new Error(`发送记录默认状态不应是其他值：${{JSON.stringify(statusFilter)}}`);
        }}
        if (JSON.stringify(statusFilter.options) !== JSON.stringify([
          {{ value: 'failed', label: '失败' }},
          {{ value: 'success', label: '成功' }},
        ])) {{
          throw new Error(`发送记录状态选项不正确：${{JSON.stringify(statusFilter)}}`);
        }}
        const filterBox = await page.$eval('#recordTodayFailedFilter', (el) => {{
          const label = el.closest('label');
          const rect = label.getBoundingClientRect();
          const inputRect = el.getBoundingClientRect();
          return {{
            labelWidth: Math.round(rect.width),
            labelHeight: Math.round(rect.height),
            inputWidth: Math.round(inputRect.width),
            inputLeft: Math.round(inputRect.left - rect.left),
          }};
        }});
        if (filterBox.labelWidth < 240 || filterBox.labelHeight < 34) {{
          throw new Error(`今日失败筛选太窄：${{JSON.stringify(filterBox)}}`);
        }}
        if (filterBox.inputWidth < 36 || filterBox.inputWidth > 50 || filterBox.inputLeft < 0) {{
          throw new Error(`今日失败开关样式异常：${{JSON.stringify(filterBox)}}`);
        }}
        const filterText = await page.$eval('#recordTodayFailedFilter', (el) => (el.closest('label')?.querySelector('span')?.textContent || '').trim());
        if (filterText !== '只看今日') {{
          throw new Error(`今日筛选文案不正确：${{filterText}}`);
        }}
      }}
    }}
  }}
  if (errors.length) {{
    throw new Error(errors.join('\\n'));
  }}
  await browser.close();
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        js_path = tmp_path / "desktop_nav_check.js"
        js_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(js_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
@pytest.mark.skipif(not PLAYWRIGHT_DIR.exists(), reason="playwright not installed")
def test_home_schedule_view_centers_today_and_marks_shift_roles(tmp_path):
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["UPLOAD_DIR"] = str(tmp_path / "uploads")
    env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18082"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_http("http://127.0.0.1:18082/health")
        script = f"""
const {{ chromium }} = require({json.dumps(str(PLAYWRIGHT_DIR.as_posix()))});

(async() => {{
  const browser = await chromium.launch({{ headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 700, height: 900 }} }});
  const nowParts = new Intl.DateTimeFormat('en-CA', {{
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }}).formatToParts(new Date()).reduce((result, part) => {{
    if (part.type !== 'literal') result[part.type] = part.value;
    return result;
  }}, {{}});
  const year = Number(nowParts.year);
  const month = Number(nowParts.month);
  const day = Number(nowParts.day);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const prevDay = String(Math.max(1, day - 1));
  const nextDay = String(Math.min(days, day + 1));
  const grid = [
    {{ name: '监控班员', days: {{ [prevDay]: '早', [String(day)]: '中', [nextDay]: '晚' }} }},
    {{ name: '巡查班员', days: {{ [prevDay]: '巡', [String(day)]: '巡', [nextDay]: '巡' }} }},
  ];
  await page.route('**/api/rosters', async (route) => {{
    await route.fulfill({{
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({{ rosters: [{{ year, month, confirmed_at: '2026-09-20T00:00:00', grid }}] }}),
    }});
  }});
  await page.goto('http://127.0.0.1:18082/', {{ waitUntil: 'networkidle' }});
  await page.waitForSelector('#homeScheduleGrid tr:nth-child(3)');

  const activeHomeTab = await page.$eval('#mainSubnav .tab-button.active', (el) => (el.innerText || el.textContent || '').trim());
  if (activeHomeTab !== '排班查看') throw new Error(`首页默认子页错误：${{activeHomeTab}}`);
  const selectedMonth = await page.$eval('#homeRosterSelect', (el) => el.value);
  if (selectedMonth !== `${{year}}-${{String(month).padStart(2, '0')}}`) {{
    throw new Error(`首页默认月份错误：${{selectedMonth}}`);
  }}
  const view = await page.$eval('#homeScheduleView .saved-wrap', (el, currentDay) => {{
    const todayCell = el.querySelector(`th[data-saved-day="${{currentDay}}"]`);
    const wrapperRect = el.getBoundingClientRect();
    const cellRect = todayCell.getBoundingClientRect();
    const monitorCell = el.querySelector('td.saved-monitor-cell');
    const patrolCell = el.querySelector('td.saved-patrol-cell');
    return {{
      scrollLeft: Math.round(el.scrollLeft),
      expectedScrollLeft: Math.round(todayCell.offsetLeft - (el.clientWidth - todayCell.offsetWidth) / 2),
      todayCenter: Math.round((cellRect.left + cellRect.right) / 2),
      wrapperCenter: Math.round((wrapperRect.left + wrapperRect.right) / 2),
      monitorBorder: getComputedStyle(monitorCell, '::after').borderTopColor,
      patrolBorder: getComputedStyle(patrolCell, '::after').borderTopColor,
      monitorCount: el.querySelectorAll('td.saved-monitor-cell').length,
      patrolCount: el.querySelectorAll('td.saved-patrol-cell').length,
    }};
  }}, day);
  if (Math.abs(view.todayCenter - view.wrapperCenter) > 2) {{
    throw new Error(`当天日期没有居中：${{JSON.stringify(view)}}`);
  }}
  if (view.monitorBorder !== 'rgb(249, 115, 22)') {{
    throw new Error(`监控班颜色错误：${{JSON.stringify(view)}}`);
  }}
  if (view.patrolBorder !== 'rgb(37, 99, 235)') {{
    throw new Error(`巡查班颜色错误：${{JSON.stringify(view)}}`);
  }}
  if (view.monitorCount !== 1 || view.patrolCount !== 1) throw new Error(`不应标注非当天班次：${{JSON.stringify(view)}}`);
  await browser.close();
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        js_path = tmp_path / "home_schedule_check.js"
        js_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(js_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(not shutil.which("node"), reason="node not available")
@pytest.mark.skipif(not PLAYWRIGHT_DIR.exists(), reason="playwright not installed")
def test_public_preview_is_default_without_login(tmp_path):
    env = os.environ.copy()
    env["DATA_DIR"] = str(tmp_path / "data")
    env["UPLOAD_DIR"] = str(tmp_path / "uploads")
    env["ADMIN_PASSWORD"] = "secret"
    env["PYTHONUTF8"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18083"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_http("http://127.0.0.1:18083/health")
        script = f"""
const {{ chromium }} = require({json.dumps(str(PLAYWRIGHT_DIR.as_posix()))});

(async() => {{
  const browser = await chromium.launch({{ headless: true }});
  const page = await browser.newPage({{ viewport: {{ width: 700, height: 900 }} }});
  const nowParts = new Intl.DateTimeFormat('en-CA', {{
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }}).formatToParts(new Date()).reduce((result, part) => {{
    if (part.type !== 'literal') result[part.type] = part.value;
    return result;
  }}, {{}});
  const year = Number(nowParts.year);
  const month = Number(nowParts.month);
  const day = Number(nowParts.day);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const prevDay = String(Math.max(1, day - 1));
  const nextDay = String(Math.min(days, day + 1));
  const grid = [
    {{ name: '监控班员', days: {{ [prevDay]: '早', [String(day)]: '中', [nextDay]: '晚' }} }},
    {{ name: '巡查班员', days: {{ [prevDay]: '巡', [String(day)]: '巡', [nextDay]: '巡' }} }},
  ];
  await page.route('**/api/public/rosters', async (route) => {{
    await route.fulfill({{
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({{ rosters: [{{ year, month, confirmed_at: '2026-09-20T00:00:00', grid }}] }}),
    }});
  }});
  await page.goto('http://127.0.0.1:18083/', {{ waitUntil: 'networkidle' }});
  await page.waitForFunction(() => document.querySelectorAll('#rosterGrid tr').length >= 3);
  const title = await page.title();
  if (title !== '排班预览') throw new Error(`默认标题错误：${{title}}`);
  if (await page.locator('input[type="password"]').count()) throw new Error('默认页面仍显示登录框');
  const view = await page.$eval('#tableWrap', (el, currentDay) => {{
    const todayCell = el.querySelector(`th[data-day="${{currentDay}}"]`);
    const wrapperRect = el.getBoundingClientRect();
    const cellRect = todayCell.getBoundingClientRect();
    const monitorCell = el.querySelector('td.monitor-cell');
    const patrolCell = el.querySelector('td.patrol-cell');
    return {{
      todayCenter: Math.round((cellRect.left + cellRect.right) / 2),
      wrapperCenter: Math.round((wrapperRect.left + wrapperRect.right) / 2),
      monitorBorder: getComputedStyle(monitorCell, '::after').borderTopColor,
      patrolBorder: getComputedStyle(patrolCell, '::after').borderTopColor,
      monitorCount: el.querySelectorAll('td.monitor-cell').length,
      patrolCount: el.querySelectorAll('td.patrol-cell').length,
    }};
  }}, day);
  if (Math.abs(view.todayCenter - view.wrapperCenter) > 2) throw new Error(`当天日期没有居中：${{JSON.stringify(view)}}`);
  if (view.monitorBorder !== 'rgb(249, 115, 22)') throw new Error(`监控班颜色错误：${{JSON.stringify(view)}}`);
  if (view.patrolBorder !== 'rgb(37, 99, 235)') throw new Error(`巡查班颜色错误：${{JSON.stringify(view)}}`);
  if (view.monitorCount !== 1 || view.patrolCount !== 1) throw new Error(`不应标注非当天班次：${{JSON.stringify(view)}}`);
  await page.goto('http://127.0.0.1:18083/admin', {{ waitUntil: 'networkidle' }});
  if (!(await page.locator('input[type="password"]').count())) throw new Error('管理后台未保持登录保护');
  await browser.close();
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        js_path = tmp_path / "public_preview_check.js"
        js_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(js_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
