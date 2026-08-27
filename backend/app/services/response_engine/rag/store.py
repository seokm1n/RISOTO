"""청크 저장소 - 임베딩 벡터와 메타데이터를 로컬 파일로 들고 있는다.

**왜 로컬 파일인가**: 자료가 계속 추가될 예정이라 재색인이 잦고, 지금 단계에서는 DB를
띄우지 않고도 돌아가야 한다. 팀 저장소로 이식할 때는 `VectorStore`를 pgvector 구현으로
갈아끼우면 되고, 검색 인터페이스(`search`)는 그대로다.

벡터는 .npy, 메타데이터는 .jsonl로 나눠 저장한다. 메타데이터만 열어보고 무엇이 색인됐는지
확인할 수 있어야 하기 때문이다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

DEFAULT_DIR = Path(__file__).with_name("index")


@dataclass
class Chunk:
    chunk_id: str
    text: str
    doc: str                       # 출처 문서명
    page: int | None = None
    risk_types: list[str] = field(default_factory=list)   # 이 청크가 속한 세부 유형
    caution: str | None = None     # 원문의 사용 제약. 프롬프트까지 따라가야 한다.
    verification: str | None = None  # "사례 미검증 · 참고용" 같은 신뢰 수준 표시


class VectorStore:
    """코사인 유사도 검색. 유형 필터를 먼저 걸고 그 안에서 순위를 매긴다."""

    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, model: str) -> None:
        if len(chunks) != vectors.shape[0]:
            raise ValueError(f"청크 {len(chunks)}개와 벡터 {vectors.shape[0]}개가 맞지 않습니다.")
        self.chunks = chunks
        # 미리 정규화해 두면 검색이 내적 한 번으로 끝난다.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        self.vectors = vectors / np.clip(norms, 1e-9, None)
        self.model = model

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self, query_vector: np.ndarray, risk_type: str | None = None,
        top_k: int = 3, min_score: float = 0.30, relative: float = 0.92,
    ) -> list[tuple[Chunk, float]]:
        """risk_type을 주면 그 유형의 청크 안에서만 찾는다.

        **절대 임계값만으로는 안 되는 이유**: 점수 분포가 자료마다 크게 다르다. 국내 자료는
        한국어 질의와 0.6대가 나오지만 영어 원문·번역본은 같은 적합도에서도 0.37 수준에
        머문다(실측: R04 0.62 / R13 0.37). 절대값 하나로 자르면 영어 자료 유형은 보충이
        통째로 사라진다.

        그래서 두 겹으로 거른다.
          - min_score: 완전한 잡음을 걷어내는 바닥
          - relative:  1위 점수의 이 비율에 못 미치면 버린다. 유형별 분포에 자동으로 맞춰지고,
                       1위만 적합하고 2·3위가 어긋난 경우(실측: R03) 그 둘을 떨어뜨린다.

        관련 없는 보충 지침은 없느니만 못하다 - 원칙과 섞여 프롬프트에 들어가면 오히려 해가 된다.
        """
        if not len(self.chunks):
            return []
        mask = np.ones(len(self.chunks), dtype=bool)
        if risk_type:
            mask = np.array([risk_type in c.risk_types for c in self.chunks])
            if not mask.any():
                return []
        q = query_vector / max(float(np.linalg.norm(query_vector)), 1e-9)
        scores = self.vectors @ q
        scores[~mask] = -1.0
        order = np.argsort(-scores)[:top_k]
        picked = [(self.chunks[i], float(scores[i])) for i in order if scores[i] >= min_score]
        if not picked:
            return []
        cutoff = picked[0][1] * relative
        return [(c, s) for c, s in picked if s >= cutoff]

    def save(self, directory: Path | str = DEFAULT_DIR) -> None:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "vectors.npy", self.vectors)
        with open(d / "chunks.jsonl", "w", encoding="utf-8") as fp:
            for c in self.chunks:
                fp.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        (d / "meta.json").write_text(
            json.dumps({"model": self.model, "count": len(self.chunks)}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


def load_store(directory: Path | str = DEFAULT_DIR) -> VectorStore | None:
    """색인이 없으면 None을 돌려준다 - RAG는 있으면 좋은 것이라 없다고 죽으면 안 된다."""
    d = Path(directory)
    if not (d / "vectors.npy").exists() or not (d / "chunks.jsonl").exists():
        return None
    vectors = np.load(d / "vectors.npy")
    chunks = []
    with open(d / "chunks.jsonl", encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                chunks.append(Chunk(**json.loads(line)))
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    return VectorStore(chunks, vectors, meta.get("model", "unknown"))
