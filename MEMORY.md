# Project Memory

## Long-Term Context
- 2026-07-21: Project uses FastAPI with local static UI, SQLite data under `data/`, and uploaded roster images under `uploads/`.
- 2026-07-21: Roster image import relies on OCR plus image/template parsing using OpenCV/Pillow/RapidOCR.

## Operational Notes
- Do not store secret values here. Only document where configuration is expected.
- Deployment/runtime secrets are configured through environment variables or `.env`-style files, not committed project memory.

## Recent Work
- 2026-07-21: Added project instruction and memory files because the repository did not contain `AGENTS.md` or `MEMORY.md`, and no `~/.codex/templates/` directory was available.
- 2026-07-21: Roster template parsing must derive personnel row count from detected horizontal grid lines instead of assuming exactly 15 people; screenshots can contain 16 people and therefore 17 row boundary lines.
- 2026-07-21: Image import should use fixed-template parsing for shifts and avoid full-image OCR on upload. Name recognition may use local OCR on the cropped name column only; users review or manually fill missed names/year/month instead of spending resources on OCR for the whole screenshot.
- 2026-07-22: Custom shift reminders are stored in `custom_reminders` and are matched by roster date, person name, and shift code. Webhook @ mobile resolution is custom reminder mobile, then shared personnel contact, then monitored person contact.
- 2026-07-22: `personnel_names` now stores optional `mention_mobile`; saving monitored people or custom reminders upserts that shared contact cache so future name inputs can autofill mobiles.
- 2026-07-22: Monitored reminder editing uses optional `original_name` on `POST /api/people` to support renaming without leaving the old monitored row. `DELETE /api/people/{name}` removes monitored reminder configs only; shared personnel contacts are preserved.
- 2026-07-22: Template shift recognition must keep white or near-white single `中` cells as empty (`-` in the UI). Colored shift cells, regardless of fill color, should become `中`/`早`/`晚`; automatic recheck should reclassify each current grid cell by its stored image box.
- 2026-07-22: Template `出差` should only come from a white cell with two stacked text groups; do not use high ink density as a global `出差` fallback. Day-column detection should prefer the real 32-column shift grid if stray left-side vertical lines could shift dates by one.
- 2026-07-22: Automatic recheck should reparse the fixed template grid before diffing current cells so stale shifted boxes can be corrected. The source image view should show the active cell's row name and day near the highlighted cell and in the image header.
- 2026-07-31: The daily duty summary ("今日在岗") uses the real next calendar day for tomorrow-related fields, even across month boundaries. On July 31, August 1 roster data should still populate `tomorrow_early` and rest transitions such as "今日下午休息".
- 2026-08-04: Orange warning patrol record lookup is implemented inside the web app. It reuses the existing patrol warning login/config/token cache, derives the patrol record endpoint from the warning list URL, and does not require controlling the phone App at runtime.
- 2026-08-04: Orange warning patrol records are cached locally at `data/patrol-warning-records-cache.json`. Queries reuse cached historical records and only fetch recent pages to merge new records; the cache scope is based on patrol record endpoint/platform/project and does not store account passwords or tokens.
- 2026-08-04: Orange warning stats table displays `次数 / 日期 / 时间 / 方向 / 巡查人 / 记录人`; adjacent up/down records still show as two rows, but the `次数` cell is merged with `rowspan` instead of repeating the same number twice. The local 18080 Docker test container should be rebuilt/replaced after UI changes, and the index page sends `Cache-Control: no-cache, max-age=0, must-revalidate` so browser refreshes pick up the latest UI.
- 2026-08-04: Orange warning adjacent up/down grouping is based on interval continuity: compare the previous record's `end_time` with the next record's `start_time`, not two start times. Example: `08:01-09:29` up and `09:30-10:02` down should count as one actual patrol and stay on the same stats page.
- 2026-08-04: Orange warning adjacent up/down grouping must allow the calendar date to cross midnight. A same-day-only condition incorrectly split pairs such as `23:57-00:37` up and `00:38-01:13` down into two actual patrols; date filtering uses the record start date, but grouping uses full timestamps and the interval window.
- 2026-08-04: When directional patrol records form a continuous time cluster, all adjacent records in the cluster belong to one actual patrol, even when the cluster contains three records or repeated directions. A standalone `双向` record remains an independent count and acts as a grouping boundary.
- 2026-08-04: Inline JavaScript changes in `app/static/index.html` should be syntax-checked with `node --check` on extracted script content and verified with a real browser click flow. A missing `);` in orange warning stats rendering broke all event binding even though static HTML tests passed.
- 2026-08-04: Orange warning stats export uses a canvas-generated PNG from the current filtered records, not only the current stats page. The exported long image should match the visible table style: merged count cells, dark group outlines for paired up/down and single `双向` rows, centered wrapped Chinese text, and the same summary counts.
- 2026-08-04: Orange warning stats now has two export actions: `导出表格` downloads an Excel-readable `.xls` table, while `导出图片` downloads a long PNG. Highlighted group borders are applied per cell with explicit edge classes so `rowspan` and collapsed table borders do not drop parts of the black outer frame.
- 2026-08-04: Orange warning PNG export should draw highlighted group borders with four solid `fillRect` bars after all cells/text are drawn. Do not use a single `strokeRect` for the group frame because canvas scaling/downsampling can make vertical borders look faint or missing in long exported images.
- 2026-08-04: Orange warning web stats table draws only the highlighted group outer edges. The `rowspan` count cell owns the left edge across paired rows, and when two highlighted groups touch, the next group's top edge is skipped because it is the same physical line as the previous group's bottom edge.
- 2026-08-05: WeChat interaction default templates should keep the original sample name `商邱宏` for patrol record and tunnel mechanical templates. Do not replace it with generic examples like `张三`; exact legacy defaults with `张三` are normalized back to the `商邱宏` defaults when loaded or saved.
- 2026-08-04: Orange warning stats pagination slices grouped patrol records, not raw patrol records. Rendering a stats page must convert the already-sliced groups directly into table rows; running the raw-record grouping function again on group objects will make date/time/person cells render as `-`.
- 2026-08-05: Orange warning actual-count grouping must consume at most one adjacent opposite-direction pair. Same-direction records and a third continuous record are separate counts; otherwise all-person batch data can collapse unrelated patrol teams into 10-18-record groups and undercount actual patrols. Web stats, Excel/PNG export, and WeChat image generation must use the same pair-only rule.
- 2026-08-05: Orange warning patrol record `responsible_person` / `recorder` fields can contain glued names without separators. Name parsing, cache dedupe, and single-person queries should split these strings with the known personnel name list first, then fall back to punctuation splitting.
- 2026-08-04: Orange warning stats names must wrap between complete names, never inside a name. The web table, Excel-readable export, and Canvas PNG export all split person lists by separators and keep each name `nowrap`, so names such as `张三` cannot render as `张`/`三` or other broken fragments.
- 2026-08-04: Built-in WeChat bridge supports patrol record queries. Sending `巡查记录` returns a copyable template; sending `查询姓名巡查记录 起始日期至结束日期` reuses the patrol record cache, filters the inclusive date range, generates a PNG with the same count/group rules as the web stats table, and sends the reply text followed by the image to the command room.
- 2026-08-04: Built-in WeChat interaction features are enabled by default and use the personal WeChat groups configured under message notification channels. The WeChat interaction settings page should manage command triggers, menu text, and reply templates, not group selection, a master enable switch, or per-business permission toggles; legacy feature-channel settings remain only as compatibility data.
- 2026-08-04: The WeChat interaction settings UI uses a left/right layout: the left side manages patrol record, tunnel mechanical entry, and tunnel mechanical modification trigger words plus reply templates; the right side shows notification rooms, menu preview, actions, and interaction logs. Tunnel template replies use the saved `tunnel_template` / `tunnel_modify_template` values and replace `{date}` with the current date.
- 2026-08-04: Do not put `side-block wide` on the two direct children of `#featureChannelSettings`; `.wide` sets `grid-column: 1 / -1` and forces the WeChat interaction page back into an up/down layout even when the panel grid has two columns.
- 2026-08-04: Inside each WeChat interaction template block, keep trigger words and reply template side by side on desktop: title spans the row, trigger words are the left column, and reply template is the right column. Collapse those inner columns only on narrow mobile width.
- 2026-08-04: NAS/Docker reboot should keep the built-in WeChat bridge logged in when `./wechat:/app/wechat` is mounted and the WeChat web session is still valid. The sidecar saves session memory immediately after login as well as during graceful shutdown; deleting or remapping `./wechat`, phone-side logout, WeChat-side session invalidation, or long offline periods can still require rescanning the QR code.
- 2026-08-07: @ 对象配置统一归口到“消息通知渠道”。企业微信群机器人保留 `mentioned_mobile_list` / `@all` 真 @；个人微信群 / Web 微信不再发送 `mention_ids`，只在消息正文前追加可见 `@姓名`，避免不稳定真 @ 导致发送失败或误导配置。监控班、自定义提醒、公路巡查页面不再单独配置 @ 对象。
- 2026-08-07: LightAgent/个人微信群推送不能只看 HTTP 200、`errcode`、`success` 或 `ok`；如果返回 JSON 里 `status` 是 `error`/`failed`/`failure`，必须记为发送失败，否则今日提醒会显示成功但群里实际收不到。
- 2026-08-07: 内置个人微信桥发送图片必须和文字一样等待 sidecar `send_result`，并且桥未登录/未连接时要先失败提示扫码登录；否则图片类提醒可能写成成功但实际没发出，或卡到超时才失败。
- 2026-08-07: “已导入排班”页面默认按 `confirmed_at` 显示最新导入月份，而不是沿用上次选中的旧月份；当前北京日期所在月份会用橙黄色横向/竖向路径默认标出当天早/中/晚监控班人员，默认高亮只延伸到当天班次单元格，相邻当天班次格共用一条边；手动点击仍使用蓝色整行整列高亮，二者颜色区分。
- 2026-08-08: 配置中心新增“导出全部配置/导入全部配置”，导出覆盖排班、通知渠道、人员、监控班、自定义提醒、公路巡查、隧道机电模板/账号配置，并随包带上内置个人微信桥 `wechat/identity.json` 的群/成员稳定 ID 映射；导入为覆盖式恢复，导入后前端会重新加载排班、今日提醒、人员和业务配置。微信登录二维码新增“刷新二维码”，仅在未连接/二维码过期时重启 sidecar，已登录时不破坏登录态。
- 2026-08-08: 公路巡查预警开始提醒不能只靠 `warning_key` 去重。新部署、配置导入或状态清空后，如果平台仍返回历史未结束预警，必须按预警开始/创建时间窗口拦截，避免把 7 月旧预警在 8 月重新当新预警推送；今日提醒和预警图片也不能继续显示超出窗口且没有结束时间的旧预警。
- 2026-08-09: 微信普通监控/提醒查询回复统一生成 PNG 图片并随查询回复发送；模板类命令（隧道机电模板、修改模板、巡查记录模板、绑定类）保持文本。个人监控班 daily/before_shift/monitor_test 支持图片卡片，同一人同一时刻多班次合并成一张卡片，计划层按人员+发送时间合并避免 7:50 固定提醒和班前提醒重复发送。
- 2026-08-10: `/api/wechat-query` 和手动模拟微信发送入口也必须先做命令识别；普通聊天/自定义提醒内容（例如 `@罗富耀\n需要开启隧道灯`）没有查询、监控、提醒模板、机电、巡查记录、绑定等命令意图时返回 `ignored` 且不写交互日志，避免被误当成功能命令回复帮助或图片。
- 2026-08-10: 自定义提醒的权威触发条件只能是“姓名 + 当天排班班次 + 提醒时间”。提醒文案开头手写的 `@姓名` 只作为旧配置兼容：与姓名一致时保存/发送前自动去掉，防止个人微信跳过统一 @ 前缀；与姓名不一致时拒绝新保存并跳过旧配置，避免某人没有对应班次却因为文案里手写 @ 被误提醒。
- 2026-08-11: 自定义提醒错 @ 的真实根因已用本机 Docker 最新镜像复现：计划层按“姓名+班次”生成是正确的，但通知渠道 `mention_mode=custom` 且 `mention_targets=罗富耀` 时，个人微信群发送层会把罗熙云/商邱宏的具体人员提醒统一渲染成 `@罗富耀`。修复原则：daily/before_shift/rest/custom/monitor_test/custom_test 这类有明确 `person_name` 的提醒，个人微信群可见 @ 和企业微信手机号 @ 都必须优先使用事件实际人员；固定指定对象只用于无具体人员的群通知。
- 2026-08-11: 服务器实查确认罗熙云早班 07:50 自定义提醒漏发不是排班错误，而是旧设备备份导入后 `custom_reminders` 中罗熙云/罗富耀的 `early` 开启隧道灯提醒时间被写成 `21:00`。已在服务器备份数据库后修正为 `07:50`；代码侧新增自定义提醒时间窗口：早班 00:00-08:00、中班 07:00-16:00、晚班 15:00-23:59。保存/测试接口拒绝越界时间，配置导入时自动把越界时间按班次默认值修正，防止旧备份再次导入错误。

