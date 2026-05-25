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
    ARENA_GO_SHORTS_ALLOWED=false \
    DECISION_ALLOW_SHORT_SELLING=true \
    DECISION_USE_POST_COST_EDGE_FOR_ACTIONS=true \
    DECISION_EXIT_USE_POST_COST_EDGE=true \
    DECISION_PARTIAL_TAKE_PROFIT_ENABLED=true \
    DECISION_PROFIT_LOCK_ENABLED=true \
    DECISION_SHORT_USE_POST_COST_EDGE=true \
    DECISION_ALLOW_LONG_TO_SHORT_FLIP=false \
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
COPY scripts /app/scripts
RUN find /app/scripts -type f -name "*.sh" -exec sed -i 's/\r$//' {} \; \
    && find /app/scripts -type f -name "*.sh" -exec chmod +x {} \;

VOLUME ["/data"]

ENTRYPOINT ["/app/scripts/start_autonomous.sh"]
