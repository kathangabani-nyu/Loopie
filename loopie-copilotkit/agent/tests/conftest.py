import os
import sys
from pathlib import Path

# Fast-lane tests must never inherit hosted credentials or observability settings
# from the developer shell. Individual tests opt back into Weave/live behavior
# explicitly with monkeypatch.
os.environ["LOOPIE_HOSTED"] = "0"
os.environ["LOOPIE_PERSISTENCE_MODE"] = "memory"
os.environ["LOOPIE_WEAVE_ENABLED"] = "false"

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from src.loopie.winloop import ensure_selector_event_loop_policy  # noqa: E402

# Integration tests exercising the real async Postgres pool need this on
# Windows dev machines before pytest creates any event loop.
ensure_selector_event_loop_policy()
