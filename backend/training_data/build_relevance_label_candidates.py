"""Sample INDIVIDUAL mentions (not day-groups) per company, stratified so the sample
includes both likely-genuine company news and likely-incidental mentions (sports team
homonyms, aggregator/listicle pages, generic digests), for manual relevance labeling.
"""

import csv
import json
import random
from collections import defaultdict

SOURCE = "/tmp/combined_mentions_v2.csv"
OUTPUT = "/tmp/relevance_label_candidates.json"

# Patterns that often signal an INCIDENTAL mention rather than real company news.
INCIDENTAL_HINTS = [
    "랜더스", "KBO", "이닝", "타율", "홈런", "직관", "불펜", "선발투수",  # SSG Landers baseball
    "전체 기사리스트", "전체기사리스트", "헤드라인", "모닝루틴", "오늘의 뉴스",
    "간추린", "숏뉴스", "신문 읽기", "뉴스 브리핑", "뉴스클리핑", "뉴스pick",
    "T멤버십", "제휴카드",  # generic brand-partnership mentions
]
PER_COMPANY_TARGET = 55
RANDOM_SEED = 7


def main() -> None:
    by_company: dict[str, list[dict]] = defaultdict(list)
    with open(SOURCE, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            company = (row.get("company") or "").strip()
            title = (row.get("title") or "").strip()
            content = (row.get("content") or "").strip()
            if not company or not title:
                continue
            by_company[company].append({
                "company": company,
                "title": title,
                "content": content[:300],
                "url": row.get("url", ""),
                "published_at": row.get("published_at", ""),
                "likely_incidental": any(hint in title for hint in INCIDENTAL_HINTS),
            })

    rng = random.Random(RANDOM_SEED)
    sample: list[dict] = []
    for company, rows in by_company.items():
        incidental = [r for r in rows if r["likely_incidental"]]
        normal = [r for r in rows if not r["likely_incidental"]]
        rng.shuffle(incidental)
        rng.shuffle(normal)
        take_incidental = min(len(incidental), PER_COMPANY_TARGET // 3)
        take_normal = min(len(normal), PER_COMPANY_TARGET - take_incidental)
        picked = incidental[:take_incidental] + normal[:take_normal]
        seen_titles = set()
        deduped = []
        for item in picked:
            if item["title"] in seen_titles:
                continue
            seen_titles.add(item["title"])
            deduped.append(item)
        sample.extend(deduped)
        print(f"{company}: {len(deduped)} picked ({take_incidental} incidental-hint, {len(deduped)-take_incidental} normal) out of {len(rows)} total")

    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(sample, handle, ensure_ascii=False, indent=2)
    print(f"\ntotal sampled: {len(sample)}, written to {OUTPUT}")


if __name__ == "__main__":
    main()
