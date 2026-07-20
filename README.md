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

部署前请先修改 `.env` 里的 `ADMIN_PASSWORD`。设置后页面和接口会启用 Basic Auth 登录保护，`/health` 保持不鉴权用于健康检查。

GitHub Actions 会自动构建镜像并推送到 GitHub Container Registry：

```text
ghcr.io/520pt/jkpb:latest
```

### 服务器推荐部署

直接用仓库里的 `docker-compose.prod.yml`，结构尽量保持简单：

```yaml
services:
  duty-reminder:
    image: ghcr.io/520pt/jkpb:latest
    container_name: duty-reminder
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      TZ: Asia/Shanghai
      ENABLE_SCHEDULER: "true"
      ADMIN_USERNAME: admin
      ADMIN_PASSWORD: CHANGE_THIS_PASSWORD
    volumes:
      - ./data:/app/data
      - ./uploads:/app/uploads
```

部署步骤：

```bash
docker compose -f docker-compose.prod.yml up -d
```

部署前把 `docker-compose.prod.yml` 里的 `ADMIN_PASSWORD: CHANGE_THIS_PASSWORD` 改成你自己的密码。更新镜像时再执行一次：

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

默认 Docker 构建会安装轻量 RapidOCR，用于识别排班图片里的姓名、年月，再结合模板识别班次颜色。需要额外启用 PaddleOCR 大模型兜底时，本地构建可以使用：

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

## 企业微信配置

在页面“设置 / 企业微信通知”里填写群机器人地址，例如：

```text
https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_WEBHOOK_KEY
```

保存后前端不会回显完整机器人地址，只显示“已配置”。监控班提醒里的“@ 手机号”填写企业微信成员手机号，机器人会通过 `mentioned_mobile_list` 在群里 @ 对应人员。页面提供“测试发送”按钮，驾驶员监测板块也提供“测试发送今日在岗”按钮。

## 运行安全

- `ADMIN_PASSWORD`：设置后启用登录保护。
- `MAX_UPLOAD_MB`：限制上传图片大小，默认 `10`。
- `UPLOAD_KEEP_DAYS`：自动清理超过指定天数的旧上传图片，默认 `90`。
- Docker 镜像已安装 `fonts-noto-cjk`，用于生成中文“今日在岗”图片。
- 页面“发送记录”可查看最近发送时间、类型、状态和失败原因。

## 提醒规则

- 每个监控班提醒人员只在对应日期有 `早`、`中`、`晚` 时提醒。
- 每日固定提醒默认 `07:50`，可按人员设置。
- 上班前提醒默认提前 `10` 分钟，可按人员设置。
- 早班时间为 `00:00至08:00`，提醒归到前一天，例如 `2025-09-16` 早班会在 `2025-09-15 23:50` 触发提前提醒。
- 今日在岗人员提醒会汇总当天监控早/中/晚班、在岗驾驶员、备勤人员、今日下午休息、正在休息、今日下午到岗人员。
- 休息提醒只对已添加的监控班提醒人员生效，并按休息区间区分状态：休息开始前一天提示“今日下午休息”，连续休息中提示“正在休息到 YYYY-MM-DD”，休息最后一天提示“今日下午到岗”。
