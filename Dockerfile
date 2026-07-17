# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app/frontend

# Copy package files
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/.npmrc ./

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy frontend source
COPY frontend/ ./

# Fetch persona emoji assets (curated set from scripts/emoji-allowlist.json).
# The build host has registry mirror access; the generated webp files ship into
# dist/emoji-assets/ so the intranet runtime has zero network dependency.
RUN node scripts/fetch-emoji-assets.mjs

# Build frontend
RUN pnpm run build

# Stage 2: Runtime image
FROM python:3.12-slim

WORKDIR /app

# Create non-root user early so subsequent COPY can take ownership directly,
# avoiding a separate `chown -R /app` layer that would duplicate ~400MB.
RUN groupadd -r -g 1000 app && \
    useradd -r -u 1000 -g app -m app && \
    mkdir -p /home/app/.cache /app && \
    chown -R app:app /home/app /app

# Install uv (as root; uv binary is world-executable)
RUN pip install --no-cache-dir uv

# Copy dependency files as app so the venv it creates is app-owned
COPY --chown=app:app pyproject.toml uv.lock* README.md ./

USER app

# Install runtime dependencies into the image. Keep uv's download cache during
# builds so repeated image builds do not re-download unchanged wheels.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=1000,gid=1000 \
    uv sync --frozen --no-dev --no-install-project

# Copy source code and frontend static files as app
COPY --chown=app:app src/ ./src/
COPY --chown=app:app main.py ./
COPY --chown=app:app --from=frontend-builder /app/frontend/dist ./static

EXPOSE 8000

CMD ["/app/.venv/bin/python", "main.py"]
