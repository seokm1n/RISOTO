"""story_v2 사건에 대한 사람 라벨 후보를 뽑는다.

지금까지 사람이 라벨링한 위기 사건 82건(human_risk_event_labels.csv)은 전부
event_source='window_v1'이다. 실제로 지금 사용자에게 보이는 사건은 6,071건이 전부
story_v2인데, story_v2 자체의 risk_probability가 사람 기준으로 얼마나 정확한지는
검증된 적이 없다 (2026-09-07 확인, RiskEventLabel 커버리지 0건).

human_risk_event_labels.csv와 같은 컬럼 스키마로 CSV를 만든다. 정보량 기준 층화:
현재 risk_probability 구간(band)과 primary_type을 모두 교차해서 뽑아, 같은 건수로
더 다양한 상황을 검증할 수 있게 한다.

DB에 쓰지 않는다 (SELECT만).
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

from sqlalchemy import select, case, func

from app.database import SessionLocal
from app.models import Company, RiskEvent, RiskEventArticle, NewsArticle

OUTPUT = Path(__file__).resolve().parent / "story_v2_label_candidates.csv"
PER_CELL_TARGET = 6  # band x primary_type 조합당 최대 표본 수
RANDOM_SEED = 42


def _band(probability: float) -> str:
    if probability >= 0.90:
        return "A_0.90+"
    if probability >= 0.70:
        return "B_0.70-0.90"
    return "C_below_0.70"


def main() -> None:
    db = SessionLocal()
    rng = random.Random(RANDOM_SEED)

    events = list(
        db.scalars(
            select(RiskEvent).where(
                RiskEvent.event_source == "story_v2",
                RiskEvent.risk_probability > 0,
                RiskEvent.primary_type.is_not(None),
            )
        )
    )

    cells: dict[tuple[str, str], list[RiskEvent]] = {}
    for event in events:
        key = (_band(event.risk_probability), event.primary_type)
        cells.setdefault(key, []).append(event)

    selected: list[RiskEvent] = []
    for key, rows in sorted(cells.items()):
        rng.shuffle(rows)
        selected.extend(rows[:PER_CELL_TARGET])

    companies = {c.id: c for c in db.scalars(select(Company))}

    written = 0
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "band", "event_id", "company", "primary_type", "시작", "종료",
            "지속시간", "기사수", "제목에기업명", "is_risk", "실제유형", "note", "max_p",
        ])
        for event in selected:
            company = companies.get(event.company_id)
            if company is None:
                continue
            evidence = list(
                db.execute(
                    select(NewsArticle.title, NewsArticle.published_at, NewsArticle.created_at)
                    .join(RiskEventArticle, RiskEventArticle.article_id == NewsArticle.id)
                    .where(RiskEventArticle.risk_event_id == event.id)
                    .order_by(RiskEventArticle.evidence_score.desc())
                    .limit(6)
                )
            )
            title_has_company = sum(
                1 for title, _, _ in evidence if company.name in (title or "")
            )
            duration_hours = round(
                max(0.0, (event.last_seen_at - event.opened_at).total_seconds() / 3600), 1
            )
            writer.writerow([
                _band(event.risk_probability),
                event.id,
                company.name,
                event.primary_type,
                event.opened_at.strftime("%Y-%m-%d %H:%M"),
                event.last_seen_at.strftime("%Y-%m-%d %H:%M"),
                f"{duration_hours}h",
                len(evidence),
                title_has_company,
                "",  # is_risk -- 사람이 채움
                "",  # 실제유형 -- 사람이 채움
                "; ".join(title for title, _, _ in evidence[:3]),  # 참고용 제목 샘플
                round(float(event.risk_probability), 4),
            ])
            written += 1

    db.close()
    print(f"cells: {len(cells)}  events considered: {len(events)}  written: {written}")
    print(f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
