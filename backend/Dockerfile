# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12.13-slim
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
# Legacy XLS reader is a pinned optional parser dependency owned by upload.
COPY backend/app/modules/upload/requirements.txt /tmp/upload-requirements.txt
RUN uv sync --frozen --no-dev && uv pip install --no-deps -r /tmp/upload-requirements.txt \
    && useradd --uid 10001 --create-home askdata
COPY eval/ /app/eval/
COPY deploy/bootstrap_minio.py /app/deploy/bootstrap_minio.py
USER askdata
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
