"""Backfill historical risk-event ground truth from the legacy raw mentions export so
Isolation Forest + LightGBM can be trained and tested end-to-end in the current system.

Phases:
  1. Load legacy raw mentions (combined_mentions_v2.csv), index by (company, published_at date).
  2. For each labeled date range (POSITIVE_EVENTS + auto-picked quiet NEGATIVE_DAYS), import a
     capped sample of the underlying article text as real NewsArticle/CompanyArticleMatch rows.
  3. Run the current sentiment model over the imported articles.
  4. Backfill company_feature_windows from the imported articles (real feature computation,
     current 15-minute schema -- not the legacy day-level schema).
  5. Create RiskEvent + RiskEventLabel rows for each labeled range (status='legacy_candidate').
  6. Train Isolation Forest, promote it, train LightGBM against it, promote it.
  7. Verify the promoted pair actually scores a REAL, currently-collected (live) feature window.
"""

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Company,
    CompanyArticleMatch,
    ModelVersion,
    NewsArticle,
    RawNewsArticle,
    RiskEvent,
    RiskEventLabel,
)
from app.services.article_filtering import content_hash, normalize_url
from app.services.review_identity import INTERNAL_REVIEW_ACTOR
from app.services.sentiment import analyze_company_articles
from app.services.risk_analysis import backfill_historical_windows
from app.training.risk_models import train_isolation_forest, train_risk_detector
from app.training.common import register_candidate  # noqa: F401  (imported for clarity of dependency)


MENTIONS_PATH = "/tmp/combined_mentions_v2.csv"
MAX_PER_DAY = 25

COMPANY_MAP = {
    "올리브영": 1, "무신사": 2, "에이블리": 3, "마켓컬리": 4, "SSG": 5,
    "11번가": 6, "카카오": 7, "네이버": 8, "쿠팡": 9,
}

# (company, start_date, end_date, risk_types, notes) -- manually judged from legacy raw titles.
POSITIVE_EVENTS = [
    ("쿠팡", "2025-12-01", "2025-12-10", ["security_privacy", "legal_regulatory"], "3,370만명 개인정보 유출 사태 초기: 국회 청문회, 이용자 대량 이탈."),
    ("쿠팡", "2025-12-24", "2025-12-26", ["security_privacy"], "개인정보 유출 사태 연장: 정부 합동TF, 탈퇴 러시 지속."),
    ("쿠팡", "2026-01-08", "2026-01-28", ["security_privacy", "legal_regulatory"], "유출 사태 후폭풍 지속: 집단소송법 논의, 이용자 110만명대 이탈, 미 정부 개입."),
    ("쿠팡", "2026-02-03", "2026-02-26", ["security_privacy", "legal_regulatory"], "유출 사태 3개월차: 미국 집단소송 확산, 공정위 갑질 22억 과징금, 대만 계정 유출."),
    ("쿠팡", "2026-03-03", "2026-03-03", ["security_privacy"], "유출 사태 3개월 연속 이용자 감소 통계 발표."),
    ("쿠팡", "2026-03-19", "2026-03-19", ["product_quality", "reputation_consumer"], "무료배송 기준 변경 논란, 금 제품 가품 판매 논란."),
    ("쿠팡", "2026-04-22", "2026-04-22", ["legal_regulatory"], "집단소송법 소급적용 두고 여야 공방, 쿠팡 사태 여전히 진행형."),
    ("쿠팡", "2026-06-10", "2026-06-18", ["security_privacy", "legal_regulatory"], "개인정보 유출 역대 최대 과징금(6,247억원) 확정, 집단분쟁조정 재개."),
    ("쿠팡", "2026-07-02", "2026-07-03", ["legal_regulatory"], "미 하원, 한국 정부의 쿠팡 차별 대우 문제 제기하며 통상 마찰로 비화."),
    ("쿠팡", "2026-07-31", "2026-07-31", ["security_privacy", "legal_regulatory"], "개인정보 유출 소비자분쟁조정위 첫 배상 결정(1인당 10만원)."),
    ("무신사", "2025-09-25", "2025-10-13", ["legal_regulatory"], "국정감사 대표이사 소환 논란(유통업계 전반과 함께 반복 거론)."),
    ("무신사", "2025-10-16", "2025-10-16", ["supply_operations", "legal_regulatory"], "카드사에 연 120억 마케팅비 요구 '지참금' 갑질 논란."),
    ("무신사", "2025-12-04", "2025-12-21", ["product_quality"], "구스다운 패딩 거위털 함량 미달(입점 판매 제품) 논란, 소비자원 점검, 집단소송 가능성."),
    ("무신사", "2026-03-12", "2026-03-17", ["product_quality", "legal_regulatory"], "입점업체 '택갈이'(라벨 바꿔치기) 논란, 영구 퇴출 조치."),
    ("무신사", "2026-04-27", "2026-04-27", ["legal_regulatory"], "공정위, 무신사 갑질 의혹 현장조사 실시."),
    ("무신사", "2026-05-20", "2026-05-21", ["reputation_consumer"], "7년 전 역사 비하 마케팅 광고 재조명, 공개 사과."),
    ("SSG", "2026-05-19", "2026-05-26", ["reputation_consumer"], "정용진 회장 '탱크데이' 5·18 조롱 논란, 불매운동 확산, 공개 사과."),
    ("마켓컬리", "2026-01-21", "2026-01-21", ["reputation_consumer", "legal_regulatory"], "김슬아 대표 남편 성추행 의혹, IPO 영향 우려."),
    ("올리브영", "2026-01-14", "2026-01-27", ["reputation_consumer"], "중국 '온리영' 매장의 올리브영 상호·로고 도용(짝퉁) 논란."),
    ("올리브영", "2026-04-20", "2026-04-20", ["legal_regulatory"], "공정위 현장 조사."),
    ("카카오", "2026-05-21", "2026-05-21", ["labor_hr"], "창사 첫 총파업 위기, 카카오페이 서비스 차질 우려."),
    ("카카오", "2026-06-12", "2026-06-12", ["security_privacy", "legal_regulatory"], "카카오페이 개인정보 유출 과징금 60억원 부과."),
    ("네이버", "2025-10-15", "2025-10-15", ["labor_hr", "reputation_consumer"], "네이버웹툰, 신인 작가 대상 무급·불공정 계약 논란."),
]

