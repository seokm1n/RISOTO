"""대응방안 생성 엔진.

탐지 유형(8개) 이후 단계를 담당한다. 세부 유형 판정 -> 대응 등급 -> 근거 수집 ->
관점별 시나리오 생성 -> 자동 검증까지가 범위이고, 진입점은 service.generate_response_draft다.

기존 services/response_generation.py를 대체할 목적으로 만들었으며, 반환 타입(ResponseDraft)과
저장 형식은 같게 맞췄다. content 구조가 다르므로 schema_version은 3이다.

**진입점을 지연 임포트하는 이유**: service는 DB 세션과 설정을 끌어온다. 여기서 바로
임포트하면 원칙·검증 같은 순수 로직만 쓰려 해도 DB가 필요해져 테스트가 어려워진다.
"""
from typing import TYPE_CHECKING

SCHEMA_VERSION = 3

if TYPE_CHECKING:  # pragma: no cover
    from .service import enqueue_response_draft, generate_response_draft, recover_interrupted_response_drafts


def __getattr__(name: str):
    if name in ("generate_response_draft", "enqueue_response_draft", "recover_interrupted_response_drafts"):
        from . import service

        return getattr(service, name)
    raise AttributeError(name)


__all__ = ["SCHEMA_VERSION", "generate_response_draft", "enqueue_response_draft", "recover_interrupted_response_drafts"]
