# Chimera headless browser (optional; docker compose --profile browser).
# Chromium and its system libraries come with the official Playwright image,
# pinned to the same version as the Python package: nothing is downloaded at
# runtime.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble
ARG CHIMERA_BUILD_COMMIT=""
ARG CHIMERA_VERSION=""
LABEL org.opencontainers.image.title="Chimera headless browser" \
      org.opencontainers.image.description="Isolated Chromium renderer for Chimera's web reader" \
      org.opencontainers.image.source="https://github.com/Riloative/chimera" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${CHIMERA_VERSION}" \
      org.opencontainers.image.revision="${CHIMERA_BUILD_COMMIT}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt \
    && python -c "from importlib.metadata import version; assert version('playwright') == '1.56.0'"
COPY chimera_browser.py .

# The image's unprivileged user.
USER pwuser
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=4).status == 200 else 1)"
CMD ["python", "chimera_browser.py"]
