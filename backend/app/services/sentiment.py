"""NLI 가설 비교를 이용해 기업 뉴스의 긍정·부정·중립 감성을 분석한다."""

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Company, CompanyArticleMatch, NewsArticle
from app.services.klue_nli import get_klue_nli_classifier
from app.services.fine_tuned_text import predict_sentiment


@dataclass(slots=True)
class SentimentResult:
    """기사 감성 레이블과 방향성 점수 및 판정 신뢰도를 표현한다."""

    label: str
    score: float
    confidence: float
    positive_probability: float
    neutral_probability: float
    negative_probability: float
    model_version: str


class KlueRobertaSentimentAnalyzer:
    """한국어 NLI 가설을 비교해 기업 뉴스를 세 가지 감성으로 분류한다."""

    # 긍정·부정 확률 차이가 이 범위 안이면 과도한 극성 판정을 피하고 중립으로 본다.
    NEUTRAL_MARGIN = 0.20
    # 첫 문장은 부정, 둘째 문장은 긍정 가설이며 반환 확률도 이 순서를 따른다.
    HYPOTHESES = (
        "기업에 부정적인 소식이다.",
        "기업에 긍정적인 소식이다.",
    )

    def __init__(self, model_name: str, allow_download: bool = True) -> None:
        """감성 분석에 사용할 KLUE 모델 설정을 보관한다."""
        self.model_name = model_name
        self.allow_download = allow_download

    def analyze(self, texts: list[str]) -> list[SentimentResult]:
        """텍스트마다 긍정·부정 NLI 확률을 비교해 3단계 감성과 신뢰도를 반환한다."""
        if not texts:
            return []
        promoted = predict_sentiment(texts)
        if promoted is not None:
            version, probability_rows = promoted
            results: list[SentimentResult] = []
            for probabilities in probability_rows:
                label_key = max(probabilities, key=probabilities.get)
                label = {"positive": "긍정", "neutral": "중립", "negative": "부정"}[label_key]
                score = probabilities["positive"] - probabilities["negative"]
                results.append(
                    SentimentResult(
                        label=label,
                        score=float(score),
                        confidence=float(probabilities[label_key]),
                        positive_probability=float(probabilities["positive"]),
                        neutral_probability=float(probabilities["neutral"]),
                        negative_probability=float(probabilities["negative"]),
                        model_version=version,
                    )
                )
            return results
        classifier = get_klue_nli_classifier(self.model_name, self.allow_download)
        raw_results = classifier.score_hypotheses(
            texts,
            [list(self.HYPOTHESES) for _ in texts],
            batch_size=min(8, max(1, len(texts))),
        )

        results: list[SentimentResult] = []
        for probabilities in raw_results:
            negative, positive = probabilities
            score = positive - negative
            neutral_strength = max(0.0, 1.0 - abs(score) / self.NEUTRAL_MARGIN)
            probability_total = positive + negative + neutral_strength
            positive_probability = positive / probability_total
            negative_probability = negative / probability_total
            neutral_probability = neutral_strength / probability_total
            if score <= -self.NEUTRAL_MARGIN:
                label, confidence = "부정", negative_probability
            elif score >= self.NEUTRAL_MARGIN:
                label, confidence = "긍정", positive_probability
            else:
                label, confidence = "중립", neutral_probability
            results.append(
                SentimentResult(
                    label=label,
                    score=max(-1.0, min(1.0, score)),
                    confidence=float(confidence),
                    positive_probability=float(positive_probability),
                    neutral_probability=float(neutral_probability),
                    negative_probability=float(negative_probability),
                    model_version=self.model_name,
                )
            )
        return results


_analyzer: KlueRobertaSentimentAnalyzer | None = None
_analyzer_lock = Lock()


def get_analyzer() -> KlueRobertaSentimentAnalyzer:
    """현재 설정과 일치하는 감성 분석기 싱글턴을 스레드 안전하게 반환한다."""
    global _analyzer
    settings = get_settings()
    analyzer_key = (
        settings.sentiment_model_name,
        settings.sentiment_allow_model_download,
    )
    current_key = (
        (_analyzer.model_name, _analyzer.allow_download)
        if _analyzer is not None
        else None
    )
    if current_key != analyzer_key:
        with _analyzer_lock:
            current_key = (
                (_analyzer.model_name, _analyzer.allow_download)
                if _analyzer is not None
                else None
            )
            if current_key != analyzer_key:
                _analyzer = KlueRobertaSentimentAnalyzer(*analyzer_key)
    return _analyzer


def analyze_company_articles(company_id: int, batch_limit: int = 100) -> int:
    """기업의 미분석 기사들을 배치로 감성 분석하고 결과와 처리 상태를 저장한다."""
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
            article.positive_probability = result.positive_probability
            article.neutral_probability = result.neutral_probability
            article.negative_probability = result.negative_probability
            article.sentiment_model = result.model_version
            article.analyzed_at = now
        company = db.get(Company, company_id)
        if company is not None:
            company.analysis_status = "warming"
        db.commit()
        return len(results)
