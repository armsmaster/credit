# Build context: ./backend  (needs libs/cds-common next to the service)
ARG DOCKERHUB_MIRROR=docker.io
FROM ${DOCKERHUB_MIRROR}/library/python:3.12.8-slim

# Corporate mirrors / proxy. HTTP(S)_PROXY and NO_PROXY are Docker's predefined
# build args: they are honoured by pip but are not persisted in the image.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/cds-common

WORKDIR /app
COPY services/credit/requirements.txt /tmp/requirements.txt
# Cache mount: downloaded wheels survive rebuilds and interrupted builds (not stored in the image)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "$PIP_INDEX_URL" ${PIP_TRUSTED_HOST:+--trusted-host "$PIP_TRUSTED_HOST"} \
      -r /tmp/requirements.txt

COPY libs/cds-common/cds_common /opt/cds-common/cds_common
COPY services/credit/ /app/

RUN useradd --system --uid 10001 --no-create-home app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn credit_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
