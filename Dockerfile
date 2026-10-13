# syntax=docker/dockerfile:1
# Chimera: one image containing the built web UI and the Python backend.
# The image never contains or downloads model weights.

FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --gid 10001 chimera && useradd --uid 10001 --gid chimera --create-home --shell /usr/sbin/nologin chimera
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/chimera backend/chimera
RUN pip install ./backend && rm -rf /root/.cache
COPY --from=frontend /src/frontend/dist /app/static

ENV CHIMERA_HOST=0.0.0.0 \
    CHIMERA_PORT=8700 \
    CHIMERA_DATA_DIR=/data \
    CHIMERA_STATIC_DIR=/app/static
RUN mkdir -p /data && chown chimera:chimera /data
VOLUME ["/data"]
USER chimera
EXPOSE 8700
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/api/health', timeout=4).status == 200 else 1)"
CMD ["chimera"]
