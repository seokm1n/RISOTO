"""Conservative duplicate-only annotation recovery, without database or network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.training.story_annotation import annotation_payload, digest, protocol_for, read_jsonl
from app.training.story_annotation_recovery import recover_labels, verified_response


def fixtures():
    snapshot = dict(key="1:1", company_name="예시기업", aliases=[],
                    articles=[dict(id=11, title="예시기업 소송 진행", summary="예시기업이 소송 당사자다.")])
    snapshot["snapshot_hash"] = digest(snapshot)
    protocol = protocol_for([snapshot], "gpt-4o-mini")
    label = dict(decision="risk", confidence="high", reason="예시기업의 소송이 진행 중이다.",
                 evidence_article_ids=[11, 11])
    response = dict(id="resp_1", model="gpt-4o-mini-2024-07-18", status="completed", error=None,
                    incomplete_details=None, output=[dict(type="message", role="assistant", status="completed",
                    content=[dict(type="output_text", text=json.dumps({"labels": {snapshot["key"]: label}}))])])
    audit = dict(run_id="run_1", batch_id=digest([protocol["prompt_hash"], protocol["model"], [snapshot["snapshot_hash"]]]),
                 attempt=1, keys=[snapshot["key"]], input_hash=digest(annotation_payload([snapshot])),
                 snapshot_hashes={snapshot["key"]: snapshot["snapshot_hash"]}, requested_model=protocol["model"],
                 prompt_hash=protocol["prompt_hash"], actual_model=response["model"], response_id=response["id"],
                 response=response, status="failed", error_type="ValueError")
    return snapshot, protocol, label, audit


def change_label(audit, field, value):
    result = deepcopy(audit)
    content = result["response"]["output"][0]["content"][0]
    parsed = json.loads(content["text"])
    parsed["labels"]["1:1"][field] = value
    content["text"] = json.dumps(parsed)
    return result


class StoryAnnotationRecoveryTests(unittest.TestCase):
    def test_duplicate_only_repair_preserves_all_semantic_fields_and_raw_audit(self):
        snapshot, protocol, label, audit = fixtures()
        unchanged = deepcopy(audit)
        labels, changes = verified_response(audit, {snapshot["key"]: snapshot}, protocol)
        self.assertEqual(audit, unchanged)
        self.assertEqual(labels[0]["evidence_article_ids"], [11])
        self.assertEqual(changes, [dict(key="1:1", field="evidence_article_ids", before=[11, 11], after=[11])])
        for field in ("decision", "reason", "confidence"):
            self.assertEqual(labels[0][field], label[field])

    def test_foreign_empty_or_malformed_evidence_cannot_be_repaired(self):
        snapshot, protocol, _, audit = fixtures()
        for evidence in ([11, 99, 99], [], [True], ["11"], None):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                verified_response(change_label(audit, "evidence_article_ids", evidence), {"1:1": snapshot}, protocol)

    def test_protocol_input_snapshot_model_and_response_identity_must_match(self):
        snapshot, protocol, _, audit = fixtures()
        for field, value in [("prompt_hash", "wrong"), ("input_hash", "wrong"),
                             ("snapshot_hashes", {}), ("requested_model", "wrong"),
                             ("actual_model", "wrong"), ("response_id", "wrong"), ("keys", ["foreign"])]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                verified_response({**audit, field: value}, {"1:1": snapshot}, protocol)
        incomplete = deepcopy(audit)
        incomplete["response"]["status"] = "incomplete"
        with self.assertRaises(ValueError):
            verified_response(incomplete, {"1:1": snapshot}, protocol)

    def write_fixture(self, output, *, status="incomplete"):
        snapshot, protocol, _, audit = fixtures()
        (output / "snapshots.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        (output / "label_protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        (output / "annotation_coverage.json").write_text(json.dumps({"status": status, "protocol_hash": digest(protocol)}), encoding="utf-8")
        (output / "annotation_audit").mkdir()
        (output / "annotation_audit" / "original-1.json").write_text(json.dumps(audit), encoding="utf-8")
        return snapshot, protocol, audit

    def test_durable_recovery_is_idempotent_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.write_fixture(output)
            raw_audit = (output / "annotation_audit" / "original-1.json").read_bytes()
            raw_coverage = (output / "annotation_coverage.json").read_bytes()
            first = recover_labels(output)
            self.assertEqual((first["imported"], first["repaired_labels"], first["pending"]), (1, 1, 0))
            labels = read_jsonl(output / "ai_labels.jsonl")
            self.assertEqual(labels[0]["evidence_article_ids"], [11])
            journal = output / labels[0]["normalization_provenance"]["journal"]
            self.assertEqual(json.loads(journal.read_text())["status"], "appended")
            before_labels = (output / "ai_labels.jsonl").read_bytes()
            second = recover_labels(output)
            self.assertEqual(second["imported"], 0)
            self.assertEqual((output / "ai_labels.jsonl").read_bytes(), before_labels)
            self.assertEqual((output / "annotation_audit" / "original-1.json").read_bytes(), raw_audit)
            self.assertEqual((output / "annotation_coverage.json").read_bytes(), raw_coverage)

    def test_running_annotation_rejects_before_any_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.write_fixture(output, status="running")
            before = sorted(str(p) for p in output.rglob("*"))
            with self.assertRaises(ValueError):
                recover_labels(output)
            self.assertEqual(sorted(str(p) for p in output.rglob("*")), before)

    def test_changed_stored_protocol_rejects_before_any_label_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            _, protocol, _ = self.write_fixture(output)
            protocol["prompt_hash"] = "wrong"
            (output / "label_protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
            with self.assertRaises(ValueError):
                recover_labels(output)
            self.assertFalse((output / "ai_labels.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
