# syntax=docker/dockerfile:1
# Chimera: one image containing the built web UI and the Python backend.
# The image never contains or downloads model weights.
# Multi-architecture (linux/amd64, linux/arm64): the web UI is built once on
# the build machine's own platform; only the Python stage is per-architecture.

FROM --platform=$BUILDPLATFORM node:25-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ARG CHIMERA_BUILD_COMMIT=""
ARG CHIMERA_VERSION=""
LABEL org.opencontainers.image.title="Chimera" \
      org.opencontainers.image.description="Self-hosted, local-first AI chat for llama.cpp and OpenAI-compatible servers" \
      org.opencontainers.image.source="https://github.com/Riloative/chimera" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${CHIMERA_VERSION}" \
      org.opencontainers.image.revision="${CHIMERA_BUILD_COMMIT}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CHIMERA_BUILD_COMMIT=${CHIMERA_BUILD_COMMIT}
RUN groupadd --gid 10001 chimera && useradd --uid 10001 --gid chimera --create-home --shell /usr/sbin/nologin chimera
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/chimera backend/chimera
RUN pip install ./backend && rm -rf /root/.cache
COPY --from=frontend /src/frontend/dist /app/static
COPY LICENSE THIRD_PARTY_NOTICES.md /app/

ENV CHIMERA_HOST=0.0.0.0 \
    CHIMERA_PORT=8700 \
    CHIMERA_DATA_DIR=/data \
    CHIMERA_STATIC_DIR=/app/static
RUN mkdir -p /data && chown chimera:chimera /data
VOLUME ["/data"]
USER chimera
EXPOSE 8700
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/api/ready', timeout=4).status == 200 else 1)"
CMD ["chimera"]
