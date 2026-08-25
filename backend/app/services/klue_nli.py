"""여러 분석 서비스가 공유하는 로컬 KLUE-RoBERTa NLI 추론을 제공한다."""

from __future__ import annotations

from threading import Lock


class KlueNliClassifier:
    """KLUE-RoBERTa NLI 체크포인트로 한국어 전제와 후보 가설을 비교한다."""

    def __init__(self, model_name: str, allow_download: bool = True) -> None:
        """사용할 NLI 모델과 다운로드 허용 여부를 설정한다."""
        self.model_name = model_name
        self.allow_download = allow_download
        self._tokenizer = None
        self._model = None
        self._load_lock = Lock()
        self._inference_lock = Lock()

    def _load(self) -> None:
        """토크나이저와 모델을 스레드 안전하게 지연 로드한다."""
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            kwargs = {
                "local_files_only": not self.allow_download,
                "trust_remote_code": False,
            }
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **kwargs)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_name, **kwargs
            )
            self._model.eval()

    def _entailment_id(self) -> int:
        """모델 레이블 설정에서 entailment 클래스 ID를 찾는다."""
        labels = {
            str(label).casefold(): int(index)
            for label, index in self._model.config.label2id.items()
        }
        if "entailment" not in labels:
            raise ValueError(
                f"NLI 모델에 entailment 레이블이 없습니다: {self.model_name}"
            )
        return labels["entailment"]

    def score_hypotheses(
        self,
        premises: list[str],
        hypotheses: list[list[str]],
        *,
        batch_size: int = 16,
        max_length: int = 256,
    ) -> list[list[float]]:
        """각 전제와 후보 가설을 비교해 그룹별 함의 확률을 계산한다."""
        if len(premises) != len(hypotheses):
            raise ValueError("premises와 hypotheses의 길이가 다릅니다.")
        if any(not group for group in hypotheses):
            raise ValueError("각 입력에는 하나 이상의 가설이 필요합니다.")
        if not premises:
            return []

        self._load()
        import torch

        flat_premises: list[str] = []
        flat_hypotheses: list[str] = []
        group_sizes: list[int] = []
        for premise, group in zip(premises, hypotheses):
            group_sizes.append(len(group))
            flat_premises.extend([premise] * len(group))
            flat_hypotheses.extend(group)

        entailment_id = self._entailment_id()
        entailment_logits: list[float] = []
        with self._inference_lock, torch.no_grad():
            for start in range(0, len(flat_premises), batch_size):
                encoded = self._tokenizer(
                    flat_premises[start : start + batch_size],
                    flat_hypotheses[start : start + batch_size],
                    padding=True,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                )
                # KLUE-RoBERTa has type_vocab_size=1.  Some older checkpoint
                # tokenizers still emit segment id 1 for the hypothesis.
                encoded.pop("token_type_ids", None)
                logits = self._model(**encoded).logits[:, entailment_id]
                entailment_logits.extend(float(value) for value in logits.cpu())

        results: list[list[float]] = []
        cursor = 0
        for size in group_sizes:
            values = torch.tensor(entailment_logits[cursor : cursor + size])
            results.append(torch.softmax(values, dim=0).tolist())
            cursor += size
        return results


_classifiers: dict[tuple[str, bool], KlueNliClassifier] = {}
_classifiers_lock = Lock()


def get_klue_nli_classifier(
    model_name: str, allow_download: bool = True
) -> KlueNliClassifier:
    """동일한 설정의 KLUE NLI 분류기를 캐시에서 가져오거나 생성한다."""
    key = (model_name, allow_download)
    with _classifiers_lock:
        if key not in _classifiers:
            _classifiers[key] = KlueNliClassifier(model_name, allow_download)
        return _classifiers[key]
