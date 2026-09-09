"""유사 사례 검색 — 팀의 네이버·Tavily 수집기 + LLM 인사이트 추출.

**검색과 추론을 분리한 이유**: 모델에게 검색 권한을 주면(OpenAI web_search 등) 모델이
스스로 출처를 만들어 낼 수 있고, 실제로 존재하지 않는 URL이 섞인다. 여기서는
  1) 검색 API가 반환한 기사만 허용 집합에 넣고
  2) 그 기사들만 읽혀 교훈을 뽑게 한다
모델이 URL을 만들어낼 경로 자체가 없으므로, 사후에 URL 실재를 검증할 필요가 사라진다.
verify.py의 규칙 1·2(사례·원문 인용 대조)가 마지막 방어선으로 남는다.

**검수된 사례를 먼저 쓴다**: CaseRecord에 verification_status="verified"인 사례가 있으면
그것을 우선하고, 모자란 만큼만 검색으로 채운다. 사례 DB가 자라는 만큼 검색 호출이 줄어든다.
"""
from __future__ import annotations

import re

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

[이번 사안] {event_title}
[사안 유형] {type_label}: {type_scope}
[관측된 내용] {query}

[규칙]
- **주어진 기사만 사용하세요.** 기사에 없는 사건을 기억에서 끌어오면 안 됩니다.
- article_index에는 해당 기사의 번호를 그대로 넣으세요. 출처는 그 번호로 연결됩니다.
- title과 모든 서술은 **한국어로** 쓰세요. 기사 제목이 영문이면 한국어로 옮겨 적습니다.
  국내 담당자가 읽는 보고서라 영문 제목이 그대로 실리면 안 됩니다.
- 유사도의 기준은 대응 방식이 아니라 **상황**입니다. [이번 사안]에 적힌 사건과 무엇이
  닮았는지로 판단하세요. 회사가 같은지는 기준이 아닙니다 - 같은 회사라는 이유만으로
  고르지도, 같은 회사라는 이유만으로 빼지도 마세요.
- 완결된 과거 사건만 사례가 되는 것은 아닙니다. **비교해서 배울 것이 있는 상황**이면
  진행 중인 사건도 됩니다. 논문·가이드북·법인 홍보 페이지처럼 사건 보도가 아닌 자료만
  제외하세요.
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


_HANGUL = re.compile(r"[가-힣]")


def _is_korean(*parts: str) -> bool:
    """한글이 한 글자도 없으면 국내 사례가 아니다.

    검색 질의는 한국어인데도 수집기가 영문 매체를 물어 온다(실측: lightreading,
    koreaherald). 국내 법령·관행을 전제로 쓰는 보고서에 영문 사례가 섞이면 담당자가
    적용 범위를 오해한다.

    **제목을 기준으로 본다.** 제목·요약 중 하나만 한글이면 통과시켰더니 국내 매체의
    영문 기사가 빠져나갔다(실측: "Coupang Faces Expanding Class-Action Lawsuits Over
    Data Leak"이 한글 요약을 달고 통과). 화면과 프롬프트에 실리는 것은 제목이다.
    """
    return bool(_HANGUL.search(parts[0] or "")) if parts else False


