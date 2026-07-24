FROM python:3.11-slim

ARG INSTALL_OCR=false

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai
ENV DATA_DIR=/app/data
ENV UPLOAD_DIR=/app/uploads
ENV ENABLE_SCHEDULER=true
ENV NOTIFICATION_SENDER_TYPE=lightagent
ENV LIGHTAGENT_BASE_URL=http://lightagent:9899
ENV MAX_UPLOAD_MB=10
ENV UPLOAD_KEEP_DAYS=90
ENV TUNNEL_MECHANICAL_KEEPALIVE_ENABLED=true
ENV TUNNEL_MECHANICAL_KEEPALIVE_INTERVAL_MINUTES=30
ENV TUNNEL_MECHANICAL_KEEPALIVE_REFRESH_BEFORE_MINUTES=30

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl fontconfig fonts-noto-cjk libgomp1 libgl1 libglib2.0-0 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir -e . \
    && if [ "$INSTALL_OCR" = "true" ]; then pip install --no-cache-dir -e ".[ocr]"; fi

RUN mkdir -p /app/data /app/uploads

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
