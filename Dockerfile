FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir . && \
    useradd --uid 65532 --no-create-home --shell /usr/sbin/nologin autojudger && \
    mkdir /app/data && chown 65532:65532 /app/data

USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "autojudger.api:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "16", "--timeout-keep-alive", "5", "--no-proxy-headers", "--no-server-header"]
