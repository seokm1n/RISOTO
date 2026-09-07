"""Recover audited annotation results with duplicate evidence IDs, without API calls.

Run only after annotation has stopped: python -m app.training.story_annotation_recovery --output PATH
The original responses, labels, protocol and coverage are preserved.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

from app.training.story_annotation import (
    LABEL_VERSION, annotation_payload, atomic_json, digest, model_matches, now,
    protocol_for, read_jsonl, validate_existing, validate_response, validate_snapshots,
)

NORMALIZATION = "order_preserving_duplicate_evidence_ids_only_v1"


def stopped_coverage(output: Path) -> dict:
    coverage = json.loads((output / "annotation_coverage.json").read_text(encoding="utf-8"))
    if coverage.get("status") not in {"complete", "incomplete", "interrupted"}:
        raise ValueError("Annotation must be stopped before recovery; coverage is running or invalid")
    return coverage


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not recoverable")
        result[key] = value
    return result


def verified_response(audit: dict, by_key: dict, protocol: dict) -> tuple[list[dict], list[dict]]:
    """Return labels and duplicate-only changes after validating every original identifier."""
    keys = audit.get("keys")
    if (not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys)
            or len(keys) != len(set(keys)) or not set(keys).issubset(by_key)):
        raise ValueError("Invalid audit batch keys")
    batch = [by_key[k] for k in keys]
    snapshot_hashes = {s["key"]: s["snapshot_hash"] for s in batch}
    expected_batch_id = digest([protocol["prompt_hash"], protocol["model"],
                                [s["snapshot_hash"] for s in batch]])
    if (audit.get("status") not in {"failed", "validated"}
            or audit.get("requested_model") != protocol["model"]
            or audit.get("prompt_hash") != protocol["prompt_hash"]
            or audit.get("snapshot_hashes") != snapshot_hashes
            or audit.get("input_hash") != digest(annotation_payload(batch))
            or audit.get("batch_id") != expected_batch_id
            or not audit.get("run_id") or type(audit.get("attempt")) is not int
            or audit["attempt"] < 1):
        raise ValueError("Audit protocol/model/input/snapshot provenance mismatch")
    response = audit.get("response")
    if (not isinstance(response, dict) or response.get("status") != "completed"
            or response.get("error") is not None or response.get("incomplete_details") is not None
            or not response.get("id") or audit.get("response_id") != response["id"]
            or audit.get("actual_model") != response.get("model")
            or not model_matches(response.get("model"), protocol["model"])):
        raise ValueError("Response is incomplete or its model/identity differs from the audit")
    output = response.get("output")
    if not isinstance(output, list) or not output:
        raise ValueError("Response output is missing")
    texts = []
    for message in output:
        if (not isinstance(message, dict) or message.get("type") != "message"
                or message.get("role") != "assistant" or message.get("status") != "completed"
                or not isinstance(message.get("content"), list) or not message["content"]):
            raise ValueError("Response contains an unsupported or incomplete output message")
        for content in message["content"]:
            if not isinstance(content, dict) or content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                raise ValueError("Response contains refusal or malformed output content")
            texts.append(content["text"])
    original = json.loads("".join(texts), object_pairs_hook=unique_object)
    if not isinstance(original, dict) or set(original) != {"labels"} or not isinstance(original["labels"], dict):
        raise ValueError("Response labels object is malformed")
    if set(original["labels"]) != set(keys):
        raise ValueError("Response keys differ from the original batch")
    repaired = deepcopy(original)
    changes = []
    for key, label in repaired["labels"].items():
        if not isinstance(label, dict):
            raise ValueError("Malformed response label")
        evidence = label.get("evidence_article_ids")
        allowed = {a["id"] for a in by_key[key]["articles"]}
        if (not isinstance(evidence, list) or any(type(i) is not int for i in evidence)
                or not set(evidence).issubset(allowed)):
            raise ValueError("Malformed or foreign evidence IDs cannot be repaired")
        unique = list(dict.fromkeys(evidence))
        if evidence != unique:
            changes.append({"key": key, "field": "evidence_article_ids", "before": evidence[:], "after": unique})
            label["evidence_article_ids"] = unique
    # Everything except redundant occurrences of already-valid IDs remains untouched.
    labels = validate_response(repaired, batch)
    for label in labels:
        source = original["labels"][label["key"]]
        if any(label[k] != source[k] for k in ("decision", "confidence", "reason")):
            raise ValueError("Recovery unexpectedly changed semantic label content")
    return labels, changes


def recover_labels(output: Path) -> dict:
    output = Path(output)
    before_coverage = stopped_coverage(output)
    snapshots = read_jsonl(output / "snapshots.jsonl")
    validate_snapshots(snapshots)
    protocol = json.loads((output / "label_protocol.json").read_text(encoding="utf-8"))
    if not isinstance(protocol.get("model"), str) or protocol != protocol_for(snapshots, protocol["model"]):
        raise ValueError("Stored annotation protocol does not match the current frozen corpus/protocol")
    if before_coverage.get("protocol_hash") != digest(protocol):
        raise ValueError("Coverage provenance does not match the annotation protocol")
    path = output / "ai_labels.jsonl"
    existing = read_jsonl(path)
    done = validate_existing(existing, snapshots, protocol)
    by_key = {s["key"]: s for s in snapshots}
    run_id = uuid.uuid4().hex
    recovery_dir = output / "annotation_recovery"
    recovery_dir.mkdir(exist_ok=True)
    atomic_json(recovery_dir / f"coverage-before-{run_id}.json", before_coverage)
    summary = dict(run_id=run_id, normalization=NORMALIZATION, started_at=now(),
                   initially_labeled=len(done), imported=0, repaired_labels=0,
                   accepted_audits=[], rejected_audits=[], protocol_hash=digest(protocol))
    # Each original batch's attempts are visited in filename order, never quality-selected.
    for audit_path in sorted((output / "annotation_audit").glob("*.json")):
        raw = audit_path.read_bytes()
        try:
            audit = json.loads(raw)
            keys = audit.get("keys")
            if isinstance(keys, list) and all(isinstance(k, str) for k in keys) and set(keys).issubset(done):
                continue
            labels, changes = verified_response(audit, by_key, protocol)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            # These controlled parser/validator exceptions never contain secrets or request headers.
            summary["rejected_audits"].append({"file": audit_path.name, "error_type": type(exc).__name__})
            continue
        missing = [l for l in labels if l["key"] not in done]
        if not missing:
            continue
        stopped_coverage(output)
        journal_name = f"{audit['batch_id']}-{run_id}-{audit['attempt']}.json"
        journal_path = recovery_dir / journal_name
        source = str(audit_path.relative_to(output)).replace("\\", "/")
        raw_hash = hashlib.sha256(raw).hexdigest()
        journal = dict(status="prepared", run_id=run_id, created_at=now(), normalization=NORMALIZATION,
                       source_audit=source, source_audit_sha256=raw_hash, response_id=audit["response_id"],
                       model=audit["actual_model"], protocol_hash=digest(protocol), changes=changes,
                       appended_keys=[l["key"] for l in missing])
        for label in missing:
            label.update(snapshot_hash=by_key[label["key"]]["snapshot_hash"], label_source="ai_generated",
                         human_reviewed=False, annotator=audit["actual_model"], requested_model=protocol["model"],
                         label_version=LABEL_VERSION, prompt_hash=protocol["prompt_hash"],
                         response_id=audit["response_id"], labeled_at=now(),
                         normalization_provenance={"normalization": NORMALIZATION,
                             "journal": f"annotation_recovery/{journal_name}", "source_audit": source,
                             "source_audit_sha256": raw_hash,
                             "evidence_ids_changed": any(c["key"] == label["key"] for c in changes)})
        validate_existing(missing, snapshots, protocol)
        journal["appended_labels_hash"] = digest(missing)
        atomic_json(journal_path, journal)
        # Recheck immediately before mutation; root must not restart the labeler concurrently.
        stopped_coverage(output)
        with path.open("ab") as sink:
            if path.stat().st_size and path.read_bytes()[-1:] != b"\n":
                sink.write(b"\n")
            sink.write("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in missing).encode("utf-8"))
            sink.flush()
            os.fsync(sink.fileno())
        done.update(l["key"] for l in missing)
        journal.update(status="appended", finished_at=now())
        atomic_json(journal_path, journal)
        summary["accepted_audits"].append(journal_name)
        summary["imported"] += len(missing)
        summary["repaired_labels"] += sum(l["normalization_provenance"]["evidence_ids_changed"] for l in missing)
    validate_existing(read_jsonl(path), snapshots, protocol)
    summary.update(finished_at=now(), labeled=len(done), pending=len(snapshots) - len(done),
                   pending_keys=[s["key"] for s in snapshots if s["key"] not in done],
                   coverage_unchanged=True, next_step="Rerun story_annotation to refresh coverage and label any remaining keys")
    atomic_json(recovery_dir / f"summary-{run_id}.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(recover_labels(args.output), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
