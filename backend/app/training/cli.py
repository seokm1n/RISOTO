"""Explicit, manual training commands; none of them auto-promote a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.training.company_reranker import train_company_reranker
from app.training.legacy_csv import train_relevance_from_csv
from app.training.risk_models import train_isolation_forest, train_risk_detector
from app.training.risk_types import train_risk_type_classifier
from app.training.text_models import train_filter, train_sentiment
from app.training.topical_relevance import train_topical_relevance_from_csv


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m app.training.cli")
    parser.add_argument(
        "task",
        choices=[
            "filter", "sentiment", "risk-types", "iforest", "risk",
            "relevance-csv", "topical-relevance-csv", "company-reranker",
        ],
    )
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--base-model")
    parser.add_argument("--isolation-artifact", type=Path)
    parser.add_argument("--csv-path", type=Path)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--seed", type=int, default=29)
    parser.add_argument("--human-weight", type=int, default=1)
    args = parser.parse_args()
    output = Path(settings.model_artifact_dir)
    default_text_model = args.base_model or "klue/roberta-base"
    if args.task == "filter":
        result = train_filter(output, default_text_model, args.epochs)
    elif args.task == "sentiment":
        result = train_sentiment(output, default_text_model, args.epochs)
    elif args.task == "risk-types":
        result = train_risk_type_classifier(output, default_text_model, args.epochs)
    elif args.task == "iforest":
        result = train_isolation_forest(output)
    elif args.task == "relevance-csv":
        if args.csv_path is None:
            raise SystemExit("--csv-path is required for relevance-csv")
        result = train_relevance_from_csv(args.csv_path, output, default_text_model, args.epochs)
    elif args.task == "topical-relevance-csv":
        if args.csv_path is None:
            raise SystemExit("--csv-path is required for topical-relevance-csv")
        result = train_topical_relevance_from_csv(args.csv_path, output, default_text_model, args.epochs)
    elif args.task == "company-reranker":
        csv_path = args.csv_path or Path("training_data/relevance_labeled.csv")
        base_model = args.base_model or settings.company_reranker_base_model
        result = train_company_reranker(
            output,
            csv_path,
            base_model=base_model,
            epochs=args.epochs,
            batch_size=args.batch_size,
            seed=args.seed,
            human_weight=args.human_weight,
        )
    else:
        result = train_risk_detector(output, args.isolation_artifact)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
