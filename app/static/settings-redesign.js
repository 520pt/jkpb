const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const API_BASE = location.protocol === 'file:'
  ? (new URLSearchParams(location.search).get('api') || 'http://127.0.0.1:8080')
  : '';
const apiUrl = (path) => `${API_BASE}${path}`;

const state = {
  main: 'overview',
  sub: { notify: 'channel', people: 'list', roster: 'import', business: 'warning', ops: 'preview' },
  system: {},
  backups: [],
  personnel: { names: [], people: [] },
  peopleCenter: [],
  notification: {},
  feature: {},
  interaction: {},
  menu: { groups: [], limits: {}, payload: {} },
  commands: [],
  daily: {},
  vacation: {},
  patrol: {},
  tunnel: {},
  rosters: [],
  versions: [],
  records: [],
  todayReminders: {},
  diagnostics: {},
  lightagent: {},
  aibot: {},
  previewMode: 'today',
  activeMenu: 'monitor',
  compareLock: false,
  constructionSites: [],
};

const mainTabs = [
  ['overview', '总览'],
  ['notify', '通知中心'],
  ['people', '人员中心'],
  ['roster', '排班在岗'],
  ['business', '机电预警'],
  ['ops', '系统运维'],
];

const NOTIFY_CHANNEL_META = {
  'wecom-app': {
    label: '企业微信自建应用',
    tab: 'app',
    tabs: new Set(['channel', 'policy', 'app', 'commands', 'simulate']),
    summary: '企业微信自建应用：图文消息使用 news 卡片，点击后查看详情图片。',
  },
  'personal-wechat': {
    label: '个人微信群',
    tab: 'wechat',
    tabs: new Set(['channel', 'policy', 'wechat', 'commands', 'simulate']),
    summary: '个人微信群：仅展示个人微信群登录、群列表和群目标配置。',
  },
  'wecom-bot': {
    label: '企业微信群机器人',
    tab: 'bot',
    tabs: new Set(['channel', 'policy', 'bot']),
    summary: '企业微信群机器人：仅保留 webhook、@ 策略和通知边界说明。',
  },
};

function notifyUiKind(kind) {
  return CHANNEL_BACKEND_TO_UI[kind] || CHANNEL_BACKEND_TO_UI[CHANNEL_UI_TO_BACKEND[kind]] || 'wecom-app';
}

function notifyMeta(kind) {
  return NOTIFY_CHANNEL_META[notifyUiKind(kind)] || NOTIFY_CHANNEL_META['wecom-app'];
}

function notifyVisibleTabs(kind) {
  return notifyMeta(kind).tabs;
}

function notifyDefaultTab(kind) {
  return notifyMeta(kind).tab;
}

function applyNotifyChannelVisibility(kind) {
  const sec = $('notify');
  if (!sec) return;
  const allowed = notifyVisibleTabs(kind);
  $$('.tab', sec).forEach((btn) => {
    const visible = allowed.has(btn.dataset.tab);
    btn.hidden = !visible;
    btn.classList.toggle('channel-hidden', !visible);
  });
  $$('.pane', sec).forEach((pane) => {
    const visible = allowed.has(pane.dataset.content);
    pane.hidden = !visible;
    pane.classList.toggle('channel-hidden', !visible);
  });
}

const compareItems = [
  { time: '08:00', title: '消息通知渠道', oldTarget: 'wecomSettings', newTarget: 'notify' },
  { time: '08:15', title: '微信交互功能', oldTarget: 'featureChannelSettings', newTarget: 'notify' },
  { time: '08:30', title: '人员与驾驶员', oldTarget: 'personnelSettings', newTarget: 'people' },
  { time: '08:45', title: '今日在岗提醒', oldTarget: 'driverSettings', newTarget: 'roster' },
  { time: '09:00', title: '公路巡查预警', oldTarget: 'patrolWarningSettings', newTarget: 'business' },
  { time: '09:15', title: '运维诊断', oldTarget: 'reminderDiagnosticSettings', newTarget: 'ops' },
];

