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
    && sed -i 's/\r$//' /entrypoint.sh \
    && chown agent:agent /entrypoint.sh

EXPOSE 9899

ENTRYPOINT ["/entrypoint.sh"]
