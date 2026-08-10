from datetime import datetime, timezone
import hashlib
import math

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    Company,
    CompanyArticleMatch,
    CompanyBaseline,
    NewsArticle,
    RiskEvent,
)


FEATURE_NAMES = [
    "hour",
    "weekday",
    "title_length",
    "summary_length",
    "source_bucket",
]


def _source_bucket(source: str) -> int:
    digest = hashlib.blake2s(source.encode("utf-8"), digest_size=2).digest()
    return int.from_bytes(digest, "big") % 32


def article_features(article: NewsArticle) -> list[float]:
    timestamp = article.published_at or article.created_at
    return [
        float(timestamp.hour),
        float(timestamp.weekday()),
        float(len(article.title or "")),
        float(len(article.summary or "")),
        float(_source_bucket(article.source)),
    ]


def negative_risk(article: NewsArticle) -> float:
    return max(0.0, -(article.sentiment_score or 0.0))


def fit_or_score_company(company_id: int) -> dict[str, int | bool]:
    import numpy as np

    settings = get_settings()
    with SessionLocal() as db:
        company = db.get(Company, company_id)
        if company is None:
            return {"baseline_ready": False, "scored": 0, "anomalies": 0}

        rows = db.execute(
            select(NewsArticle, CompanyArticleMatch)
            .join(CompanyArticleMatch, CompanyArticleMatch.article_id == NewsArticle.id)
            .where(
                CompanyArticleMatch.company_id == company_id,
                NewsArticle.analyzed_at.is_not(None),
                NewsArticle.sentiment_score.is_not(None),
            )
            .order_by(NewsArticle.published_at, NewsArticle.id)
        ).all()
        dates = {
            (article.published_at or article.created_at).date()
            for article, _match in rows
        }
        if len(rows) < settings.baseline_min_articles or len(dates) < settings.baseline_min_days:
            company.analysis_status = "warming"
            if company.monitoring_status not in {"paused", "archived"}:
                company.monitoring_status = "warming"
            company.analysis_error = (
                f"기준선 학습 대기: 분석 기사 {len(rows)}/{settings.baseline_min_articles}건, "
                f"수집 일수 {len(dates)}/{settings.baseline_min_days}일"
            )
            db.commit()
            return {"baseline_ready": False, "scored": 0, "anomalies": 0}

        baseline = db.get(CompanyBaseline, company_id)
        if baseline is None:
            import lightgbm as lgb

            features = np.asarray([article_features(article) for article, _ in rows], dtype=float)
            targets = np.asarray([negative_risk(article) for article, _ in rows], dtype=float)
            dataset = lgb.Dataset(features, label=targets, feature_name=FEATURE_NAMES)
            booster = lgb.train(
                {
                    "objective": "regression",
                    "metric": "rmse",
                    "learning_rate": 0.08,
                    "num_leaves": 7,
                    "min_data_in_leaf": 3,
                    "feature_fraction": 0.9,
                    "bagging_fraction": 0.9,
                    "bagging_freq": 1,
                    "verbosity": -1,
                    "seed": 42,
                    "num_threads": 2,
                },
                dataset,
                num_boost_round=40,
            )
            predictions = booster.predict(features)
            residuals = targets - predictions
            residual_mean = float(np.mean(residuals))
            residual_std = max(float(np.std(residuals)), 0.05)
            baseline = CompanyBaseline(
                company_id=company_id,
                model_version=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                model_text=booster.model_to_string(),
                feature_names=FEATURE_NAMES,
                training_article_count=len(rows),
                training_day_count=len(dates),
                residual_mean=residual_mean,
                residual_std=residual_std,
            )
            db.add(baseline)
            db.flush()
        else:
            import lightgbm as lgb

            booster = lgb.Booster(model_str=baseline.model_text)

        pending = [
            (article, match)
            for article, match in rows
            if match.anomaly_scored_at is None
        ]
        scored = 0
        anomalies = 0
        now = datetime.now(timezone.utc)
        for article, match in pending:
            expected = float(booster.predict(np.asarray([article_features(article)]))[0])
            residual = negative_risk(article) - expected - baseline.residual_mean
            anomaly_score = max(0.0, residual / max(baseline.residual_std, 0.05))
            if not math.isfinite(anomaly_score):
                anomaly_score = 0.0
            match.anomaly_score = anomaly_score
            match.is_anomaly = anomaly_score >= 3.0
            match.anomaly_scored_at = now
            scored += 1
            if not match.is_anomaly:
                continue
            anomalies += 1
            is_realtime_article = (
                company.monitoring_started_at is None
                or (article.published_at or article.created_at) >= company.monitoring_started_at
            )
            if is_realtime_article and db.scalar(
                select(RiskEvent.id).where(
                    RiskEvent.company_id == company_id,
                    RiskEvent.article_id == article.id,
                )
            ) is None:
                severity = "critical" if anomaly_score >= 5.0 else "warning"
                db.add(
                    RiskEvent(
                        company_id=company_id,
                        article_id=article.id,
                        anomaly_score=anomaly_score,
                        severity=severity,
                        status="new",
                    )
                )

        company.analysis_status = "ready"
        company.analysis_error = None
        company.baseline_ready_at = company.baseline_ready_at or now
        if company.monitoring_status not in {"paused", "archived"}:
            company.monitoring_status = "active"
        db.commit()
        return {"baseline_ready": True, "scored": scored, "anomalies": anomalies}
