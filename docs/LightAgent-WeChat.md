# LightAgent 个人微信群通知接入

`duty-reminder` 已支持把提醒发送到 LightAgent 个人微信群通道。这样排班提醒仍由本项目负责，个人微信群登录、群消息发送由 LightAgent 负责。

## 前提

1. 部署并启动 LightAgent。
2. 在 LightAgent 的 `config.json` 中启用 `wechat_group` 渠道，并安装 `channel/wechat_group/sidecar` 的 npm 依赖。
3. 进入 LightAgent Web 控制台，扫码登录个人微信，选择目标微信群并记录群的 `room_id`。
4. 使用本仓库的 `docker/lightagent.Dockerfile` 构建 LightAgent 镜像。该镜像会在构建阶段给 LightAgent Web 服务注入 `/api/push/send` 推送入口。

LightAgent 当前上游主要提供 Web 控制台和内部调度能力，没有稳定的外部 `/send` API。因此本仓库把 LightAgent 作为 Git submodule 引入，并只在 Docker 镜像构建阶段注入一个很小的推送 API；不会直接修改上游 submodule 源码。

## Docker Compose

本仓库的 `docker-compose.yml` 和 `docker-compose.prod.yml` 会同时启动：

- `duty-reminder`：排班提醒服务。
- `lightagent`：LightAgent Web 控制台和个人微信群通道。

同一个 Compose 网络内，`duty-reminder` 页面里的 LightAgent 推送地址填写：

```text
http://lightagent:9899/api/push/send
```

`推送 token` 填写 Compose 环境变量 `LIGHTAGENT_PUSH_TOKEN` 的值。

## duty-reminder 页面配置

打开 `duty-reminder` 页面：

1. 进入“设置 / 企业微信通知”。
2. “发送通道”选择 `LightAgent 个人微信群`。
3. 填写：
   - `LightAgent 推送地址`：例如 `http://lightagent:9899/api/push/send`
   - `目标群 room_id`：LightAgent 群聊管理里的目标群 ID
   - `推送 token`：可选，填写后请求会带 `Authorization: Bearer <token>`
4. 点击“测试发送”。

## 推送网关契约

文本消息：

```json
{
  "channel": "wechat_group",
  "target": "room-id",
  "msgtype": "text",
  "text": {
    "content": "提醒内容",
    "mentioned_mobile_list": ["10000000000"]
  }
}
```

图片消息：

```json
{
  "channel": "wechat_group",
  "target": "room-id",
  "msgtype": "image",
  "image": {
    "base64": "PNG_BASE64",
    "md5": "image-md5"
  }
}
```

成功响应可返回任一格式：

```json
{"success": true}
```

```json
{"ok": true}
```

```json
{"errcode": 0}
```

失败时返回非 2xx，或返回 `{"success": false, "error": "原因"}` / `{"ok": false, "error": "原因"}` / `{"errcode": 1, "errmsg": "原因"}`。

## 边界

- 企业微信群机器人仍然可用，选择 `企业微信群机器人` 后保持原来的 webhook 发送方式。
- 个人微信/微信群登录稳定性取决于 LightAgent 使用的 Wechaty sidecar 和微信账号状态。
- `mentioned_mobile_list` 是为了兼容现有排班配置保留的字段。当前推送 API 发送普通文本；如果需要真实微信 mention，需要扩展为把手机号映射为群成员 ID 后传入 `mention_ids`。