- 2026-08-12: 公路巡查橙色预警开始提醒不能用固定 60 分钟窗口判断是否最新。平台可能在预警开始数小时后才返回/更新最新预警；开始提醒现在优先按 create_time（没有则 start_time）在结束后巡查窗口内判断新鲜度，避免 2026-08-12 03:45 开始、04:55 创建、08:32 首次查到的 S41 橙色预警被误判成历史预警。监控任务即使通知通道不可用也要先查询平台并更新 patrol_warning_state，只是不发送，避免前端一直看不到最新预警状态。

- 2026-08-12: 广东服务器实测完整功能矩阵时确认：当前已部署旧镜像的内置微信桥状态为 qr_ready，所有真实发送接口会失败但会正确记录失败；配置/排班/图片生成/微信模拟查询/隧道模板/巡查记录查询/公路预警平台查询均可用。功能通道测试曾使用旧 feature_channel 群而非通知渠道群，已改为优先使用消息通知渠道的个人微信群，避免配置不一致时 403。
- 2026-08-12: 广东服务器微信重新登录后实测真实发送可用：通知渠道测试、监控班测试、今日在岗图片测试、自定义提醒测试、公路巡查预警发送测试、微信交互模板测试均返回成功；普通查询模拟继续生成图片，模板类保持文本，普通聊天 `@罗富耀\n需要开启隧道灯` 仍为 ignored。Web 手机端适配应保留顶部主菜单/配置子菜单/大表格横向滚动，其余主体卡片、表单、提醒内容必须不撑宽页面。