function esc(v) {
  return String(v ?? '').replace(/[&<>\"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;' }[ch]));
}

function jsQuote(value) {
  return `'${String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}



async function api(path, options = {}) {
  const res = await fetch(apiUrl(path), {
    credentials: 'include',
    ...options,
    headers: { ...(options.headers || {}) },
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) throw new Error(data.detail || data.message || text || `HTTP ${res.status}`);
  return data;
}

function toast(message, kind = 'info') {
  const box = $('toast');
  if (!box) return;
  box.textContent = message;
  box.className = `status ${kind}`;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { if (box.textContent === message) box.textContent = ''; }, 3000);
}

function renderShell() {
  const meta = {
    overview: ['总览', '查看系统状态、配置导入导出和备份。'],
    notify: ['通知中心', '配置发送通道、接收人、菜单和测试发送。'],
    people: ['人员中心', '维护权威人员名单、岗位分类和绑定状态。'],
    roster: ['排班在岗', '管理排班导入、今日在岗、监控班和休息提醒。'],
    business: ['机电预警', '管理公路预警、隧道机电、施工图片和模板。'],
    ops: ['系统运维', '查看预览、诊断、记录、状态和文件清理。'],
  }[state.main];
  $('title').textContent = meta[0];
  $('desc').textContent = meta[1];
}

function renderSubnav() {
  const sec = $(state.main);
  if (!sec) return;
  const tabs = $$('.tab', sec).filter((btn) => !btn.hidden);
  const active = tabs.find((btn) => btn.dataset.tab === state.sub[state.main]) || tabs[0];
  $('subnav').innerHTML = tabs.map((btn) => `<button class="${btn === active ? 'active' : ''}" onclick="setTab('${state.main}','${btn.dataset.tab}')">${btn.textContent}</button>`).join('');
}

function renderRail() {
  const items = {
    overview: [
      ['notify', 'channel', '通知通道'],
      ['people', 'list', '人员中心'],
      ['roster', 'import', '排班在岗'],
      ['business', 'warning', '机电预警'],
    ],
    notify: [
      ['notify', 'channel', '通知通道'],
      ['notify', 'app', '自建应用'],
      ['notify', 'commands', '交互菜单'],
      ['notify', 'simulate', '交互测试'],
    ],
    people: [
      ['people', 'list', '人员名单'],
      ['people', 'bind', '已绑定人员'],
      ['people', 'scope', '通知接收人'],
      ['people', 'audit', '是否已设置'],
    ],
    roster: [
      ['roster', 'import', '导入/核对'],
      ['roster', 'imported', '已导入排班'],
      ['roster', 'daily', '今日在岗'],
      ['roster', 'monitor', '监控班提醒'],
      ['roster', 'vacation', '休息/假期'],
    ],
    business: [
      ['business', 'warning', '公路预警'],
      ['business', 'tunnel', '隧道机电'],
      ['business', 'construction', '施工图片'],
      ['business', 'templates', '模板管理'],
    ],
    ops: [
      ['ops', 'preview', '提醒预览'],
      ['ops', 'diagnose', '提醒诊断'],
      ['ops', 'records', '发送记录'],
      ['ops', 'status', '系统状态'],
      ['ops', 'clean', '文件清理'],
    ],
  };
  const html = (items[state.main] || items.overview)
    .map(([main, sub, label]) => `<button class="secondary mini" onclick="jump('${main}','${sub}')">${label}</button>`)
    .join('');
  if ($('quickMap')) $('quickMap').innerHTML = html;
}

function setMain(main) {
  state.main = main;
  $$('.nav').forEach((btn) => btn.classList.toggle('active', btn.dataset.sec === main));
  $$('.sec').forEach((sec) => sec.classList.toggle('active', sec.id === main));
  renderShell();
  renderSubnav();
  renderRail();
  if (main === 'overview') renderOverview();
  if (main === 'notify') renderNotify();
  if (main === 'people') renderPeople();
  if (main === 'roster') renderRoster();
  if (main === 'business') renderBusiness();
  if (main === 'ops') renderOps();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function jump(main, sub) {
  setMain(main);
  if (sub) setTab(main, sub);
}

function setTab(main, tab) {
  if (main === 'notify' && !notifyVisibleTabs(state.notification.sender_type || 'wecom_app').has(tab)) {
    tab = notifyDefaultTab(state.notification.sender_type || 'wecom_app');
  }
  state.sub[main] = tab;
  const sec = $(main);
  if (!sec) return;
  $$('.tab', sec).forEach((btn) => btn.classList.toggle('active', !btn.hidden && btn.dataset.tab === tab));
  $$('.pane', sec).forEach((pane) => pane.classList.toggle('active', !pane.hidden && pane.dataset.content === tab));
  renderSubnav();
}

async function loadAll() {
  const tasks = {
    system: api('/api/system-status'),
    backups: api('/api/config/backups'),
    personnel: api('/api/personnel'),
    peopleCenter: api('/api/people-center'),
    notification: api('/api/notification-config'),
    feature: api('/api/feature-channel-config'),
    interaction: api('/api/wechat-interaction-config'),
    menu: api('/api/wecom-app/menu'),
    commands: api('/api/interaction-commands'),
    daily: api('/api/daily-duty-config'),
    vacation: api('/api/vacation-reminder-config'),
    patrol: api('/api/patrol-warning-config'),
    tunnel: api('/api/tunnel-mechanical/config'),
    rosters: api('/api/rosters'),
    records: api('/api/send-records?limit=50'),
    todayReminders: api('/api/reminders/today'),
    diagnostics: api(`/api/reminders/diagnostics?target_date=${new Date().toISOString().slice(0, 10)}`),
    lightagent: api('/api/lightagent/wechat/status'),
    aibot: api('/api/wecom-aibot/status').catch(() => ({})),
  };
  const entries = Object.entries(tasks);
  await Promise.all(entries.map(async ([key, promise]) => {
    try {
      const value = await promise;
      if (key === 'backups') state.backups = value.backups || [];
      else if (key === 'personnel') state.personnel = value;
      else if (key === 'peopleCenter') state.peopleCenter = value.people || [];
      else if (key === 'notification') state.notification = value.config || {};
      else if (key === 'feature') state.feature = value.config || {};
      else if (key === 'interaction') state.interaction = value.config || {};
      else if (key === 'menu') state.menu = value.menu || {};
      else if (key === 'commands') state.commands = value.commands || [];
      else if (key === 'daily') state.daily = value.config || {};
      else if (key === 'vacation') state.vacation = value.config || {};
      else if (key === 'patrol') state.patrol = value.config || {};
      else if (key === 'tunnel') state.tunnel = value.config || {};
      else if (key === 'rosters') state.rosters = value.rosters || [];
      else if (key === 'records') state.records = value.records || [];
      else if (key === 'todayReminders') state.todayReminders = value;
      else if (key === 'diagnostics') state.diagnostics = value;
      else if (key === 'system') state.system = value;
      else if (key === 'lightagent') state.lightagent = value;
      else if (key === 'aibot') state.aibot = value;
    } catch (error) {
      console.warn(key, error);
    }
  }));
}

function renderOverview() {
  const cards = $$('#overview .g4 .card');
  const sys = state.system || {};
  const values = [
    [sys.overall_status || 'unknown', '系统'],
    [`${sys.rosters_count || 0} 个月`, '排班'],
    [`${sys.monitored_people_count || 0} 人`, '监控班'],
    [`${sys.bound_people_count || 0} 人`, '个人绑定'],
  ];
  cards.forEach((card, idx) => {
    const [value, label] = values[idx] || ['0', ''];
    card.innerHTML = `<span class="pill ${idx === 0 ? (sys.overall_status === 'ok' ? 'ok' : 'warn') : 'info'}">${esc(label)}</span><div style="font-size:28px;font-weight:900">${esc(value)}</div><p>${esc(label)} 实际状态</p>`;
  });
  $('issueCards').innerHTML = [
    ['通知通道', state.notification.sender_type || '未配置', 'ok'],
    ['人员名单', `${state.personnel.names.length} 人`, state.personnel.names.length ? 'ok' : 'warn'],
    ['企业微信菜单', `${(state.menu.groups || []).length} 组`, (state.menu.groups || []).length ? 'ok' : 'warn'],
    ['排班版本', `${state.rosters.length} 个月`, state.rosters.length ? 'ok' : 'warn'],
    ['发送记录', `${state.records.length} 条`, state.records.length ? 'ok' : 'warn'],
  ].map(([label, value, kind]) => `<div class="issue"><div><b>${esc(label)}</b><p>${esc(value)}</p></div><span class="pill ${kind}">${kind === 'ok' ? '正常' : '待处理'}</span></div>`).join('');
  if ($('backupList')) $('backupList').innerHTML = state.backups.length ? state.backups.map((b) => `<div class="issue"><div><b>${esc(b.filename)}</b><p>${esc(b.created_at || '')}</p></div><div>${esc(b.size || '')}</div></div>`).join('') : '<div class="cardnote">暂无备份</div>';
}

function renderPeopleMulti() {
  const selected = new Set(state.personnel.names || []);
  $('peopleCards').innerHTML = (state.personnel.names || []).map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    return `<label class="person"><strong>${esc(name)}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><small>手机号：${esc(person.mention_mobile || '未配置')}</small><label class="check"><input type="checkbox" data-person-name="${esc(name)}" ${selected.has(name) ? 'checked' : ''} onchange="togglePersonName(this)"> 选入权威名单</label></label>`;
  }).join('') || '<div class="cardnote">暂无人员，请先导入排班或手动补名单。</div>';
}

function renderPeopleScope() {
  const names = state.personnel.names || [];
  const selected = new Set(state.notification.wecom_app_target_names || []);
  const targets = state.notification.wecom_app_function_target_names || {};
  const render = (title, key) => {
    const chosen = new Set(targets[key] || []);
    return `<div class="multi"><button class="multi-btn" type="button" onclick="toggleMulti(this)"><span>${title}</span><span>${chosen.size ? `已选 ${chosen.size} 人` : '请选择'}</span></button><div class="multi-panel"><input class="multi-search" placeholder="搜索人员" oninput="filterMulti(this)"><div class="multi-options">${names.map((name) => {
      const person = state.peopleCenter.find((item) => item.name === name) || {};
      const checked = chosen.size ? chosen.has(name) : selected.has(name);
      return `<label class="multi-option"><input type="checkbox" data-scope-key="${esc(key)}" data-scope-name="${esc(name)}" ${checked ? 'checked' : ''}><span>${esc(name)}</span><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span></label>`;
    }).join('')}</div></div><div class="multi-summary">${[...chosen].join('、') || [...selected].join('、') || '未选择接收人'}</div></div>`;
  };
  $('scopePublic').innerHTML = render('公共通知', 'public');
  $('scopeDaily').innerHTML = render('今日在岗', 'daily');
  $('scopeWarning').innerHTML = render('公路预警', 'warning');
}

function renderPeople() {
  renderPeopleMulti();
  renderPeopleScope();
  if ($('peopleList')) $('peopleList').innerHTML = state.peopleCenter.map((person) => {
    const fields = [
      ['企业微信', person.wecom_bound ? '已绑定' : '未绑定', person.wecom_bound ? 'ok' : 'bad', person.wecom_userid || ''],
      ['个人微信群', person.wechat_group_bound ? '已绑定' : '未绑定', person.wechat_group_bound ? 'ok' : 'bad', person.wechat_group_member_name || person.wechat_group_member_id || ''],
      ['手机号', person.mention_mobile ? '已配置' : '未配置', person.mention_mobile ? 'ok' : 'bad', person.mention_mobile || ''],
      ['监控班', person.monitor_configured ? '已配置' : '未配置', person.monitor_enabled ? 'ok' : 'warn', person.monitor_detail || ''],
      ['自定义提醒', person.custom_reminders_enabled ? '已配置' : '未配置', person.custom_reminders_enabled ? 'ok' : 'warn', person.custom_reminders_summary || ''],
      ['休息提醒', person.rest_reminder_enabled ? '已启用' : '未启用', person.rest_reminder_enabled ? 'ok' : 'warn', ''],
      ['机电搭档', person.tunnel_mechanical_partner ? '已设置' : '未设置', person.tunnel_mechanical_partner ? 'ok' : 'bad', person.tunnel_mechanical_partner || ''],
    ].map(([label, text, kind, detail]) => `<div class="people-center-field"><span>${esc(label)}</span><span class="people-status-pill ${kind}">${esc(text)}</span>${detail ? `<span class="people-center-detail">${esc(detail)}</span>` : ''}</div>`).join('');
    return `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><div class="people-center-fields">${fields}</div></div>`;
  }).join('') || '<div class="item">暂无人员；请先导入排班或维护人员名单。</div>';
  const bindTable = $('personCenterList');
  if (bindTable) bindTable.innerHTML = state.peopleCenter.map((person) => `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span><small>${esc(person.wecom_userid || '')}</small></div>`).join('');
}

function renderMenuRows() {
  const activeIndex = { monitor: 0, mech: 1, more: 2 }[state.activeMenu] || 0;
  const group = state.menu.groups?.[activeIndex] || { name: '', items: [] };
  $('menuTitle').textContent = group.name || '未命名';
  $('menuRows').innerHTML = (group.items || []).map((item, idx) => `<div class="menu-row"><input value="${esc(item.name || '')}" oninput="editMenuItem(${activeIndex}, ${idx}, 'name', this.value)"><input value="${esc(item.command || '')}" oninput="editMenuItem(${activeIndex}, ${idx}, 'command', this.value)"><button class="danger mini" onclick="deleteMenuItem(${activeIndex}, ${idx})">删除</button></div>`).join('') || '<div class="card">暂无二级菜单</div>';
  $('cmdTable').innerHTML = '<tr><th>命令</th><th>需要绑定</th><th>能否上菜单</th><th>建议归类</th></tr>' + (state.commands || []).map((item) => {
    const command = item.command || item.name || '';
    const needBind = /绑定|导入排班/.test(command) ? '<span class="pill gray">不强制</span>' : '<span class="pill ok">需要</span>';
    const canMenu = /绑定/.test(command) ? '<span class="pill warn">不建议</span>' : '<span class="pill ok">可以</span>';
    const groupName = /机电|橙色/.test(command) ? '机电预警' : /施工|导入|休息/.test(command) ? '更多查询' : '监控在岗';
    return `<tr><td>${esc(command)}</td><td>${needBind}</td><td>${canMenu}</td><td>${groupName}</td></tr>`;
  }).join('');
}

function renderNotifySupport() {
  const notification = state.notification || {};
  const ui = notifyUiKind(notification.sender_type || 'wecom_app');
  const meta = notifyMeta(ui);
  $('chanBadge').textContent = `当前：${meta.label}`;
  $('chanSummary').innerHTML = `<h4>当前通道说明</h4><p>${esc(meta.summary)}</p><div class="toolbar"><button class="primary" onclick="saveNotificationConfigFromInputs()">保存配置</button><button class="secondary" onclick="testNotificationConfig()">测试发送</button><button class="secondary" onclick="testWecomApp()">测试自建应用</button></div>`;
  $('sendMode').value = notification.send_content_mode || 'both';
  $('isolate').checked = notification.wecom_app_enabled !== false;
  $('wecomAppCorpId').value = notification.wecom_app_corp_id || '';
  $('wecomAppAgentId').value = notification.wecom_app_agent_id || '';
  $('wecomAppSecret').value = notification.wecom_app_secret || '';
  $('wecomAppToken').value = notification.wecom_app_token || '';
  $('wecomAppAesKey').value = notification.wecom_app_encoding_aes_key || '';
  if ($('lightagentStatus')) $('lightagentStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>连接状态</td><td><span class="pill ${state.lightagent?.connected ? 'ok' : 'warn'}">${state.lightagent?.connected ? '已连接' : '未连接'}</span></td></tr><tr><td>登录状态</td><td>${esc(state.lightagent?.login_status || '未知')}</td></tr><tr><td>可发送群</td><td>${esc(state.lightagent?.sendable_room_count || 0)} 个</td></tr><tr><td>二维码</td><td>${esc(state.lightagent?.qr_status || '未知')}</td></tr></table>`;
  if ($('aibotStatus')) $('aibotStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>启用</td><td><span class="pill ${state.aibot?.enabled ? 'ok' : 'warn'}">${state.aibot?.enabled ? '已启用' : '未启用'}</span></td></tr><tr><td>连接</td><td>${esc(state.aibot?.status || '未知')}</td></tr><tr><td>模式</td><td>${esc(state.aibot?.mode || '未知')}</td></tr></table>`;
  $('simPerson').innerHTML = (state.personnel.names || []).map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('') || '<option value="">暂无人员</option>';
  if (!$('simPerson').value && state.personnel.names.length) $('simPerson').value = state.personnel.names[0];
  $('simInput').value = $('simInput').value || '查询今日在岗';
  renderMenuRows();
  renderPublicReceivers();
  applyNotifyChannelVisibility(ui);
  if (state.main === 'notify') renderSubnav();
}

function renderPublicReceivers() {
  const names = state.personnel.names || [];
  const selected = new Set(state.notification.wecom_app_target_names || []);
  $('publicReceivers').innerHTML = `<div class="multi"><button class="multi-btn" type="button" onclick="toggleMulti(this)"><span>公共通知接收人</span><span>${selected.size ? `已选 ${selected.size} 人` : '请选择'}</span></button><div class="multi-panel"><input class="multi-search" placeholder="搜索人员" oninput="filterMulti(this)"><div class="multi-options">${names.map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    const checked = selected.size ? selected.has(name) : !!person.wecom_bound;
    return `<label class="multi-option"><input type="checkbox" data-public-name="${esc(name)}" ${checked ? 'checked' : ''}><span>${esc(name)}</span><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span></label>`;
  }).join('')}</div></div><div class="multi-summary">${[...selected].join('、') || '未选择接收人'}</div></div>`;
}

function renderPreview(mode = 'today') {
  state.previewMode = mode;
  $$('#previewSeg button').forEach((btn) => btn.classList.toggle('active', btn.dataset.mode === mode));
  const summary = state.todayReminders?.summary || {};
  if (mode === 'today') {
    $('previewView').innerHTML = `<div class="news"><div class="img">今日在岗图文</div><div class="txt"><b>今日在岗</b><small>早班：${esc((summary.early || []).join('、') || '无')}｜中班：${esc((summary.middle || []).join('、') || '无')}｜晚班：${esc((summary.night || []).join('、') || '无')}｜明日早班：${esc((summary.tomorrow_early || []).join('、') || '无')}</small></div></div>`;
  } else if (mode === 'tomorrow') {
    $('previewView').innerHTML = `<div class="news"><div class="img">明日在岗图文</div><div class="txt"><b>明日在岗</b><small>${esc(state.todayReminders?.tomorrow_preview || '使用预览接口生成明日预览。')}</small></div></div>`;
  } else {
    const rows = [];
    for (let i = 0; i < 7; i += 1) {
      const day = new Date();
      day.setDate(day.getDate() + i);
      rows.push(`<tr><td>${day.toISOString().slice(0, 10)}</td><td><span class="pill ${i === 0 ? 'ok' : i === 1 ? 'info' : 'gray'}">${i === 0 ? '今日' : i === 1 ? '明日' : '近7天'}</span></td><td>排班预览</td></tr>`);
    }
    $('previewView').innerHTML = `<table><tr><th>日期</th><th>状态</th><th>人员</th></tr>${rows.join('')}</table>`;
  }
}

function renderRosterSupport() {
  const rosters = state.rosters || [];
  $('rosterVersionsList').innerHTML = rosters.length ? '<div class="cardnote">点击“查看版本”可展开版本列表。</div>' : '<div class="cardnote">暂无已导入排班。</div>';
  $('dailyEnabled').checked = !!state.daily.enabled;
  $('dailyReminderTime').value = state.daily.reminder_time || '07:50';
  $('dailySendMode').value = state.daily.send_content_mode || 'both';
  $('vacationEnabled').checked = !!state.vacation.enabled;
}

function renderBusinessSupport() {
  $('patrolUsername').value = state.patrol.username || '';
  $('patrolPollInterval').value = state.patrol.poll_interval_minutes || 10;
  $('tunnelBaseUrl').value = state.tunnel.base_url || '';
  $('tunnelUsername').value = state.tunnel.username || '';
  $('tunnelPassword').value = state.tunnel.password || '';
}

function renderOpsSupport() {
  $('previewView').innerHTML = $('previewView').innerHTML || '';
  $('recordStatus').value = '';
  $('recordKind').value = '';
  const checks = state.system?.checks || [];
  if (checks.length) {
    $('statusCards')?.remove();
  }
}

function renderPeopleSummaryCards() {
  if ($('peopleList')) $('peopleList').innerHTML = state.peopleCenter.map((person) => {
    const fields = [
      ['企业微信', person.wecom_bound ? '已绑定' : '未绑定', person.wecom_bound ? 'ok' : 'bad', person.wecom_userid || ''],
      ['个人微信群', person.wechat_group_bound ? '已绑定' : '未绑定', person.wechat_group_bound ? 'ok' : 'warn', person.wechat_group_member_name || person.wechat_group_member_id || ''],
      ['手机号', person.mention_mobile ? '已配置' : '未配置', person.mention_mobile ? 'ok' : 'bad', person.mention_mobile || ''],
      ['监控班', person.monitor_configured ? '已配置' : '未配置', person.monitor_enabled ? 'ok' : 'warn', person.monitor_detail || ''],
      ['自定义提醒', person.custom_reminders_enabled ? '已配置' : '未配置', person.custom_reminders_enabled ? 'ok' : 'warn', person.custom_reminders_summary || ''],
      ['休息提醒', person.rest_reminder_enabled ? '已启用' : '未启用', person.rest_reminder_enabled ? 'ok' : 'warn', ''],
      ['机电搭档', person.tunnel_mechanical_partner ? '已设置' : '未设置', person.tunnel_mechanical_partner ? 'ok' : 'bad', person.tunnel_mechanical_partner || ''],
    ].map(([label, text, kind, detail]) => `<div class="people-center-field"><span>${esc(label)}</span><span class="people-status-pill ${kind}">${esc(text)}</span>${detail ? `<span class="people-center-detail">${esc(detail)}</span>` : ''}</div>`).join('');
    return `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><div class="people-center-fields">${fields}</div></div>`;
  }).join('');
}

function renderNotifySummary() {
  $('sendMode').value = state.notification.send_content_mode || 'both';
  $('isolate').checked = state.notification.wecom_app_enabled !== false;
  $('wecomAppCorpId').value = state.notification.wecom_app_corp_id || '';
  $('wecomAppAgentId').value = state.notification.wecom_app_agent_id || '';
  $('wecomAppSecret').value = state.notification.wecom_app_secret || '';
  $('wecomAppToken').value = state.notification.wecom_app_token || '';
  $('wecomAppAesKey').value = state.notification.wecom_app_encoding_aes_key || '';
  $('chanBadge').textContent = `当前：${state.notification.sender_type || '未配置'}`;
  $('chanSummary').innerHTML = `<h4>当前通道说明</h4><p>当前通知通道：<b>${esc(state.notification.sender_type || '未配置')}</b>。若启用自建应用，菜单、交互和通知都应以它为唯一通道。</p><div class="toolbar"><button class="primary" onclick="saveNotificationConfigFromInputs()">保存配置</button><button class="secondary" onclick="testNotificationConfig()">测试发送</button><button class="secondary" onclick="testWecomApp()">测试自建应用</button></div>`;
  $('lightagentStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>连接状态</td><td><span class="pill ${state.lightagent?.connected ? 'ok' : 'warn'}">${state.lightagent?.connected ? '已连接' : '未连接'}</span></td></tr><tr><td>登录状态</td><td>${esc(state.lightagent?.login_status || '未知')}</td></tr><tr><td>可发送群</td><td>${esc(state.lightagent?.sendable_room_count || 0)} 个</td></tr><tr><td>二维码</td><td>${esc(state.lightagent?.qr_status || '未知')}</td></tr></table>`;
  $('aibotStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>启用</td><td><span class="pill ${state.aibot?.enabled ? 'ok' : 'warn'}">${state.aibot?.enabled ? '已启用' : '未启用'}</span></td></tr><tr><td>连接</td><td>${esc(state.aibot?.status || '未知')}</td></tr><tr><td>模式</td><td>${esc(state.aibot?.mode || '未知')}</td></tr></table>`;
  $$('#simPerson option').forEach((opt) => opt.remove());
  $('simPerson').innerHTML = (state.personnel.names || []).map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
  if (!$('simPerson').value && state.personnel.names.length) $('simPerson').value = state.personnel.names[0];
  $('simInput').value = $('simInput').value || '查询今日在岗';
  renderPublicReceivers();
  renderMenuRows();
}

function renderPeopleScope() {
  const selected = new Set(state.notification.wecom_app_target_names || []);
  const names = state.personnel.names || [];
  const targets = state.notification.wecom_app_function_target_names || {};
  const render = (title, key) => {
    const chosen = new Set(targets[key] || []);
    return `<div class="multi"><button class="multi-btn" type="button" onclick="toggleMulti(this)"><span>${title}</span><span>${chosen.size ? `已选 ${chosen.size} 人` : '请选择'}</span></button><div class="multi-panel"><input class="multi-search" placeholder="搜索人员" oninput="filterMulti(this)"><div class="multi-options">${names.map((name) => {
      const person = state.peopleCenter.find((item) => item.name === name) || {};
      const checked = chosen.size ? chosen.has(name) : selected.has(name);
      return `<label class="multi-option"><input type="checkbox" data-scope-key="${esc(key)}" data-scope-name="${esc(name)}" ${checked ? 'checked' : ''}><span>${esc(name)}</span><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span></label>`;
    }).join('')}</div></div><div class="multi-summary">${[...chosen].join('、') || [...selected].join('、') || '未选择接收人'}</div></div>`;
  };
  $('scopePublic').innerHTML = render('公共通知', 'public');
  $('scopeDaily').innerHTML = render('今日在岗', 'daily');
  $('scopeWarning').innerHTML = render('公路预警', 'warning');
}

function renderNotify() {
  renderNotifySummary();
  renderPublicReceivers();
  renderMenuRows();
  renderPreview(state.previewMode);
  setTimeout(() => renderSubnav(), 0);
}

function renderPeople() {
  renderPeopleSummaryCards();
  renderPeopleScope();
  const saveBtn = $('#people .head .primary');
  if (saveBtn) saveBtn.onclick = savePersonnel;
  const bindTable = $('personCenterList');
  if (bindTable) bindTable.innerHTML = state.peopleCenter.map((person) => `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span><small>${esc(person.wecom_userid || '')}</small></div>`).join('');
}

function renderPeopleCards() {
  const selected = new Set(state.personnel.names || []);
  $('peopleCards').innerHTML = (state.personnel.names || []).map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    return `<label class="person"><strong>${esc(name)}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><small>手机号：${esc(person.mention_mobile || '未配置')}</small><label class="check"><input type="checkbox" data-person-name="${esc(name)}" ${selected.has(name) ? 'checked' : ''} onchange="togglePersonName(this)"> 选入权威名单</label></label>`;
  }).join('') || '<div class="cardnote">暂无人员，请先导入排班或手动补名单。</div>';
}

function renderRoster() {
  const latest = state.rosters[0] || {};
  const cards = $$('#roster .pane[data-content="import"] .card');
  if (cards[0]) {
    cards[0].innerHTML = `<h4>网页导入</h4><p>上传排班图片 → OCR/模板识别 → 人工核对 → 导入。</p><input id="rosterUploadFile" type="file" accept="image/*"><div class="toolbar" style="margin-top:10px"><button class="primary" onclick="uploadRoster()">上传排班图片</button></div>`;
  }
  if (cards[1]) {
    cards[1].innerHTML = `<h4>企业微信导入</h4><p>菜单“更多查询 → 导入排班”后，5 分钟内发送图片才会触发，避免普通图片误导入。</p><button class="secondary" onclick="setSim('导入排班');jump('notify','simulate');simulate()">打开预览</button>`;
  }
  const imported = $$('#roster .pane[data-content="imported"] table tr');
  if (imported.length > 1) {
    const rows = state.rosters.map((roster) => `<tr><td>${esc(roster.year)}年${esc(roster.month)}月</td><td>${esc(roster.confirmed_at || roster.imported_at || '')}</td><td>${esc(roster.people_count || roster.grid?.length || '')}</td><td><span class="pill ${roster.is_latest ? 'ok' : 'gray'}">${roster.is_latest ? '当前最新' : '历史'}</span></td><td><button class="secondary mini" onclick="loadRosterVersions(${roster.year}, ${roster.month})">查看版本</button></td></tr>`).join('');
    imported[0].parentElement.innerHTML = `<tr><th>月份</th><th>导入时间</th><th>人员数</th><th>状态</th><th>操作</th></tr>${rows}`;
  }
  $('previewView').innerHTML = `<div class="news"><div class="img">今日在岗图文</div><div class="txt"><b>今日在岗</b><small>早班：${esc((state.todayReminders.summary?.early || []).join('、') || '无')}｜中班：${esc((state.todayReminders.summary?.middle || []).join('、') || '无')}｜晚班：${esc((state.todayReminders.summary?.night || []).join('、') || '无')}｜明日早班：${esc((state.todayReminders.summary?.tomorrow_early || []).join('、') || '无')}</small></div></div>`;
  $('rosterVersionsList').innerHTML = latest.year ? '<div class="cardnote">点击“查看版本”可展开版本列表。</div>' : '<div class="cardnote">暂无已导入排班。</div>';
  $('#dailyEnabled').checked = !!state.daily.enabled;
  $('#dailyReminderTime').value = state.daily.reminder_time || '07:50';
  $('#dailySendMode').value = state.daily.send_content_mode || 'both';
  $('#vacationEnabled').checked = !!state.vacation.enabled;
  $('vacationStartTemplate').value = (state.vacation.start_message_templates || []).join('\n');
}

function renderBusiness() {
  const warnCards = $$('#business .pane[data-content="warning"] .card');
  if (warnCards[0]) warnCards[0].innerHTML = `<h4>数据源连接</h4><div class="form"><label>平台账号</label><input id="patrolUsername" value="${esc(state.patrol.username || '')}"></div><div class="form"><label>轮询间隔</label><input id="patrolPollInterval" type="number" value="${esc(state.patrol.poll_interval_minutes || 10)}"></div><div class="form"><label>抖动</label><select id="patrolJitter"><option>保留</option><option>关闭</option></select></div><div class="toolbar"><button class="primary" onclick="savePatrol()">保存配置</button><button class="secondary" onclick="testPatrol()">测试查询</button><button class="secondary" onclick="sendPatrolTest('start')">发送开始提醒</button><button class="secondary" onclick="sendPatrolTest('end')">发送结束提醒</button></div>`;
  if (warnCards[1]) warnCards[1].innerHTML = `<h4>提醒策略</h4><label class="check"><input checked> 预警开始发送一次</label><label class="check"><input checked> 预警结束发送一次</label><label class="check"><input checked> 历史预警窗口拦截</label><label class="check"><input checked> 橙色预警巡查记录查询</label>`;
  const tunnelCards = $$('#business .pane[data-content="tunnel"] .card');
  if (tunnelCards[0]) tunnelCards[0].innerHTML = `<h4>隧道机电账号</h4><div class="form"><label>基础地址</label><input id="tunnelBaseUrl" value="${esc(state.tunnel.base_url || '')}"></div><div class="form"><label>账号</label><input id="tunnelUsername" value="${esc(state.tunnel.username || '')}"></div><div class="form"><label>密码</label><input id="tunnelPassword" type="password" value="${esc(state.tunnel.password || '')}"></div><div class="toolbar"><button class="primary" onclick="saveTunnel()">保存配置</button><button class="secondary" onclick="testTunnelLogin()">测试登录</button></div>`;
  if (tunnelCards[1]) tunnelCards[1].innerHTML = `<h4>今日机电录入</h4><p>点击“录入今日机电” → 生成确认信息 → 回复确认/1 → 录入并发送图文确认。</p><button class="secondary" onclick="setSim('录入今日机电');jump('notify','simulate');simulate()">打开预览</button>`;
}

function renderOps() {
  $('previewView').innerHTML = buildPreview(state.previewMode);
  const checks = state.system.checks || [];
  const statusPane = $$('#ops .pane[data-content="status"] .g3 .card');
  if (statusPane.length) {
    const items = checks.length ? checks : [
      ['应用', state.system.overall_status || 'unknown', 'FastAPI / 调度器正常'],
      ['数据库', state.system.db_status || 'unknown', 'WAL + busy_timeout'],
      ['通知', state.system.notification_status || 'unknown', '自建应用 / 机器人 / 个人微信群'],
    ];
    statusPane.forEach((card, idx) => {
      const row = items[idx];
      if (!row) return;
      card.innerHTML = `<h4>${esc(row[0])}</h4><span class="pill ok">${esc(row[1])}</span><p>${esc(row[2])}</p>`;
    });
  }
  const diagRows = (state.diagnostics.items || []).map((item) => `<tr><td>${esc(item.date || '')}</td><td>${esc(item.title || item.name || '')}</td><td><span class="pill ${item.status === 'ok' ? 'ok' : item.status === 'warn' ? 'warn' : 'bad'}">${esc(item.status || '')}</span></td><td>${esc(item.reason || item.detail || '')}</td></tr>`).join('');
  if ($$('#ops .pane[data-content="diagnose"] table tr').length) {
    $$('#ops .pane[data-content="diagnose"] table')[0].innerHTML = `<tr><th>日期</th><th>提醒</th><th>结果</th><th>原因</th></tr>${diagRows}`;
  }
  const recordRows = state.records.map((item) => `<tr><td>${esc(item.created_at || item.send_at || '')}</td><td>${esc(item.kind || '')}</td><td>${esc(item.target || '')}</td><td><span class="pill ${item.status === 'success' ? 'ok' : 'bad'}">${esc(item.status || '')}</span></td><td><button class="secondary mini" onclick="resendRecord(${item.id})">补发</button></td></tr>`).join('');
  if ($$('#ops .pane[data-content="records"] table tr').length) {
    $$('#ops .pane[data-content="records"] table')[0].innerHTML = `<tr><th>时间</th><th>类型</th><th>目标</th><th>状态</th><th>操作</th></tr>${recordRows}`;
  }
  const cleanCard = $$('#ops .pane[data-content="clean"] .card')[0];
  if (cleanCard) cleanCard.querySelectorAll('button')[0].onclick = cleanupUploads;
}

function buildPreview(mode) {
  const summary = state.todayReminders.summary || {};
  if (mode === 'today') {
    return `<div class="news"><div class="img">今日在岗图文</div><div class="txt"><b>今日在岗</b><small>早班：${esc((summary.early || []).join('、') || '无')}｜中班：${esc((summary.middle || []).join('、') || '无')}｜晚班：${esc((summary.night || []).join('、') || '无')}｜明日早班：${esc((summary.tomorrow_early || []).join('、') || '无')}</small></div></div>`;
  }
  if (mode === 'tomorrow') {
    return `<div class="news"><div class="img">明日在岗图文</div><div class="txt"><b>明日在岗</b><small>${esc(state.todayReminders.tomorrow_preview || '使用预览接口生成明日预览。')}</small></div></div>`;
  }
  const rows = [];
  for (let i = 0; i < 7; i += 1) {
    const day = new Date();
    day.setDate(day.getDate() + i);
    rows.push(`<tr><td>${day.toISOString().slice(0, 10)}</td><td><span class="pill ${i === 0 ? 'ok' : i === 1 ? 'info' : 'gray'}">${i === 0 ? '今日' : i === 1 ? '明日' : '近7天'}</span></td><td>排班预览</td></tr>`);
  }
  return `<table><tr><th>日期</th><th>状态</th><th>人员</th></tr>${rows.join('')}</table>`;
}

function renderPreview(mode = 'today') {
  state.previewMode = mode;
  $$('#previewSeg button').forEach((btn) => btn.classList.toggle('active', btn.dataset.mode === mode));
  $('previewView').innerHTML = buildPreview(mode);
}

function renderMenu() {
  const idx = { monitor: 0, mech: 1, more: 2 }[state.activeMenu] || 0;
  const group = state.menu.groups?.[idx] || { name: '', items: [] };
  $('menuTitle').textContent = group.name || '未命名';
  if ($('menuRows')) $('menuRows').innerHTML = (group.items || []).map((item, i) => `<div class="menu-row"><input value="${esc(item.name || '')}" oninput="editMenuItem(${idx}, ${i}, 'name', this.value)"><input value="${esc(item.command || '')}" oninput="editMenuItem(${idx}, ${i}, 'command', this.value)"><button class="danger mini" onclick="deleteMenuItem(${idx}, ${i})">删除</button></div>`).join('') || '<div class="card">暂无二级菜单</div>';
  if ($('cmdTable')) $('cmdTable').innerHTML = '<tr><th>命令</th><th>需要绑定</th><th>能否上菜单</th><th>建议归类</th></tr>' + (state.commands || []).map((item) => {
    const command = item.command || item.name || '';
    const needBind = /绑定|导入排班/.test(command) ? '<span class="pill gray">不强制</span>' : '<span class="pill ok">需要</span>';
    const canMenu = /绑定/.test(command) ? '<span class="pill warn">不建议</span>' : '<span class="pill ok">可以</span>';
    const groupName = /机电|橙色/.test(command) ? '机电预警' : /施工|导入|休息/.test(command) ? '更多查询' : '监控在岗';
    return `<tr><td>${esc(command)}</td><td>${needBind}</td><td>${canMenu}</td><td>${groupName}</td></tr>`;
  }).join('');
  $('simPerson').innerHTML = (state.personnel.names || []).map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('') || '<option value="">暂无人员</option>';
  if (!$('simPerson').value && state.personnel.names.length) $('simPerson').value = state.personnel.names[0];
  $('simInput').value = $('simInput').value || '查询今日在岗';
}

function renderNotifySupport() {
  $('sendMode').value = state.notification.send_content_mode || 'both';
  $('isolate').checked = state.notification.wecom_app_enabled !== false;
  $('wecomAppCorpId').value = state.notification.wecom_app_corp_id || '';
  $('wecomAppAgentId').value = state.notification.wecom_app_agent_id || '';
  $('wecomAppSecret').value = state.notification.wecom_app_secret || '';
  $('wecomAppToken').value = state.notification.wecom_app_token || '';
  $('wecomAppAesKey').value = state.notification.wecom_app_encoding_aes_key || '';
  const ui = notifyUiKind(state.notification.sender_type || 'wecom_app');
  const meta = notifyMeta(ui);
  $('chanBadge').textContent = `当前：${meta.label}`;
  $('chanSummary').innerHTML = `<h4>当前通道说明</h4><p>${esc(meta.summary)}</p><div class="toolbar"><button class="primary" onclick="saveNotificationConfigFromInputs()">保存配置</button><button class="secondary" onclick="testNotificationConfig()">测试发送</button><button class="secondary" onclick="testWecomApp()">测试自建应用</button></div>`;
  if ($('lightagentStatus')) $('lightagentStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>连接状态</td><td><span class="pill ${state.lightagent?.connected ? 'ok' : 'warn'}">${state.lightagent?.connected ? '已连接' : '未连接'}</span></td></tr><tr><td>登录状态</td><td>${esc(state.lightagent?.login_status || '未知')}</td></tr><tr><td>可发送群</td><td>${esc(state.lightagent?.sendable_room_count || 0)} 个</td></tr><tr><td>二维码</td><td>${esc(state.lightagent?.qr_status || '未知')}</td></tr></table>`;
  if ($('aibotStatus')) $('aibotStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>启用</td><td><span class="pill ${state.aibot?.enabled ? 'ok' : 'warn'}">${state.aibot?.enabled ? '已启用' : '未启用'}</span></td></tr><tr><td>连接</td><td>${esc(state.aibot?.status || '未知')}</td></tr><tr><td>模式</td><td>${esc(state.aibot?.mode || '未知')}</td></tr></table>`;
  applyNotifyChannelVisibility(ui);
  if (state.main === 'notify') renderSubnav();
  if (state.main === 'notify') setTab('notify', state.sub.notify || notifyDefaultTab(ui));
  $('simInput').value = $('simInput').value || '查询今日在岗';
}

function renderPublicReceivers() {
  const names = state.personnel.names || [];
  const selected = new Set(state.notification.wecom_app_target_names || []);
  $('publicReceivers').innerHTML = `<div class="multi"><button class="multi-btn" type="button" onclick="toggleMulti(this)"><span>公共通知接收人</span><span>${selected.size ? `已选 ${selected.size} 人` : '请选择'}</span></button><div class="multi-panel"><input class="multi-search" placeholder="搜索人员" oninput="filterMulti(this)"><div class="multi-options">${names.map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    const checked = selected.size ? selected.has(name) : !!person.wecom_bound;
    return `<label class="multi-option"><input type="checkbox" data-public-name="${esc(name)}" ${checked ? 'checked' : ''}><span>${esc(name)}</span><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span></label>`;
  }).join('')}</div></div><div class="multi-summary">${[...selected].join('、') || '未选择接收人'}</div></div>`;
}

function renderPeopleSelection() {
  const selected = new Set(state.personnel.names || []);
  $('peopleCards').innerHTML = (state.personnel.names || []).map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    return `<div class="person"><strong>${esc(name)}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><small>手机号：${esc(person.mention_mobile || '未配置')}</small><label class="check"><input type="checkbox" data-person-name="${esc(name)}" ${selected.has(name) ? 'checked' : ''} onchange="togglePersonName(this)"> 选入权威名单</label><div class="toolbar" style="margin-top:8px"><button class="secondary mini" onclick="editPerson(${jsQuote(name)})">编辑</button><button class="danger mini" onclick="deletePersonnel(${jsQuote(name)})">删除</button></div></div>`;
  }).join('') || '<div class="cardnote">暂无人员，请先导入排班或手动补名单。</div>';
}

function renderPeople() {
  renderPeopleSelection();
  renderPeopleScope();
  if ($('peopleList')) $('peopleList').innerHTML = state.peopleCenter.map((person) => {
    const fields = [
      ['企业微信', person.wecom_bound ? '已绑定' : '未绑定', person.wecom_bound ? 'ok' : 'bad', person.wecom_userid || ''],
      ['个人微信群', person.wechat_group_bound ? '已绑定' : '未绑定', person.wechat_group_bound ? 'ok' : 'warn', person.wechat_group_member_name || person.wechat_group_member_id || ''],
      ['手机号', person.mention_mobile ? '已配置' : '未配置', person.mention_mobile ? 'ok' : 'bad', person.mention_mobile || ''],
      ['监控班', person.monitor_configured ? '已配置' : '未配置', person.monitor_enabled ? 'ok' : 'warn', person.monitor_detail || ''],
      ['自定义提醒', person.custom_reminders_enabled ? '已配置' : '未配置', person.custom_reminders_enabled ? 'ok' : 'warn', person.custom_reminders_summary || ''],
      ['休息提醒', person.rest_reminder_enabled ? '已启用' : '未启用', person.rest_reminder_enabled ? 'ok' : 'warn', ''],
      ['机电搭档', person.tunnel_mechanical_partner ? '已设置' : '未设置', person.tunnel_mechanical_partner ? 'ok' : 'bad', person.tunnel_mechanical_partner || ''],
    ].map(([label, text, kind, detail]) => `<div class="people-center-field"><span>${esc(label)}</span><span class="people-status-pill ${kind}">${esc(text)}</span>${detail ? `<span class="people-center-detail">${esc(detail)}</span>` : ''}</div>`).join('');
    return `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><div class="people-center-fields">${fields}</div><div class="toolbar" style="margin-top:8px"><button class="secondary mini" onclick="editPerson(${jsQuote(person.name || '')})">编辑</button><button class="danger mini" onclick="deletePersonnel(${jsQuote(person.name || '')})">删除</button></div></div>`;
  }).join('');
}

function renderRoster() {
  const latest = state.rosters[0] || {};
  const cards = $$('#roster .pane[data-content="import"] .card');
  if (cards[0]) cards[0].innerHTML = `<h4>网页导入</h4><p>上传排班图片 → OCR/模板识别 → 人工核对 → 导入。</p><input id="rosterUploadFile" type="file" accept="image/*"><div class="toolbar" style="margin-top:10px"><button class="primary" onclick="uploadRoster()">上传排班图片</button></div>`;
  if (cards[1]) cards[1].innerHTML = `<h4>企业微信导入</h4><p>菜单“更多查询 → 导入排班”后，5 分钟内发送图片才会触发，避免普通图片误导入。</p><button class="secondary" onclick="setSim('导入排班');jump('notify','simulate');simulate()">打开预览</button>`;
  const importedTable = $$('#roster .pane[data-content="imported"] table')[0];
  if (importedTable) importedTable.innerHTML = `<tr><th>月份</th><th>导入时间</th><th>人员数</th><th>状态</th><th>操作</th></tr>${(state.rosters || []).map((roster) => `<tr><td>${esc(roster.year)}年${esc(roster.month)}月</td><td>${esc(roster.confirmed_at || roster.imported_at || '')}</td><td>${esc(roster.people_count || roster.grid?.length || '')}</td><td><span class="pill ${roster.is_latest ? 'ok' : 'gray'}">${roster.is_latest ? '当前最新' : '历史'}</span></td><td><button class="secondary mini" onclick="loadRosterVersions(${roster.year}, ${roster.month})">查看版本</button></td></tr>`).join('')}`;
  $('rosterVersionsList').innerHTML = latest.year ? '<div class="cardnote">点击“查看版本”可展开版本列表。</div>' : '<div class="cardnote">暂无已导入排班。</div>';
  $('dailyEnabled').checked = !!state.daily.enabled;
  $('dailyReminderTime').value = state.daily.reminder_time || '07:50';
  $('dailySendMode').value = state.daily.send_content_mode || 'both';
  $('vacationEnabled').checked = !!state.vacation.enabled;
  $('vacationStartTemplate').value = (state.vacation.start_message_templates || []).join('\n');
  if ($('previewView')) $('previewView').innerHTML = buildPreview(state.previewMode);
}

function renderBusiness() {
  const warningCards = $$('#business .pane[data-content="warning"] .card');
  if (warningCards[0]) warningCards[0].innerHTML = `<h4>数据源连接</h4><div class="form"><label>平台账号</label><input id="patrolUsername" value="${esc(state.patrol.username || '')}"></div><div class="form"><label>轮询间隔</label><input id="patrolPollInterval" type="number" value="${esc(state.patrol.poll_interval_minutes || 10)}"></div><div class="form"><label>抖动</label><select id="patrolJitter"><option>保留</option><option>关闭</option></select></div><div class="toolbar"><button class="primary" onclick="savePatrol()">保存配置</button><button class="secondary" onclick="testPatrol()">测试查询</button><button class="secondary" onclick="sendPatrolTest('start')">发送开始提醒</button><button class="secondary" onclick="sendPatrolTest('end')">发送结束提醒</button></div>`;
  if (warningCards[1]) warningCards[1].innerHTML = `<h4>提醒策略</h4><label class="check"><input checked> 预警开始发送一次</label><label class="check"><input checked> 预警结束发送一次</label><label class="check"><input checked> 历史预警窗口拦截</label><label class="check"><input checked> 橙色预警巡查记录查询</label>`;
  const tunnelCards = $$('#business .pane[data-content="tunnel"] .card');
  if (tunnelCards[0]) tunnelCards[0].innerHTML = `<h4>隧道机电账号</h4><div class="form"><label>基础地址</label><input id="tunnelBaseUrl" value="${esc(state.tunnel.base_url || '')}"></div><div class="form"><label>账号</label><input id="tunnelUsername" value="${esc(state.tunnel.username || '')}"></div><div class="form"><label>密码</label><input id="tunnelPassword" type="password" value="${esc(state.tunnel.password || '')}"></div><div class="toolbar"><button class="primary" onclick="saveTunnel()">保存配置</button><button class="secondary" onclick="testTunnelLogin()">测试登录</button></div>`;
  if (tunnelCards[1]) tunnelCards[1].innerHTML = `<h4>今日机电录入</h4><p>点击“录入今日机电” → 生成确认信息 → 回复确认/1 → 录入并发送图文确认。</p><button class="secondary" onclick="setSim('录入今日机电');jump('notify','simulate');simulate()">打开预览</button>`;
}

function buildPreview(mode) {
  const summary = state.todayReminders.summary || {};
  if (mode === 'today') {
    return `<div class="news"><div class="img">今日在岗图文</div><div class="txt"><b>今日在岗</b><small>早班：${esc((summary.early || []).join('、') || '无')}｜中班：${esc((summary.middle || []).join('、') || '无')}｜晚班：${esc((summary.night || []).join('、') || '无')}｜明日早班：${esc((summary.tomorrow_early || []).join('、') || '无')}</small></div></div>`;
  }
  if (mode === 'tomorrow') {
    return `<div class="news"><div class="img">明日在岗图文</div><div class="txt"><b>明日在岗</b><small>${esc(state.todayReminders.tomorrow_preview || '使用预览接口生成明日预览。')}</small></div></div>`;
  }
  const rows = [];
  for (let i = 0; i < 7; i += 1) {
    const day = new Date();
    day.setDate(day.getDate() + i);
    rows.push(`<tr><td>${day.toISOString().slice(0, 10)}</td><td><span class="pill ${i === 0 ? 'ok' : i === 1 ? 'info' : 'gray'}">${i === 0 ? '今日' : i === 1 ? '明日' : '近7天'}</span></td><td>排班预览</td></tr>`);
  }
  return `<table><tr><th>日期</th><th>状态</th><th>人员</th></tr>${rows.join('')}</table>`;
}

function renderOps() {
  $('previewView').innerHTML = buildPreview(state.previewMode);
  const checks = state.system.checks || [];
  const statusCards = $$('#ops .pane[data-content="status"] .g3 .card');
  if (statusCards.length) {
    const items = checks.length ? checks : [
      ['应用', state.system.overall_status || 'unknown', 'FastAPI / 调度器正常'],
      ['数据库', state.system.db_status || 'unknown', 'WAL + busy_timeout'],
      ['通知', state.system.notification_status || 'unknown', '自建应用 / 机器人 / 个人微信群'],
    ];
    statusCards.forEach((card, idx) => {
      const item = items[idx];
      if (!item) return;
      card.innerHTML = `<h4>${esc(item[0])}</h4><span class="pill ok">${esc(item[1])}</span><p>${esc(item[2])}</p>`;
    });
  }
  const diagTable = $$('#ops .pane[data-content="diagnose"] table')[0];
  if (diagTable) diagTable.innerHTML = `<tr><th>日期</th><th>提醒</th><th>结果</th><th>原因</th></tr>${(state.diagnostics.items || []).map((item) => `<tr><td>${esc(item.date || '')}</td><td>${esc(item.title || item.name || '')}</td><td><span class="pill ${item.status === 'ok' ? 'ok' : item.status === 'warn' ? 'warn' : 'bad'}">${esc(item.status || '')}</span></td><td>${esc(item.reason || item.detail || '')}</td></tr>`).join('')}`;
  const recordTable = $$('#ops .pane[data-content="records"] table')[0];
  if (recordTable) recordTable.innerHTML = `<tr><th>时间</th><th>类型</th><th>目标</th><th>状态</th><th>操作</th></tr>${state.records.map((item) => `<tr><td>${esc(item.created_at || item.send_at || '')}</td><td>${esc(item.kind || '')}</td><td>${esc(item.target || '')}</td><td><span class="pill ${item.status === 'success' ? 'ok' : 'bad'}">${esc(item.status || '')}</span></td><td><button class="secondary mini" onclick="resendRecord(${item.id})">补发</button></td></tr>`).join('')}`;
}

function renderPeopleSelection() {
  const selected = new Set(state.personnel.names || []);
  $('peopleCards').innerHTML = (state.personnel.names || []).map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    return `<label class="person"><strong>${esc(name)}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><small>手机号：${esc(person.mention_mobile || '未配置')}</small><label class="check"><input type="checkbox" data-person-name="${esc(name)}" ${selected.has(name) ? 'checked' : ''} onchange="togglePersonName(this)"> 选入权威名单</label></label>`;
  }).join('') || '<div class="cardnote">暂无人员，请先导入排班或手动补名单。</div>';
}

async function reloadAll() {
  await loadAll();
  setMain(state.main);
  renderNotifySupport();
  renderPeopleSelection();
  renderPeopleScope();
  renderRoster();
  renderBusiness();
  renderOps();
  renderPreview(state.previewMode);
  renderRail();
}

function renderNotify() {
  renderNotifySupport();
  renderPublicReceivers();
  renderMenuRows();
  renderPreview(state.previewMode);
}

function renderPublicReceivers() {
  const names = state.personnel.names || [];
  const selected = new Set(state.notification.wecom_app_target_names || []);
  $('publicReceivers').innerHTML = `<div class="multi"><button class="multi-btn" type="button" onclick="toggleMulti(this)"><span>公共通知接收人</span><span>${selected.size ? `已选 ${selected.size} 人` : '请选择'}</span></button><div class="multi-panel"><input class="multi-search" placeholder="搜索人员" oninput="filterMulti(this)"><div class="multi-options">${names.map((name) => {
    const person = state.peopleCenter.find((item) => item.name === name) || {};
    const checked = selected.size ? selected.has(name) : !!person.wecom_bound;
    return `<label class="multi-option"><input type="checkbox" data-public-name="${esc(name)}" ${checked ? 'checked' : ''}><span>${esc(name)}</span><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '已绑定' : '未绑定'}</span></label>`;
  }).join('')}</div></div><div class="multi-summary">${[...selected].join('、') || '未选择接收人'}</div></div>`;
}

