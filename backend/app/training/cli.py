"""Explicit, manual training commands; none of them auto-promote a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.training.risk_models import train_isolation_forest, train_risk_detector
from app.training.risk_types import train_risk_type_classifier
from app.training.text_models import train_filter, train_sentiment


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m app.training.cli")
    parser.add_argument(
        "task",
        choices=["filter", "sentiment", "risk-types", "iforest", "risk"],
    )
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--base-model", default="klue/roberta-base")
    parser.add_argument("--isolation-artifact", type=Path)
    args = parser.parse_args()
    output = Path(settings.model_artifact_dir)
    if args.task == "filter":
        result = train_filter(output, args.base_model, args.epochs)
    elif args.task == "sentiment":
        result = train_sentiment(output, args.base_model, args.epochs)
    elif args.task == "risk-types":
        result = train_risk_type_classifier(output, args.base_model, args.epochs)
    elif args.task == "iforest":
        result = train_isolation_forest(output)
    else:
        result = train_risk_detector(output, args.isolation_artifact)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
