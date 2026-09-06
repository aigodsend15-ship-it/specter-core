# Specter Sovereign Core - Minimal Production Container
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SPECTER_HOME=/app

WORKDIR /app

RUN groupadd -r specter && useradd -r -g specter -d /app -s /sbin/nologin specter

COPY Core/ /app/Core/
COPY config/ /app/config/
COPY README.md LICENSE pyproject.toml setup.py /app/

RUN mkdir -p /app/storage && chown -R specter:specter /app

USER specter

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

ENTRYPOINT ["python", "-m", "Core.unified_inference_gateway"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
