FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG SERVICE

# Needed by worker's pytesseract/pdf2image for the OCR stage. Installed
# unconditionally — this image is already shared across all 3 Python
# services, and conditional per-SERVICE apt logic isn't worth the
# complexity for one extra layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY libs/shared /app/libs/shared
COPY libs/auth /app/libs/auth
COPY services/core-api/pyproject.toml /app/services/core-api/pyproject.toml
COPY services/ai-service/pyproject.toml /app/services/ai-service/pyproject.toml
COPY services/worker/pyproject.toml /app/services/worker/pyproject.toml

WORKDIR /app/services/${SERVICE}
RUN uv sync --frozen

COPY services/${SERVICE} /app/services/${SERVICE}
