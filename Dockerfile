# Optional container build (Linux). ComfyUI stays on the host: set its URL to http://host.docker.internal:8188.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git espeak-ng fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY workflows/ workflows/
COPY --from=web /web/dist frontend/dist
ENV AUTOCONTENT_HOST=0.0.0.0 AUTOCONTENT_STORAGE_DIR=/data
WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "app"]
