"""Windows asyncio event loop compatibility for async psycopg.

psycopg's async mode refuses to run under the default Windows
`ProactorEventLoop` (it needs selector-based sockets). Production runs on
Linux (Render), where this is a no-op, but any local Windows dev process that
touches the Postgres pool — `uvicorn loopie_server:app`, the durable worker,
or a test importing `psycopg_pool.AsyncConnectionPool` directly — needs the
selector policy set before the first event loop is created.
"""

from __future__ import annotations

import asyncio
import sys


def ensure_selector_event_loop_policy() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
