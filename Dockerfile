FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gosu \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --all-extras --no-editable

RUN uv run playwright install --with-deps chromium

# Build the web frontend into the image so the API serves it same-origin at
# /app. No STUDYLENS_BACKEND_URL is set, so the bundled app uses its own origin.
COPY web ./web
RUN cd web && npm ci && npm run build && rm -rf node_modules

RUN useradd --create-home --uid 1000 studylens \
 && mkdir -p /app/data /home/studylens/.cache \
 && cp -r /root/.cache/ms-playwright /home/studylens/.cache/ms-playwright \
 && chown -R studylens:studylens /app /home/studylens

COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/studylens
# Package is installed non-editable, so point the static mount at the build.
ENV WEB_DIST_DIR=/app/web/dist

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn studylens.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
