# syntax=docker/dockerfile:1
# Multi-stage build: WebUI (Node) + runtime (Python).
# Build:  docker build -t deepseek-web2api-free .
# Run:    docker run -d -p 18080:18080 -v ./data:/app/data --env-file .env deepseek-web2api-free
# Slow network? Add:
#   --build-arg NPM_REGISTRY=https://registry.npmmirror.com
#   --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# ---------- WebUI ----------
FROM node:22-alpine AS webui
WORKDIR /build/webui-new
COPY webui-new/package.json webui-new/package-lock.json ./
ARG NPM_REGISTRY
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm ci
COPY webui-new/ ./
RUN npm run build

# ---------- Runtime ----------
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
ARG PIP_INDEX_URL
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi
COPY . ./
COPY --from=webui /build/webui-new/dist ./webui-new/dist
EXPOSE 18080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '18080') + '/health', timeout=4)"
CMD ["sh", "-c", "python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-18080}"]
