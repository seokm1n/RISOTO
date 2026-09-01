"""임베딩 호출. 색인 구축과 검색이 같은 모델을 쓰도록 한 곳에 모아둔다.

모델을 바꾸면 기존 색인이 무효가 된다(차원과 벡터 공간이 달라진다). 그래서 색인에
모델명을 저장하고, 검색 시 불일치하면 경고한다.
"""
from __future__ import annotations

import numpy as np

from .._llm import embed_texts

MODEL = "text-embedding-3-small"


def embed(texts: list[str]) -> tuple[np.ndarray, dict[str, int]]:
    """반환: (벡터 배열, 사용량). 빈 문자열은 API가 거부하므로 미리 걸러야 한다.

    모델을 인자로 받지 않는다. 예전에는 `model` 파라미터가 있었지만 무시하고 있어서,
    호출자가 넘긴 모델과 실제로 쓰인 모델이 다를 수 있었다. 색인과 검색이 반드시 같은
    모델을 써야 하므로 선택지를 두지 않고 설정 한 곳(_llm.embed_texts)에서만 정한다.
    """
    vectors, raw = embed_texts(texts)
    usage = {"tokens": raw.get("tokens", 0), "calls": raw.get("calls", 0)}
    return np.asarray(vectors, dtype=np.float32), usage


def embed_one(text: str) -> np.ndarray:
    vectors, _ = embed([text])
    return vectors[0]
