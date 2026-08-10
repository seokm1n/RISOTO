from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Company, CompanyArticleMatch, NewsArticle


EMOTION_POLARITY = {
    "공포": -0.85,
    "놀람": -0.25,
    "분노": -0.95,
    "슬픔": -0.75,
    "중립": 0.0,
    "행복": 1.0,
    "혐오": -1.0,
    "negative": -1.0,
    "negative sentiment": -1.0,
    "neg": -1.0,
    "neutral": 0.0,
    "positive": 1.0,
    "positive sentiment": 1.0,
    "pos": 1.0,
    "0": -1.0,
    "1": 1.0,
}

DISPLAY_LABELS = {
    "0": "부정",
    "1": "긍정",
    "neg": "부정",
    "negative": "부정",
    "pos": "긍정",
    "positive": "긍정",
}


@dataclass(slots=True)
class SentimentResult:
    label: str
    score: float
    confidence: float


class KoElectraSentimentAnalyzer:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._pipeline = None
        self._load_lock = Lock()
        self._inference_lock = Lock()

    def _get_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        with self._load_lock:
            if self._pipeline is None:
                from transformers import pipeline

                self._pipeline = pipeline(
                    "text-classification",
                    model=self.model_name,
                    tokenizer=self.model_name,
                    device=-1,
                    trust_remote_code=True,
                )
        return self._pipeline

    def analyze(self, texts: list[str]) -> list[SentimentResult]:
        if not texts:
            return []
        classifier = self._get_pipeline()
        with self._inference_lock:
            raw_results = classifier(
                texts,
                truncation=True,
                max_length=256,
                top_k=None,
                batch_size=min(8, len(texts)),
            )

        results: list[SentimentResult] = []
        for candidates in raw_results:
            if isinstance(candidates, dict):
                candidates = [candidates]
            best = max(candidates, key=lambda item: float(item["score"]))
            weighted_score = 0.0
            recognized = False
            for item in candidates:
                label = str(item["label"])
                polarity = EMOTION_POLARITY.get(label.casefold())
                if polarity is None:
                    polarity = EMOTION_POLARITY.get(label)
                if polarity is not None:
                    recognized = True
                    weighted_score += polarity * float(item["score"])
            if not recognized:
                raise ValueError(
                    f"감성 모델의 라벨 매핑을 확인해야 합니다: {best['label']}"
                )
            results.append(
                SentimentResult(
                    label=DISPLAY_LABELS.get(str(best["label"]).casefold(), str(best["label"])),
                    score=max(-1.0, min(1.0, weighted_score)),
                    confidence=float(best["score"]),
                )
            )
        return results


_analyzer: KoElectraSentimentAnalyzer | None = None
_analyzer_lock = Lock()


def get_analyzer() -> KoElectraSentimentAnalyzer:
    global _analyzer
    settings = get_settings()
    if _analyzer is None or _analyzer.model_name != settings.sentiment_model_name:
        with _analyzer_lock:
            if _analyzer is None or _analyzer.model_name != settings.sentiment_model_name:
                _analyzer = KoElectraSentimentAnalyzer(settings.sentiment_model_name)
    return _analyzer


def analyze_company_articles(company_id: int, batch_limit: int = 100) -> int:
    settings = get_settings()
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            return 0
        articles = list(
            db.scalars(
                select(NewsArticle)
                .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
                .where(
                    CompanyArticleMatch.company_id == company_id,
                    NewsArticle.analyzed_at.is_(None),
                )
                .order_by(NewsArticle.published_at, NewsArticle.id)
                .limit(batch_limit)
            )
        )
        if not articles:
            return 0
        company.analysis_status = "running"
        company.analysis_error = None
        db.commit()

        texts = [" ".join(filter(None, [item.title, item.summary]))[:4000] for item in articles]
        try:
            results = get_analyzer().analyze(texts)
        except Exception as exc:
            company = db.get(Company, company_id)
            if company is not None:
                company.analysis_status = "error"
                company.analysis_error = str(exc)[:1000]
                db.commit()
            return 0

        now = datetime.now(timezone.utc)
        for article, result in zip(articles, results):
            article.sentiment_label = result.label
            article.sentiment_score = result.score
            article.sentiment_confidence = result.confidence
            article.sentiment_model = settings.sentiment_model_name
            article.analyzed_at = now
        company = db.get(Company, company_id)
        if company is not None:
            company.analysis_status = "warming"
        db.commit()
        return len(results)