- 2026-08-12: 消息通知渠道可配置多个个人微信群作为群池，但监控班提醒、自定义提醒、今日在岗提醒、公路巡查预警现在都可单独选择发送微信群；未选择时保持旧逻辑发送到全部通知群。发送计划、测试发送、定时发送、今日提醒预览、发送记录和失败补发都保留并使用目标群，避免多群场景误广播或补发到错误群。
- 2026-08-12: 个人微信群监控班图片提醒前置文字不能写“监控班提醒图片如下”。实际发送文案统一生成简短班次提示，例如 `@商邱宏今天是你的中班`；图片仍随后发送。测试发送接口返回实际发送文字，避免页面误判。
- 2026-08-15: 企业微信群内双向交互接入企业微信“智能机器人 API 模式”WebSocket 长连接，不再依赖普通群机器人 Webhook 接收消息。普通 Webhook 仍只负责通知发送；智能机器人使用 `WECOM_AIBOT_ENABLED/WECOM_AIBOT_ID/WECOM_AIBOT_SECRET` 或网页配置启动 sidecar，接收文本/语音转文字后走现有微信命令解析，先回复“正在查询”，最终用 stream 回复文本并可附带 PNG 查询图片。企业微信成员“绑定姓名/查询我的绑定”使用企业微信 userid 单独绑定到 `personnel_names.wecom_userid`，不复用个人微信群成员 ID。

