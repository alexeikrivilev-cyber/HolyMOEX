FROM python:3.11-slim AS python_runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build
COPY requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

FROM postgres:16

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    SYSTEM_MODE=automatic_live_trading \
    RUN_MODE=live_trading \
    ARENA_GO_SANDBOX=true \
    SAFE_LIVE_SUBMIT=false \
    SINGLE_SCHEDULER_INSTANCE=true \
    DATA_DIR=/data \
    POSTGRES_DB=moex_agent \
    POSTGRES_USER=moex_agent \
    PGPORT=5432 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_DIR=/etc/ssl/certs

COPY --from=python_runtime /usr/local /usr/local
COPY --from=python_runtime /etc/ssl/certs /etc/ssl/certs

WORKDIR /app
COPY agent_app /app/agent_app
COPY scripts/start_autonomous.sh /app/scripts/start_autonomous.sh
RUN chmod +x /app/scripts/start_autonomous.sh

VOLUME ["/data"]

ENTRYPOINT ["/app/scripts/start_autonomous.sh"]
