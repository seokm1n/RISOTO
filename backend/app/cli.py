"""Operational CLI entry points kept outside the request-serving API process."""

from __future__ import annotations

import argparse
import json

from app.services.monitoring_pipeline import run_due_collection_retries, run_realtime_tick
from app.services.risk_analysis import backfill_historical_windows


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill = subparsers.add_parser("backfill-features", help="cluster existing articles and build 15-minute windows")
    backfill.add_argument("--company-id", type=int)
    subparsers.add_parser("realtime-tick", help="run one due live collection tick")
    subparsers.add_parser("retry-collections", help="run due collection incident retries")
    args = parser.parse_args()
    if args.command == "backfill-features":
        print(json.dumps(backfill_historical_windows(args.company_id), ensure_ascii=False))
    elif args.command == "realtime-tick":
        run_realtime_tick()
        print(json.dumps({"status": "completed"}))
    elif args.command == "retry-collections":
        print(json.dumps({"retried": run_due_collection_retries()}))


if __name__ == "__main__":
    main()