- 2026-08-15: 企业微信自建应用启用后作为独占通知/交互通道：通知客户端优先且只使用自建应用，不再回退企业微信群机器人或个人微信群；智能机器人长连接会自动停用，个人微信桥/智能机器人收到命令也会忽略。自建应用通知按企业微信 `wecom_userid` 发送，个人提醒发给对应绑定成员，群体类提醒发给已绑定企业微信成员，未绑定时使用应用可见范围的 `@all`。
- 2026-08-15: 企业微信自建应用已接入自定义菜单创建接口；菜单按企业微信限制分为 3 个一级分类、每类不超过 5 个二级 click 菜单，菜单点击事件会通过回调 EventKey 映射为现有微信查询命令，自建应用配置页可一键创建/更新菜单。2026-08-16 调整为可编辑菜单配置，默认分为“监控在岗 / 机电预警 / 更多查询”；监控在岗菜单只放今日在岗、今日监控、明日监控、本周监控、我的监控，不再放“查询今日提醒/下次提醒”；机电预警只放机电模板、修改模板、橙色预警巡查记录查询；自建应用内除绑定和菜单/帮助外，未绑定使用菜单或发送命令都先提示绑定姓名，绑定后机电模板记录人和巡查记录查询姓名按企业微信 userid 绑定人员动态生成。
- 2026-08-16: 企业微信自建应用配置页新增独立“测试自建应用交互”按钮；测试会先保存配置、校验 CorpID/AgentId/Secret/Token/EncodingAESKey，再通过自建应用发送测试消息到已绑定企业微信成员（没有绑定则 @all 应用可见范围），收到后用户回复“菜单”验证回调交互是否生效。

