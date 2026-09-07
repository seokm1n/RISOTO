"""Compare frozen story models on candidate test evidence unseen by either model.

Run after training has finished, using trusted local model artifacts only::

    python -m app.training.story_model_comparison \
        --candidate /app/training_data/story_model_full_v2 \
        --baseline /app/model_artifacts/story-risk-v1-20260907

The only files written are comparison.json and comparison.md in the candidate
directory. No labels, splits, models, thresholds, or stored predictions are changed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import types


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def index_rows(rows: list[dict], name: str) -> dict[str, dict]:
    indexed = {str(row["key"]): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f"Duplicate keys in {name}")
    return indexed


def evidence_tokens(snapshot: dict) -> set[tuple[str, str]]:
    """Use the trainer's exact title normalization, across company boundaries."""
    if snapshot.get("story_id") is None or not snapshot.get("articles"):
        raise ValueError(f"Missing story/evidence in snapshot {snapshot.get('key')}")
    tokens = {("story_id", str(snapshot["story_id"]))}
    for article in snapshot["articles"]:
        if article.get("id") is None:
            raise ValueError(f"Missing article ID in snapshot {snapshot.get('key')}")
        tokens.add(("article_id", str(article["id"])))
        title = re.sub(r"\W", "", article.get("title") or "").casefold()
        # Empty titles are not evidence that unrelated records describe one event.
        if title:
            tokens.add(("normalized_title", title))
    return tokens


def split_keys(manifest: dict, split: str) -> set[str]:
    if split not in manifest or not isinstance(manifest[split], list):
        raise ValueError(f"Missing split {split}")
    keys = [str(row["key"]) for row in manifest[split]]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate keys in split {split}")
    return set(keys)


def exposure_filter(candidate: dict[str, dict], baseline: dict[str, dict],
                    candidate_manifest: dict, baseline_manifest: dict,
                    test_keys: set[str]) -> tuple[dict[str, dict], dict]:
    """Conservatively exclude direct and transitive evidence exposure.

    All snapshots, including unused/purged rows, may bridge duplicate evidence.
    Only train/validation rows seed exposure; baseline test alone is not a seed.
    Candidate train/validation are also checked to detect accidental own leakage.
    """
    all_rows = {(origin, key): snapshot for origin, rows in
                (("candidate", candidate), ("baseline", baseline))
                for key, snapshot in rows.items()}
    parent = {node: node for node in all_rows}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    seen = {}
    tokens_by_node = {}
    for node, snapshot in all_rows.items():
        tokens_by_node[node] = evidence_tokens(snapshot)
        for token in tokens_by_node[node]:
            if token in seen:
                parent[find(node)] = find(seen[token])
            else:
                seen[token] = node

    components = defaultdict(set)
    direct_tokens = defaultdict(set)
    seed_counts = {}
    for origin, rows, manifest in (("baseline", baseline, baseline_manifest),
                                   ("candidate", candidate, candidate_manifest)):
        for split in ("train", "validation"):
            keys = split_keys(manifest, split)
            if missing := keys - rows.keys():
                raise ValueError(f"{origin} {split} snapshots missing: {sorted(missing)[:5]}")
            exposure = f"{origin}_{split}"
            seed_counts[exposure] = len(keys)
            for key in keys:
                node = (origin, key)
                components[find(node)].add(exposure)
                direct_tokens[exposure].update(tokens_by_node[node])

    excluded = {}
    counts = Counter()
    for key in sorted(test_keys):
        node = ("candidate", key)
        exposures = sorted(components[find(node)])
        if not exposures:
            continue
        direct = {}
        for exposure in exposures:
            kinds = sorted({kind for kind, value in
                            tokens_by_node[node] & direct_tokens[exposure]})
            if kinds:
                direct[exposure] = kinds
                for kind in kinds:
                    counts[f"{exposure}_direct_{kind}"] += 1
            counts[exposure] += 1
        baseline_exposed = any(s.startswith("baseline_") for s in exposures)
        candidate_exposed = any(s.startswith("candidate_") for s in exposures)
        if baseline_exposed:
            counts["baseline_train_or_validation"] += 1
        if candidate_exposed:
            counts["candidate_train_or_validation"] += 1
        transitive = [exposure for exposure in exposures if exposure not in direct]
        if transitive:
            counts["has_transitive_exposure"] += 1
        excluded[key] = dict(exposures=exposures, direct_match_types=direct,
                             transitive_exposures=transitive)
    return excluded, dict(
        candidate_test_total=len(test_keys), excluded_total=len(excluded),
        comparable_total=len(test_keys) - len(excluded),
        baseline_train_or_validation=counts["baseline_train_or_validation"],
        candidate_train_or_validation=counts["candidate_train_or_validation"],
        has_transitive_exposure=counts["has_transitive_exposure"],
        counts_by_reason=dict(counts), exposure_seed_counts=seed_counts,
        counts_note="Reason counts overlap; excluded_total counts unique candidate keys.",
    )


