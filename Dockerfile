# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIRBANK_BASE=https://my.birbank.business \
    BIRBANK_STATE=/data/state.json \
    BIRBANK_TOKEN_FILE=/data/token.json \
    BIRBANK_LOCK=/data/.poller.lock \
    POLL_INTERVAL_S=300 \
    LOG_LEVEL=INFO

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin birbank \
    && install -d -o birbank -g birbank -m 700 /data

WORKDIR /app
COPY --chown=root:root --chmod=644 birbank_poller.py /app/birbank_poller.py

USER birbank
VOLUME ["/data"]

# Credentials come from container env (Dokploy "Environment" tab / compose env_file),
# not from a baked-in .env file.
ENTRYPOINT ["python", "/app/birbank_poller.py"]
