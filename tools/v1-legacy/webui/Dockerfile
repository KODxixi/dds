FROM node:24-bookworm-slim AS web-deps

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.13-slim

WORKDIR /app

# System deps for PDF scraping (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
COPY cloud/volcengine/requirements.volcengine.txt ./cloud/volcengine/requirements.volcengine.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r cloud/volcengine/requirements.volcengine.txt

# App: explicit allowlist. Vault、data_out、env 和宿主 node_modules 不进入镜像。
COPY app.py index.html ./
COPY scripts/ ./scripts/
COPY web/ ./web/
COPY templates/ ./templates/
COPY data/ ./data/
COPY cloud/volcengine/ ./cloud/volcengine/
COPY --from=web-deps /app/node_modules /app/node_modules

RUN useradd --create-home --uid 10001 dds \
    && mkdir -p /app/cache /app/data_out/projects /app/data_out/report_revisions \
    && chown -R dds:dds /app/cache /app/data_out

USER dds

EXPOSE 8080
CMD ["python", "app.py"]
