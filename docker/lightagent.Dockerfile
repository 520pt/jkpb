FROM python:3.11-slim-bookworm

ARG INSTALL_LIGHTAGENT_BROWSER=false
ARG USE_CN_MIRROR=false

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright
ENV CHATGPT_ON_WECHAT_PREFIX=/app
ENV CHATGPT_ON_WECHAT_CONFIG_PATH=/app/config.json
ENV CHATGPT_ON_WECHAT_EXEC="python app.py"

WORKDIR /app

RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources; \
        pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        espeak \
        ffmpeg \
        fonts-wqy-zenhei \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN python - <<'PY'
from pathlib import Path

path = Path("/app/channel/web/web_channel.py")
text = path.read_text(encoding="utf-8")

if "class PushSendHandler:" not in text:
    text = text.replace("import hashlib\n", "import base64\nimport hashlib\n")
    text = text.replace("import shutil\n", "import shutil\nimport sys\n")
    text = text.replace(
        "            '/api/wechat-group/stickers/(.*)', 'WechatGroupStickersHandler',\n",
        "            '/api/wechat-group/stickers/(.*)', 'WechatGroupStickersHandler',\n"
        "            '/api/push/send', 'PushSendHandler',\n",
    )
    handler = r'''


class PushSendHandler:
    def POST(self):
        web.header('Content-Type', 'application/json; charset=utf-8')
        try:
            _require_push_auth()
            body = json.loads(web.data() or b"{}")
            channel = str(body.get("channel") or "wechat_group")
            if channel != const.WECHAT_GROUP:
                return json.dumps({"success": False, "error": "unsupported channel"}, ensure_ascii=False)
            target = str(body.get("target") or "").strip()
            if not target:
                return json.dumps({"success": False, "error": "target is required"}, ensure_ascii=False)
            group_channel = _get_running_channel(const.WECHAT_GROUP)
            if group_channel is None:
                return json.dumps({"success": False, "error": "wechat_group channel is not running"}, ensure_ascii=False)
            msgtype = str(body.get("msgtype") or "text").lower()
            if msgtype == "image":
                image = body.get("image") if isinstance(body.get("image"), dict) else {}
                image_path = _write_push_image_file(image)
                group_channel.client.send_image(target, image_path)
            else:
                text_payload = body.get("text") if isinstance(body.get("text"), dict) else {}
                content = str(text_payload.get("content") or body.get("content") or "")
                if not content:
                    return json.dumps({"success": False, "error": "content is required"}, ensure_ascii=False)
                mention_ids = text_payload.get("mention_ids") or body.get("mention_ids") or []
                if not isinstance(mention_ids, list):
                    mention_ids = []
                group_channel.client.send_text(target, content, mention_ids=mention_ids)
            return json.dumps({"success": True}, ensure_ascii=False)
        except web.HTTPError:
            raise
        except Exception as e:
            logger.exception("[PushSendHandler] push send failed: %s", e)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def _require_push_auth():
    token = str(os.environ.get("LIGHTAGENT_PUSH_TOKEN") or conf().get("push_api_token", "") or "").strip()
    if token:
        auth = str(web.ctx.env.get("HTTP_AUTHORIZATION") or "")
        prefix = "Bearer "
        supplied = auth[len(prefix):].strip() if auth.startswith(prefix) else ""
        if not hmac.compare_digest(supplied, token):
            raise web.HTTPError("401 Unauthorized",
                                {"Content-Type": "application/json; charset=utf-8"},
                                json.dumps({"success": False, "error": "Unauthorized"}))
        return
    _require_auth()


def _get_running_channel(channel_type):
    for module_name in ("app", "__main__"):
        module = sys.modules.get(module_name)
        getter = getattr(module, "get_channel_manager", None)
        if not getter:
            continue
        manager = getter()
        if manager is None:
            continue
        channel = manager.get_channel(channel_type)
        if channel is not None:
            return channel
    return None


def _write_push_image_file(image):
    encoded = str(image.get("base64") or "")
    if not encoded:
        raise ValueError("image.base64 is required")
    raw = base64.b64decode(encoded)
    expected_md5 = str(image.get("md5") or "").strip().lower()
    if expected_md5 and hashlib.md5(raw).hexdigest() != expected_md5:
        raise ValueError("image md5 mismatch")
    upload_dir = _get_upload_dir()
    path = os.path.join(upload_dir, "push-{}.png".format(uuid.uuid4().hex))
    with open(path, "wb") as handle:
        handle.write(raw)
    return path
'''
    marker = "\nclass FeishuRegisterHandler:"
    text = text.replace(marker, handler + marker)
    path.write_text(text, encoding="utf-8")
PY

RUN cp config-template.json config.json \
    && python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-optional.txt \
    && pip install --no-cache-dir -e . \
    && cd /app/channel/wechat_group/sidecar \
    && npm ci --omit=dev \
    && if [ "$INSTALL_LIGHTAGENT_BROWSER" = "true" ]; then \
        pip install --no-cache-dir "playwright==1.52.0" \
        && python -m playwright install-deps chromium \
        && python -m playwright install chromium; \
    fi \
    && mkdir -p /home/agent/lightagent \
    && groupadd -r agent \
    && useradd -r -g agent -s /bin/bash -d /home/agent agent \
    && chown -R agent:agent /home/agent /app /usr/local/lib

COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh \
    && chown agent:agent /entrypoint.sh

EXPOSE 9899

ENTRYPOINT ["/entrypoint.sh"]
