#!/usr/bin/env python3
"""
PostToolUse hook for file edits.
Runs ruff format and check on backend Python files only.
Reads the tool input JSON from stdin.
Always exits 0. This is advisory, not blocking.
"""
import json
import subprocess
import sys
from pathlib import Path

data = json.load(sys.stdin)
file_path = data.get("file_path") or data.get("path", "")

if not file_path:
    sys.exit(0)

p = Path(file_path)

if p.suffix != ".py":
    sys.exit(0)

if "backend" not in p.parts:
    sys.exit(0)

try:
    subprocess.run(["ruff", "format", str(p)], check=False, capture_output=True)
    result = subprocess.run(
        ["ruff", "check", "--fix", str(p)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[loopie-hook] ruff issues in {p.name}:", file=sys.stderr)
        print(result.stdout[:500], file=sys.stderr)
except FileNotFoundError:
    pass

sys.exit(0)