- 2026-08-16: 通知渠道页面现在只有选择“个人微信群”时才显示和使用各业务的“发送到微信群”选择；企业微信群机器人/企业微信自建应用不会再显示或保存这些个人微信群目标。新增“查询休息/查询姓名休息”微信命令，按当月排班中的连续休息区间统计总天数、已休/剩余天数和距离下次休息天数；企业微信自建应用菜单默认“更多查询”加入查询休息。新增“假期余额提醒”独立配置页和导出配置表，按休息开始前一天、休息最后一天生成提醒，支持文字/图片/图文发送模式。今日在岗、监控班、自定义提醒也补充发送方式配置。

- 2026-08-16: 假期余额提醒文案从单条模板扩展为“开始休息文案库 / 假期余额不足文案库”，配置页每行一条，发送时使用 `secrets.choice` 随机抽取；默认文案库已内置牛马、自嘲、假期余额不足等网络热梗风格文案，导出/导入会随 `vacation_reminder_config` 一起迁移。
- 2026-08-16: 企业微信自建应用“机电预警”新增“录入今日机电”：首次使用先按企业微信 userid 绑定姓名，再发送“设置机电负责人姓名”保存默认搭档；点击菜单只生成今日录入确认信息，回复“确认”或“1”后才真正提交，复制模板修改天气/负责人/记录人后也会先更新待确认信息，不直接误提交。已有自定义菜单中如果存在“机电预警”分组且未满 5 项，会自动补上“录入今日机电”。
- 2026-08-16: 企业微信自建应用图文消息必须走官方 news 单条图文卡片；通知发送 helper 在 `image/both` 模式都优先发 news，查询类交互有图片时也发 news，不再把结果拆成文字一条、图片一条。流程类命令（绑定、设置机电负责人、录入确认）保持纯文字，避免无意义卡片。休息/假期余额提醒图片改为状态卡+信息卡+分段文案，查询休息图片改为统计卡+休息区间卡片。
- 2026-08-16: 企业微信自建应用菜单内置项改用稳定 EventKey（例如 DR_TUNNEL_TODAY_SUBMIT）并兼容旧的 DR_MENU_组_项 key，避免菜单顺序调整后已创建菜单点击映射到错命令或无反应；“机电预警”菜单会把“录入今日机电”强制排在第一位。录入今日机电确认提交如果因智慧养护平台账号/登录失败等原因失败，会保留待确认信息，用户修正配置后可继续回复“确认/1”重试，也可重新点击菜单生成新的确认信息。
- 2026-08-16: 企业微信自建应用自定义菜单配置页改为一级菜单标签页切换维护，只显示当前一级菜单下的二级菜单，避免全部菜单堆在一起且命令输入显示不全。自建应用普通文本不再先走数字菜单映射，只有 EventKey 点击才解析菜单 key；机电录入失败后的待确认状态支持回复 `1/重试` 重试、回复 `2/修改账号密码` 获取网页端修改账号密码和登录测试指引，避免 `2` 被误当成“查询今日在岗”。
- 2026-08-16: 微信/企业微信交互数字回复改为上下文状态驱动：普通数字不再全局映射菜单，只有用户刚收到“菜单/查询帮助”后的 5 分钟内，同一发送者回复序号才会执行，执行后立即消费状态；企业微信自建应用机电录入待确认也增加 prompt 状态，初次确认只允许 `确认/1`，失败后才允许 `1.重试 / 2.修改账号密码`，回复 2 后切换为只允许 `1/重试`，避免后续数字误触其它功能。