def probability(value, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError(f"Invalid probability/threshold for {name}: {value}")
    return number


def frozen_threshold(report: dict, model: str) -> float:
    # Read a threshold already frozen by training, never select it using this test.
    threshold = probability(report[model]["validation"]["threshold"], model)
    for split in ("train", "test"):
        if split in report[model] and not math.isclose(
                threshold, float(report[model][split]["threshold"]), abs_tol=1e-12):
            raise ValueError(f"Inconsistent frozen thresholds in {model}")
    return threshold


def calculate_metrics(targets: list[int], scores: list[float], threshold: float) -> dict:
    from sklearn.metrics import (average_precision_score, confusion_matrix,
                                 f1_score, precision_score, recall_score)
    n = len(targets)
    if n != len(scores):
        raise ValueError("Metric inputs differ in length")
    if not n:
        return dict(n=0, positive=0, negative=0, threshold=threshold,
                    precision=None, recall=None, f1=None, average_precision=None,
                    true_positive=0, true_negative=0, false_positive=0,
                    false_negative=0, confusion_matrix=[[0, 0], [0, 0]])
    predicted = [int(score >= threshold) for score in scores]
    tn, fp, fn, tp = confusion_matrix(targets, predicted, labels=[0, 1]).ravel().tolist()
    return dict(n=n, positive=sum(targets), negative=n - sum(targets), threshold=threshold,
                precision=float(precision_score(targets, predicted, zero_division=0)),
                recall=float(recall_score(targets, predicted, zero_division=0)),
                f1=float(f1_score(targets, predicted, zero_division=0)),
                average_precision=float(average_precision_score(targets, scores))
                    if len(set(targets)) == 2 else None,
                true_positive=tp, true_negative=tn, false_positive=fp, false_negative=fn,
                confusion_matrix=[[tn, fp], [fn, tp]])


def safe_child(directory: Path, name: str) -> Path:
    path = (directory / name).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError(f"Artifact path escapes its directory: {name}")
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_predictor(path: Path):
    # Loading the trusted copied source preserves the baseline feature contract.
    # Compile in memory so comparison never creates __pycache__ in the baseline.
    module = types.ModuleType("_frozen_baseline_story_model")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8-sig"), str(path), "exec"), module.__dict__)
    return module.predict_stories


def compare(candidate_dir: Path, baseline_dir: Path) -> dict:
    candidate_dir, baseline_dir = candidate_dir.resolve(), baseline_dir.resolve()
    candidate_report = read_json(candidate_dir / "report.json")
    baseline_report = read_json(baseline_dir / "report.json")
    baseline_artifact = safe_child(baseline_dir, baseline_report["artifact"])
    baseline_source = baseline_dir / "source_code" / "story_model.py"
    paths = [candidate_dir / name for name in
             ("report.json", "snapshots.jsonl", "ai_labels.jsonl", "split_manifest.json", "predictions.json")]
    paths += [baseline_dir / name for name in ("report.json", "snapshots.jsonl", "split_manifest.json")]
    paths += [baseline_source, baseline_artifact]
    review_path = candidate_dir / "ai_review.json"
    if review_path.exists():
        paths.append(review_path)
    fingerprints = {str(path): file_hash(path) for path in paths}
    expected_hash = baseline_report.get("artifact_sha256")
    if expected_hash and fingerprints[str(baseline_artifact)] != expected_hash:
        raise ValueError("Baseline artifact checksum does not match its report")

    candidate = index_rows(read_jsonl(candidate_dir / "snapshots.jsonl"), "candidate snapshots")
    baseline = index_rows(read_jsonl(baseline_dir / "snapshots.jsonl"), "baseline snapshots")
    predictions = index_rows(read_json(candidate_dir / "predictions.json"), "candidate predictions")
    labels = index_rows(read_jsonl(candidate_dir / "ai_labels.jsonl"), "candidate AI labels")
    candidate_manifest = read_json(candidate_dir / "split_manifest.json")
    baseline_manifest = read_json(baseline_dir / "split_manifest.json")
    test_keys = split_keys(candidate_manifest, "test")
    predicted_test = {key for key, row in predictions.items() if row["split"] == "test"}
    if test_keys != predicted_test:
        raise ValueError("Candidate test predictions do not match the frozen test manifest")
    if missing := test_keys - candidate.keys():
        raise ValueError(f"Candidate test snapshots missing: {sorted(missing)[:5]}")

    review = read_json(review_path) if review_path.exists() else {"corrections": []}
    corrected_keys = set()
    for correction in review.get("corrections", []):
        key = str(correction["key"])
        if key in corrected_keys:
            raise ValueError(f"Duplicate AI review correction: {key}")
        if key not in labels or correction["snapshot_hash"] != labels[key]["snapshot_hash"]:
            raise ValueError(f"AI review correction does not match original label: {key}")
        labels[key] = {**labels[key], **correction}
        corrected_keys.add(key)

    thresholds = dict(candidate_if=frozen_threshold(candidate_report, "if_lightgbm"),
                      candidate_without_if=frozen_threshold(candidate_report, "lightgbm_without_if"),
                      baseline_if=frozen_threshold(baseline_report, "if_lightgbm"))
    for key in sorted(test_keys):
        label, snapshot, prediction = labels[key], candidate[key], predictions[key]
        if label["snapshot_hash"] != snapshot["snapshot_hash"]:
            raise ValueError(f"Candidate test snapshot/label hash mismatch: {key}")
        if label["decision"] not in ("risk", "normal") or label["confidence"] == "low":
            raise ValueError(f"Ineligible effective AI label in frozen candidate test: {key}")
        if int(prediction["y"]) != int(label["decision"] == "risk"):
            raise ValueError(f"Candidate prediction target differs from effective AI label: {key}")
        score = probability(prediction["risk_probability"], key)
        probability(prediction["without_if_probability"], key)
        if int(prediction["prediction"]) != int(score >= thresholds["candidate_if"]):
            raise ValueError(f"Candidate stored prediction differs from frozen threshold: {key}")

    excluded, coverage = exposure_filter(candidate, baseline, candidate_manifest,
                                         baseline_manifest, test_keys)
    comparable_keys = sorted(test_keys - excluded.keys())
    comparable = [candidate[key] for key in comparable_keys]
    baseline_predictions = load_frozen_predictor(baseline_source)(baseline_artifact, comparable)
    if len(baseline_predictions) != len(comparable):
        raise ValueError("Baseline inference returned a different number of rows")
    audited = []
    for key, old in zip(comparable_keys, baseline_predictions):
        snapshot, prediction, label = candidate[key], predictions[key], labels[key]
        if str(old["story_id"]) != str(snapshot["story_id"]) or str(old["company_id"]) != str(snapshot["company_id"]):
            raise ValueError(f"Baseline inference identity mismatch: {key}")
        if not math.isclose(float(old["threshold"]), thresholds["baseline_if"], abs_tol=1e-12):
            raise ValueError("Baseline report threshold differs from frozen artifact")
        scores = dict(candidate_if=probability(prediction["risk_probability"], key),
                      candidate_without_if=probability(prediction["without_if_probability"], key),
                      baseline_if=probability(old["risk_probability"], key))
        if bool(old["is_risk"]) != (scores["baseline_if"] >= thresholds["baseline_if"]):
            raise ValueError(f"Baseline prediction/threshold mismatch: {key}")
        audited.append(dict(key=key, story_id=snapshot["story_id"], company_id=snapshot["company_id"],
                            company_name=snapshot["company_name"], split="test",
                            split_group=prediction.get("split_group"), as_of=snapshot["as_of"],
                            titles=[article["title"] for article in snapshot["articles"]],
                            snapshot_hash=snapshot["snapshot_hash"], y=int(label["decision"] == "risk"),
                            label={field: label.get(field) for field in
                                   ("decision", "confidence", "reason", "evidence_article_ids",
                                    "label_source", "human_reviewed", "annotator", "label_version")},
                            ai_review_applied=key in corrected_keys,
                            models={name: dict(risk_probability=score, threshold=thresholds[name],
                                               prediction=int(score >= thresholds[name]))
                                    for name, score in scores.items()}))
    targets = [row["y"] for row in audited]
    metrics = {name: calculate_metrics(targets, [row["models"][name]["risk_probability"]
                                                for row in audited], threshold)
               for name, threshold in thresholds.items()}
    limitations = [
        "Scores measure agreement with the same candidate AI labels, not human-verified real-world accuracy.",
        "No training, label editing, threshold selection, calibration, or split changes occur in this utility.",
        "Candidate test cases with baseline or candidate train/validation evidence exposure are excluded conservatively.",
        "Story IDs, article IDs and exact normalized titles detect direct/transitive overlap; paraphrased duplicate events may remain.",
        "Previously observed baseline test data are not excluded solely for belonging to that test; this is not necessarily a never-seen prospective benchmark.",
        "Exposure filtering changes coverage; these results must not be compared directly with old report metrics on a different test set or labels.",
        "Current accepted-filter status, cluster membership, stored summaries and first-24-hour snapshots reconstruct history rather than a prospective backtest.",
        "PR-AUC is reported as sklearn average precision (AP), not trapezoidal integration; it is null when either class is absent.",
        "Thresholds were frozen using each model's own validation set; this comparison does not tune them on test outcomes.",
    ]
    if not audited:
        limitations.append("No comparable test cases remain; no relative performance conclusion is possible.")
    elif min(sum(targets), len(targets) - sum(targets)) < 20:
        limitations.append("Fewer than 20 comparable examples in at least one class; metric estimates are especially unstable.")
    if corrected_keys & test_keys:
        limitations.append("Candidate AI review corrections include test cases; report only the already-frozen effective labels, with review keys disclosed.")
    baseline_test_keys = split_keys(baseline_manifest, "test")
    coverage.update(positive=sum(targets), negative=len(targets) - sum(targets),
                    distinct_stories=len({row["story_id"] for row in audited}),
                    distinct_candidate_groups=len({row["split_group"] for row in audited}),
                    by_company=dict(Counter(row["company_name"] for row in audited)),
                    exact_keys_previously_in_baseline_test=len(set(comparable_keys) & baseline_test_keys))
    if any(file_hash(Path(path)) != expected for path, expected in fingerprints.items()):
        raise RuntimeError("An input file changed during comparison; rerun after training has finished")
    return dict(created_at=datetime.now(timezone.utc).isoformat(),
                candidate_directory=str(candidate_dir), baseline_directory=str(baseline_dir),
                candidate_version=candidate_report["version"], baseline_version=baseline_report["version"],
                label_source="candidate_ai_labels_with_existing_ai_review", human_validated=False,
                threshold_policy="Each model's previously frozen validation threshold; no test tuning",
                pr_auc_method="sklearn.metrics.average_precision_score",
                evaluation_split="candidate_test_without_detected_training_or_validation_exposure",
                coverage=coverage, metrics=metrics, limitations=limitations,
                candidate_ai_review_total=len(corrected_keys),
                candidate_test_ai_review_keys=sorted(corrected_keys & test_keys),
                excluded=[dict(key=key, y=int(predictions[key]["y"]), **reason)
                          for key, reason in excluded.items()],
                predictions=audited, input_sha256=fingerprints)


def markdown_report(report: dict) -> str:
    coverage = report["coverage"]
    lines = ["# Frozen story model comparison", "",
             "Metrics measure agreement with AI-generated labels; they are not human-validated accuracy.", "",
             f"Candidate: `{report['candidate_version']}`  ",
             f"Baseline: `{report['baseline_version']}`", "",
             f"Candidate test: {coverage['candidate_test_total']:,}; excluded for detected exposure: "
             f"{coverage['excluded_total']:,}; comparable: {coverage['comparable_total']:,} "
             f"({coverage['positive']:,} risk / {coverage['negative']:,} normal).", "",
             "| Model | Frozen threshold | Precision | Recall | F1 | PR-AUC (AP) | FP | FN |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    names = dict(candidate_if="Candidate IF + LightGBM", candidate_without_if="Candidate LightGBM without IF",
                 baseline_if="Baseline IF + LightGBM")
    for key, metrics in report["metrics"].items():
        values = ["N/A" if metrics[field] is None else f"{metrics[field]:.4f}"
                  for field in ("threshold", "precision", "recall", "f1", "average_precision")]
        lines.append(f"| {names[key]} | {' | '.join(values)} | {metrics['false_positive']} | {metrics['false_negative']} |")
    lines += ["", "## Exposure and coverage", "",
              f"- Baseline train/validation overlap: {coverage['baseline_train_or_validation']:,} candidate test cases.",
              f"- Candidate own train/validation overlap: {coverage['candidate_train_or_validation']:,} cases.",
              f"- Cases with transitive overlap: {coverage['has_transitive_exposure']:,}.",
              f"- Comparable distinct stories: {coverage['distinct_stories']:,}; candidate split groups: {coverage['distinct_candidate_groups']:,}.",
              f"- Comparable exact keys previously in baseline test: {coverage['exact_keys_previously_in_baseline_test']:,}.",
              "- Reason counts overlap; they must not be added to obtain the total.", "",
              "## Limitations", "", *[f"- {item}" for item in report["limitations"]], "",
              "`comparison.json` records the source hashes, every excluded key and reason, and every comparable "
              "key's effective label, article titles, frozen probabilities, thresholds and decisions.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=Path("/app/model_artifacts/story-risk-v1-20260907"))
    args = parser.parse_args()
    report = compare(args.candidate, args.baseline)
    (args.candidate / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (args.candidate / "comparison.md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"coverage": report["coverage"], "metrics": report["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
