"""유사 사례 검색 — 팀의 네이버·Tavily 수집기 + LLM 인사이트 추출.

**검색과 추론을 분리한 이유**: 모델에게 검색 권한을 주면(OpenAI web_search 등) 모델이
스스로 출처를 만들어 낼 수 있고, 실제로 존재하지 않는 URL이 섞인다. 여기서는
  1) 검색 API가 반환한 기사만 허용 집합에 넣고
  2) 그 기사들만 읽혀 교훈을 뽑게 한다
모델이 URL을 만들어낼 경로 자체가 없으므로, 사후에 URL 실재를 검증할 필요가 사라진다.
response_generation._filter_citations가 마지막 방어선으로 남는다.

**검수된 사례를 먼저 쓴다**: CaseRecord에 verification_status="verified"인 사례가 있으면
그것을 우선하고, 모자란 만큼만 검색으로 채운다. 사례 DB가 자라는 만큼 검색 호출이 줄어든다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.config import get_settings

from ._llm import structured_call
from .retrieval import PastCase
from .risk_types import get as get_type

LOOKBACK_YEARS = 10
MAX_ARTICLES = 8

_INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article_index": {"type": "integer"},
                    "title": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["성공", "실패", "혼재", "미상"]},
                    "summary_what": {"type": "string"},
                    "summary_response": {"type": "string"},
                    "summary_result": {"type": "string"},
                    "lesson": {"type": "string"},
                },
                "required": [
                    "article_index", "title", "outcome",
                    "summary_what", "summary_response", "summary_result", "lesson",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cases"],
    "additionalProperties": False,
}

_INSIGHT_PROMPT = """당신은 기업 위기관리 사례 분석가입니다.
아래는 검색으로 수집한 기사 목록입니다. 이 중 **{company}의 이번 사안과 상황이 유사한
실제 사건**을 최대 {top_k}건 골라 정리하세요.

[이번 사안] {type_label}: {type_scope}
[관측된 내용] {query}

