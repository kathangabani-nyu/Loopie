"""Local entrypoint that loads uncommitted root env files before uvicorn."""

from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env.local", override=False)
load_dotenv(ROOT / ".env", override=False)


def _apply_migrations() -> None:
    """Mirror render.yaml's production start command (`alembic upgrade head
    && uvicorn ...`). A local docker-compose Postgres volume is easy to lose
    (container reset, `down -v`, Docker Desktop restart) and the resulting
    "migrations are not applied" crash is otherwise silent and easy to
    forget to fix by hand. A no-op when already at head; a soft warning
    (not a crash) when Postgres isn't reachable at all, so pure in-memory
    dev workflows without Postgres running aren't blocked by this."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(AGENT_ROOT / "alembic.ini"))
    try:
        command.upgrade(config, "head")
    except Exception as exc:
        print(
            f"[loopie_dev] Skipping auto-migration ({type(exc).__name__}: {exc}). "
            "If you're using a real Postgres, run `uv run alembic upgrade head` manually."
        )


if __name__ == "__main__":
    _apply_migrations()
    uvicorn.run("loopie_server:app", host="127.0.0.1", port=8001, reload=True)