function renderMenuRows() {
  const idx = { monitor: 0, mech: 1, more: 2 }[state.activeMenu] || 0;
  const group = state.menu.groups?.[idx] || { name: '', items: [] };
  if ($('menuTitle')) $('menuTitle').textContent = group.name || '未命名';
  if ($('menuRows')) $('menuRows').innerHTML = (group.items || []).map((item, i) => `<div class="menu-row"><input value="${esc(item.name || '')}" oninput="editMenuItem(${idx}, ${i}, 'name', this.value)"><input value="${esc(item.command || '')}" oninput="editMenuItem(${idx}, ${i}, 'command', this.value)"><button class="danger mini" onclick="deleteMenuItem(${idx}, ${i})">删除</button></div>`).join('') || '<div class="card">暂无二级菜单</div>';
  if ($('cmdTable')) $('cmdTable').innerHTML = '<tr><th>命令</th><th>需要绑定</th><th>能否上菜单</th><th>建议归类</th></tr>' + (state.commands || []).map((item) => {
    const command = item.command || item.name || '';
    const needBind = /绑定|导入排班/.test(command) ? '<span class="pill gray">不强制</span>' : '<span class="pill ok">需要</span>';
    const canMenu = /绑定/.test(command) ? '<span class="pill warn">不建议</span>' : '<span class="pill ok">可以</span>';
    const groupName = /机电|橙色/.test(command) ? '机电预警' : /施工|导入|休息/.test(command) ? '更多查询' : '监控在岗';
    return `<tr><td>${esc(command)}</td><td>${needBind}</td><td>${canMenu}</td><td>${groupName}</td></tr>`;
  }).join('');
  if ($('simPerson')) {
    $('simPerson').innerHTML = (state.personnel.names || []).map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('') || '<option value="">暂无人员</option>';
    if (!$('simPerson').value && state.personnel.names.length) $('simPerson').value = state.personnel.names[0];
  }
}

