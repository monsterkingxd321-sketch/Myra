# ─────────────────────────────────────────────────────────────────────────────
#  Myra — hybrid Hinglish chatbot + multi-userbot AutoChat
#
#  Multi-stage build:
#    builder → installs every dependency into a self-contained virtualenv
#    runtime → slim image with only the venv + the code (no C toolchain)
#
#  Used automatically by Railway (see railway.json → builder: DOCKERFILE), and
#  it works unchanged on Render, Fly.io, any VPS and plain local Docker:
#
#     docker build -t myra-bot .
#     docker run -d --env-file .env -p 5000:5000 --name myra-bot myra-bot
#
#  The Flask health server binds to $PORT (Railway/Render inject it): /  and  /health
# ─────────────────────────────────────────────────────────────────────────────

# ══════════════════════════════ Stage 1 — builder ═══════════════════════════
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# Only used if a dependency has no prebuilt wheel for linux/amd64.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Self-contained venv → copied as-is into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
RUN pip install --upgrade pip wheel setuptools

COPY requirements.txt .
RUN pip install -r requirements.txt

# 🛑 Build gate — `pyrogram` MUST be the Kurigram fork. Plain `pyrogram` (2.0.x,
# API layer 151/158) makes Telegram reject EVERY userbot login with
# `406 UPDATE_APP_TO_LOGIN`. Fail the build here instead of shipping a bot whose
# Add-Userbot button can never work.
COPY tools/check_pyrogram.py .
RUN python check_pyrogram.py

# ══════════════════════════════ Stage 2 — runtime ═══════════════════════════
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Asia/Kolkata \
    PORT=5000

# ca-certificates → HTTPS to Telegram / MongoDB / Gemini
# tzdata          → sane timestamps in log.txt and the platform logs
# curl            → the HEALTHCHECK below
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 myra

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# .env, .git, *.session and the logs are excluded via .dockerignore
COPY --chown=myra:myra . .

# The bot appends to log.txt next to the code → keep /app writable for `myra`.
RUN mkdir -p /app/sessions && chown -R myra:myra /app

# Never run a Telegram client as root.
USER myra

EXPOSE 5000

# Liveness probe — the same URL Railway uses (railway.json → healthcheckPath).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# exec form + `-u`: unbuffered logs (so Railway shows them live) and the process
# receives SIGTERM/SIGINT directly for a clean shutdown.
CMD ["python3", "-u", "-m", "Myra"]
