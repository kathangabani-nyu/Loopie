#!/usr/bin/env python3
"""
Stop hook. Runs when Claude finishes a response.
Reminds the agent to update TODO.md or DEMO_STATUS.md if relevant work was done.
Always exits 0.
"""
import json
import sys

data = json.load(sys.stdin) if not sys.stdin.isatty() else {}

stop_reason = data.get("stop_reason", "")

if stop_reason in ("end_turn", ""):
    print(
        "[loopie-hook] Reminder: if you changed pipeline logic, scorers, or demo paths, "
        "update TODO.md and/or DEMO_STATUS.md before finishing.",
        file=sys.stderr,
    )

sys.exit(0)

