"""Evaluate a saved company reranker at alternative operating thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.company_reranker import build_article_passage, build_company_query
from app.training.company_reranker import (
    _load_database_rows,
    _load_human_rows,
    _metrics,
    _stratified_human_split,
    company_holdout_split,
)


def main() -> None:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=Path("training_data/relevance_labeled.csv"),
    )
    parser.add_argument("--seed", type=int, default=29)
    args = parser.parse_args()

    database_rows = _load_database_rows()
    db_splits = company_holdout_split(database_rows, seed=args.seed)
    human_splits = _stratified_human_split(
        _load_human_rows(args.csv_path), seed=args.seed + 12
    )
    train_companies = {row["company_group"] for row in db_splits["train"]}
    held_out = [
        row for row in human_splits["train"]
        if row["company_group"] not in train_companies
    ]
    human_splits["validation"].extend(held_out)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(args.artifact, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.artifact, local_files_only=True
    ).to(device).eval()

    def score(items: list[dict]) -> tuple[list[int], list[float]]:
        targets: list[int] = []
        scores: list[float] = []
        for start in range(0, len(items), 8):
            batch = items[start:start + 8]
            encoded = tokenizer(
                [
                    build_company_query(row["company"], row["aliases"], row["products"])
                    for row in batch
                ],
                [build_article_passage(row["title"], row["summary"]) for row in batch],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.no_grad(), torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(**encoded).logits.view(-1)
            targets.extend(row["label"] for row in batch)
            scores.extend(float(value) for value in torch.sigmoid(logits).cpu().tolist())
        return targets, scores

    validation_targets, validation_scores = score(human_splits["validation"])
    test_targets, test_scores = score(human_splits["test"])
    candidates: list[dict] = []
    for value in range(50, 96):
        threshold = value / 100
        validation = _metrics(validation_targets, validation_scores, threshold)
        if (
            validation["precision_relevant"] >= 0.80
            and validation["recall_relevant"] >= 0.60
        ):
            candidates.append(
                {
                    "threshold": threshold,
                    "validation": validation,
                    "test": _metrics(test_targets, test_scores, threshold),
                }
            )
    print(
        json.dumps(
            {
                "artifact": str(args.artifact),
                "eligible_thresholds": candidates,
                "best_balanced": max(
                    candidates,
                    key=lambda row: (
                        row["validation"]["f1_relevant"],
                        row["validation"]["precision_relevant"],
                    ),
                ) if candidates else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

