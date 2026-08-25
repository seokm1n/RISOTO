"""Lazy inference adapters for manually promoted KLUE text artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from sqlalchemy import select

from app.database import SessionLocal
from app.config import get_settings
from app.models import ModelVersion
from app.risk_taxonomy import RISK_TYPES


_lock = Lock()
_filter_cache: tuple[int, object, object, dict, object] | None = None
_sentiment_cache: tuple[int, object, object, object] | None = None
_risk_type_cache: tuple[int, object, object, object] | None = None
_relevance_sequence_cache: tuple[str, object, object, object] | None = None
_sentiment_sequence_cache: tuple[str, object, object, object] | None = None


def _configured_path(value: str) -> Path | None:
    """Return a usable explicitly configured local Hugging Face artifact path."""
    path = Path(value).expanduser() if value.strip() else None
    return path if path is not None and path.is_dir() else None


def _label_probabilities(model: object, probabilities: list[float]) -> dict[str, float]:
    """Map classifier outputs by config labels instead of assuming class order."""
    raw_labels = getattr(model.config, "id2label", {})
    labels = {
        int(index): str(label).casefold()
        for index, label in raw_labels.items()
    }
    return {
        labels.get(index, f"label_{index}"): float(probability)
        for index, probability in enumerate(probabilities)
    }


def _predict_local_sequence(
    path: Path,
    texts: list[str],
    cache: tuple[str, object, object, object] | None,
) -> tuple[tuple[str, object, object, object], list[dict[str, float]]]:
    """Load a local sequence classifier lazily and return label-keyed probabilities."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    cache_key = str(path.resolve())
    if cache is None or cache[0] != cache_key:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True)
        model.to(device).eval()
        tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        cache = (cache_key, model, tokenizer, device)
    _, model, tokenizer, device = cache
    rows: list[dict[str, float]] = []
    for start in range(0, len(texts), 16):
        batch = tokenizer(
            texts[start:start + 16],
            truncation=True,
            max_length=384,
            padding=True,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = model(
                input_ids=batch["input_ids"].to(device),
                attention_mask=batch["attention_mask"].to(device),
            ).logits
        rows.extend(
            _label_probabilities(model, values)
            for values in torch.softmax(logits, dim=-1).cpu().tolist()
        )
    return cache, rows


def predict_relevance(text: str) -> dict | None:
    """Return relevance probabilities from the shared local normal/filter model."""
    global _relevance_sequence_cache
    path = _configured_path(get_settings().pretrained_relevance_model_path)
    if path is None or not text.strip():
        return None
    try:
        with _lock:
            _relevance_sequence_cache, rows = _predict_local_sequence(
                path, [text], _relevance_sequence_cache
            )
        labels = rows[0]
        if "normal" not in labels or "filter" not in labels:
            return None
        return {
            "version": f"local:{path.name}",
            "relevant": labels["normal"],
            "irrelevant": labels["filter"],
        }
    except Exception:
        return None


def _active(task: str) -> ModelVersion | None:
    with SessionLocal() as db:
        return db.scalar(
            select(ModelVersion)
            .where(ModelVersion.task == task, ModelVersion.status == "production")
            .order_by(ModelVersion.promoted_at.desc().nullslast(), ModelVersion.id.desc())
            .limit(1)
        )


def predict_filter(text: str) -> dict | None:
    """Return relevance/ad probabilities from the promoted multi-head artifact."""
    global _filter_cache
    version = _active("article_filter")
    if version is None or not Path(version.artifact_path).is_dir():
        return None
    try:
        if _filter_cache is None or _filter_cache[0] != version.id:
            with _lock:
                if _filter_cache is None or _filter_cache[0] != version.id:
                    import torch
                    from torch import nn
                    from transformers import AutoModel, AutoTokenizer

                    path = Path(version.artifact_path)
                    metadata = json.loads((path / "filter_metadata.json").read_text(encoding="utf-8"))

                    class FilterModel(nn.Module):
                        def __init__(self):
                            super().__init__()
                            self.encoder = AutoModel.from_pretrained(metadata["base_model"])
                            hidden = self.encoder.config.hidden_size
                            self.dropout = nn.Dropout(0.1)
                            self.relevance_head = nn.Linear(hidden, 3)
                            self.advertisement_head = nn.Linear(hidden, 2)

                        def forward(self, input_ids, attention_mask):
                            output = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
                            pooled = self.dropout(output.last_hidden_state[:, 0])
                            return self.relevance_head(pooled), self.advertisement_head(pooled)

                    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                    model = FilterModel()
                    model.load_state_dict(torch.load(path / "filter_state.pt", map_location=device))
                    model.to(device).eval()
                    tokenizer = AutoTokenizer.from_pretrained(path)
                    _filter_cache = (version.id, model, tokenizer, metadata, device)
        _, model, tokenizer, metadata, device = _filter_cache
        import torch

        encoded = tokenizer(
            text,
            truncation=True,
            max_length=int(metadata.get("max_length", 384)),
            return_tensors="pt",
        )
        with torch.no_grad():
            relevance, advertisement = model(
                encoded["input_ids"].to(device), encoded["attention_mask"].to(device)
            )
        rel = torch.softmax(relevance, dim=-1)[0].cpu().tolist()
        ad = torch.softmax(advertisement, dim=-1)[0].cpu().tolist()
        return {
            "version": version.version,
            "relevance": {"relevant": rel[0], "incidental": rel[1], "irrelevant": rel[2]},
            "advertisement": {"no": ad[0], "yes": ad[1]},
        }
    except Exception:
        return None


def predict_sentiment(texts: list[str]) -> tuple[str, list[dict]] | None:
    """Return three-class soft probabilities from the promoted sentiment artifact."""
    global _sentiment_cache
    global _sentiment_sequence_cache
    configured_path = _configured_path(get_settings().pretrained_sentiment_model_path)
    if configured_path is not None:
        try:
            with _lock:
                _sentiment_sequence_cache, rows = _predict_local_sequence(
                    configured_path, texts, _sentiment_sequence_cache
                )
            required = {"positive", "neutral", "negative"}
            if all(required.issubset(row) for row in rows):
                return f"local:{configured_path.name}", rows
        except Exception:
            pass
    version = _active("sentiment")
    if version is None or not Path(version.artifact_path).is_dir():
        return None
    try:
        if _sentiment_cache is None or _sentiment_cache[0] != version.id:
            with _lock:
                if _sentiment_cache is None or _sentiment_cache[0] != version.id:
                    import torch
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                    model = AutoModelForSequenceClassification.from_pretrained(version.artifact_path)
                    model.to(device).eval()
                    tokenizer = AutoTokenizer.from_pretrained(version.artifact_path)
                    _sentiment_cache = (version.id, model, tokenizer, device)
        _, model, tokenizer, device = _sentiment_cache
        import torch

        output: list[dict] = []
        for start in range(0, len(texts), 16):
            batch = tokenizer(
                texts[start:start + 16],
                truncation=True,
                max_length=384,
                padding=True,
                return_tensors="pt",
            )
            with torch.no_grad():
                logits = model(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                ).logits
            for probabilities in torch.softmax(logits, dim=-1).cpu().tolist():
                output.append(_label_probabilities(model, probabilities))
        return version.version, output
    except Exception:
        return None


def predict_risk_types(texts: list[str]) -> dict | None:
    """Return reviewed-taxonomy probabilities from the promoted multi-label model."""
    global _risk_type_cache
    if not texts:
        return None
    version = _active("risk_type_classifier")
    if version is None or not Path(version.artifact_path).is_dir():
        return None
    try:
        if _risk_type_cache is None or _risk_type_cache[0] != version.id:
            with _lock:
                if _risk_type_cache is None or _risk_type_cache[0] != version.id:
                    import torch
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                    model = AutoModelForSequenceClassification.from_pretrained(version.artifact_path)
                    model.to(device).eval()
                    tokenizer = AutoTokenizer.from_pretrained(version.artifact_path)
                    _risk_type_cache = (version.id, model, tokenizer, device)
        _, model, tokenizer, device = _risk_type_cache
        import torch

        text = " ".join(item.strip() for item in texts if item.strip())[:6000]
        if not text:
            return None
        encoded = tokenizer(
            text,
            truncation=True,
            max_length=384,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = model(
                input_ids=encoded["input_ids"].to(device),
                attention_mask=encoded["attention_mask"].to(device),
            ).logits[0]
        probabilities = torch.sigmoid(logits).cpu().tolist()
        return {
            "version": version.version,
            "scores": {
                risk_type: float(probabilities[index])
                for index, risk_type in enumerate(RISK_TYPES)
            },
        }
    except Exception:
        return None
