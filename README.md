# 监控班提醒工具

一个可部署到服务器的排班提醒服务：上传每月排班图片，校对识别结果，配置监控班提醒和企业微信群机器人后，系统按日期自动提醒。

## 本地开发

```powershell
cd duty-reminder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
python -m pytest
```

## 运行

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

打开 `http://localhost:8080`。

Windows 本地也可以直接运行：

```powershell
.\启动监控班提醒.ps1
```

本地启动脚本默认启用定时提醒。只想测试界面、不发送定时提醒时：

```powershell
.\启动监控班提醒.ps1 -NoScheduler
```

## Docker 部署

### 本地构建运行

```bash
cp .env.example .env
docker compose up -d --build
```

部署前请先修改 `.env` 里的 `ADMIN_PASSWORD`、`LIGHTAGENT_WEB_PASSWORD`、`LIGHTAGENT_PUSH_TOKEN` 和模型 API key。设置 `ADMIN_PASSWORD` 后页面和接口会启用应用内登录保护，`/health` 保持不鉴权用于健康检查。

GitHub Actions 会自动构建镜像并推送到 GitHub Container Registry：

```text
ghcr.io/520pt/jkpb:latest
ghcr.io/520pt/jkpb-lightagent:latest
```

`docker-compose.yml` 会同时启动两个服务：

- `duty-reminder`：排班提醒服务，默认端口 `8080`。
- `lightagent`：本项目内维护的 `LightAgent/` 源码，提供 LightAgent Web 控制台和个人微信群通道，默认端口 `9899`。

`LightAgent/` 不是 Git submodule。同步上游 `yideng966/LightAgent` 时，只把上游改动合并到本仓库内的 `LightAgent/` 目录，并最终推送本仓库 `520pt/jkpb`。

### Docker 镜像部署

服务器 Docker 部署用 `docker-compose.prod.yml`。这个文件直接拉取已经发布的镜像：

```text
ghcr.io/520pt/jkpb:latest
ghcr.io/520pt/jkpb-lightagent:latest
```

不要用根目录的 `docker-compose.yml` 做服务器部署，那个文件用于本地源码构建和开发调试。

大部分固定配置已经写进镜像默认值：内部服务地址、微信查询接口、频道类型、Web 监听地址、上传清理、隧道机电登录保活等都不需要在 Compose 里配置。

账号、密码、token、模型 key 这些部署时需要确认的环境变量仍然保留在 Compose 里，直接打开 `docker-compose.prod.yml` 修改即可。

当前生产 Compose 的核心配置如下：

```yaml
services:
  lightagent:
    image: ghcr.io/520pt/jkpb-lightagent:latest
    container_name: lightagent
    restart: unless-stopped
    ports:
      - "9899:9899"
    environment:
      LIGHTAGENT_WEB_PASSWORD: 520pt
      LIGHTAGENT_PUSH_TOKEN: 520pt
      DUTY_REMINDER_QUERY_TOKEN: 520pt
      DEEPSEEK_API_KEY: ""
    volumes:
      - ./lightagent:/home/agent/lightagent

  duty-reminder:
    image: ghcr.io/520pt/jkpb:latest
    container_name: duty-reminder
    restart: unless-stopped
    ports:
      - "2222:8080"
    environment:
      ADMIN_USERNAME: 520pt
      ADMIN_PASSWORD: 520pt
      LIGHTAGENT_WEB_PASSWORD: 520pt
      LIGHTAGENT_PUSH_TOKEN: 520pt
      DUTY_REMINDER_QUERY_TOKEN: 520pt
    volumes:
      - ./data:/app/data
      - ./uploads:/app/uploads
    depends_on:
      - lightagent
```

部署步骤：

```bash
docker compose -f docker-compose.prod.yml up -d
```

部署前一般只需要确认 `ADMIN_USERNAME`、`ADMIN_PASSWORD`、`LIGHTAGENT_WEB_PASSWORD`、`LIGHTAGENT_PUSH_TOKEN`、`DUTY_REMINDER_QUERY_TOKEN` 和 `DEEPSEEK_API_KEY`。其中 `LIGHTAGENT_PUSH_TOKEN` 和 `DUTY_REMINDER_QUERY_TOKEN` 两个服务里要保持一致。更新镜像时再执行一次：

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

图片导入默认使用固定排班表模板解析：检测表格线并按单元格颜色/像素特征识别班次，不再对整张图片运行 OCR。姓名列会做局部 OCR，识别不到的姓名需要在导入校对页面手动修正。

RapidOCR 依赖保留用于兼容旧代码路径。需要额外安装 PaddleOCR 大模型依赖做本地实验时，可以使用：

