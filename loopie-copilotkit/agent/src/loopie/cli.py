"""CLI entrypoint for Loopie dry-runs."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopie eval pipeline")
    parser.add_argument("command", choices=["run_suite", "seed", "baseline"])
    parser.add_argument("--mode", default="mock", choices=["mock", "live"])
    parser.add_argument("--case-id", default="security_001")
    args = parser.parse_args()

    from src.loopie.pipeline import LoopiePipeline

    pipeline = LoopiePipeline()
    if args.command == "seed":
        print(pipeline.seed())
    elif args.command == "baseline":
        pipeline.seed()
        print(pipeline.run_baseline(case_id=args.case_id))
    else:
        print(pipeline.run_suite(mode=args.mode))


if __name__ == "__main__":
    main()
