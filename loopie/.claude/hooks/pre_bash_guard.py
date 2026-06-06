#!/usr/bin/env python3
"""
PreToolUse hook for Bash tool.
Blocks dangerous commands before they run.
Reads the tool input JSON from stdin.
Exit 1 (with message to stderr) to block. Exit 0 to allow.
"""
import json
import re
import sys

data = json.load(sys.stdin)
cmd = data.get("command", "")

BLOCKED = [
    (r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f", "rm -rf is blocked"),
    (r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r", "rm -rf is blocked"),
    (r"git\s+push\s+.*--force(?!-with-lease)", "git push --force is blocked (use --force-with-lease)"),
    (r"echo\s+.*\$(?:OPENAI|ANTHROPIC|WEAVE|REDIS|SECRET|TOKEN|API_KEY|PASSWORD)", "printing secrets is blocked"),
    (r"printenv\s+(?:OPENAI|ANTHROPIC|WEAVE|REDIS|SECRET|TOKEN|API_KEY|PASSWORD)", "printing secrets is blocked"),
    (r"cat\s+.*\.env\b", "printing .env files is blocked"),
]

for pattern, reason in BLOCKED:
    if re.search(pattern, cmd, re.IGNORECASE):
        print(f"[loopie-hook] BLOCKED: {reason}", file=sys.stderr)
        print(f"Command was: {cmd[:200]}", file=sys.stderr)
        sys.exit(1)

sys.exit(0)
