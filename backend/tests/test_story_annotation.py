"""Full-corpus annotation contract and resumption tests; no database or network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.training.story_annotation import (
    CONTRACT, LABEL_VERSION, annotation_payload, digest, label_schema, label_snapshots, model_matches,
    protocol_for, read_jsonl, validate_existing, validate_response, validate_snapshots,
)


def snapshot(index=1):
    item = dict(key=f"1:{index}", company_id=1, company_name="예시기업", aliases=["예시"],
                story_id=index, articles=[dict(id=index, title="예시기업 제품 발표", summary="신제품 출시",
                                              negative_probability=.99)], risk_probability=.99)
    item["snapshot_hash"] = digest(item)
    return item


def response(item):
    return {"labels": {item["key"]: dict(decision="normal", confidence="high",
                                       reason="대상 기업의 일반적인 제품 출시다.",
                                       evidence_article_ids=[item["articles"][0]["id"]])}}


class StoryAnnotationTests(unittest.TestCase):
    def test_schema_requires_each_key_and_only_own_evidence(self):
        batch = [snapshot(1), snapshot(2)]
        schema = label_schema(batch)["properties"]["labels"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["1:1", "1:2"])
        self.assertFalse(schema["additionalProperties"])
        evidence = schema["properties"]["1:2"]["properties"]["evidence_article_ids"]
        self.assertEqual(evidence["items"]["enum"], [2])

    def test_response_rejects_wrong_keys_evidence_and_missing_reason(self):
        item = snapshot()
        valid = response(item)
        self.assertEqual(validate_response(valid, [item])[0]["key"], item["key"])
        invalid = [
            {"labels": {}}, {"labels": list(valid["labels"].values())},
            {"labels": {"wrong": valid["labels"][item["key"]]}},
        ]
        for field, value in [("evidence_article_ids", [2]), ("evidence_article_ids", [True]),
                             ("evidence_article_ids", [1, 1]), ("evidence_article_ids", []),
                             ("reason", " "), ("decision", "maybe"), ("confidence", "sure")]:
            changed = deepcopy(valid)
            changed["labels"][item["key"]][field] = value
            invalid.append(changed)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_response(value, [item])

    def test_payload_cannot_copy_stored_predictions(self):
        item = snapshot()
        payload = annotation_payload([item])[0]
        self.assertEqual(set(payload), {"key", "company", "aliases", "articles"})
        self.assertEqual(set(payload["articles"][0]), {"id", "title", "summary"})
        altered = deepcopy(item)
        altered["risk_probability"] = .01
        altered["articles"][0]["negative_probability"] = .01
        self.assertEqual(annotation_payload([item]), annotation_payload([altered]))

    def test_frozen_input_hash_and_duplicate_keys_are_verified(self):
        item = snapshot()
        validate_snapshots([item])
        for invalid in [[item, item], [{**item, "company_name": "변경"}], []]:
            with self.assertRaises(ValueError):
                validate_snapshots(invalid)

    def test_resume_requires_exact_protocol_and_matching_model(self):
        item = snapshot()
        protocol = protocol_for([item], "gpt-4o-mini")
        label = validate_response(response(item), [item])[0]
        label.update(snapshot_hash=item["snapshot_hash"], prompt_hash=protocol["prompt_hash"],
                     label_version=LABEL_VERSION, requested_model=protocol["model"],
                     annotator="gpt-4o-mini-2024-07-18", human_reviewed=False,
                     label_source="ai_generated", response_id="resp_test", labeled_at="2026-09-07")
        self.assertEqual(validate_existing([label], [item], protocol), {item["key"]})
        for change in [{"label_version": "story-independent-ai-v1"}, {"snapshot_hash": "bad"},
                       {"prompt_hash": "bad"}, {"requested_model": "different"},
                       {"annotator": "gpt-4o"}, {"human_reviewed": True}, {"response_id": ""}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_existing([{**label, **change}], [item], protocol)
        with self.assertRaises(ValueError):
            validate_existing([label, label], [item], protocol)

    def test_torn_final_append_recovered_but_earlier_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            valid = json.dumps({"key": "1:1"}).encode() + b"\n"
            path.write_bytes(valid + b'{"key": "partial')
            self.assertEqual(read_jsonl(path, recover_tail=True), [{"key": "1:1"}])
            self.assertEqual(path.read_bytes(), valid)
            self.assertEqual(len(list(Path(directory).glob("*.torn-*"))), 1)
            path.write_bytes(b"broken\n" + valid)
            with self.assertRaises(ValueError):
                read_jsonl(path, recover_tail=True)

    def test_complete_last_record_without_newline_remains_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.jsonl"
            path.write_bytes(b'{"key": "1:1"}')
            self.assertEqual(read_jsonl(path, recover_tail=True), [{"key": "1:1"}])
            self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_mock_annotation_writes_audit_and_resumes_without_api_requests(self):
        items = [snapshot(1), snapshot(2)]
        settings = SimpleNamespace(llm_labeling_model_name="gpt-4o-mini", openai_api_key="not-a-real-key")

        def create(**kwargs):
            payload = json.loads(kwargs["input"])
            self.assertEqual(len(payload), 1)
            item = next(s for s in items if s["key"] == payload[0]["key"])
            text = json.dumps(response(item))
            model = "gpt-4o-mini-2024-07-18"
            return SimpleNamespace(id="resp_" + item["key"], status="completed", model=model,
                                   output_text=text, model_dump=lambda **kw: {
                                       "output_text": text, "model": model,
                                       "usage": {"input_tokens": 100, "output_tokens": 30}})

        with tempfile.TemporaryDirectory() as directory, patch("app.config.get_settings", return_value=settings), \
                patch("openai.OpenAI") as client, patch("builtins.print"):
            path = Path(directory)
            (path / "snapshots.jsonl").write_text("".join(json.dumps(s) + "\n" for s in items), encoding="utf-8")
            client.return_value.__enter__.return_value.responses.create.side_effect = create
            label_snapshots(path, workers=1, batch_size=1)
            self.assertEqual(client.call_count, 2)
            self.assertEqual(len(read_jsonl(path / "ai_labels.jsonl")), 2)
            audits = list((path / "annotation_audit").glob("*.json"))
            self.assertEqual(len(audits), 2)
            self.assertEqual(json.loads(audits[0].read_text(encoding="utf-8"))["response"]["usage"]["input_tokens"], 100)
            client.reset_mock()
            label_snapshots(path, workers=1, batch_size=1)
            client.assert_not_called()
            coverage = json.loads((path / "annotation_coverage.json").read_text(encoding="utf-8"))
            self.assertEqual((coverage["status"], coverage["pending"], coverage["labeled"]), ("complete", 0, 2))
            settings.llm_labeling_model_name = "different-model"
            with self.assertRaises(ValueError):
                label_snapshots(path, workers=1, batch_size=1)
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
