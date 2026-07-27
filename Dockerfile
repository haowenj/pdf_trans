FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDF_TRANS_WEB_HOST=0.0.0.0 \
    PDF_TRANS_WEB_PORT=8000

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" '.[web]' \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/web \
    && chown -R appuser:appuser /app

USER appuser

VOLUME ["/app/data/web"]
EXPOSE 8000

CMD ["python", "-m", "pdf_trans.web"]
