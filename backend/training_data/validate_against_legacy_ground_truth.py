"""Replay legacy human-reviewed (company, date) risk labels through the CURRENT
relevance + sentiment models to check whether today's pipeline agrees with the
old ground truth. This does not (and cannot) validate the live 15-minute
pipeline directly -- the current DB only has data from 2026-08-25 onward,
while this ground truth covers 2025-07 through 2026-07, so there is zero
calendar overlap. This script instead re-scores the OLD raw mention text with
TODAY's models as a proxy check on the models themselves.
"""

import csv
from collections import Counter, defaultdict

from app.services.fine_tuned_text import predict_relevance, predict_sentiment

GROUND_TRUTH = "/tmp/if_validation_result_v3.csv"
MENTIONS = "/tmp/mentions_classified_checkpoint_v3.csv"


def load_ground_truth():
    with open(GROUND_TRUTH, encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _normalize_date(value: str) -> str:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def load_mentions_index():
    index = defaultdict(list)
    with open(MENTIONS, encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            published = (row.get("published_at") or "").strip()
            pub_date = published[:10] if published else ""
            for date_value in {_normalize_date(row["collected_date"]), pub_date}:
                if date_value:
                    index[(row["company"], date_value)].append(row)
    return index


def main():
    ground_truth = load_ground_truth()
    mentions_index = load_mentions_index()

    print(f"ground truth rows: {len(ground_truth)}")
    print(f"mention rows indexed: {sum(len(v) for v in mentions_index.values())}")
    print(f"distinct (company,date) keys in mentions: {len(mentions_index)}")
    print()

    matched = 0
    unmatched = []
    rows_out = []

    for gt in ground_truth:
        key = (gt["company"], gt["date"])
        mentions = mentions_index.get(key, [])
        if not mentions:
            unmatched.append(key)
            continue
        matched += 1
        sample = mentions[:20]
        texts = [f"{m['title']} {m['content']}".strip()[:2000] for m in sample if m.get("title") or m.get("content")]

        old_filter_counts = Counter(m.get("filter_label", "") for m in mentions)
        old_sentiment_counts = Counter(m.get("sentiment_label", "") for m in mentions)

        new_relevant_scores = []
        for text in texts:
            result = predict_relevance(text)
            if result:
                new_relevant_scores.append(result["relevant"])
        new_avg_relevant = sum(new_relevant_scores) / len(new_relevant_scores) if new_relevant_scores else None

        _, sentiment_rows = predict_sentiment(texts) or (None, [])
        new_sentiment_counts = Counter()
        for row in sentiment_rows:
            label = max(row, key=row.get)
            new_sentiment_counts[label] += 1

        rows_out.append({
            "company": gt["company"],
            "date": gt["date"],
            "is_risk_event": gt["is_risk_event"],
            "if_is_anomaly": gt["if_is_anomaly"],
            "notes": gt["notes"][:60],
            "mention_count": len(mentions),
            "old_filter_counts": dict(old_filter_counts),
            "old_sentiment_counts": dict(old_sentiment_counts),
            "new_avg_relevant": round(new_avg_relevant, 3) if new_avg_relevant is not None else None,
            "new_sentiment_counts": dict(new_sentiment_counts),
        })

    print(f"matched {matched}/{len(ground_truth)} ground-truth rows to mention data")
    print(f"unmatched keys: {unmatched}")
    print()
    for row in rows_out:
        print(row)


if __name__ == "__main__":
    main()
