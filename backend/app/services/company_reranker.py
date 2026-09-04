"""Target-aware cross-encoder relevance scoring for company/article pairs.

The old topical classifier only saw article text and therefore had to memorize the
companies that appeared in its training set.  This adapter always supplies the
target company as the first sequence and the article as the second sequence, so a
single promoted model can also serve newly registered companies.
"""

from __future__ import annotations

from html import unescape
import logging
from pathlib import Path
import re
from threading import Lock
from time import monotonic

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ModelVersion


logger = logging.getLogger(__name__)

RERANKER_TASK = "company_relevance_reranker"
RERANKER_INPUT_SCHEMA = "company-query-article-pair-v1"

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_AFFILIATE_DISCLOSURE_RE = re.compile(
    r"(?:[이·본]\s*)?(?:글|게시물|포스팅|콘텐츠)?(?:은|는|이|가)?\s*"
    r"쿠팡\s*파트너스\s*활동의\s*일환(?:으로)?\s*[,.:;\-]?\s*"
    r"(?:이에\s*따른\s*)?(?:일정액의?\s*)?(?:수수료|커미션)(?:를|을)?\s*"
    r"(?:제공|지급)?받(?:을\s*수\s*있|습니|았습니|는)?다?\s*[.!]?",
    re.IGNORECASE,
)

_lock = Lock()
_model_cache: tuple[int, object, object, object] | None = None
_registry_cache: tuple[float, ModelVersion | None] | None = None


def clean_text(value: str | None) -> str:
    """Remove markup and normalize whitespace without destroying model casing."""
    return _SPACE_RE.sub(" ", unescape(_TAG_RE.sub(" ", value or ""))).strip()


def strip_affiliate_boilerplate(value: str | None) -> tuple[str, bool]:
    """Remove Korean affiliate disclosures and report whether one was present."""
    text = clean_text(value)
    cleaned, count = _AFFILIATE_DISCLOSURE_RE.subn(" ", text)
    return _SPACE_RE.sub(" ", cleaned).strip(), count > 0


def build_company_query(
    company_name: str,
    aliases: list[str] | tuple[str, ...] = (),
    products: list[str] | tuple[str, ...] = (),
) -> str:
    """Build the query side of the pair from data available for every company."""
    name = clean_text(company_name)
    alias_values = list(
        dict.fromkeys(clean_text(item) for item in aliases if clean_text(item))
    )[:8]
    product_values = list(dict.fromkeys(clean_text(item) for item in products if clean_text(item)))[:12]
    parts = [f"대상 기업: {name}"]
    if alias_values:
        parts.append(f"별칭: {', '.join(alias_values)}")
    if product_values:
        parts.append(f"제품·서비스: {', '.join(product_values)}")
    parts.append(
        "이 기사가 대상 기업의 사업, 경영, 제품·서비스, 물류·사업장, "
        "임직원, 고객, 규제, 사고 또는 평판을 실질적으로 다루는가?"
    )
    return " ".join(parts)


def build_article_passage(title: str | None, summary: str | None) -> str:
    """Build and clean the passage side while excluding affiliate boilerplate."""
    title_text = clean_text(title)
    summary_text, _ = strip_affiliate_boilerplate(summary)
    return " ".join(
        part for part in (
            f"제목: {title_text}" if title_text else "",
            f"본문: {summary_text}" if summary_text else "",
        )
        if part
    )


def _production_version() -> ModelVersion | None:
    """Resolve the promoted reranker with a short TTL so promotion needs no restart."""
    global _registry_cache
    now = monotonic()
    if _registry_cache is not None and now - _registry_cache[0] < 60:
        return _registry_cache[1]
    try:
        with SessionLocal() as db:
            version = db.scalar(
                select(ModelVersion)
                .where(
                    ModelVersion.task == RERANKER_TASK,
                    ModelVersion.status == "production",
                )
                .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.id.desc())
                .limit(1)
            )
    except Exception as exc:  # The deterministic filter remains available during DB/model outages.
        logger.warning("Unable to resolve the company relevance reranker: %s", exc)
        version = None
    _registry_cache = (now, version)
    return version


def _label_probabilities(model: object, logits: object) -> list[float]:
    """Convert either the native one-logit BGE head or a two-label head to relevance."""
    import torch

    if logits.shape[-1] == 1:
        return torch.sigmoid(logits.view(-1)).detach().cpu().tolist()
    probabilities = torch.softmax(logits, dim=-1).detach().cpu()
    raw_labels = getattr(model.config, "id2label", {}) or {}
    relevant_index = next(
        (
            int(index)
            for index, label in raw_labels.items()
            if str(label).casefold() in {"relevant", "label_1"}
        ),
        1 if probabilities.shape[-1] > 1 else 0,
    )
    return probabilities[:, relevant_index].tolist()


def predict_company_relevance_batch(
    items: list[tuple[str, list[str] | tuple[str, ...], list[str] | tuple[str, ...], str, str]],
) -> list[dict | None]:
    """Score ``(company, aliases, products, title, summary)`` pairs in batches."""
    global _model_cache
    if not items or not get_settings().company_reranker_enabled:
        return [None] * len(items)
    version = _production_version()
    if version is None:
        return [None] * len(items)
    path = Path(version.artifact_path).expanduser()
    if not path.is_dir():
        logger.warning("Company relevance reranker artifact is unavailable: %s", path)
        return [None] * len(items)

    pairs = [
        (
            build_company_query(company, aliases, products),
            build_article_passage(title, summary),
        )
        for company, aliases, products, title, summary in items
    ]
    valid_indices = [index for index, (query, passage) in enumerate(pairs) if query and passage]
    if not valid_indices:
        return [None] * len(items)
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        with _lock:
            if _model_cache is None or _model_cache[0] != version.id:
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                model = AutoModelForSequenceClassification.from_pretrained(
                    path,
                    local_files_only=True,
                    trust_remote_code=True,
                )
                model.to(device).eval()
                tokenizer = AutoTokenizer.from_pretrained(
                    path,
                    local_files_only=True,
                    trust_remote_code=True,
                )
                _model_cache = (version.id, model, tokenizer, device)
        _, model, tokenizer, device = _model_cache
        scores: list[float] = []
        batch_size = max(1, get_settings().company_reranker_batch_size)
        for start in range(0, len(valid_indices), batch_size):
            indices = valid_indices[start:start + batch_size]
            encoded = tokenizer(
                [pairs[index][0] for index in indices],
                [pairs[index][1] for index in indices],
                padding=True,
                truncation=True,
                max_length=get_settings().company_reranker_max_length,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.no_grad():
                logits = model(**encoded).logits
            scores.extend(float(value) for value in _label_probabilities(model, logits))

        output: list[dict | None] = [None] * len(items)
        thresholds = version.thresholds or {}
        for index, score in zip(valid_indices, scores):
            output[index] = {
                "version": version.version,
                "relevant": score,
                "irrelevant": 1.0 - score,
                "accept_threshold": float(thresholds.get("accept", 0.70)),
                "reject_threshold": float(thresholds.get("reject", 0.30)),
                "input_schema": RERANKER_INPUT_SCHEMA,
            }
        return output
    except Exception as exc:
        logger.exception("Company relevance reranker inference failed: %s", exc)
        return [None] * len(items)


def predict_company_relevance(
    company_name: str,
    aliases: list[str] | tuple[str, ...],
    products: list[str] | tuple[str, ...],
    title: str,
    summary: str,
) -> dict | None:
    """Score one company/article pair."""
    return predict_company_relevance_batch(
        [(company_name, aliases, products, title, summary)]
    )[0]