[규칙]
- **주어진 기사만 사용하세요.** 기사에 없는 사건을 기억에서 끌어오면 안 됩니다.
- article_index에는 해당 기사의 번호를 그대로 넣으세요. 출처는 그 번호로 연결됩니다.
- 유사도의 기준은 대응 방식이 아니라 **상황**입니다.
- 기사 내용만으로 확인되지 않는 항목은 "확인되지 않음"이라고 쓰세요. 추측해서 채우지 마세요.
- outcome은 대응이 성공적이었는지로 판단하되, 판단 근거가 없으면 "미상"을 고르세요.
- lesson은 이번 사안에 적용할 교훈을 한 문장으로 적으세요.
- 유사한 기사가 없으면 cases를 빈 배열로 두세요. 억지로 채우지 마세요."""


def _collectors():
    """설정에 키가 있는 수집기만 만든다. 둘 다 없으면 검색 자체를 건너뛴다."""
    settings = get_settings()
    out = []
    if settings.naver_api_hub_client_id and settings.naver_api_hub_client_secret:
        from app.services.news_collectors import NaverNewsCollector

        out.append(
            NaverNewsCollector(
                settings.naver_api_hub_client_id,
                settings.naver_api_hub_client_secret,
            )
        )
    if settings.tavily_api_key:
        from app.services.news_collectors import TavilyNewsCollector

        out.append(TavilyNewsCollector(settings.tavily_api_key))
    return out


def _norm_url(url: str) -> str:
    """URL 비교용 정규화. 스킴·대소문자·끝 슬래시 차이로 같은 기사를 놓치지 않게 한다."""
    u = (url or "").strip().lower()
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


def _search_articles(company: str, risk_type: str, query: str) -> list[dict]:
    """수집기를 돌려 기사 목록을 만든다. URL 중복은 제거한다."""
    rt = get_type(risk_type)
    since = (datetime.now(timezone.utc) - timedelta(days=365 * LOOKBACK_YEARS)).date()
    search_query = f"{rt.label} {query}"[:120]

    articles: list[dict] = []
    seen: set[str] = set()
    for collector in _collectors():
        try:
            items = collector.search(search_query, since)
        except Exception:
            # 한 수집기가 죽어도 다른 수집기 결과는 살린다.
            continue
        for item in items:
            url = (item.url or "").strip()
            if not url.lower().startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            articles.append({
                "title": item.title or "",
                "summary": (item.summary or "")[:600],
                "url": url,
                "source": item.source,
                "published_at": item.published_at.isoformat() if item.published_at else None,
            })
            if len(articles) >= MAX_ARTICLES:
                return articles
    return articles


class TeamCaseRetriever:
    """CaseRetriever 프로토콜 구현. 검수 사례를 먼저 쓰고 모자란 만큼 검색으로 채운다."""

    def __init__(self, company_name: str = "", db=None, exclude_urls=None) -> None:
        self.company_name = company_name
        self.db = db
        # 이번 사안 자체의 기사 URL. 검색어가 회사명 + 유형이라 방금 터진 사건의 기사가
        # 그대로 "과거 유사 사례"로 되돌아온다. 자기 자신을 근거로 인용하는 보고서가
        # 되므로 검색 결과 단계에서 걸러낸다.
        self.exclude_urls = {_norm_url(u) for u in (exclude_urls or []) if u}
        self.last_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self.last_error: str | None = None

    def _verified_cases(self, risk_type: str, top_k: int) -> list[PastCase]:
        """CaseRecord에서 검수 완료된 사례를 가져온다. DB 세션이 없으면 건너뛴다."""
        if self.db is None:
            return []
        try:
            from sqlalchemy import select

            from app.models import CaseRecord

            rows = self.db.scalars(
                select(CaseRecord)
                .where(CaseRecord.verification_status == "verified")
                .order_by(CaseRecord.occurred_at.desc().nullslast())
                .limit(50)
            ).all()
        except Exception:
            return []
        out = []
        for row in rows:
            types = row.risk_types or []
            # CaseRecord는 팀의 탐지 유형(8개)으로 태깅돼 있으므로 상위로 비교한다.
            if get_type(risk_type).parent not in types and risk_type not in types:
                continue
            out.append(PastCase(
                case_id=f"DB-{row.id}",
                title=row.title,
                risk_type=risk_type,
                outcome=row.outcome or "미상",
                summary_what=row.summary or "",
                source_urls=[],
                provenance="curated",
            ))
            if len(out) >= top_k:
                break
        return out

    def search(self, risk_type: str, query_text: str, top_k: int = 3) -> list[PastCase]:
        cases = self._verified_cases(risk_type, top_k)
        if len(cases) >= top_k:
            return cases[:top_k]

        articles = _search_articles(self.company_name, risk_type, query_text[:300])
        if self.exclude_urls:
            articles = [a for a in articles if _norm_url(a["url"]) not in self.exclude_urls]
        if not articles:
            return cases

        listing = "\n".join(
            f"[{i}] {a['title']}\n    {a['summary']}\n    ({a['source']}, {a['published_at'] or '날짜 미상'})"
            for i, a in enumerate(articles)
        )
        rt = get_type(risk_type)
        need = top_k - len(cases)
        try:
            parsed, usage = structured_call(
                system=_INSIGHT_PROMPT.format(
                    company=self.company_name or "당사",
                    top_k=need,
                    type_label=rt.label,
                    type_scope=rt.scope,
                    query=query_text[:300] or "(원문 없음)",
                ),
                user=listing,
                schema=_INSIGHT_SCHEMA,
                schema_name="similar_case_insights",
            )
            self.last_usage = usage
        except Exception as exc:
            self.last_error = str(exc)[:200]
            return cases

        for i, item in enumerate(parsed.get("cases", [])[:need]):
            idx = item.get("article_index")
            if not isinstance(idx, int) or not (0 <= idx < len(articles)):
                # 모델이 없는 기사 번호를 지어낸 경우. 출처를 확정할 수 없으므로 버린다.
                continue
            article = articles[idx]
            cases.append(PastCase(
                case_id=f"WEB-{risk_type}-{i + 1}",
                title=item.get("title") or article["title"],
                risk_type=risk_type,
                outcome=item.get("outcome", "미상"),
                summary_what=item.get("summary_what", ""),
                summary_response=item.get("summary_response", ""),
                summary_result=item.get("summary_result", ""),
                lesson=item.get("lesson", ""),
                source_urls=[article["url"]],
                provenance="web_search",
            ))
        return cases[:top_k]
