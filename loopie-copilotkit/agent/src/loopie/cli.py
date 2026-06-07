"""CLI entrypoint for Loopie dry-runs."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopie eval pipeline")
    parser.add_argument("command", choices=["run_suite", "seed", "baseline"])
    parser.add_argument("--mode", default="test", choices=["test", "live"])
    parser.add_argument("--case-id", default="security_001")
    reset_group = parser.add_mutually_exclusive_group()
    reset_group.add_argument(
        "--reset",
        dest="reset",
        action="store_true",
        help="Wipe Redis/ledger and reseed before run (default for --mode live)",
    )
    reset_group.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        help="Keep existing Redis/ledger state",
    )
    parser.set_defaults(reset=None)
    args = parser.parse_args()

    if args.mode == "live":
        os.environ["LOOPIE_LLM_MODE"] = "live"
        os.environ.setdefault("LOOPIE_LIVE_CONFIRMED", "1")
        missing = [
            name
            for name in ("OPENAI_API_KEY", "WANDB_API_KEY", "WEAVE_PROJECT")
            if not os.getenv(name)
        ]
        if missing:
            raise SystemExit(
                f"Live recorded pass requires env vars: {', '.join(missing)}. "
                "Also set LOOPIE_LLM_MODE=live and LOOPIE_LIVE_CONFIRMED=1."
            )

    from src.loopie.pipeline import LoopiePipeline

    pipeline = LoopiePipeline()
    if args.command == "seed":
        print(pipeline.seed())
    elif args.command == "baseline":
        if args.reset:
            pipeline.reset()
        else:
            pipeline.seed()
        print(pipeline.run_baseline(case_id=args.case_id))
    else:
        print(pipeline.run_suite(mode=args.mode, reset=args.reset))


if __name__ == "__main__":
    main()
