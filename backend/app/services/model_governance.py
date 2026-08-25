"""Pure model-promotion eligibility shared by commands and read-only views."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CompanyFeatureWindow, ModelVersion
from app.services.model_operations import build_training_readiness


READINESS_GATED_TASKS = {
    "article_filter",
    "sentiment",
    "risk_type_classifier",
    "risk_detector",
}


@dataclass(frozen=True, slots=True)
class PromotionEligibility:
    """A side-effect-free answer to whether a model can be promoted now."""

    allowed: bool
    blocker: str | None = None
    target_model_state: str | None = None


def evaluate_model_promotion(
    db: Session,
    model: ModelVersion,
    *,
    readiness: dict | None = None,
) -> PromotionEligibility:
    """Apply the authoritative promotion gates without mutating ORM state."""
    if not Path(model.artifact_path).exists():
        return PromotionEligibility(False, "모델 아티팩트 파일을 찾을 수 없습니다.")

    task_readiness = None
    if model.task in READINESS_GATED_TASKS:
        readiness = readiness or build_training_readiness(db)
        task_readiness = next(
            (item for item in readiness["tasks"] if item["task"] == model.task),
            None,
        )
        if task_readiness is None:
            return PromotionEligibility(False, "모델 학습 준비도 정보를 찾을 수 없습니다.")
        minimum_blockers = [
            blocker
            for blocker in task_readiness["blockers"]
            if not blocker.startswith("최근 후보 이후")
        ]
        if minimum_blockers:
            return PromotionEligibility(False, " ".join(minimum_blockers))

    target_model_state = None
    if model.task == "risk_detector" and task_readiness is not None:
        positives = task_readiness["class_counts"]["risk"]
        negatives = task_readiness["class_counts"]["normal"]
        target_model_state = (
            "provisional" if positives < 50 or negatives < 150 else "production"
        )

    if model.task == "isolation_forest":
        windows = int(
            db.scalar(
                select(func.count(CompanyFeatureWindow.id)).where(
                    CompanyFeatureWindow.data_quality != "unavailable"
                )
            )
            or 0
        )
        if windows < 200:
            return PromotionEligibility(
                False,
                f"유효 특징 창이 {windows}개입니다. 최소 200개가 필요합니다.",
            )

    return PromotionEligibility(True, target_model_state=target_model_state)

