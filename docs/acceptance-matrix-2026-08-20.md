# duty-reminder 验收矩阵

日期：2026-08-20

## 1. 一级分类

| 一级分类 | 二级入口 | 关键接口 / 页面 | 当前状态 |
|---|---|---|---|
| 首页 | 今日提醒、下次提醒、今日在岗摘要、最近发送情况 | `/api/reminders/today` | 已有 |
| 排班与在岗 | 导入/核对、已导入排班、今日在岗 | `/api/rosters/*`、`/api/daily-duty-image` | 已有 |
| 提醒中心 | 监控班提醒、自定义提醒、假期余额提醒 | `/api/custom-reminders`、`/api/vacation-reminder-config`、`/api/reminders/preview` | 已有 |
| 机电施工 | 隧道机电录入、隧道模板、修改模板、施工图片、施工点维护 | `/api/tunnel-mechanical/*`、`/api/construction-sites` | 已有 |
| 巡查预警 | 公路巡查预警、橙色预警查询/统计 | `/api/patrol-warning/*` | 已有 |
| 消息通知 | 通知通道、交互命令、交互模板、发送测试/模拟 | `/api/notification-config`、`/api/wecom-app/*`、`/api/feature-channel-config`、`/api/wechat-interaction-config` | 已有 |
| 人员管理 | 人员名单、岗位分组（含驾驶员）、绑定与状态 | `/api/people`、`/api/personnel`、`/api/people-center` | 已有 |
| 记录与工具 | 发送记录、提醒诊断、系统状态、配置导入/导出、数据库备份、文件清理 | `/api/send-records`、`/api/reminders/diagnostics`、`/api/system-status`、`/api/config/*`、`/api/uploads/cleanup` | 已有 |

## 2. 已核对的关键联动

1. **通知通道互斥**
   - 选择 `企业微信自建应用` 时，只显示自建应用相关配置。
   - `个人微信群` 才显示发送群配置。
   - `企业微信群机器人` 只保留群机器人配置。

2. **人员与绑定联动**
   - 企业微信绑定状态、个人微信群绑定状态、通知接收人、监控班、自定义提醒共用同一份人员数据。
   - 岗位分组会同步到日常在岗配置。

3. **提醒类联动**
   - 监控班、自定义提醒、假期余额提醒都走统一提醒配置与诊断入口。
   - 休息班不单独配置，按排班表自动计算。

4. **导入/导出联动**
   - 配置导出/导入覆盖通知、人员、排班、提醒、巡查、机电、菜单等核心配置。
   - 自定义菜单、施工点、假期文案库都会随配置一起迁移。

5. **交互联动**
   - 企业微信自建应用菜单、交互命令、绑定状态、测试发送是连在一起的。
   - 施工图片、机电录入、排班导入都使用上下文状态机，避免普通图片/数字误触。

## 3. 当前验证结果

- `python -X utf8 -m pytest -q`：**353 passed**
- 本地 Docker：`duty-reminder-duty-reminder-1` 运行中，`127.0.0.1:8080` 可访问
- 浏览器真实点击：
  - 8 个一级分类都能切换
  - 顶部二级标签会随一级分类变化
  - 通知通道切换后，对应配置块会随之显隐
- 配置快照边缘项已补断言：
  - 假期余额提醒文案
  - 企业微信自建应用菜单
  - 施工点
  - 导出 / 导入都会一起迁移
- 通知通道回显已补断言：
  - 自建应用启用后 `/api/notification-config` 会返回 `sender_type=wecom_app`
  - 前端刷新不会再回到旧通道假象
- 假期余额提醒文案库已补断言：
  - 测试接口会从 `start_message_templates / end_message_templates` 里真实抽取
  - 不会永远只发第一条模板
- 导入排班覆盖确认已复验：
  - `tests/test_api.py::test_wechat_roster_import_conflict_can_be_confirmed_with_token`
  - 结果：冲突导入会返回 `conflict`，回复覆盖后可继续完成导入
- 假期余额提醒文案随机抽取已复验：
  - `tests/test_api.py::test_vacation_reminder_test_uses_template_library_randomly`
  - 结果：测试发送会在文案库里真实随机选取，不会固定第一条
- 容器内实时验证：
  - `/api/notification-config` 在自建应用启用后会回显 `sender_type=wecom_app`
  - 浏览器/容器看到的通道状态与保存结果一致
- 真实菜单/施工链路复验：
  - `tests/test_wecom_app.py -k 'construction_image_flow_generates_docx or menu_click_event_runs_mapped_command or all_legacy_menu_positions_map_to_current_commands'`
  - 结果：`3 passed`
- 施工图片 Word 生成 / 菜单回调 / 旧菜单兼容已复验：
  - `tests/test_wecom_app.py -k 'roster_image_conflict_requires_overwrite_confirmation or construction_image_flow_generates_docx or menu_click_event_runs_mapped_command or all_legacy_menu_positions_map_to_current_commands'`
  - 结果：`4 passed`
- 手机窄屏真实回归：
  - `tests/test_frontend_mobile.py::test_frontend_mobile_navigation_real_clicks`
  - 结果：390px 下左侧一级分类仍为两列网格，8 个一级分类切换后顶部二级标签与当前分类一致，未出现空白标签
- 配置快照范围继续扩大：
  - `feature_channel_config`
  - `wechat_interaction_config`
  - 仍保持导出 / 导入双向回写通过

## 4. 当前无新增待验收项

以上矩阵项已按当前代码和测试全部补齐并复验。