- 2026-08-16: 企业微信自建应用交互发送图文时，不能只把查询类 query_type 加入白名单；隧道机电录入成功 query_type=tunnel_mechanical、修改成功 query_type=tunnel_mechanical_modify、查询结果 query_type=tunnel_mechanical_result 只要带 /api/uploads 图片，都必须发送官方 news 图文卡片，流程确认/模板/绑定类仍保持文字。
- 2026-08-16: 企业微信自建应用 news 图文卡片的 description 不能直接复用微信图片回复文案，例如“休息查询结果如下：”；交互图文现在使用独立简短摘要：休息查询显示本月休息/已休/剩余天数，今日在岗显示早班/中班/晚班/明日早班摘要，巡查/隧道机电显示日期、条数/次数。图片内容和点击详情仍保留完整结果。
- 2026-08-16: 企业微信自建应用启用时，所有具体人员类提醒必须只发给已绑定 wecom_userid 的人员；未绑定人员不应出现在今日提醒/定时发送里，也不应通过“测试发送假期提醒”等测试入口退回默认群发或写失败记录。未绑定测试应直接 400 提示发送“绑定姓名”。
- 2026-08-16: 企业微信自建应用具体到人的所有发送入口都必须先绑定 `wecom_userid`：监控班测试、自定义提醒测试、通知渠道按人测试、假期测试、今日提醒计划、定时发送和失败补发都不能把未绑定人员回退到默认成员或 @all；`*_resend` 也要按原始事件类型识别个人目标。
- 2026-08-16: 企业微信自建应用增加公共通知接收人配置；具体到人的监控班/自定义/休息/假期提醒仍按姓名绑定的 `wecom_userid` 单发，公共类通知（今日在岗、公路预警、自建应用测试等）按配置的接收人发送，未配置时才发给全部已绑定成员，不再回退 `@all` 作为业务通知默认目标。监控班和自定义提醒列表会从人员名录解析并显示企业微信绑定状态，避免绑定在企业微信侧存在但业务配置页看不到。
- 2026-08-16: 配置导入前会自动用 SQLite backup 在 `data/backups/` 生成 `before-config-import-*.db`，导入成功后前端提示备份文件名；SQLite 连接启用 `busy_timeout=5000`、`foreign_keys=ON`，并尽量切换 WAL，降低定时任务、回调和网页配置同时读写时锁库风险。
- 2026-08-16: 系统状态页补充“系统自检”，检查数据库读写、调度、通知通道、中文字体、排班、监控人员、企业微信绑定、公路预警错误等；`/api/system-status` 返回 `checks` 和 `overall_status`，用于快速定位“为什么不发送/为什么不可用”。
- 2026-08-16: 企业微信自建应用公共通知支持按功能拆分接收人：今日在岗、公路预警、系统测试可以单独配置姓名列表；留空时回退公共通知接收人，再回退全部已绑定成员。今日提醒接口会返回未绑定被跳过的 `skipped_events`，并在分组状态里说明“已生成但未发送：未绑定企业微信”。
- 2026-08-16: 配置总览新增数据库备份列表、手动立即备份和备份下载；系统状态页新增上传/生成文件占用统计和“立即清理过期文件”。生成类图片/详情按 `GENERATED_UPLOAD_KEEP_DAYS` 清理，原始上传仍按 `UPLOAD_KEEP_DAYS` 保留，避免今日在岗、查询图片、图文详情长期堆积。
- 2026-08-16: 配置中心新增人员中心、交互命令管理和提醒诊断：人员中心汇总企业微信/个人微信群绑定、监控班、自定义提醒、休息提醒和机电搭档；交互命令管理集中展示命令是否需要绑定、是否能上菜单、当前菜单是否已配置；提醒诊断按日期解释 pending/due/skipped/not_generated，并且自定义提醒未生成按 reminder_id 区分，不能只按人员判断，避免同一个人有其它提醒时漏报“不是对应班次”。发送记录筛选支持状态、类型、目标和今日失败，目标筛选使用已脱敏/已映射的展示名称。