TARGET_NEGATIVE_COUNT = 80


def load_index() -> dict[tuple[str, str], list[dict]]:
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with open(MENTIONS_PATH, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            published = (row.get("published_at") or "").strip()
            company = (row.get("company") or "").strip()
            if len(published) < 10 or not company:
                continue
            index[(company, published[:10])].append(row)
    return index


def pick_negative_days(candidates_path: str, positive_ranges: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    with open(candidates_path, encoding="utf-8") as handle:
        candidates = json.load(handle)

    def in_positive_range(company: str, date: str) -> bool:
        for pcompany, start, end in positive_ranges:
            if company == pcompany and start <= date <= end:
                return True
        return False

    import re
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    quiet = [
        item for item in candidates
        if item["signal_score"] < 0.5
        and date_pattern.match(item["date"])
        and not in_positive_range(item["company"], item["date"])
    ]
    quiet.sort(key=lambda item: item["signal_score"])
    by_company: dict[str, list[str]] = defaultdict(list)
    picked: list[tuple[str, str]] = []
    for item in quiet:
        if len(by_company[item["company"]]) >= 10:
            continue
        by_company[item["company"]].append(item["date"])
        picked.append((item["company"], item["date"]))
        if len(picked) >= TARGET_NEGATIVE_COUNT:
            break
    return picked


def import_day(db, index, company_name: str, date: str, company_id: int) -> int:
    rows = index.get((company_name, date), [])[:MAX_PER_DAY]
    imported = 0
    for row in rows:
        title = (row.get("title") or "").strip()
        content = (row.get("content") or "").strip()
        url = (row.get("url") or "").strip()
        if not title or not url:
            continue
        published_raw = (row.get("published_at") or "").strip()
        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        source = "historical_backfill"
        norm_url = normalize_url(url)
        item_hash = content_hash(title, content)
        raw = db.scalar(
            select(RawNewsArticle).where(
                RawNewsArticle.source == source,
                RawNewsArticle.normalized_url == norm_url,
                RawNewsArticle.content_hash == item_hash,
            )
        )
        if raw is None:
            raw = RawNewsArticle(
                source=source, title=title, summary=content[:2000], url=url,
                original_url=url, normalized_url=norm_url, content_hash=item_hash,
                published_at=published_at, raw_payload={"legacy": True},
            )
            db.add(raw)
            db.flush()
        article = db.scalar(select(NewsArticle).where(NewsArticle.url == norm_url))
        if article is None:
            article = NewsArticle(
                raw_article_id=raw.id, source=source, title=title, summary=content[:2000],
                url=norm_url, original_url=url, published_at=published_at, raw_payload={"legacy": True},
            )
            db.add(article)
            db.flush()
        existing_match = db.get(CompanyArticleMatch, {"company_id": company_id, "article_id": article.id})
        if existing_match is None:
            db.add(CompanyArticleMatch(company_id=company_id, article_id=article.id, matched_keyword=company_name))
            imported += 1
    return imported


def create_risk_event(db, company_id: int, start: str, end: str, is_risk: bool, risk_types: list[str], notes: str) -> None:
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    event = RiskEvent(
        company_id=company_id, anomaly_score=0.0, severity="medium" if is_risk else "low",
        status="legacy_candidate", primary_type=risk_types[0] if risk_types else None,
        summary=notes[:500], model_version="legacy_backfill", model_state="provisional",
        approval_state="draft", opened_at=start_dt, last_seen_at=end_dt, detected_at=start_dt,
    )
    db.add(event)
    db.flush()
    db.add(RiskEventLabel(
        risk_event_id=event.id, annotator=INTERNAL_REVIEW_ACTOR, is_risk=is_risk,
        event_start=start_dt, event_end=end_dt, risk_types=risk_types if is_risk else [],
        status="confirmed", notes=notes,
    ))


def main() -> None:
    print("Phase 1: loading legacy mentions index...")
    index = load_index()
    print(f"  indexed {sum(len(v) for v in index.values())} mentions across {len(index)} (company,date) keys")

    positive_ranges = [(c, s, e) for c, s, e, *_ in POSITIVE_EVENTS]
    negative_days = pick_negative_days("/tmp/risk_label_candidates.json", positive_ranges)
    print(f"Phase 2: importing article text for {len(POSITIVE_EVENTS)} positive ranges + {len(negative_days)} negative days...")

    imported_total = 0
    touched_companies: set[int] = set()
    with SessionLocal() as db:
        for company, start, end, *_ in POSITIVE_EVENTS:
            company_id = COMPANY_MAP[company]
            touched_companies.add(company_id)
            current = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            while current <= end_dt:
                imported_total += import_day(db, index, company, current.strftime("%Y-%m-%d"), company_id)
                current += timedelta(days=1)
        for company, date in negative_days:
            company_id = COMPANY_MAP[company]
            touched_companies.add(company_id)
            imported_total += import_day(db, index, company, date, company_id)
        db.commit()
    print(f"  imported {imported_total} new company-article matches across {len(touched_companies)} companies")

    print("Phase 3: running sentiment analysis over imported articles...")
    for company_id in sorted(touched_companies):
        total = 0
        while True:
            done = analyze_company_articles(company_id, batch_limit=200)
            total += done
            if done < 200:
                break
        print(f"  company {company_id}: analyzed {total} articles")

    print("Phase 4: backfilling 15-minute feature windows from imported articles...")
    for company_id in sorted(touched_companies):
        result = backfill_historical_windows(company_id)
        print(f"  company {company_id}: {result}")

    print("Phase 5: creating risk_events + risk_event_labels...")
    with SessionLocal() as db:
        for company, start, end, risk_types, notes in POSITIVE_EVENTS:
            create_risk_event(db, COMPANY_MAP[company], start, end, True, risk_types, notes)
        for company, date in negative_days:
            create_risk_event(db, COMPANY_MAP[company], date, date, False, [], "배경 노이즈 수준: 기업명은 언급되나 실질적 위험 사건 아님(자동 선별).")
        db.commit()
    print(f"  created {len(POSITIVE_EVENTS)} positive + {len(negative_days)} negative risk events")

    print("Phase 6: training Isolation Forest...")
    from app.config import get_settings
    settings = get_settings()
    output_root = Path(settings.model_artifact_dir)
    if_result = train_isolation_forest(output_root)
    print(f"  {if_result}")
    with SessionLocal() as db:
        if_version = db.get(ModelVersion, if_result["model_version_id"])
        if_version.status = "production"
        if_version.promoted_at = datetime.now(timezone.utc)
        db.commit()
    print("  Isolation Forest promoted to production.")

    print("Phase 7: training LightGBM risk detector...")
    lgbm_result = train_risk_detector(output_root)
    print(f"  {lgbm_result}")
    with SessionLocal() as db:
        lgbm_version = db.get(ModelVersion, lgbm_result["model_version_id"])
        lgbm_version.status = "production"
        lgbm_version.promoted_at = datetime.now(timezone.utc)
        db.commit()
    print("  LightGBM promoted to production.")

    print("Phase 8: verifying against a REAL, currently-collected (live) feature window...")
    from app.services.risk_analysis import resolve_production_risk_detector, score_window
    from app.models import CompanyFeatureWindow
    with SessionLocal() as db:
        detector = resolve_production_risk_detector(db)
        print(f"  detector.available = {detector.available} (reason={detector.reason})")
        live_window = db.scalar(
            select(CompanyFeatureWindow)
            .where(CompanyFeatureWindow.data_quality != "unavailable", CompanyFeatureWindow.window_start >= datetime(2026, 8, 25, tzinfo=timezone.utc))
            .order_by(CompanyFeatureWindow.window_start.desc())
            .limit(1)
        )
        if live_window is None:
            print("  no real live window found to verify against")
        else:
            score_window(db, live_window, settings)
            db.commit()
            print(f"  scored LIVE window id={live_window.id} company={live_window.company_id} window_start={live_window.window_start}: "
                  f"risk_probability={live_window.risk_probability}, is_risk={live_window.is_risk}, model_version={live_window.model_version}")


if __name__ == "__main__":
    main()
