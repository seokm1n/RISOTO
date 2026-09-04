"""모니터링 대상 기업의 역할(메인 / 동종) 구분.

**역할은 기업의 속성이 아니라 (사용자, 기업) 관계의 속성이다.** 같은 쿠팡이라도 쿠팡 재직자에겐
메인 기업이고, 11번가 재직자에겐 동종 기업이다. 그래서 `storage/models.py`의 `Company` 테이블에
role 컬럼을 두면 안 되고, 사용자별 등록 정보(user_id × company_id)에 있어야 한다.

지금은 그 테이블이 없으므로 **알림 페이로드에 실어 보내는 것**으로 처리한다. 나중에 사용자
등록 테이블이 생기면 `resolve_role()`만 DB 조회로 바꾸면 되고, 파이프라인은 손대지 않는다.

역할에 따라 위기 판정 이후의 경로가 갈린다:
  MAIN → 유형 분류 → 대응 등급 → 근거 수집 → 대응방안 생성 (기존 경로)
  PEER → 영향 분석 (우리 기업에 영향이 오는가) → 영향 있을 때만 추천 생성
"""
from __future__ import annotations

from enum import Enum


class CompanyRole(str, Enum):
    MAIN = "main"   # 사용자의 재직 기업
    PEER = "peer"   # 사용자가 함께 등록한 동종 기업


_ALIASES: dict[str, CompanyRole] = {
    "main": CompanyRole.MAIN,
    "self": CompanyRole.MAIN,
    "own": CompanyRole.MAIN,
    "메인": CompanyRole.MAIN,
    "자사": CompanyRole.MAIN,
    "peer": CompanyRole.PEER,
    "competitor": CompanyRole.PEER,
    "동종": CompanyRole.PEER,
    "비교 기업": CompanyRole.PEER,
    "경쟁사": CompanyRole.PEER,
}


def resolve_role(raw: str | None, default: CompanyRole = CompanyRole.MAIN) -> CompanyRole:
    """페이로드의 역할 값을 enum으로 정규화한다.

    기본값을 MAIN으로 두는 이유: 역할 정보가 빠졌을 때 동종 기업으로 처리하면 실제 자사
    위기인데도 '영향 분석'만 하고 대응방안을 안 만드는 사고가 난다. 반대로 동종사 건을
    메인으로 오인하면 불필요한 보고서가 하나 더 생길 뿐이라, 두 실수의 비용이 비대칭이다.
    """
    if raw is None:
        return default
    return _ALIASES.get(str(raw).strip().lower(), default)