```bash
INSTALL_OCR=true docker compose up -d --build
```

### Docker 持久化存储

服务器简洁版 `docker-compose.prod.yml` 会把业务数据保存到当前目录：

- `./data:/app/data`：SQLite 数据库、排班、配置、发送记录。
- `./uploads:/app/uploads`：上传的排班图片。

因此更新镜像、重新 `docker compose -f docker-compose.prod.yml up -d` 后数据仍会保留。不要删除服务器目录里的 `data` 和 `uploads`。

本地构建用的 `docker-compose.yml` 默认使用 Docker volume：

- `duty-data:/app/data`：SQLite 数据库、排班、配置、发送记录。
- `duty-uploads:/app/uploads`：上传的排班图片。

不要执行 `docker compose down -v` 或手动删除 `duty-data` / `duty-uploads` volume，否则会删除本地构建环境的数据。

备份可以先查看实际 volume 名：

```bash
docker volume ls | grep duty
```

再把 volume 打包到当前目录，例如：

```bash
docker run --rm -v duty-reminder_duty-data:/data -v "$PWD":/backup alpine tar czf /backup/duty-data-backup.tgz -C /data .
docker run --rm -v duty-reminder_duty-uploads:/data -v "$PWD":/backup alpine tar czf /backup/duty-uploads-backup.tgz -C /data .
```

## 通知通道配置

页面“配置中心 / 消息通知渠道”里可以选择两种发送通道。

### 企业微信群机器人

选择“企业微信群机器人”后填写群机器人地址，例如：

```text
https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_WEBHOOK_KEY
```

保存后前端不会回显完整机器人地址，只显示“已配置”。监控班提醒里的“@ 手机号”填写企业微信成员手机号，机器人会通过 `mentioned_mobile_list` 在群里 @ 对应人员。页面提供“测试发送”按钮，驾驶员监测板块也提供“测试发送今日在岗”按钮。

### LightAgent 个人微信群

选择“LightAgent 个人微信群”后填写 LightAgent 地址、添加一个或多个通知微信群和可选 token。`duty-reminder` 会把文本和图片提醒 POST 到 LightAgent，由 LightAgent 侧负责个人微信扫码登录和微信群发送。

同一个 Docker Compose 网络内部，地址通常填写：

```text
http://lightagent:9899
```

页面里的“推送 token”填写 `LIGHTAGENT_PUSH_TOKEN` 的值。通知群可以在页面同步微信群后添加多个；“微信群交互配置”用于配置哪些微信群可以主动触发查询、隧道机电和排班导入。

接入细节见 [docs/LightAgent-WeChat.md](docs/LightAgent-WeChat.md)。

## 运行安全

- `ADMIN_PASSWORD`：设置后启用应用内登录保护。
- `ADMIN_SESSION_SECRET`：可选，用于固定登录 cookie 签名密钥；不设置时使用 `ADMIN_PASSWORD`。
- `MAX_UPLOAD_MB`：限制上传图片大小，默认 `10`。
- `UPLOAD_KEEP_DAYS`：自动清理超过指定天数的旧上传图片，默认 `90`。
- `TUNNEL_MECHANICAL_KEEPALIVE_ENABLED`：隧道机电登录保活开关，默认 `true`。开启定时任务后会自动提前刷新智慧养护 token。
- `TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES`：保活检查间隔，默认 `30` 分钟。
- `TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES`：距离 token 过期多少分钟内提前刷新，默认 `30` 分钟。
- Docker 镜像已安装 `fonts-noto-cjk` 并刷新字体缓存，用于生成中文图片；如果图片中文乱码，请重新 build 镜像，不要继续使用旧镜像。也可以通过 `CJK_FONT_PATH=/app/fonts/your-font.ttf` 指定挂载进容器的中文字体。
- 页面“发送记录”可查看最近发送时间、类型、状态和失败原因。

## 提醒规则

- 每个监控班提醒人员只在对应日期有 `早`、`中`、`晚` 时提醒。
- 每日固定提醒默认 `07:50`，可按人员设置。
- 上班前提醒默认提前 `10` 分钟，可按人员设置。
- 早班时间为 `00:00至08:00`，提醒归到前一天，例如 `2025-09-16` 早班会在 `2025-09-15 23:50` 触发提前提醒。
- 今日在岗人员提醒会汇总当天监控早/中/晚班、在岗驾驶员、备勤人员、今日下午休息、正在休息、今日下午到岗人员。
- 休息提醒只对已添加的监控班提醒人员生效，并按休息区间区分状态：休息开始前一天提示“今日下午休息”，连续休息中提示“正在休息到 YYYY-MM-DD”，休息最后一天提示“今日下午到岗”。
