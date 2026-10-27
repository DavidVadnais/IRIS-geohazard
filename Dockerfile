FROM python:3.15-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt requirements-dev.txt requirements.lock ./
RUN pip wheel --wheel-dir /wheels -r requirements-dev.txt -c requirements.lock


FROM python:3.15-slim AS runtime

RUN useradd --create-home --uid 1000 iris

COPY --from=builder /wheels /wheels
COPY requirements.txt requirements-dev.txt requirements.lock ./
ARG INSTALL_DEV=true
RUN if [ "$INSTALL_DEV" = "true" ]; then REQ=requirements-dev.txt; else REQ=requirements.txt; fi \
    && pip install --no-cache-dir --no-index --find-links /wheels -r "$REQ" -c requirements.lock \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=iris:iris . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IRIS_CONFIG=/app/config.yaml

USER iris

ENTRYPOINT ["sh", "-c"]
CMD ["alembic upgrade head && python -m geohazard.runner"]