# 조사·어미. 낱말 끝에서 떼어내 명사 어간만 남긴다. 긴 것부터 봐야 "에서"를 "서"로
# 잘못 떼지 않는다.
_TRAILING = tuple(sorted(
    ("으로", "에서", "부터", "까지", "이란", "라는", "이라", "에게", "한테",
     "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "과", "와", "로", "랑"),
    key=len, reverse=True,
))
# 활용 어미로 끝나면 동사·형용사다. 검색어로 쓰면 사안을 좁히지 못한다.
_INFLECTED = ("하는", "했다", "한다", "된다", "되는", "으면", "면", "한", "된", "될", "할", "고", "며")


def _stem(word: str) -> str:
    for suffix in _TRAILING:
        if len(word) > len(suffix) + 1 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _broad_query(query: str, company: str, type_label: str, risk_type: str = "",
                 keywords_hint=None) -> str:
    """사건 특정 질의로 아무것도 못 찾았을 때 쓸 넓은 질의.

    제목을 통째로 넣으면 그 사건만 정확히 매칭돼, 같은 사건을 걷어낸 뒤 남는 것이 없다.
    **사안의 핵심어만 뽑아야 한다** - "라면 먹고 식중독"에서 중요한 것은 "식중독"이지
    "먹고"가 아니다.

    핵심어를 고르는 순서:
      1) 분류 LLM이 이 사건을 읽고 뽑은 핵심어(keywords_hint). 사건마다 다른 말을 낼 수
         있어 가장 정확하다 - 사전에 없는 "법인 국적" 같은 말도 나온다.
      2) 유형 키워드 사전(keywords.TYPE_KEYWORDS)에 있는 말. 유형을 가리키려고 모아 둔
         어휘라 성격은 잘 드러내지만 사건 고유의 쟁점은 못 담는다.
      3) 둘 다 없으면 조사를 뗀 명사 후보. 활용형(들어가면·편리한)은 검색을 좁히지
         못하므로 제외한다.
    """
    from . import keywords as _kw

    hinted = [w for w in (keywords_hint or []) if w][:3]
    if hinted:
        return " ".join(hinted + [type_label])[:120]

    # 제목 꼬리의 출처 표기를 먼저 걷는다. "(2026.09.02/뉴스데스크/MBC)" 같은 메타데이터가
    # 남으면 방송사·프로그램명이 핵심어 자리를 차지해 검색이 엉뚱한 데로 간다.
    cleaned = _without_company(re.sub(r"[\(\[][^)\]]*[\)\]]", " ", query or ""), company)

    hits = [w for w in _kw.TYPE_KEYWORDS.get(risk_type, ()) if w in cleaned]
    # 사전에 "중독"과 "식중독"이 함께 있으면 둘 다 걸린다. 짧은 쪽은 긴 쪽에 포함되므로
    # 검색어를 하나 더 쓰는 값어치가 없다.
    picked = [w for w in hits if not any(w != o and w in o for o in hits)]
    # 유형 키워드가 하나라도 걸리면 그것만 쓴다. 모자란 자리를 명사 후보로 채우면
    # "식중독 잇따라 증세"처럼 핵심어 옆에 군더더기가 붙어 검색이 흐려진다.
    if not picked:
        candidates = [
            _stem(w) for w in _title_words(cleaned)
            if not w.isdigit() and not w.endswith(_INFLECTED)
        ]
        for w in sorted(set(candidates), key=len, reverse=True):
            if w not in picked:
                picked.append(w)
            if len(picked) >= 3:
                break
    return " ".join(picked[:3] + [type_label])[:120]


def _title_words(text: str) -> set[str]:
    """제목 비교용 어절 집합. 기호를 걷고 두 글자 이상만 남긴다."""
    return {w for w in re.split(r"[^0-9A-Za-z가-힣]+", (text or "")) if len(w) >= 2}


def _is_same_event(article_title: str, event_title: str, threshold: float = 0.5) -> bool:
    """이 기사가 지금 그 사건을 다룬 보도인지.

    **exclude_urls만으로는 부족하다.** URL이 다른 같은 사건 보도(같은 뉴스를 받아쓴
    다른 매체, 같은 매체의 후속 기사)가 "과거 유사 사례"로 올라온다. 사례의 목적은
    남들이 비슷한 상황에서 어떻게 대응했는지 배우는 것이라, 지금 터진 사건 자신은
    사례가 될 수 없다.

    제목 어절의 겹침 비율로 본다. 검색 질의가 사건 제목이라 상위 결과는 대개 그
    사건이고, 그것들을 걷어낸 뒤 남는 것이 실제 비교 대상이다.
    """
    if not event_title:
        return False
    a, b = _title_words(article_title), _title_words(event_title)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def _without_company(text: str, company: str) -> str:
    """질의에서 회사명을 뺀다.

    **유사 사례는 남의 사건이어야 배울 것이 있다.** 회사명이 질의에 있으면 검색이
    자기 회사 기사만 물어오고, 그중 대부분은 지금 터진 그 사건의 다른 보도다
    (exclude_urls는 같은 URL만 막지 같은 사건의 다른 기사는 통과시킨다).
    회사명을 빼면 같은 상황을 겪은 다른 회사 사례가 올라오고, 자사의 과거 사건이
    걸리더라도 그건 URL이 달라 유효한 사례다.

    별칭까지 다 지우지는 못한다. 대표 표기 하나만 지워도 검색 결과가 크게 갈린다.
    """
    cleaned = (text or "").strip()
    name = (company or "").strip()
    if name:
        cleaned = re.sub(re.escape(name), " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _search_articles(company: str, risk_type: str, query: str) -> list[dict]:
    """수집기를 돌려 기사 목록을 만든다. URL 중복은 제거한다.

    **질의는 사건이 앞이고 유형 라벨은 뒤다.** 예전에는 `유형라벨 + 원문`이었는데,
    유형 라벨이 "규제제재·소송"처럼 넓은 말이면 그 단어가 검색을 지배해 사건과 무관한
    기사가 올라온다(실측: 국적 논란 사건에 개인정보 유출 집단소송이 딸려 왔다).
    query 앞부분에는 evidence.build가 넣은 사건 제목이 들어 있다.
    """
    rt = get_type(risk_type)
    since = (datetime.now(timezone.utc) - timedelta(days=365 * LOOKBACK_YEARS)).date()
    search_query = " ".join(
        part for part in (_without_company(query, company), rt.label) if part
    )[:120]

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
            if not _is_korean(item.title, item.summary):
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

    def __init__(self, company_name: str = "", db=None, exclude_urls=None,
                 event_title: str = "", search_keywords=None) -> None:
        self.company_name = company_name
        # 스토리 대표 제목. 사례 선별 프롬프트에서 "이번 사안"이 무엇인지 말해 준다.
        # 유형 라벨만으로는 "규제제재·소송" 같은 넓은 범주라 무엇이 닮았는지 판단이 안 된다.
        self.event_title = event_title
        # 분류 LLM이 이 사건을 읽고 뽑은 검색 핵심어. 비어 있으면(키워드 경로·드라이런)
        # 유형 키워드 사전으로 폴백한다.
        self.search_keywords = [str(w).strip() for w in (search_keywords or []) if str(w).strip()]
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

            # 유형 필터를 SQL에서 먼저 건다. 예전에는 최근 50건을 가져온 뒤 파이썬에서
            # 걸렀는데, 사례가 쌓이면 50건 밖의 해당 유형 사례가 조용히 사라지고 유료
            # 웹검색이 대신 돌게 된다. risk_types에는 상위 탐지 유형과 세부 유형이 섞여
            # 들어올 수 있어 둘 다 후보로 넣는다.
            from sqlalchemy import cast, or_
            from sqlalchemy.dialects.postgresql import JSONB

            # risk_types 컬럼이 json이라 포함 연산자(@>)를 바로 못 쓴다. jsonb로 캐스팅해야
            # 한다 - json 타입에는 그 연산자가 정의돼 있지 않다.
            parent = get_type(risk_type).parent
            types_jsonb = cast(CaseRecord.risk_types, JSONB)
            rows = self.db.scalars(
                select(CaseRecord)
                .where(
                    CaseRecord.verification_status == "verified",
                    or_(
                        types_jsonb.contains([parent]),
                        types_jsonb.contains([risk_type]),
                    ),
                )
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

        rt = get_type(risk_type)

        def _fetch(q: str) -> list[dict]:
            found = _search_articles(self.company_name, risk_type, q)
            if self.exclude_urls:
                found = [a for a in found if _norm_url(a["url"]) not in self.exclude_urls]
            # 같은 사건을 다룬 다른 보도도 사례가 아니다(위 _is_same_event 참고).
            return [a for a in found if not _is_same_event(a["title"], self.event_title)]

        # 1차: 사건을 그대로 겨냥한다. 가장 가까운 사례가 있으면 여기서 걸린다.
        articles = _fetch(query_text[:300])
        if not articles:
            # 2차: 1차 결과가 전부 이 사건 자신이었다는 뜻이다. 질의를 넓혀 다른 회사의
            # 비슷한 사건을 찾는다.
            articles = _fetch(_broad_query(query_text, self.company_name, rt.label, risk_type, self.search_keywords))
        if not articles:
            return cases

        listing = "\n".join(
            f"[{i}] {a['title']}\n    {a['summary']}\n    ({a['source']}, {a['published_at'] or '날짜 미상'})"
            for i, a in enumerate(articles)
        )
        need = top_k - len(cases)
        try:
            parsed, usage = structured_call(
                system=_INSIGHT_PROMPT.format(
                    company=self.company_name or "당사",
                    top_k=need,
                    event_title=self.event_title or "(제목 없음 - 아래 관측된 내용으로 판단)",
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
