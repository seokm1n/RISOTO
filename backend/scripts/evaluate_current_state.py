"""팀 공유 문서용 성능 스냅샷을 한 번에 뽑는 읽기 전용 스크립트다.

수정 전/후 relevance filter 비교(human_relevance_labels.csv 재실행), 광고
탐지 최초 실측, review_required 큐 상태, 위험 이벤트 분포, 응답 초안 검증
통과율, 그리고 model_versions에 저장된 지표를 한 파일(JSON)로 모은다.

DB에는 쓰지 않는다 (SELECT만 실행). 결과는 backend/training_data/ 아래
JSON으로 남겨 호스트에서 바로 읽을 수 있게 한다.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from sklearn.metrics import roc_auc_score

from app.database import SessionLocal
from app.models import (
    ArticleFilterResult,
    Company,
    CompanyKeyword,
    ModelVersion,
    ResponseDraft,
    RiskEvent,
)
from app.services.article_filtering import FilterConfig, classify_article
from sqlalchemy import func, text as sql_text

CSV_PATH = Path(__file__).resolve().parent.parent / "training_data" / "human_relevance_labels.csv"
OUT_PATH = Path(__file__).resolve().parent.parent / "training_data" / "current_state_eval.json"


class Item:
    __slots__ = ("title", "summary", "url", "source", "published_at", "id")

    def __init__(self, title, summary, url):
        self.title = title
        self.summary = summary
        self.url = url
        self.source = "kakao_daum"
        self.published_at = None
        self.id = None


def _metrics(y_true: list[int], y_score: list[float], y_pred_positive: list[bool]) -> dict:
    tp = sum(1 for t, p in zip(y_true, y_pred_positive) if t == 1 and p)
    fp = sum(1 for t, p in zip(y_true, y_pred_positive) if t == 0 and p)
    fn = sum(1 for t, p in zip(y_true, y_pred_positive) if t == 1 and not p)
    tn = sum(1 for t, p in zip(y_true, y_pred_positive) if t == 0 and not p)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    accuracy = (tp + tn) / len(y_true) if y_true else 0.0
    try:
        auc = roc_auc_score(y_true, y_score) if len(set(y_true)) > 1 else None
    except ValueError:
        auc = None
    return {
        "n": len(y_true),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "accuracy": round(accuracy, 4),
        "auc": round(auc, 4) if auc is not None else None,
    }


def evaluate_relevance_and_ads(db) -> dict:
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    company_cache: dict[int, tuple[Company, list[CompanyKeyword]]] = {}

    old_true, old_score, old_pred = [], [], []
    new_true, new_score, new_pred = [], [], []
    ad_true, ad_score, ad_pred = [], [], []
    skipped = 0
    body_only_examples = []

    for row in rows:
        label = row["relevance_label"]
        if label == "uncertain":
            continue
        company_id = int(row["company_id"])
        if company_id not in company_cache:
            company = db.get(Company, company_id)
            keywords = db.query(CompanyKeyword).filter(
                CompanyKeyword.company_id == company_id
            ).all()
            company_cache[company_id] = (company, keywords)
        company, keywords = company_cache[company_id]
        if company is None:
            skipped += 1
            continue

        item = Item(row["title"], row["body"], row["url"])
        try:
            decision = classify_article(company, keywords, item, config=FilterConfig())
        except Exception as exc:  # keep the sweep going; report the failure count
            skipped += 1
            continue

        is_relevant = 1 if label == "relevant" else 0

        old_decision = row["decision"]
        old_rel_score = float(row["relevance_score"])
        old_true.append(is_relevant)
        old_score.append(old_rel_score)
        old_pred.append(old_decision == "accepted")

        new_true.append(is_relevant)
        new_score.append(decision.relevance_score)
        new_pred.append(decision.decision == "accepted")

        if row["advertisement_label"] in ("yes", "no"):
            ad_true.append(1 if row["advertisement_label"] == "yes" else 0)
            ad_score.append(decision.advertising_score)
            ad_pred.append(decision.decision in ("rejected", "review_required") and decision.reason == "advertisement")

        evid = decision.details.get("relevance_evidence", [])
        is_body_only = any(
            e.startswith("identity_in_summary:") or e.startswith("product_in_summary:") for e in evid
        ) and not any(
            e.startswith("identity_in_title:") or e.startswith("product_in_title:") for e in evid
        )
        if is_body_only and len(body_only_examples) < 5:
                body_only_examples.append({
                    "title": row["title"][:80],
                    "label": label,
                    "old_score": old_rel_score,
                    "new_score": decision.relevance_score,
                    "old_decision": old_decision,
                    "new_decision": decision.decision,
                })

    return {
        "relevance_before_fix": _metrics(old_true, old_score, old_pred),
        "relevance_after_fix": _metrics(new_true, new_score, new_pred),
        "advertising_current": _metrics(ad_true, ad_score, ad_pred),
        "skipped_rows": skipped,
        "total_rows": len(rows),
        "body_only_examples": body_only_examples,
    }


def review_queue_stats(db) -> dict:
    rows = db.execute(sql_text(
        """
        SELECT decision, reason, count(*) AS n,
               avg(extract(epoch from (now() - filtered_at)) / 3600) AS avg_age_hours
        FROM article_filter_results
        GROUP BY decision, reason
        ORDER BY n DESC
        """
    )).mappings().all()
    return {"by_decision_reason": [dict(r) for r in rows]}


def risk_event_stats(db) -> dict:
    status_rows = db.execute(sql_text(
        "SELECT status, severity, count(*) AS n FROM risk_events GROUP BY status, severity ORDER BY n DESC"
    )).mappings().all()
    type_rows = db.execute(sql_text(
        "SELECT primary_type, count(*) AS n FROM risk_events WHERE primary_type IS NOT NULL "
        "GROUP BY primary_type ORDER BY n DESC"
    )).mappings().all()
    total = db.query(func.count(RiskEvent.id)).scalar()
    return {
        "total": total,
        "by_status_severity": [dict(r) for r in status_rows],
        "by_primary_type": [dict(r) for r in type_rows],
    }


def response_draft_stats(db) -> dict:
    verify_rows = db.execute(sql_text(
        "SELECT content->>'status' AS verify_status, count(*) AS n FROM response_drafts "
        "GROUP BY content->>'status' ORDER BY n DESC"
    )).mappings().all()
    approval_rows = db.execute(sql_text(
        "SELECT approval_state, count(*) AS n FROM response_drafts GROUP BY approval_state ORDER BY n DESC"
    )).mappings().all()
    return {
        "by_verify_status": [dict(r) for r in verify_rows],
        "by_approval_state": [dict(r) for r in approval_rows],
    }


def model_registry_snapshot(db) -> dict:
    rows = db.query(ModelVersion).filter(ModelVersion.status.in_(("production", "candidate"))).all()
    out = []
    for row in rows:
        out.append({
            "task": row.task,
            "version": row.version,
            "status": row.status,
            "base_model": row.base_model,
            "metrics": row.metrics,
            "training_counts": row.training_counts,
            "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
        })
    return {"model_versions": out}


def main() -> None:
    db = SessionLocal()
    try:
        result = {
            "relevance_and_ads": evaluate_relevance_and_ads(db),
            "review_queue": review_queue_stats(db),
            "risk_events": risk_event_stats(db),
            "response_drafts": response_draft_stats(db),
            "model_registry": model_registry_snapshot(db),
        }
    finally:
        db.close()

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:3000])


if __name__ == "__main__":
    main()
