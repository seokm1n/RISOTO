"""Lazy inference adapters for manually promoted KLUE text artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ModelVersion
from app.risk_taxonomy import RISK_TYPES


_lock = Lock()
_filter_cache: tuple[int, object, object, dict, object] | None = None
_sentiment_cache: tuple[int, object, object, object] | None = None
_risk_type_cache: tuple[int, object, object, object] | None = None


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
                output.append(
                    {
                        "positive": probabilities[0],
                        "neutral": probabilities[1],
                        "negative": probabilities[2],
                    }
                )
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
