FROM python:3.13.18-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.13.18-slim-trixie
RUN useradd --uid 1000 --no-create-home curator
WORKDIR /app
COPY --from=build /app ./
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER curator
EXPOSE 8000
CMD ["uvicorn", "--factory", "paper_curator.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