- 2026-08-16: 通知渠道前端切换必须以“当前 UI 选中的通道”为准，不能混用旧的已保存配置判断显示/提交。企业微信自建应用现在作为独占通道：选择自建应用会同步启用开关并隐藏个人微信群/群机器人不可用设置；切回企业微信群机器人或个人微信群会关闭自建应用启用状态，并且各业务“发送到微信群”只在个人微信群通道显示和提交。交互模板页的通道提示也按当前通道显示，避免自建应用时仍提示个人微信群。
- 2026-08-16: 隧道机电交互发送记录不能保存用户回复的确认数字作为可补发内容；录入/查询/修改记录保存实际结果文案，补发隧道机电记录会按日期重新生成结果图文，旧记录 content=1 也不会再把 1 当正文发送。纯数字 notification_room_id 视为旧无效发送目标，补发时回退默认目标并在前端标记为已失效。
- 2026-08-16: 企业微信自建应用现在支持直接发送排班表图片导入：回调收到 image 后用 MediaId 下载图片、保存上传目录、识别后自动执行模板核对，成功导入后发送官方 news 图文确认；如果同年同月已存在，会保存 5 分钟待确认状态，用户回复“覆盖导入/1”才覆盖，回复“取消导入/2”放弃。

- 2026-08-16: 企业微信自建应用图片导入排班不能对任意图片消息直接触发；默认菜单“更多查询”新增“导入排班”，点击后进入 5 分钟等待图片状态，只有同一用户随后发送的图片才下载识别。未进入导入模式时收到图片只提示先点击菜单，不下载、不解析、不导入，避免普通图片误覆盖排班。

- 2026-08-16: 企业微信自建应用排班导入确认图不能只显示前 8 人/前 6 条差异，也不能用粗略高度计算；图片现在按全部识别人员和全部差异动态计算长图高度，冲突时把所有可回复选项集中放到“下一步操作”卡片，news 摘要使用“回复 1 覆盖｜2 取消”等短提示，详情页提示改为上下滑动查看完整内容。
- 2026-08-16: 企业微信自建应用公共通知/功能通知接收人配置从手输姓名改为勾选已绑定人员；未绑定人员会显示但不可选择，公共/今日在岗/公路预警/系统测试都可分别选择接收人，留空仍按原有回退规则。
- 2026-08-16: 人员中心配置状态改为彩色状态标签：绿色表示已绑定/已启用/已配置，黄色表示已配置但停用或未启用，红色表示未绑定/未配置/未设置，避免所有状态同色看不出来。
- 2026-08-16: 配置状态视觉区分补齐到监控班列表、自定义提醒列表、企业微信绑定状态、个人微信群绑定列表、系统状态和系统自检；统一使用绿色正常、黄色停用/待处理、红色缺失/失败的状态标签。
