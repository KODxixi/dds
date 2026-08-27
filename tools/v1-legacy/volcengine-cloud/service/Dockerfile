# DDS Flask 云容器 — 火山引擎 Docker 镜像
# 构建上下文为项目根目录（docker compose context: ../../..）
#
# 构建命令：
#   docker build -t dds-app:latest -f cloud/volcengine/service/Dockerfile .
#
# 运行命令：
#   docker run -d --name dds-app -p 8080:8080 --env-file .env.dds-cloud dds-app:latest

FROM node:24-bookworm-slim AS web-deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.13-slim

# ── 系统依赖 ──────────────────────────────────────────────────────────────
# tesseract-ocr: OCR 识别（PDF 解析三级降级第二级）
# poppler-utils: pdf2image 依赖（PDF → 图片渲染）
# curl: 健康检查辅助工具
# libgomp1: duckdb/pandas 部分数值库需要的 OpenMP 运行时
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        poppler-utils \
        curl \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python 依赖（分层缓存：先装依赖，再拷代码） ──────────────────────────────
# 第一层：DDS 核心依赖（flask, duckdb, pandas, anthropic 等）
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 第二层：火山引擎 TOS 云端依赖（tos SDK 等）
COPY cloud/volcengine/requirements.volcengine.txt /app/cloud/volcengine/requirements.volcengine.txt
RUN pip install --no-cache-dir -r /app/cloud/volcengine/requirements.volcengine.txt

# ── 拷贝项目代码（显式 allowlist；不复制 Vault、env、data_out）────────────
COPY app.py index.html /app/
COPY scripts/ /app/scripts/
COPY web/ /app/web/
COPY templates/ /app/templates/
COPY data/ /app/data/
COPY cloud/volcengine/ /app/cloud/volcengine/
COPY --from=web-deps /app/node_modules /app/node_modules

# ── 环境变量 ──────────────────────────────────────────────────────────────
ENV DDS_MODE=cloud \
    DDS_FLASK_HOST=0.0.0.0 \
    DDS_FLASK_PORT=8080 \
    DDS_TOS_CACHE_DIR=/app/cache \
    DDS_REPORT_REVISION_ROOT=/app/data_out/report_revisions \
    DDS_RELEASE=current \
    DDS_TOS_RELEASE_KEY=dds/releases/current.json \
    PYTHONPATH=/app/scripts:/app/scripts/governance:/app/cloud/volcengine

# 创建缓存与持久化目录，并以非 root 用户运行。
RUN useradd --create-home --uid 10001 dds \
    && mkdir -p /app/cache /app/data_out/projects /app/data_out/report_revisions /tmp/dds_gov \
    && chown -R dds:dds /app/cache /app/data_out /tmp/dds_gov

USER dds

EXPOSE 8080

# ── 健康检查 ──────────────────────────────────────────────────────────────
# 调用 healthcheck.py，检查 /api/health 端点 + 数据池城市数
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -B /app/cloud/volcengine/service/healthcheck.py

# ── 启动 ──────────────────────────────────────────────────────────────────
CMD ["python", "app.py"]
