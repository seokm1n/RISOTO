"""Group legacy raw mentions by (company, published_at date) and summarize each group
so a human/LLM can quickly judge whether a genuine company risk event happened that day.
Output is a compact JSON list sorted by risk signal strength (keyword ratio + negative
sentiment ratio), for use in manually building risk_event ground truth.
"""

import csv
import json
from collections import Counter, defaultdict

SOURCE = "/tmp/mentions_classified_checkpoint_v3.csv"
OUTPUT = "/tmp/risk_label_candidates.json"
MIN_MENTIONS = 8


def main():
    groups = defaultdict(list)
    with open(SOURCE, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            published = (row.get("published_at") or "").strip()
            if len(published) < 10:
                continue
            date = published[:10]
            company = row.get("company", "").strip()
            if not company:
                continue
            groups[(company, date)].append(row)

    candidates = []
    for (company, date), rows in groups.items():
        if len(rows) < MIN_MENTIONS:
            continue
        risk_hits = sum(1 for r in rows if r.get("risk_keyword", "").strip())
        sentiment = Counter(r.get("sentiment_label", "") for r in rows)
        total = len(rows)
        negative_ratio = sentiment.get("negative", 0) / total
        risk_ratio = risk_hits / total
        titles = []
        seen = set()
        for r in rows:
            title = r.get("title", "").strip()
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
            if len(titles) >= 6:
                break
        candidates.append({
            "company": company,
            "date": date,
            "mention_count": total,
            "risk_keyword_ratio": round(risk_ratio, 3),
            "negative_ratio": round(negative_ratio, 3),
            "signal_score": round(risk_ratio + negative_ratio, 3),
            "sample_titles": titles,
        })

    candidates.sort(key=lambda item: item["signal_score"], reverse=True)
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(candidates, handle, ensure_ascii=False, indent=2)

    print(f"total (company,date) groups: {len(groups)}")
    print(f"candidates with >= {MIN_MENTIONS} mentions: {len(candidates)}")
    print(f"written to {OUTPUT}")


if __name__ == "__main__":
    main()
