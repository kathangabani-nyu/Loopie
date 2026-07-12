#!/usr/bin/env python3
"""Gate Cursor provider wiring — must pass before LOOPIE_PROVIDER_CURSOR_ENABLED=1."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.loopie.llm import DecisionSchema  # noqa: E402
from src.loopie.providers import provider_registry, write_cursor_smoke_marker  # noqa: E402
from src.loopie.stores.llm_cache import cache_key, get_cached, set_cached  # noqa: E402


def main() -> int:
    registry = provider_registry()
    cursor = registry.get("cursor")
    if not cursor or not cursor.api_key:
        print("SKIP: CURSOR_API_KEY / LOOPIE_CURSOR_API_KEY not set")
        return 0

    from langchain_openai import ChatOpenAI

    from src.loopie.providers import openai_client_kwargs

    model = ChatOpenAI(**openai_client_kwargs(cursor))
    structured = model.with_structured_output(DecisionSchema, strict=True, include_raw=True)
    prompt = (
        "Ticket security_flag=true with refund request and security_flag_blocks_refund rule present. "
        "Choose escalate_security and propose escalate_tool."
    )
    raw = structured.invoke(prompt)
    parsed = raw["parsed"] if isinstance(raw, dict) else raw
    action = parsed.action.value if hasattr(parsed.action, "value") else str(parsed.action)
    if action != "escalate_security":
        print(f"FAIL: unexpected action {action}")
        return 1

    key = cache_key(
        model=cursor.model,
        node="smoke",
        fixture_id="cursor_smoke",
        artifact_version="v1",
        provider="cursor",
        prompt_version="v1",
        schema_version="v1",
        artifact_hash="smoke",
    )
    set_cached(key, json.dumps({"action": action}))
    if get_cached(key) is None:
        print("FAIL: cache round-trip failed")
        return 1

    os.environ["LOOPIE_CURSOR_SMOKE_OK"] = "1"
    marker = write_cursor_smoke_marker()
    print("PASS: Cursor smoke test — structured output + cache round-trip")
    print("")
    print("Cursor is enabled for subsequent shells via marker file:")
    print(f"  {marker}")
    print("")
    print("Optional (same effect in CI shells without the marker file):")
    print("  export LOOPIE_CURSOR_SMOKE_OK=1")
    print("  export LOOPIE_PROVIDER_CURSOR_ENABLED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
