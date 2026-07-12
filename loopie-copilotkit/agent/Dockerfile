FROM python:3.13-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./

FROM base AS test
RUN uv sync --frozen --dev
COPY alembic.ini pytest.ini loopie_server.py loopie_graph.py loopie_control.py ./
COPY migrations ./migrations
COPY src ./src
COPY tests ./tests

FROM base AS runtime
RUN uv sync --frozen --no-dev

COPY alembic.ini loopie_server.py loopie_graph.py loopie_control.py ./
COPY migrations ./migrations
COPY src ./src

EXPOSE 8000

CMD ["sh", "-c", "uvicorn loopie_server:app --host 0.0.0.0 --port ${PORT:-8000}"]
