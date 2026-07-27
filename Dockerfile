FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDF_TRANS_WEB_HOST=0.0.0.0 \
    PDF_TRANS_WEB_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir '.[web]' \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/web \
    && chown -R appuser:appuser /app

USER appuser

VOLUME ["/app/data/web"]
EXPOSE 8000

CMD ["python", "-m", "pdf_trans.web"]