function renderNotifySupportMinimal() {
  $('sendMode').value = state.notification.send_content_mode || 'both';
  if ($('isolate')) $('isolate').checked = state.notification.wecom_app_enabled !== false;
  if ($('wecomAppCorpId')) $('wecomAppCorpId').value = state.notification.wecom_app_corp_id || '';
  if ($('wecomAppAgentId')) $('wecomAppAgentId').value = state.notification.wecom_app_agent_id || '';
  if ($('wecomAppSecret')) $('wecomAppSecret').value = state.notification.wecom_app_secret || '';
  if ($('wecomAppToken')) $('wecomAppToken').value = state.notification.wecom_app_token || '';
  if ($('wecomAppAesKey')) $('wecomAppAesKey').value = state.notification.wecom_app_encoding_aes_key || '';
  const ui = notifyUiKind(state.notification.sender_type || 'wecom_app');
  const meta = notifyMeta(ui);
  if ($('chanBadge')) $('chanBadge').textContent = `当前：${meta.label}`;
  if ($('chanSummary')) $('chanSummary').innerHTML = `<h4>当前通道说明</h4><p>${esc(meta.summary)}</p><div class="toolbar"><button class="primary" onclick="saveNotificationConfigFromInputs()">保存配置</button><button class="secondary" onclick="testNotificationConfig()">测试发送</button><button class="secondary" onclick="testWecomApp()">测试自建应用</button></div>`;
  if ($('lightagentStatus')) $('lightagentStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>连接状态</td><td><span class="pill ${state.lightagent?.connected ? 'ok' : 'warn'}">${state.lightagent?.connected ? '已连接' : '未连接'}</span></td></tr><tr><td>登录状态</td><td>${esc(state.lightagent?.login_status || '未知')}</td></tr><tr><td>可发送群</td><td>${esc(state.lightagent?.sendable_room_count || 0)} 个</td></tr><tr><td>二维码</td><td>${esc(state.lightagent?.qr_status || '未知')}</td></tr></table>`;
  if ($('aibotStatus')) $('aibotStatus').innerHTML = `<table><tr><th>项目</th><th>值</th></tr><tr><td>启用</td><td><span class="pill ${state.aibot?.enabled ? 'ok' : 'warn'}">${state.aibot?.enabled ? '已启用' : '未启用'}</span></td></tr><tr><td>连接</td><td>${esc(state.aibot?.status || '未知')}</td></tr><tr><td>模式</td><td>${esc(state.aibot?.mode || '未知')}</td></tr></table>`;
  applyNotifyChannelVisibility(ui);
  if (state.main === 'notify') renderSubnav();
  if (state.main === 'notify') setTab('notify', state.sub.notify || notifyDefaultTab(ui));
}

function renderPreview(mode = 'today') {
  state.previewMode = mode;
  $$('#previewSeg button').forEach((btn) => btn.classList.toggle('active', btn.dataset.mode === mode));
  const s = state.todayReminders.summary || {};
  if (mode === 'today') {
    $('previewView').innerHTML = `<div class="news"><div class="img">今日在岗图文</div><div class="txt"><b>今日在岗</b><small>早班：${esc((s.early || []).join('、') || '无')}｜中班：${esc((s.middle || []).join('、') || '无')}｜晚班：${esc((s.night || []).join('、') || '无')}｜明日早班：${esc((s.tomorrow_early || []).join('、') || '无')}</small></div></div>`;
  } else if (mode === 'tomorrow') {
    $('previewView').innerHTML = `<div class="news"><div class="img">明日在岗图文</div><div class="txt"><b>明日在岗</b><small>${esc(state.todayReminders.tomorrow_preview || '使用预览接口生成明日预览。')}</small></div></div>`;
  } else {
    const rows = [];
    for (let i = 0; i < 7; i += 1) {
      const day = new Date();
      day.setDate(day.getDate() + i);
      rows.push(`<tr><td>${day.toISOString().slice(0, 10)}</td><td><span class="pill ${i === 0 ? 'ok' : i === 1 ? 'info' : 'gray'}">${i === 0 ? '今日' : i === 1 ? '明日' : '近7天'}</span></td><td>排班预览</td></tr>`);
    }
    $('previewView').innerHTML = `<table><tr><th>日期</th><th>状态</th><th>人员</th></tr>${rows.join('')}</table>`;
  }
}

function renderOpsSupportMinimal() {
  const checks = state.system.checks || [];
  const cards = $$('#ops .pane[data-content="status"] .g3 .card');
  const items = checks.length ? checks : [
    ['应用', state.system.overall_status || 'unknown', 'FastAPI / 调度器正常'],
    ['数据库', state.system.db_status || 'unknown', 'WAL + busy_timeout'],
    ['通知', state.system.notification_status || 'unknown', '自建应用 / 机器人 / 个人微信群'],
  ];
  cards.forEach((card, idx) => {
    const item = items[idx];
    if (item) card.innerHTML = `<h4>${esc(item[0])}</h4><span class="pill ok">${esc(item[1])}</span><p>${esc(item[2])}</p>`;
  });
  const diagTable = $$('#ops .pane[data-content="diagnose"] table')[0];
  if (diagTable) diagTable.innerHTML = `<tr><th>日期</th><th>提醒</th><th>结果</th><th>原因</th></tr>${(state.diagnostics.items || []).map((item) => `<tr><td>${esc(item.date || '')}</td><td>${esc(item.title || item.name || '')}</td><td><span class="pill ${item.status === 'ok' ? 'ok' : item.status === 'warn' ? 'warn' : 'bad'}">${esc(item.status || '')}</span></td><td>${esc(item.reason || item.detail || '')}</td></tr>`).join('')}`;
  const recordTable = $$('#ops .pane[data-content="records"] table')[0];
  if (recordTable) recordTable.innerHTML = `<tr><th>时间</th><th>类型</th><th>目标</th><th>状态</th><th>操作</th></tr>${state.records.map((item) => `<tr><td>${esc(item.created_at || item.send_at || '')}</td><td>${esc(item.kind || '')}</td><td>${esc(item.target || '')}</td><td><span class="pill ${item.status === 'success' ? 'ok' : 'bad'}">${esc(item.status || '')}</span></td><td><button class="secondary mini" onclick="resendRecord(${item.id})">补发</button></td></tr>`).join('')}`;
  if ($('previewView')) $('previewView').innerHTML = buildPreview(state.previewMode);
}

function buildPreview(mode) {
  const s = state.todayReminders.summary || {};
  if (mode === 'today') return `<div class="news"><div class="img">今日在岗图文</div><div class="txt"><b>今日在岗</b><small>早班：${esc((s.early || []).join('、') || '无')}｜中班：${esc((s.middle || []).join('、') || '无')}｜晚班：${esc((s.night || []).join('、') || '无')}｜明日早班：${esc((s.tomorrow_early || []).join('、') || '无')}</small></div></div>`;
  if (mode === 'tomorrow') return `<div class="news"><div class="img">明日在岗图文</div><div class="txt"><b>明日在岗</b><small>${esc(state.todayReminders.tomorrow_preview || '使用预览接口生成明日预览。')}</small></div></div>`;
  return `<table><tr><th>日期</th><th>状态</th><th>人员</th></tr>${Array.from({ length: 7 }, (_, i) => { const d = new Date(); d.setDate(d.getDate() + i); return `<tr><td>${d.toISOString().slice(0, 10)}</td><td><span class="pill ${i === 0 ? 'ok' : i === 1 ? 'info' : 'gray'}">${i === 0 ? '今日' : i === 1 ? '明日' : '近7天'}</span></td><td>排班预览</td></tr>`; }).join('')}</table>`;
}

function setupEmbedded() {
  if (!new URLSearchParams(location.search).has('embed')) return;
  document.querySelector('.side')?.remove();
  document.querySelector('.right')?.remove();
  document.querySelector('.app')?.style.setProperty('grid-template-columns', '1fr');
}

async function reloadAll() {
  await loadAll();
  renderShell();
  renderRail();
  renderOverview();
  renderNotify();
  renderPeople();
  renderRoster();
  renderBusiness();
  renderOps();
  renderSubnav();
  renderNotifySupportMinimal();
  renderOpsSupportMinimal();
  renderPreview(state.previewMode);
  renderMenu();
}

function renderNotify() {
  renderNotifySupportMinimal();
  renderPublicReceivers();
  renderMenu();
  renderPreview(state.previewMode);
}

function renderPeople() {
  renderPeopleSelection();
  renderPeopleScope();
  if ($('peopleList')) $('peopleList').innerHTML = state.peopleCenter.map((person) => {
    const fields = [
      ['企业微信', person.wecom_bound ? '已绑定' : '未绑定', person.wecom_bound ? 'ok' : 'bad', person.wecom_userid || ''],
      ['个人微信群', person.wechat_group_bound ? '已绑定' : '未绑定', person.wechat_group_bound ? 'ok' : 'warn', person.wechat_group_member_name || person.wechat_group_member_id || ''],
      ['手机号', person.mention_mobile ? '已配置' : '未配置', person.mention_mobile ? 'ok' : 'bad', person.mention_mobile || ''],
      ['监控班', person.monitor_configured ? '已配置' : '未配置', person.monitor_enabled ? 'ok' : 'warn', person.monitor_detail || ''],
      ['自定义提醒', person.custom_reminders_enabled ? '已配置' : '未配置', person.custom_reminders_enabled ? 'ok' : 'warn', person.custom_reminders_summary || ''],
      ['休息提醒', person.rest_reminder_enabled ? '已启用' : '未启用', person.rest_reminder_enabled ? 'ok' : 'warn', ''],
      ['机电搭档', person.tunnel_mechanical_partner ? '已设置' : '未设置', person.tunnel_mechanical_partner ? 'ok' : 'bad', person.tunnel_mechanical_partner || ''],
    ].map(([label, text, kind, detail]) => `<div class="people-center-field"><span>${esc(label)}</span><span class="people-status-pill ${kind}">${esc(text)}</span>${detail ? `<span class="people-center-detail">${esc(detail)}</span>` : ''}</div>`).join('');
    return `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${person.wecom_bound ? 'ok' : 'bad'}">${person.wecom_bound ? '企微已绑定' : '未绑定'}</span><small>UserID：${esc(person.wecom_userid || '未配置')}</small><div class="people-center-fields">${fields}</div></div>`;
  }).join('');
}

function renderRoster() {
  const cards = $$('#roster .pane[data-content="import"] .card');
  if (cards[0]) cards[0].innerHTML = `<h4>网页导入</h4><p>上传排班图片 → OCR/模板识别 → 人工核对 → 导入。</p><input id="rosterUploadFile" type="file" accept="image/*"><div class="toolbar" style="margin-top:10px"><button class="primary" onclick="uploadRoster()">上传排班图片</button></div>`;
  if (cards[1]) cards[1].innerHTML = `<h4>企业微信导入</h4><p>菜单“更多查询 → 导入排班”后，5 分钟内发送图片才会触发，避免普通图片误导入。</p><button class="secondary" onclick="setSim('导入排班');jump('notify','simulate');simulate()">打开预览</button>`;
  const importedTable = $$('#roster .pane[data-content="imported"] table')[0];
  if (importedTable) importedTable.innerHTML = `<tr><th>月份</th><th>导入时间</th><th>人员数</th><th>状态</th><th>操作</th></tr>${(state.rosters || []).map((roster) => `<tr><td>${esc(roster.year)}年${esc(roster.month)}月</td><td>${esc(roster.confirmed_at || roster.imported_at || '')}</td><td>${esc(roster.people_count || roster.grid?.length || '')}</td><td><span class="pill ${roster.is_latest ? 'ok' : 'gray'}">${roster.is_latest ? '当前最新' : '历史'}</span></td><td><button class="secondary mini" onclick="loadRosterVersions(${roster.year}, ${roster.month})">查看版本</button></td></tr>`).join('')}`;
  const dailyCard = $$('#roster .pane[data-content="daily"] .card')[0];
  if (dailyCard) dailyCard.innerHTML = `<h4>今日在岗提醒</h4><div class="form"><label>启用</label><label><input id="dailyEnabled" type="checkbox" ${state.daily.enabled ? 'checked' : ''}> 开启</label></div><div class="form"><label>发送时间</label><input id="dailyReminderTime" type="time" value="${esc(state.daily.reminder_time || '07:50')}"></div><div class="form"><label>发送方式</label><select id="dailySendMode"><option value="news">图文</option><option value="both">文字+图片</option><option value="image">图片</option><option value="text">文字</option></select></div><div class="toolbar" style="margin-top:10px"><button class="primary" onclick="saveDailyDuty()">保存今日在岗</button><button class="secondary" onclick="testDailyDuty()">发送测试</button><button class="secondary" onclick="renderPreview('today')">刷新预览</button></div><p class="cardnote">人员/驾驶员数据从人员中心读取，不在这里重复维护。</p>`;
  const vacationCard = $$('#roster .pane[data-content="vacation"] .card')[1] || $$('#roster .pane[data-content="vacation"] .card')[0];
  if (vacationCard) vacationCard.innerHTML = `<h4>假期余额提醒</h4><label class="check"><input id="vacationEnabled" type="checkbox" ${state.vacation.enabled ? 'checked' : ''}> 启用假期余额提醒</label><div class="form" style="margin-top:10px"><label>开始休息文案</label><textarea id="vacationStartTemplate" style="width:100%;min-height:120px">${esc((state.vacation.start_message_templates || []).join('\n'))}</textarea></div><div class="form"><label>结束休息文案</label><textarea id="vacationEndTemplate" style="width:100%;min-height:120px">${esc((state.vacation.end_message_templates || []).join('\n'))}</textarea></div><div class="toolbar" style="margin-top:10px"><button class="primary" onclick="saveVacation()">保存假期配置</button><button class="secondary" onclick="toast('可用随机文案库发送开始/结束提醒。','info')">文案预览</button></div>`;
  const vacationIntroCard = $$('#roster .pane[data-content="vacation"] .card')[0];
  if (vacationIntroCard) vacationIntroCard.innerHTML = `<h4>查询休息</h4><p>支持：查询休息、查询商邱宏休息。未带名字时使用绑定人。</p><textarea readonly>本月休息共{total}天，已经休息{used}天，本月休息还剩{left}天……</textarea><div class="toolbar" style="margin-top:10px"><button class="secondary" onclick="setSim('查询休息');jump('notify','simulate');simulate()">打开预览</button></div>`;
  if ($('rosterVersionsList')) $('rosterVersionsList').innerHTML = '<div class="cardnote">点击“查看版本”可展开版本列表。</div>'; 
  if ($('dailySendMode')) $('dailySendMode').value = state.daily.send_content_mode || 'both';
  if ($('vacationEnabled')) $('vacationEnabled').checked = !!state.vacation.enabled;
  if ($('vacationStartTemplate')) $('vacationStartTemplate').value = (state.vacation.start_message_templates || []).join('\n');
  if ($('vacationEndTemplate')) $('vacationEndTemplate').value = (state.vacation.end_message_templates || []).join('\n');
  if ($('previewView')) $('previewView').innerHTML = buildPreview(state.previewMode);
}

function renderBusiness() {
  const warningCards = $$('#business .pane[data-content="warning"] .card');
  if (warningCards[0]) warningCards[0].innerHTML = `<h4>数据源连接</h4><div class="form"><label>平台账号</label><input id="patrolUsername" value="${esc(state.patrol.username || '')}"></div><div class="form"><label>轮询间隔</label><input id="patrolPollInterval" type="number" value="${esc(state.patrol.poll_interval_minutes || 10)}"></div><div class="form"><label>抖动</label><select id="patrolJitter"><option>保留</option><option>关闭</option></select></div><div class="toolbar"><button class="primary" onclick="savePatrol()">保存配置</button><button class="secondary" onclick="testPatrol()">测试查询</button><button class="secondary" onclick="sendPatrolTest('start')">发送开始提醒</button><button class="secondary" onclick="sendPatrolTest('end')">发送结束提醒</button></div>`;
  if (warningCards[1]) warningCards[1].innerHTML = `<h4>提醒策略</h4><label class="check"><input checked> 预警开始发送一次</label><label class="check"><input checked> 预警结束发送一次</label><label class="check"><input checked> 历史预警窗口拦截</label><label class="check"><input checked> 橙色预警巡查记录查询</label>`;
  const tunnelCards = $$('#business .pane[data-content="tunnel"] .card');
  if (tunnelCards[0]) tunnelCards[0].innerHTML = `<h4>隧道机电账号</h4><div class="form"><label>基础地址</label><input id="tunnelBaseUrl" value="${esc(state.tunnel.base_url || '')}"></div><div class="form"><label>账号</label><input id="tunnelUsername" value="${esc(state.tunnel.username || '')}"></div><div class="form"><label>密码</label><input id="tunnelPassword" type="password" value="${esc(state.tunnel.password || '')}"></div><div class="toolbar"><button class="primary" onclick="saveTunnel()">保存配置</button><button class="secondary" onclick="testTunnelLogin()">测试登录</button></div>`;
  if (tunnelCards[1]) tunnelCards[1].innerHTML = `<h4>今日机电录入</h4><p>点击“录入今日机电” → 生成确认信息 → 回复确认/1 → 录入并发送图文确认。</p><button class="secondary" onclick="setSim('录入今日机电');jump('notify','simulate');simulate()">打开预览</button>`;
}

function renderOpsSupport() {
  const checks = state.system.checks || [];
  const statusCards = $$('#ops .pane[data-content="status"] .g3 .card');
  const items = checks.length ? checks : [
    ['应用', state.system.overall_status || 'unknown', 'FastAPI / 调度器正常'],
    ['数据库', state.system.db_status || 'unknown', 'WAL + busy_timeout'],
    ['通知', state.system.notification_status || 'unknown', '自建应用 / 机器人 / 个人微信群'],
  ];
  statusCards.forEach((card, idx) => {
    const item = items[idx];
    if (item) card.innerHTML = `<h4>${esc(item[0])}</h4><span class="pill ok">${esc(item[1])}</span><p>${esc(item[2])}</p>`;
  });
  const diagTable = $$('#ops .pane[data-content="diagnose"] table')[0];
  if (diagTable) diagTable.innerHTML = `<tr><th>日期</th><th>提醒</th><th>结果</th><th>原因</th></tr>${(state.diagnostics.items || []).map((item) => `<tr><td>${esc(item.date || '')}</td><td>${esc(item.title || item.name || '')}</td><td><span class="pill ${item.status === 'ok' ? 'ok' : item.status === 'warn' ? 'warn' : 'bad'}">${esc(item.status || '')}</span></td><td>${esc(item.reason || item.detail || '')}</td></tr>`).join('')}`;
  const recordTable = $$('#ops .pane[data-content="records"] table')[0];
  if (recordTable) recordTable.innerHTML = `<tr><th>时间</th><th>类型</th><th>目标</th><th>状态</th><th>操作</th></tr>${state.records.map((item) => `<tr><td>${esc(item.created_at || item.send_at || '')}</td><td>${esc(item.kind || '')}</td><td>${esc(item.target || '')}</td><td><span class="pill ${item.status === 'success' ? 'ok' : 'bad'}">${esc(item.status || '')}</span></td><td><button class="secondary mini" onclick="resendRecord(${item.id})">补发</button></td></tr>`).join('')}`;
}

function openCompare(idx) {
  const item = compareItems[idx] || compareItems[0];
  $('cmpTitle').textContent = `${item.time} · ${item.title}`;
  $('compareList').innerHTML = compareItems.map((x, i) => `<button class="secondary ${i === idx ? 'active' : ''}" onclick="openCompare(${i})">${x.time} ${x.title}</button>`).join('');
  $('compareOld').innerHTML = `<iframe id="oldFrame" src="/" style="width:100%;height:100%;border:0"></iframe>`;
  $('compareNew').innerHTML = `<iframe id="newFrame" src="/settings-redesign?embed=1&section=${encodeURIComponent(item.newTarget)}" style="width:100%;height:100%;border:0"></iframe>`;
  $('compareModal').classList.add('open');
  const oldFrame = $('compareOld').querySelector('iframe');
  const newFrame = $('compareNew').querySelector('iframe');
  const sync = (srcFrame, dstFrame) => {
    try {
      if (!srcFrame?.contentWindow || !dstFrame?.contentWindow) return;
      const y = srcFrame.contentWindow.document.scrollingElement?.scrollTop || 0;
      dstFrame.contentWindow.scrollTo(0, y);
    } catch {}
  };
  oldFrame.onload = () => {
    try {
      const doc = oldFrame.contentWindow.document;
      const btn = doc.querySelector(`[data-settings-target="${item.oldTarget}"]`);
      if (btn) btn.click();
    } catch {}
  };
  newFrame.onload = () => {
    try {
      const doc = newFrame.contentWindow.document;
      const btn = doc.querySelector(`[data-sec="${item.newTarget}"]`);
      if (btn) btn.click();
    } catch {}
  };
  oldFrame.addEventListener('scroll', () => sync(oldFrame, newFrame));
  newFrame.addEventListener('scroll', () => sync(newFrame, oldFrame));
}

function closeCompare() {
  $('compareModal').classList.remove('open');
}

const CHANNEL_UI_TO_BACKEND = { 'wecom-app': 'wecom_app', 'personal-wechat': 'lightagent', 'wecom-bot': 'wecom_webhook' };
const CHANNEL_BACKEND_TO_UI = { 'wecom_app': 'wecom-app', 'lightagent': 'personal-wechat', 'wecom_webhook': 'wecom-bot' };
const CHANNEL_UI_TO_ONLY = { 'wecom-app': 'only-app', 'personal-wechat': 'only-wechat', 'wecom-bot': 'only-bot' };
function channel(kind) {
  const backend = CHANNEL_UI_TO_BACKEND[kind] || kind || state.notification.sender_type || 'wecom_app';
  state.notification.sender_type = backend;
  const ui = CHANNEL_BACKEND_TO_UI[backend] || CHANNEL_BACKEND_TO_UI[kind] || 'wecom-app';
  state.sub.notify = state.sub.notify && notifyVisibleTabs(ui).has(state.sub.notify) ? state.sub.notify : notifyDefaultTab(ui);
  applyNotifyChannelVisibility(ui);
  const badge = $('chanBadge');
  if (badge) {
    badge.textContent = `当前：${{ wecom_app: '企业微信自建应用', lightagent: '个人微信群', wecom_webhook: '企业微信群机器人' }[backend] || backend}`;
    badge.className = `pill ${backend === 'wecom_app' ? 'ok' : backend === 'lightagent' ? 'warn' : 'info'}`;
  }
  $$('.choice').forEach((choice) => choice.classList.toggle('active', choice.dataset.ch === ui));
  $$('.only').forEach((elm) => {
    const target = CHANNEL_UI_TO_ONLY[ui];
    elm.classList.toggle('active', target ? elm.classList.contains(target) : false);
  });
  if (state.main === 'notify') setTab('notify', state.sub.notify);
  else renderSubnav();
}

function renderMenu() { renderMenuRows(); }
function addMenu() {
  const idx = { monitor: 0, mech: 1, more: 2 }[state.activeMenu] || 0;
  const group = state.menu.groups?.[idx];
  if (!group) return;
  if ((group.items || []).length >= 5) return toast('企业微信每个一级菜单最多 5 个二级菜单', 'warn');
  group.items = group.items || [];
  group.items.push({ name: '新菜单', command: '菜单' });
  renderMenuRows();
}
function addPrimary() { toast('企业微信最多 3 个一级菜单，当前已满；正式系统应禁止继续新增。', 'warn'); }
function editMenuItem(groupIndex, itemIndex, field, value) { if (state.menu.groups?.[groupIndex]?.items?.[itemIndex]) state.menu.groups[groupIndex].items[itemIndex][field] = value; }
function deleteMenuItem(groupIndex, itemIndex) { if (state.menu.groups?.[groupIndex]?.items) state.menu.groups[groupIndex].items.splice(itemIndex, 1); renderMenuRows(); }
function setSim(value) { if ($('simInput')) $('simInput').value = value; }
function renderPreviewButtons() { $$('#previewSeg button').forEach((btn) => btn.onclick = () => renderPreview(btn.dataset.mode)); }
async function savePersonnel() {
  const names = $$('#peopleCards input[type="checkbox"][data-person-name]:checked').map((input) => input.dataset.personName);
  state.personnel.names = names;
  state.personnel.people = (state.personnel.people || []).filter((p) => names.includes(p.name));
  await api('/api/personnel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.personnel) });
  toast('人员配置已保存', 'ok');
  await reloadAll();
}
function togglePersonName() {}
function toggleMulti(btn) { const root = btn.closest('.multi'); $$('.multi').forEach((elm) => { if (elm !== root) elm.classList.remove('open'); }); root?.classList.toggle('open'); }
function filterMulti(input) { const kw = input.value.trim(); $$('.multi-option', input.closest('.multi')).forEach((option) => { option.style.display = !kw || option.textContent.includes(kw) ? 'flex' : 'none'; }); }

async function saveNotificationConfigFromInputs() {
  state.notification.wecom_app_corp_id = $('wecomAppCorpId')?.value || '';
  state.notification.wecom_app_agent_id = $('wecomAppAgentId')?.value || '';
  state.notification.wecom_app_secret = $('wecomAppSecret')?.value || '';
  state.notification.wecom_app_token = $('wecomAppToken')?.value || '';
  state.notification.wecom_app_encoding_aes_key = $('wecomAppAesKey')?.value || '';
  state.notification.send_content_mode = $('sendMode')?.value || state.notification.send_content_mode || 'both';
  state.notification.wecom_app_enabled = $('isolate')?.checked ?? state.notification.wecom_app_enabled;
  state.notification.wecom_app_target_names = $$('#publicReceivers input[data-public-name]:checked').map((input) => input.dataset.publicName);
  state.notification.sender_type = state.notification.sender_type || 'wecom_app';
  await api('/api/notification-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.notification) });
  toast('通知配置已保存', 'ok');
  await reloadAll();
}
function testNotificationConfig() {
  api('/api/notification-config/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ person_name: state.personnel.names[0] || '示例甲' }) })
    .then(() => toast('通知测试已发送', 'ok'))
    .catch((error) => toast(`通知测试失败：${error.message}`, 'bad'));
}
function testWecomApp() {
  api('/api/wecom-app/test', { method: 'POST' })
    .then(() => toast('自建应用测试成功', 'ok'))
    .catch((error) => toast(`自建应用测试失败：${error.message}`, 'bad'));
}
function refreshLightAgentStatus() { api('/api/lightagent/wechat/status').then((data) => { state.lightagent = data; renderNotifySupport(); toast('个人微信群状态已刷新', 'ok'); }); }
function refreshLightAgentQr() { api('/api/lightagent/wechat/refresh-qr', { method: 'POST' }).then((data) => { state.lightagent = data; renderNotifySupport(); toast('二维码已刷新', 'ok'); }); }
function reconnectAibot() { api('/api/wecom-aibot/reconnect', { method: 'POST' }).then((data) => { state.aibot = data; renderNotifySupport(); toast('智能机器人已重新连接', 'ok'); }); }
function saveMenu() { api('/api/wecom-app/menu', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.menu) }).then(() => toast('菜单已保存', 'ok')).catch((e) => toast(`保存菜单失败：${e.message}`, 'bad')); }
function createMenu() { api('/api/wecom-app/menu/create', { method: 'POST' }).then(() => toast('企业微信菜单已同步', 'ok')).catch((e) => toast(`菜单同步失败：${e.message}`, 'bad')); }
async function saveDailyDuty() { state.daily.enabled = $('dailyEnabled')?.checked ?? state.daily.enabled; state.daily.reminder_time = $('dailyReminderTime')?.value || state.daily.reminder_time || '07:50'; state.daily.send_content_mode = $('dailySendMode')?.value || state.daily.send_content_mode || 'both'; await api('/api/daily-duty-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.daily) }); toast('今日在岗配置已保存', 'ok'); await reloadAll(); }
async function saveVacation() {
  state.vacation.enabled = $('vacationEnabled')?.checked ?? state.vacation.enabled;
  const startTemplates = ($('vacationStartTemplate')?.value || '').split('\n').map((line) => line.trim()).filter(Boolean);
  const endTemplates = ($('vacationEndTemplate')?.value || '').split('\n').map((line) => line.trim()).filter(Boolean);
  if (startTemplates.length) state.vacation.start_message_templates = startTemplates;
  if (endTemplates.length) state.vacation.end_message_templates = endTemplates;
  await api('/api/vacation-reminder-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.vacation) });
  toast('假期配置已保存', 'ok');
  await reloadAll();
}
async function savePatrol() { state.patrol.username = $('patrolUsername')?.value || state.patrol.username || ''; state.patrol.poll_interval_minutes = Number($('patrolPollInterval')?.value || state.patrol.poll_interval_minutes || 10); await api('/api/patrol-warning-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.patrol) }); toast('公路预警配置已保存', 'ok'); await reloadAll(); }
async function saveTunnel() { state.tunnel.base_url = $('tunnelBaseUrl')?.value || state.tunnel.base_url || ''; state.tunnel.username = $('tunnelUsername')?.value || state.tunnel.username || ''; state.tunnel.password = $('tunnelPassword')?.value || state.tunnel.password || ''; await api('/api/tunnel-mechanical/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.tunnel) }); toast('隧道机电配置已保存', 'ok'); await reloadAll(); }
function testPatrol() { api('/api/patrol-warning-config/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state.patrol) }).then(() => toast('公路巡查测试完成', 'ok')).catch((e) => toast(`公路巡查测试失败：${e.message}`, 'bad')); }
function sendPatrolTest(mode) { api('/api/patrol-warning-config/send-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) }).then(() => toast(`公路巡查${mode === 'end' ? '结束' : '开始'}提醒已发送`, 'ok')).catch((e) => toast(`发送失败：${e.message}`, 'bad')); }
function testTunnelLogin() { api('/api/tunnel-mechanical/login-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: '', uuid: '' }) }).then(() => toast('隧道机电登录测试成功', 'ok')).catch((e) => toast(`隧道机电登录测试失败：${e.message}`, 'bad')); }
function saveNotificationConfig() { saveNotificationConfigFromInputs(); }
function setSimAndRun(value) { setSim(value); jump('notify', 'simulate'); simulate(); }
async function uploadRoster() { const input = $('rosterUploadFile'); if (!input?.files?.length) return toast('请先选择排班图片', 'warn'); const form = new FormData(); form.append('file', input.files[0]); await api('/api/rosters/upload', { method: 'POST', body: form }); toast('排班图片已上传', 'ok'); await reloadAll(); }
function loadRosterVersions(year, month) { api(`/api/rosters/${year}/${month}/versions`).then((data) => { state.versions = data.versions || []; $('rosterVersionsList').innerHTML = state.versions.length ? state.versions.map((version) => `<div class="issue"><div><b>版本 ${esc(version.version_id || '')}</b><p>${esc(version.created_at || '')}</p></div><button class="secondary mini" onclick="restoreRosterVersion(${year}, ${month}, ${version.version_id})">恢复</button></div>`).join('') : '<div class="cardnote">暂无版本</div>'; }); }
function restoreRosterVersion(year, month, versionId) { api(`/api/rosters/${year}/${month}/versions/${versionId}/restore`, { method: 'POST' }).then(() => toast('已恢复排班版本', 'ok')).catch((e) => toast(`恢复失败：${e.message}`, 'bad')); }
function refreshSendRecords() { const status = $('recordStatus')?.value || ''; const kind = $('recordKind')?.value || ''; api(`/api/send-records?limit=50&status=${encodeURIComponent(status)}&kind=${encodeURIComponent(kind)}`).then((data) => { state.records = data.records || []; renderOps(); }).catch((e) => toast(`发送记录加载失败：${e.message}`, 'bad')); }
function resendRecord(id) { api(`/api/send-records/${id}/resend`, { method: 'POST' }).then(() => toast('补发完成', 'ok')).catch((e) => toast(`补发失败：${e.message}`, 'bad')); }
function cleanupUploads() { api('/api/uploads/cleanup', { method: 'POST' }).then(() => toast('已清理过期文件', 'ok')).catch((e) => toast(`清理失败：${e.message}`, 'bad')); }
function testMonitor(name) { api('/api/people/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) }).then(() => toast(`已发送 ${name} 的监控测试`, 'ok')).catch((e) => toast(`监控测试失败：${e.message}`, 'bad')); }
async function testDailyDuty() {
  try {
    await api('/api/daily-duty-config/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
    toast('今日在岗测试已发送', 'ok');
  } catch (error) {
    toast(`今日在岗测试失败：${error.message}`, 'bad');
  }
}

async function editPerson(name) {
  const currentName = String(name || '').trim();
  if (!currentName) return;
  const nextName = prompt('修改人员姓名', currentName);
  if (nextName === null) return;
  const cleanName = nextName.trim();
  if (!cleanName) return toast('姓名不能为空', 'warn');
  if (cleanName === currentName) return;
  try {
    await api(`/api/personnel/${encodeURIComponent(currentName)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: cleanName }),
    });
    toast(`已修改为 ${cleanName}`, 'ok');
    await reloadAll();
  } catch (error) {
    toast(`修改失败：${error.message}`, 'bad');
  }
}

async function deletePersonnel(name) {
  const currentName = String(name || '').trim();
  if (!currentName) return;
  if (!confirm(`确定删除人员「${currentName}」吗？相关监控班、自定义提醒和绑定会一起清掉。`)) return;
  try {
    await api(`/api/personnel/${encodeURIComponent(currentName)}`, { method: 'DELETE' });
    toast(`已删除 ${currentName}`, 'ok');
    await reloadAll();
  } catch (error) {
    toast(`删除失败：${error.message}`, 'bad');
  }
}

async function simulate() {
  const text = $('simInput')?.value || '';
  const sender = $('simPerson')?.value || state.personnel.names[0] || '';
  const payload = { text, sender_name: sender, sender_id: sender, runtime_sender_id: sender, stable_member_id: sender, room_id: '', stable_room_id: '', room_name: '' };
  let html = `<div class="bubble me">${esc(text || '（空）')}</div>`;
  try {
    const result = await api('/api/wechat-interaction-config/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const reply = result.result?.reply || result.result?.content || result.result?.text || '已触发';
    html += `<div class="bubble bot">${esc(reply)}</div>`;
    if (result.image_full_url) html += `<div class="news"><div class="img">预览图</div><div class="txt"><b>交互图文</b><small>${esc(result.image_full_url)}</small></div></div>`;
  } catch (e) {
    html += `<div class="bubble bot">${esc(e.message || '未识别为功能命令：不触发提醒、不写发送记录。')}</div>`;
  }
  $('chat').innerHTML = html;
}

function openCompare(idx) {
  const item = compareItems[idx] || compareItems[0];
  $('cmpTitle').textContent = `${item.time} · ${item.title}`;
  $('compareList').innerHTML = compareItems.map((x, i) => `<button class="secondary ${i === idx ? 'active' : ''}" onclick="openCompare(${i})">${x.time} ${x.title}</button>`).join('');
  $('compareOld').innerHTML = `<iframe id="oldFrame" src="/" style="width:100%;height:100%;border:0"></iframe>`;
  $('compareNew').innerHTML = `<iframe id="newFrame" src="/settings-redesign?embed=1&section=${encodeURIComponent(item.newTarget)}" style="width:100%;height:100%;border:0"></iframe>`;
  $('compareModal').classList.add('open');
  const oldFrame = $('compareOld').querySelector('iframe');
  const newFrame = $('compareNew').querySelector('iframe');
  oldFrame.onload = () => { try { const btn = oldFrame.contentWindow.document.querySelector(`[data-settings-target="${item.oldTarget}"]`); if (btn) btn.click(); } catch {} };
  newFrame.onload = () => { try { const btn = newFrame.contentWindow.document.querySelector(`[data-sec="${item.newTarget}"]`); if (btn) btn.click(); } catch {} };
  const sync = (src, dst) => { try { const y = src.contentWindow.document.scrollingElement?.scrollTop || 0; dst.contentWindow.scrollTo(0, y); } catch {} };
  oldFrame.addEventListener('scroll', () => !state.compareLock && sync(oldFrame, newFrame));
  newFrame.addEventListener('scroll', () => !state.compareLock && sync(newFrame, oldFrame));
}
function closeCompare() { $('compareModal').classList.remove('open'); }

function toggleMulti(btn) {
  const root = btn.closest('.multi');
  $$('.multi').forEach((elm) => { if (elm !== root) elm.classList.remove('open'); });
  root?.classList.toggle('open');
}
function filterMulti(input) {
  const kw = input.value.trim();
  $$('.multi-option', input.closest('.multi')).forEach((option) => { option.style.display = !kw || option.textContent.includes(kw) ? 'flex' : 'none'; });
}
function togglePersonName(input) {
  const name = input.dataset.personName;
  const set = new Set(state.personnel.names || []);
  if (input.checked) set.add(name); else set.delete(name);
  state.personnel.names = [...set];
}
function renderPreviewButtons() { $$('#previewSeg button').forEach((btn) => btn.onclick = () => renderPreview(btn.dataset.mode)); }
function show(main) { setMain(main); }

async function savePersonnel() {
  const payload = { names: state.personnel.names, people: state.personnel.people || [] };
  await api('/api/personnel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  toast('人员配置已保存', 'ok');
  await reloadAll();
}

function addPersonnel() {
  const value = prompt('输入要新增的姓名，多个用逗号或换行分隔');
  if (!value) return;
  const names = value.split(/[\n,，;；\s]+/).map((name) => name.trim()).filter(Boolean);
  if (!names.length) return toast('没有识别到姓名', 'warn');
  const set = new Set(state.personnel.names || []);
  names.forEach((name) => set.add(name));
  state.personnel.names = [...set];
  renderPeople();
  toast(`已加入 ${names.length} 个姓名，记得点保存`, 'ok');
}

function batchImportPeople() {
  const value = prompt('粘贴人员名单，支持逗号/空格/换行分隔');
  if (!value) return;
  const names = value.split(/[\n,，;；\s]+/).map((name) => name.trim()).filter(Boolean);
  if (!names.length) return toast('没有可导入的姓名', 'warn');
  const set = new Set(state.personnel.names || []);
  names.forEach((name) => set.add(name));
  state.personnel.names = [...set];
  renderPeople();
  toast(`已导入 ${names.length} 个姓名，记得点保存`, 'ok');
}

function checkDuplicatePeople() {
  const names = state.personnel.names || [];
  const seen = new Set();
  const dup = [];
  names.forEach((name) => {
    if (seen.has(name)) dup.push(name);
    else seen.add(name);
  });
  toast(dup.length ? `发现重复：${Array.from(new Set(dup)).join('、')}` : '当前名单没有重复', dup.length ? 'warn' : 'ok');
}

async function exportConfig() { const res = await fetch(apiUrl('/api/config/export'), { credentials: 'include' }); if (!res.ok) throw new Error(await res.text()); const blob = await res.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'duty-reminder-config.json'; a.click(); toast('配置已导出', 'ok'); }
async function createBackup() { await api('/api/config/backups', { method: 'POST' }); toast('已创建数据库备份', 'ok'); await reloadAll(); }
function importConfigPrompt() { const input = document.createElement('input'); input.type = 'file'; input.accept = '.json,application/json'; input.onchange = async () => { if (!input.files?.length) return; const form = new FormData(); form.append('file', input.files[0]); await api('/api/config/import', { method: 'POST', body: form }); toast('配置包导入成功', 'ok'); await reloadAll(); }; input.click(); }

function renderOpsPreviewOnly() { $('previewView').innerHTML = buildPreview(state.previewMode); }

function attachGeneralEvents() {
  $$('.nav').forEach((btn) => btn.addEventListener('click', () => setMain(btn.dataset.sec)));
  $$('.bottom button').forEach((btn) => btn.addEventListener('click', () => { if (btn.dataset.main) setMain(btn.dataset.main); }));
  $$('.sec').forEach((sec) => { $$('.tab', sec).forEach((btn) => btn.addEventListener('click', () => setTab(sec.id, btn.dataset.tab))); });
  $$('.choice').forEach((choice) => choice.addEventListener('click', () => channel(choice.dataset.ch)));
  $$('.l1').forEach((btn) => btn.addEventListener('click', () => { state.activeMenu = btn.dataset.menu; renderMenuRows(); }));
  if ($('sendMode')) $('sendMode').addEventListener('change', (e) => { state.notification.send_content_mode = e.target.value; });
  if ($('isolate')) $('isolate').addEventListener('change', (e) => { state.notification.wecom_app_enabled = e.target.checked; });
  renderPreviewButtons();
}

function setupEmbed() {
  if (!new URLSearchParams(location.search).has('embed')) return;
  document.querySelector('.side')?.remove();
  document.querySelector('.right')?.remove();
  document.querySelector('.app')?.style.setProperty('grid-template-columns', '1fr');
}

async function init() {
  setupEmbed();
  attachGeneralEvents();
  await reloadAll();
  state.main = new URLSearchParams(location.search).get('section') || state.main;
  setMain(state.main);
  renderPreview(state.previewMode);
  renderNotifySupportMinimal();
  renderOpsSupport();
  channel(state.notification.sender_type || 'wecom_app');
  if (state.main === 'notify') renderMenuRows();
}

window.jump = jump;
window.show = show;
window.setMain = setMain;
window.setTab = setTab;
window.openCompare = openCompare;
window.closeCompare = closeCompare;
window.toggleMulti = toggleMulti;
window.filterMulti = filterMulti;
window.togglePersonName = togglePersonName;
window.savePersonnel = savePersonnel;
window.exportConfig = exportConfig;
window.createBackup = createBackup;
window.importConfigPrompt = importConfigPrompt;
window.renderPreview = renderPreview;
window.renderMenu = renderMenu;
window.addMenu = addMenu;
window.addPrimary = addPrimary;
window.renderRail = renderRail;
window.setSim = setSim;
window.simulate = simulate;
window.toast = toast;
window.saveNotificationConfigFromInputs = saveNotificationConfigFromInputs;
window.testNotificationConfig = testNotificationConfig;
window.testWecomApp = testWecomApp;
window.refreshLightAgentStatus = refreshLightAgentStatus;
window.refreshLightAgentQr = refreshLightAgentQr;
window.reconnectAibot = reconnectAibot;
window.saveMenu = saveMenu;
window.createMenu = createMenu;
window.editMenuItem = editMenuItem;
window.deleteMenuItem = deleteMenuItem;
window.saveDailyDuty = saveDailyDuty;
window.saveVacation = saveVacation;
window.savePatrol = savePatrol;
window.saveTunnel = saveTunnel;
window.testPatrol = testPatrol;
window.sendPatrolTest = sendPatrolTest;
window.testTunnelLogin = testTunnelLogin;
window.uploadRoster = async function uploadRoster() { const input = $('rosterUploadFile'); if (!input?.files?.length) return toast('请先选择排班图片', 'warn'); const form = new FormData(); form.append('file', input.files[0]); await api('/api/rosters/upload', { method: 'POST', body: form }); toast('排班图片已上传', 'ok'); await reloadAll(); };
window.loadRosterVersions = loadRosterVersions;
window.restoreRosterVersion = restoreRosterVersion;
window.refreshSendRecords = refreshSendRecords;
window.resendRecord = resendRecord;
window.cleanupUploads = cleanupUploads;
window.testMonitor = testMonitor;
window.editPerson = editPerson;
window.deletePersonnel = deletePersonnel;
window.testDailyDuty = testDailyDuty;
window.state = state;

async function loadConstructionSites() {
  try {
    const data = await api('/api/construction-sites');
    state.constructionSites = data.sites || [];
  } catch (error) {
    console.warn('construction-sites', error);
    state.constructionSites = [];
  }
}

function renderMonitorCards() {
  const root = $('monitorCards');
  if (!root) return;
  const people = (state.peopleCenter || []).filter((person) => person.monitor_configured || person.monitor_enabled || person.wecom_bound || person.custom_reminders_enabled || person.rest_reminder_enabled);
  root.innerHTML = people.length ? people.map((person) => {
    const status = person.monitor_enabled ? 'ok' : person.monitor_configured ? 'warn' : 'gray';
    const detail = person.monitor_detail || person.custom_reminders_summary || person.wecom_userid || '未配置';
    return `<div class="person"><strong>${esc(person.name || '')}</strong><span class="pill ${status}">${person.monitor_enabled ? '已启用' : person.monitor_configured ? '已配置' : '未配置'}</span><small>${esc(detail)}</small><div class="toolbar" style="margin-top:8px"><button class="secondary mini" onclick="testMonitor(${jsQuote(person.name || '')})">发送测试</button>${person.wecom_bound ? '' : '<span class="pill bad">未绑定企微</span>'}</div></div>`;
  }).join('') : '<div class="cardnote">暂无监控班配置人员，请先到人员中心绑定。</div>';
}

function renderConstructionSites() {
  const root = $('constructionSitesList');
  if (!root) return;
  const sites = state.constructionSites || [];
  root.innerHTML = sites.length ? sites.map((site) => `<div class="issue"><div><b>${esc(site.name || '')}</b><p>ID ${esc(site.id || '')}</p></div><div class="toolbar"><button class="secondary mini" onclick="editConstructionSite(${site.id}, ${jsQuote(site.name || '')})">修改</button><button class="danger mini" onclick="deleteConstructionSite(${site.id})">删除</button></div></div>`).join('') : '<div class="cardnote">暂无施工点，先新增一个。</div>';
}

async function refreshConstructionSites() {
  await loadConstructionSites();
  renderConstructionSites();
}

async function addConstructionSite() {
  const name = prompt('请输入施工点名称');
  if (!name || !name.trim()) return;
  await api('/api/construction-sites', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name.trim() }) });
  toast('施工点已新增', 'ok');
  await refreshConstructionSites();
}

async function editConstructionSite(id, currentName) {
  const name = prompt('修改施工点名称', currentName || '');
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) return toast('施工点名称不能为空', 'warn');
  await api(`/api/construction-sites/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: trimmed }) });
  toast('施工点已更新', 'ok');
  await refreshConstructionSites();
}

async function deleteConstructionSite(id) {
  if (!confirm('确定删除这个施工点吗？')) return;
  await api(`/api/construction-sites/${id}`, { method: 'DELETE' });
  toast('施工点已删除', 'ok');
  await refreshConstructionSites();
}

const __renderBusiness = renderBusiness;
renderBusiness = function renderBusiness() {
  __renderBusiness();
  renderConstructionSites();
};

const __renderRoster = renderRoster;
renderRoster = function renderRoster() {
  __renderRoster();
  renderMonitorCards();
};

const __reloadAll = reloadAll;
reloadAll = async function reloadAll() {
  await loadConstructionSites();
  await __reloadAll();
  renderConstructionSites();
  renderMonitorCards();
};

window.addConstructionSite = addConstructionSite;
window.refreshConstructionSites = refreshConstructionSites;
window.editConstructionSite = editConstructionSite;
window.deleteConstructionSite = deleteConstructionSite;

init().catch((error) => {
  console.error(error);
  toast(`初始化失败：${error.message}`, 'bad');
});
