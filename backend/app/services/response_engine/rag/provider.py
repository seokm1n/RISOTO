"""RAG 보충 지침 공급자.

`principles.PrincipleProvider` 프로토콜을 만족하되, 원칙 자체는 정적 데이터를 그대로
돌려주고 **보충 지침만 검색으로 덧붙인다.** 검색이 실패하거나 색인이 없으면 정적 원칙만
나가므로, RAG가 죽어도 파이프라인은 그대로 돈다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import principles as static_principles
from ..risk_types import Stakeholder
from .store import Chunk, VectorStore, load_store


@dataclass
class Supplement:
    text: str
    doc: str
    page: int | None
    score: float
    caution: str | None = None
    verification: str | None = None


class RagPrincipleProvider:
    """정적 원칙 + 상황별 보충.

    store를 주지 않으면 기본 경로에서 색인을 읽고, 색인이 없으면 보충 없이 동작한다.
    """

    def __init__(self, store: VectorStore | None = None, top_k: int = 3, min_score: float = 0.30) -> None:
        # 색인 로드 실패가 초안 생성을 죽이면 안 된다. 파일이 깨졌거나 벡터 차원이
        # 어긋나도 보충만 빠지고 정적 원칙으로 계속 가야 한다.
        if store is not None:
            self.store = store
        else:
            try:
                self.store = load_store()
            except Exception as exc:
                print(f"  [rag] 색인 로드 실패 - 보충 없이 진행: {str(exc)[:120]}")
                self.store = None
        self.top_k = top_k
        self.min_score = min_score
        self.last_supplements: list[Supplement] = []

    @property
    def version(self) -> str:
        n = len(self.store) if self.store else 0
        return f"{static_principles.PROMPT_VERSION}+rag:{n}청크"

    @property
    def available(self) -> bool:
        return self.store is not None and len(self.store) > 0

    def principle_for(self, risk_type_code: str) -> str:
        return static_principles.principle_for(risk_type_code)

    def guide_for(self, stakeholder: Stakeholder) -> str:
        return static_principles.guide_for(stakeholder)

    def supplements(self, risk_type_code: str, situation: str) -> list[Supplement]:
        """이번 상황에 맞는 보충 지침. 없으면 빈 목록."""
        self.last_supplements = []
        if not self.available or not situation.strip():
            return []
        try:
            from .embed import embed_one

            q = embed_one(situation[:1500])
        except Exception as exc:
            print(f"  [rag] 임베딩 실패 - 보충 없이 진행: {str(exc)[:120]}")
            return []
        try:
            hits = self.store.search(
                q, risk_type=risk_type_code, top_k=self.top_k, min_score=self.min_score
            )
        except Exception as exc:
            # 질의 벡터와 색인 차원이 어긋나는 경우가 여기로 온다(임베딩 모델 교체 후
            # 재색인 누락 등). 보충을 포기하고 정적 원칙만으로 진행한다.
            print(f"  [rag] 검색 실패 - 보충 없이 진행: {str(exc)[:120]}")
            return []
        self.last_supplements = [
            Supplement(
                text=c.text, doc=c.doc, page=c.page, score=round(s, 4),
                caution=c.caution, verification=c.verification,
            )
            for c, s in hits
        ]
        return self.last_supplements

    def render_supplements(self, risk_type_code: str, situation: str) -> str:
        """프롬프트에 넣을 텍스트. 원칙과 구분되게 성격을 명시한다."""
        items = self.supplements(risk_type_code, situation)
        if not items:
            return ""
        lines = []
        for i, s in enumerate(items, 1):
            head = f"[보충 {i}] {s.doc}"
            if s.page:
                head += f" p.{s.page}"
            if s.verification:
                head += f" ({s.verification})"
            lines.append(head)
            lines.append(s.text.strip())
            if s.caution:
                lines.append(f"  ※ 이 자료의 한계: {s.caution}")
        return "\n".join(lines)
