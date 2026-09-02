"""Operational CLI entry points kept outside the request-serving API process."""

from __future__ import annotations

import argparse
import json

from app.services.monitoring_pipeline import run_due_collection_retries, run_realtime_tick
from app.services.risk_analysis import backfill_historical_windows
from app.services.story_risk import rebuild_recent_story_events


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backfill = subparsers.add_parser("backfill-features", help="cluster existing articles and build 15-minute windows")
    backfill.add_argument("--company-id", type=int)
    rebuild = subparsers.add_parser(
        "rebuild-story-events",
        help="close active window events and rebuild recent story-centered events",
    )
    rebuild.add_argument("--company-id", type=int)
    rebuild_range = rebuild.add_mutually_exclusive_group()
    rebuild_range.add_argument("--hours", type=int)
    rebuild_range.add_argument(
        "--all",
        action="store_true",
        dest="all_history",
        help="rebuild every matched article instead of the recent window",
    )
    rebuild.add_argument("--batch-size", type=int, default=250)
    rebuild.add_argument(
        "--recluster",
        action="store_true",
        help="recompute title+summary semantic story clusters before rebuilding events",
    )
    rebuild.add_argument(
        "--generate-drafts",
        action="store_true",
        help="generate response drafts for active rebuilt events (may use substantial tokens)",
    )
    rebuild.add_argument(
        "--draft-limit",
        type=int,
        help="maximum active response drafts to generate; requires --generate-drafts",
    )
    subparsers.add_parser("realtime-tick", help="run one due live collection tick")
    subparsers.add_parser("retry-collections", help="run due collection incident retries")
    args = parser.parse_args()
    if args.command == "backfill-features":
        print(json.dumps(backfill_historical_windows(args.company_id), ensure_ascii=False))
    elif args.command == "rebuild-story-events":
        if args.draft_limit is not None and not args.generate_drafts:
            parser.error("--draft-limit requires --generate-drafts")
        print(
            json.dumps(
                rebuild_recent_story_events(
                    args.company_id,
                    args.hours,
                    all_history=args.all_history,
                    batch_size=args.batch_size,
                    recluster=args.recluster,
                    enqueue_drafts=args.generate_drafts,
                    draft_limit=args.draft_limit,
                ),
                ensure_ascii=False,
            )
        )
    elif args.command == "realtime-tick":
        run_realtime_tick()
        print(json.dumps({"status": "completed"}))
    elif args.command == "retry-collections":
        print(json.dumps({"retried": run_due_collection_retries()}))


if __name__ == "__main__":
    main()
