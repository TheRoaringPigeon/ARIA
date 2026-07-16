FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ARG SERVICE

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
