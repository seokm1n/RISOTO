"""수집 기사를 중복·광고·관련성 기준으로 감사 가능하게 판정한다.

정확한 중복과 명백한 광고는 결정적 규칙으로 처리한다. 관련성과 광고 문맥은
KLUE-RoBERTa NLI로 보조 판정하고, 근접 중복은 별도 임베딩 모델로 비교한다.
모델을 불러오지 못해도 보수적인 규칙 판정을 계속하며 실패 근거를 함께 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from hashlib import sha256
from html import unescape
import re
from threading import Lock
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.klue_nli import KlueNliClassifier, get_klue_nli_classifier
from app.services.fine_tuned_text import predict_filter, predict_relevance, predict_topical_relevance
from app.services.company_reranker import (
    predict_company_relevance,
    strip_affiliate_boilerplate,
)


_PRECOMPUTED_UNSET = object()


# 비교 전에 외부 기사 본문을 정리하고 토큰화하는 전처리 규칙이다.
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
SPACE_RE = re.compile(r"\s+")
# 동일 문서를 다른 URL로 오인하지 않도록 제거하는 대표 추적 파라미터다.
TRACKING_PARAMETERS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "source",
}
# URL이 달라도 같은 제목이 짧은 시간 안에 반복되면 제공자가 같은 기사를
# 사진 번호 등으로 여러 번 내보낸 경우로 본다. 스토리 확산을 보존하기 위해
# 비슷한 제목까지 넓히지는 않고 정규화 후 완전히 같은 제목에만 적용한다.
TITLE_DUPLICATE_WINDOW_MINUTES = 15
TITLE_DUPLICATE_EXCLUDED_SOURCES = {"youtube_comment"}
# 각 항목은 광고 표현 정규식, 누적 가중치, 감사 로그용 근거 코드로 구성된다.
AD_PATTERNS: tuple[tuple[str, float, str], ...] = (
    (r"(?:유료\s*)?광고(?:입니다|포함|성)?", 0.34, "advertising_disclosure"),
    (r"협찬|체험단|공동\s*구매|공구\s*(?:오픈|마감)", 0.55, "promotion_disclosure"),
    (r"sponsored|paid\s+partnership", 0.55, "sponsored_english"),
    (r"affiliate", 0.45, "affiliate_english"),
    (r"(?:지금|바로)\s*(?:구매|신청)|구매하기|주문하기|buy\s+now|order\s+now", 0.42, "purchase_cta"),
    (r"무료\s*(?:상담|체험|견적)|상담\s*(?:문의|신청)|free\s+consultation", 0.42, "consultation_cta"),
    (r"할인\s*(?:코드|쿠폰|이벤트)|특가|최저가|선착순", 0.36, "discount_cta"),
    (r"카카오톡\s*(?:채널|문의)|오픈\s*채팅|DM\s*(?:문의|주세요)", 0.44, "direct_contact"),
    (r"(?:문의|예약)\s*[:：]?\s*0\d{1,2}[- )]?\d{3,4}[- ]?\d{4}", 0.50, "phone_contact"),
)
# 쇼핑몰·단축 URL 호스트는 본문 신호와 별도로 광고 가능성을 높인다.
AD_HOST_FRAGMENTS = (
    "shopping.", "smartstore.", "storefarm.", "coupang.", "11st.",
    "gmarket.", "auction.", "linktr.ee", "bit.ly",
)
# 기사 본문이 아니라 검색·카테고리 목록 자체를 가리키는 대표 제목이다.
NON_ARTICLE_PAGE_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:^|[\s)>｜|·-])(?:뉴스|기사|이슈|보도자료)\s*"
        r"(?:카테고리|목록|리스트)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s)>｜|·-])(?:뉴스|기사|보도자료)?\s*검색\s*결과\s*$",
        re.IGNORECASE,
    ),
    # 종목토론방·종토방 차트 페이지: 실제 보도가 아니라 투자자 커뮤니티 템플릿이다.
    re.compile(r"종토방|종목토론방|종토넷", re.IGNORECASE),
    # 핫딜 게시판 특유의 "쇼핑몰명::상품명 (가격) > 국내핫딜" 말미 카테고리 표기다.
    # 뉴스 제목에 "핫딜"이 주제로 등장하는 경우와 구분하기 위해 말미 ">카테고리" 형태만 잡는다.
    re.compile(r">\s*(?:국내|해외|온라인)?\s*핫딜\s*$", re.IGNORECASE),
    # 온라인 커뮤니티 베스트글·인기글·댓글 모음 페이지 제목이다.
    re.compile(r"베플|베스트\s*(?:댓글|리플|게시글)|인기\s*(?:글|댓글)\s*모음", re.IGNORECASE),
)
# 뉴스 기사를 게재하지 않는 커뮤니티·핫딜 게시판·리뷰·정보 디렉터리 호스트다.
# 회사명이 제목에 그대로 등장해도 기사 자체가 아니므로 규칙 관련성 판정보다 앞서 걸러낸다.
NON_ARTICLE_PAGE_HOST_FRAGMENTS = (
    "dcinside.com", "dealbada.com", "diningcode.com", "mangoplate.com",
    "nate.com", "jongto.net", "adrything.com",
    "fmkorea.com", "clien.net", "ppomppu.co.kr", "ruliweb.com",
    "theqoo.net", "instiz.net", "todayhumor.co.kr", "82cook.com",
    "ilbe.com", "ygosu.com", "mlbpark.donga.com", "bobaedream.co.kr",
    "humoruniv.com",
)
# topical_relevance 모델이 실제 라벨링 데이터로 학습한 기업만 나열한다. 이 밖의(사용자가 새로
# 등록한) 기업에는 모델이 어떻게 반응할지 검증되지 않아 적용하지 않는다.
TOPICAL_RELEVANCE_TRAINED_COMPANIES = {
    "올리브영", "무신사", "에이블리", "마켓컬리", "SSG",
    "11번가", "카카오", "네이버", "쿠팡",
}


@dataclass(slots=True)
class FilterConfig:
    """필터 버전, 판정 임계값 및 선택적 AI 모델 설정을 한데 묶는다."""

    version: str = "hybrid-company-reranker-v5"
    duplicate_threshold: float = 0.92
    advertising_reject_threshold: float = 0.85
    advertising_review_threshold: float = 0.55
    relevance_accept_threshold: float = 0.70
    relevance_reject_threshold: float = 0.30
    ai_enabled: bool = True
    classifier_model_name: str = "Huffon/klue-roberta-base-nli"
    semantic_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    allow_model_download: bool = True
    max_semantic_duplicate_candidates: int = 5
    # topical_relevance 모델이 이 확률 이상으로 "무관"을 확신할 때만 관련성 점수를 그만큼 낮춘다.
    # 정밀도가 낮은 모델이라(관련 있음 예측의 오탐이 잦음) 점수를 올리는 데는 절대 쓰지 않는다.
    topical_irrelevant_override_threshold: float = 0.90


@dataclass(slots=True)
class FilterDecision:
    """기사 한 건의 최종 판정과 점수, 중복 대상 및 감사 근거를 전달한다."""

    decision: str
    reason: str
    relevance_score: float
    advertising_score: float
    confidence: float
    classifier_kind: str
    filter_version: str
    duplicate_score: float = 0.0
    duplicate_of_raw_id: int | None = None
    details: dict = field(default_factory=dict)

def normalize_text(value: str | None) -> str:
    """HTML과 유니코드·공백·대소문자를 정리해 비교 가능한 텍스트를 만든다."""
    text = unicodedata.normalize("NFKC", unescape(TAG_RE.sub(" ", value or "")))
    return SPACE_RE.sub(" ", text).strip().casefold()


def normalized_content(value: object) -> str:
    """기사 제목과 요약을 정규화해 하나의 비교용 본문으로 합친다."""
    return " ".join(
        part for part in (
            normalize_text(getattr(value, "title", "")),
            normalize_text(getattr(value, "summary", "")),
        ) if part
    )


def content_hash(title: str | None, summary: str | None) -> str:
    """정규화한 제목과 요약으로 중복 판별용 SHA-256 해시를 생성한다."""
    payload = f"{normalize_text(title)}\n{normalize_text(summary)}".encode("utf-8")
    return sha256(payload).hexdigest()


def normalize_url(value: str) -> str:
    """추적 매개변수와 프래그먼트를 제거하고 URL 구성 요소를 표준화한다."""
    parts = urlsplit((value or "").strip())
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_PARAMETERS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), "")
    )


def _tokens(value: str) -> set[str]:
    """정규화된 텍스트에서 유사도 비교에 사용할 고유 토큰 집합을 추출한다."""
    return set(TOKEN_RE.findall(normalize_text(value)))


def _jaccard(left: str, right: str) -> float:
    """두 텍스트 토큰 집합의 자카드 유사도를 계산한다."""
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _text_similarity(left: object, right: object) -> float:
    """제목과 전체 내용의 유사도를 조합해 두 기사의 중복 가능성을 계산한다."""
    left_title = normalize_text(getattr(left, "title", ""))
    right_title = normalize_text(getattr(right, "title", ""))
    if not left_title or not right_title:
        return 0.0
    title_ratio = SequenceMatcher(None, left_title, right_title).ratio()
    title_tokens = _jaccard(left_title, right_title)
    body_tokens = _jaccard(normalized_content(left), normalized_content(right))
    return min(1.0, 0.45 * title_ratio + 0.35 * title_tokens + 0.20 * body_tokens)


def _contains_term(text: str, term: str) -> bool:
    """한글과 영문 경계를 고려해 텍스트에 검색어가 실제로 포함되는지 확인한다."""
    term = normalize_text(term)
    if len(term) < 2:
        return False
    if re.fullmatch(r"[a-z0-9 .&+-]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


class LocalSemanticScorer:
    """Lazy, process-local transformer embedder with a recorded fallback."""

    def __init__(self, model_name: str, allow_download: bool) -> None:
        """의미 유사도 모델 설정과 지연 로딩 상태를 초기화한다."""
        self.model_name = model_name
        self.allow_download = allow_download
        self._tokenizer = None
        self._model = None
        self._load_attempted = False
        self._lock = Lock()
        self.last_error: str | None = None

    def _load(self) -> bool:
        """로컬 의미 임베딩 모델을 한 번만 로드하고 성공 여부를 반환한다."""
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True
            try:
                from transformers import AutoModel, AutoTokenizer

                kwargs = {
                    "local_files_only": not self.allow_download,
                    "trust_remote_code": False,
                }
                self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **kwargs)
                self._model = AutoModel.from_pretrained(self.model_name, **kwargs)
                self._model.eval()
            except Exception as exc:  # fallback is intentionally non-fatal
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                self._tokenizer = None
                self._model = None
                return False
        return True

    def similarities(self, reference: str, candidates: list[str]) -> list[float] | None:
        """기준 텍스트와 각 후보 사이의 코사인 의미 유사도를 계산한다."""
        if not candidates or not self._load():
            return None
        try:
            import torch
            import torch.nn.functional as functional

            texts = [reference, *candidates]
            encoded = self._tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=256,
                return_tensors="pt",
            )
            with self._lock, torch.no_grad():
                hidden = self._model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).expand(hidden.size()).float()
            vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            vectors = functional.normalize(vectors, p=2, dim=1)
            scores = torch.matmul(vectors[1:], vectors[0]).cpu().tolist()
            return [max(-1.0, min(1.0, float(score))) for score in scores]
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:500]
            return None

    def embeddings(self, texts: list[str], *, batch_size: int = 256):
        """여러 문장의 정규화 임베딩을 한 번씩만 계산해 재군집에 사용한다.

        반환형은 호출부가 선택적으로 NumPy를 사용하도록 명시하지 않는다. 모델을
        불러올 수 없으면 기존 필터와 마찬가지로 ``None``을 반환해 결정적 텍스트
        유사도 폴백을 허용한다.
        """
        if not texts:
            return None
        if batch_size < 1:
            raise ValueError("batch_size는 1 이상이어야 합니다.")
        if not self._load():
            return None
        try:
            import numpy as np
            import torch
            import torch.nn.functional as functional

            chunks = []
            for start in range(0, len(texts), batch_size):
                encoded = self._tokenizer(
                    texts[start:start + batch_size],
                    padding=True,
                    truncation=True,
                    max_length=256,
                    return_tensors="pt",
                )
                with self._lock, torch.no_grad():
                    hidden = self._model(**encoded).last_hidden_state
                mask = encoded["attention_mask"].unsqueeze(-1).expand(hidden.size()).float()
                vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                chunks.append(functional.normalize(vectors, p=2, dim=1).cpu().numpy())
            return np.concatenate(chunks, axis=0)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:500]
            return None


_scorers: dict[tuple[str, bool], LocalSemanticScorer] = {}
_scorers_lock = Lock()


def get_semantic_scorer(config: FilterConfig) -> LocalSemanticScorer | None:
    """AI 필터가 활성화된 경우 설정별 의미 유사도 분석기를 캐시해 반환한다."""
    if not config.ai_enabled:
        return None
    key = (config.semantic_model_name, config.allow_model_download)
    with _scorers_lock:
        if key not in _scorers:
            _scorers[key] = LocalSemanticScorer(*key)
        return _scorers[key]


def _keyword_groups(company: object, keywords: list[object]) -> dict[str, list[str]]:
    """기업 기본 정보와 키워드를 식별·제품·위험 그룹으로 정리한다."""
    groups: dict[str, list[str]] = {"identity": [], "product": [], "risk": []}
    for value in (
        getattr(company, "name", None),
        getattr(company, "normalized_name", None),
        getattr(company, "ticker", None),
    ):
        if value:
            groups["identity"].append(str(value))
    for keyword in keywords:
        kind, value = getattr(keyword, "keyword_type", ""), getattr(keyword, "value", "")
        if not value:
            continue
        if kind == "alias":
            groups["identity"].append(str(value))
        elif kind == "product":
            groups["product"].append(str(value))
        elif kind == "risk":
            groups["risk"].append(str(value))
    return {key: list(dict.fromkeys(values)) for key, values in groups.items()}


def _rule_relevance(item: object, groups: dict[str, list[str]]) -> tuple[float, list[str]]:
    """제목과 요약의 키워드 출현을 규칙 점수와 근거 코드로 환산한다."""
    title = normalize_text(getattr(item, "title", ""))
    summary = normalize_text(getattr(item, "summary", ""))
    evidence: list[str] = []
    score = 0.0
    for term in groups["identity"]:
        if _contains_term(title, term):
            score = max(score, 0.96)
            evidence.append(f"identity_in_title:{term}")
        elif _contains_term(summary, term):
            score = max(score, 0.84)
            evidence.append(f"identity_in_summary:{term}")
    for term in groups["product"]:
        if _contains_term(title, term):
            score = max(score, 0.82)
            evidence.append(f"product_in_title:{term}")
        elif _contains_term(summary, term):
            score = max(score, 0.68)
            evidence.append(f"product_in_summary:{term}")
    if any(_contains_term(title + " " + summary, term) for term in groups["risk"]):
        score = min(1.0, score + 0.05)
        evidence.append("risk_keyword_support")
    return score, evidence


def _advertising_rules(item: object) -> tuple[float, list[str]]:
    """광고성 표현 패턴을 적용해 광고 가능성과 근거를 계산한다."""
    text = normalized_content(item)
    evidence: list[str] = []
    score = 0.0
    for pattern, weight, code in AD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            score += weight
            evidence.append(code)
    host = urlsplit(getattr(item, "url", "") or "").netloc.casefold()
    if any(fragment in host for fragment in AD_HOST_FRAGMENTS):
        score += 0.55
        evidence.append("commerce_or_redirect_host")
    return min(1.0, score), evidence


def _affiliate_only_target_mention(item: object, identity_terms: list[str]) -> bool:
    """Detect search hits where the target only occurs in affiliate boilerplate."""
    combined = " ".join(
        part for part in (
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "summary", "") or ""),
        )
        if part
    )
    cleaned, disclosure_found = strip_affiliate_boilerplate(combined)
    if not disclosure_found:
        return False
    original = normalize_text(combined)
    without_disclosure = normalize_text(cleaned)
    mentioned_before = any(_contains_term(original, term) for term in identity_terms)
    mentioned_after = any(_contains_term(without_disclosure, term) for term in identity_terms)
    return mentioned_before and not mentioned_after


def _calibrated_reranker_score(prediction: dict) -> float:
    """Map model-specific review thresholds onto the filter's 0.30/0.70 bands."""
    score = max(0.0, min(1.0, float(prediction["relevant"])))
    reject = max(0.0, min(1.0, float(prediction.get("reject_threshold", 0.30))))
    accept = max(reject + 1e-6, min(1.0, float(prediction.get("accept_threshold", 0.70))))
    if score <= reject:
        return 0.30 * score / reject if reject > 0 else 0.0
    if score < accept:
        return 0.30 + 0.40 * (score - reject) / (accept - reject)
    return 0.70 + 0.30 * (score - accept) / max(1e-6, 1.0 - accept)


def _non_article_page_evidence(item: object) -> list[str]:
    """검색 결과나 카테고리 목록, 커뮤니티·핫딜 게시판처럼 기사 한 건이 아닌 페이지를 식별한다."""
    title = normalize_text(getattr(item, "title", ""))
    if any(pattern.search(title) for pattern in NON_ARTICLE_PAGE_TITLE_PATTERNS):
        return ["generic_listing_title"]
    host = urlsplit(getattr(item, "url", "") or "").netloc.casefold()
    if any(fragment in host for fragment in NON_ARTICLE_PAGE_HOST_FRAGMENTS):
        return ["non_article_host"]
    return []


def _published_within_minutes(
    left: object,
    right: object,
    max_minutes: int = TITLE_DUPLICATE_WINDOW_MINUTES,
) -> bool:
    """발행 시각, 없으면 수집 시각이 지정한 분 이내인지 확인한다."""
    for field in ("published_at", "collected_at"):
        left_date = getattr(left, field, None)
        right_date = getattr(right, field, None)
        if not isinstance(left_date, datetime) or not isinstance(right_date, datetime):
            continue
        try:
            seconds = abs((left_date - right_date).total_seconds())
        except TypeError:
            # 한쪽만 timezone 정보를 가진 비정상 입력도 비교 자체가 실패하지 않게 한다.
            seconds = abs(
                (
                    left_date.replace(tzinfo=None)
                    - right_date.replace(tzinfo=None)
                ).total_seconds()
            )
        return seconds <= max(0, max_minutes) * 60
    return False


def _supports_title_duplicate_check(item: object) -> bool:
    """제목이 개별 수집 항목의 정체성을 나타내는 소스인지 확인한다.

    YouTube 수집 항목은 제목이 영상명이고 실제 개별 항목은 댓글이다. 같은 영상에
    달린 서로 다른 댓글을 기사 제목 중복 규칙으로 합치지 않는다.
    """
    source = str(getattr(item, "source", "") or "").strip().casefold()
    return source not in TITLE_DUPLICATE_EXCLUDED_SOURCES


def classify_article(
    company: object,
    keywords: list[object],
    item: object,
    raw_record: object | None = None,
    *,
    candidate_articles: list[object] | None = None,
    semantic_scorer: LocalSemanticScorer | None = None,
    nli_classifier: KlueNliClassifier | None = None,
    config: FilterConfig | None = None,
    precomputed_company_reranker: dict | None | object = _PRECOMPUTED_UNSET,
    precomputed_relevance: dict | None | object = _PRECOMPUTED_UNSET,
    precomputed_topical_relevance: dict | None | object = _PRECOMPUTED_UNSET,
) -> FilterDecision:
    """데이터베이스를 변경하지 않고 원문 기사 하나의 중복·광고·관련성을 판정한다."""

    config = config or FilterConfig()
    page_type_evidence = _non_article_page_evidence(item)
    if page_type_evidence:
        return FilterDecision(
            decision="rejected",
            reason="irrelevant",
            relevance_score=0.0,
            advertising_score=0.0,
            confidence=1.0,
            classifier_kind="rules_only",
            filter_version=config.version,
            details={
                "page_type_evidence": page_type_evidence,
                "thresholds": {
                    "duplicate": config.duplicate_threshold,
                    "advertising_reject": config.advertising_reject_threshold,
                    "advertising_review": config.advertising_review_threshold,
                    "relevance_accept": config.relevance_accept_threshold,
                    "relevance_reject": config.relevance_reject_threshold,
                },
            },
        )
    scorer = semantic_scorer if semantic_scorer is not None else get_semantic_scorer(config)
    nli = nli_classifier
    if nli is None and config.ai_enabled:
        nli = get_klue_nli_classifier(
            config.classifier_model_name, config.allow_model_download
        )
    groups = _keyword_groups(company, keywords)
    text = normalized_content(item)
    item_url = normalize_url(getattr(item, "url", ""))

    relevance_score, relevance_evidence = _rule_relevance(item, groups)
    identity_in_title = any(
        evidence.startswith("identity_in_title:") for evidence in relevance_evidence
    )
    product_in_title = any(
        evidence.startswith("product_in_title:") for evidence in relevance_evidence
    )
    # 사람이 매긴 정답지(2026-09-04, docs/2026-09-04-model-quality-devlog.md 1-1)로 측정한 결과,
    # 제목 언급은 71% 유효한 반면 본문에만 있는 언급은 32%만 유효했다. 통과시킨 기사의 60%가
    # 본문 전용 언급이라 필터 정밀도(48.7%)를 크게 끌어내리고 있었다.
    mentioned_in_body_only = not identity_in_title and not product_in_title and any(
        evidence.startswith("identity_in_summary:") or evidence.startswith("product_in_summary:")
        for evidence in relevance_evidence
    )
    ai_used = False
    ai_relevance: float | None = None
    advertising_score, advertising_evidence = _advertising_rules(item)
    affiliate_only = _affiliate_only_target_mention(item, groups["identity"])
    if affiliate_only:
        advertising_score = max(advertising_score, 0.98)
        relevance_score = 0.0
        advertising_evidence.append("target_only_in_affiliate_disclosure")
    ai_advertising: float | None = None
    nli_labels: dict[str, float] | None = None
    nli_error: str | None = None
    company_reranker = (
        precomputed_company_reranker
        if precomputed_company_reranker is not _PRECOMPUTED_UNSET
        else predict_company_relevance(
            str(getattr(company, "name", "")),
            groups["identity"],
            groups["product"],
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "summary", "") or ""),
        )
        if text and config.ai_enabled and not affiliate_only
        else None
    )
    local_relevance = (
        precomputed_relevance
        if precomputed_relevance is not _PRECOMPUTED_UNSET
        else predict_relevance(str(getattr(company, "name", "")), text)
        if text and config.ai_enabled and nli_classifier is None and company_reranker is None
        else None
    )
    fine_tuned = (
        predict_filter(text)
        if text and local_relevance is None and company_reranker is None and not affiliate_only
        else None
    )
    if company_reranker is not None:
        raw_reranker_score = float(company_reranker["relevant"])
        model_relevance = _calibrated_reranker_score(company_reranker)
        if identity_in_title:
            # A direct company name in a news headline is a high-recall anchor. This
            # protects genuine logistics/facility incidents from an overconfident model.
            relevance_score = max(0.76, 0.45 * relevance_score + 0.55 * model_relevance)
        elif product_in_title:
            relevance_score = max(0.70, 0.35 * relevance_score + 0.65 * model_relevance)
        elif mentioned_in_body_only:
            # Reranker confidence alone isn't enough evidence here (measured 32% precision on
            # this exact case). Cap below the accept threshold so these route to review instead
            # of auto-passing.
            relevance_score = min(0.55, 0.30 * relevance_score + 0.70 * model_relevance)
        elif relevance_score > 0:
            relevance_score = 0.25 * relevance_score + 0.75 * model_relevance
        else:
            relevance_score = min(0.30, 0.55 * model_relevance)
        ai_used = True
        ai_relevance = model_relevance
        nli_labels = {
            "substantive": raw_reranker_score,
            "incidental": 0.0,
            "unrelated": 1.0 - raw_reranker_score,
        }
    elif local_relevance is not None:
        model_relevance = float(local_relevance["relevant"])
        if relevance_score > 0:
            relevance_score = min(
                1.0, 0.35 * relevance_score + 0.65 * model_relevance
            )
        else:
            # 회사·제품 언급이 전혀 없는 글은 도메인 모델만으로 자동 승인하지 않는다.
            relevance_score = min(0.30, 0.55 * model_relevance)
        ai_used = True
        ai_relevance = model_relevance
        nli_labels = {
            "substantive": model_relevance,
            "incidental": 0.0,
            "unrelated": float(local_relevance["irrelevant"]),
        }
    elif fine_tuned is not None:
        rel = fine_tuned["relevance"]
        relevance_score = float(rel["relevant"] + 0.5 * rel["incidental"])
        advertising_score = float(fine_tuned["advertisement"]["yes"])
        ai_used = True
        ai_relevance = float(rel["relevant"])
        ai_advertising = advertising_score
        nli_labels = {
            "substantive": float(rel["relevant"]),
            "incidental": float(rel["incidental"]),
            "unrelated": float(rel["irrelevant"]),
            "advertisement": advertising_score,
        }
    # 규칙 점수를 NLI 문맥 판정으로 보정하되 기업 언급이 전혀 없으면 자동 승인하지 않는다.
    if company_reranker is None and local_relevance is None and fine_tuned is None and nli is not None and text:
        company_name = str(getattr(company, "name", "해당 기업"))
        relevance_hypotheses = [
            f"이 글의 중심 주제는 {company_name}의 사업, 경영, 제품 또는 기업 위험이다.",
            f"이 글에서 {company_name}은 부수적으로만 언급된다.",
            f"이 글은 {company_name}과 관련이 없다.",
        ]
        try:
            values = nli.score_hypotheses([text], [relevance_hypotheses])[0]
            nli_labels = dict(
                zip(("substantive", "incidental", "unrelated"), values)
            )
            ai_used = True
            ai_relevance = nli_labels["substantive"]
            if relevance_score > 0:
                relevance_score = min(
                    1.0, 0.35 * relevance_score + 0.65 * ai_relevance
                )
            else:
                # 기업·제품 언급이 없는 모델 단독 추측은 모호한 동명이인 기사를 자동 승인할 수 없다.
                relevance_score = min(0.30, 0.55 * ai_relevance)

            if advertising_score > 0:
                advertising_hypotheses = [
                    "이 글은 정보 전달을 위한 일반 기사다.",
                    "이 글은 상품 구매, 상담 또는 홍보를 유도하는 광고다.",
                ]
                ad_values = nli.score_hypotheses(
                    [text], [advertising_hypotheses]
                )[0]
                ai_advertising = ad_values[1]
                nli_labels["advertisement"] = ai_advertising
                if ai_advertising >= 0.70:
                    advertising_score = max(advertising_score, ai_advertising)
        except Exception as exc:  # 모델 실패 시에도 결정적 규칙 판정과 실패 기록을 유지한다.
            nli_error = f"{type(exc).__name__}: {exc}"[:500]

    # 회사 관련성(동명이인 등) 보조 모델: "관련 있음" 예측은 오탐이 잦아 점수를 올리는 데 쓰지 않고,
    # "무관"을 확신할 때(예: SSG 랜더스 야구팀 vs SSG 회사)만 점수를 그만큼 낮추는 안전장치로만 쓴다.
    # 학습에 쓴 9개 실제 기업 밖에서는 일반화가 검증되지 않았다 (영문·가상 기업 입력에서 90%+ 확신으로
    # "무관"을 오판하는 과신 현상을 테스트에서 확인함) -- 그래서 학습된 기업일 때만 적용한다.
    topical_relevance = (
        precomputed_topical_relevance
        if precomputed_topical_relevance is not _PRECOMPUTED_UNSET
        else predict_topical_relevance(text)
        if text and str(getattr(company, "name", "")) in TOPICAL_RELEVANCE_TRAINED_COMPANIES
        else None
    )
    if topical_relevance is not None and company_reranker is None:
        topical_irrelevant = float(topical_relevance.get("irrelevant", 0.0))
        if (
            topical_irrelevant >= config.topical_irrelevant_override_threshold
            and not identity_in_title
        ):
            relevance_score = min(relevance_score, 1.0 - topical_irrelevant)
            details_topical_override = topical_irrelevant
        else:
            details_topical_override = None
    else:
        details_topical_override = None

    # URL이 같거나, 정규화 제목이 완전히 같고 15분 이내인 경우만 중복 제거한다.
    # 비슷한 제목·본문은 실제 타 언론사의 후속 보도일 수 있으므로 story_cluster_id로만 묶는다.
    # YouTube 댓글처럼 제목이 개별 항목이 아니라 상위 콘텐츠(영상)를 나타내는 소스는
    # 제목 중복 비교에서 제외하고 URL로만 중복을 판정한다.
    candidates = [
        candidate for candidate in (candidate_articles or [])
        if getattr(candidate, "id", None) != getattr(raw_record, "id", None)
    ]
    ranked: list[tuple[float, object, str]] = []
    item_title = normalize_text(getattr(item, "title", ""))
    time_reference = raw_record or item
    for candidate in candidates:
        raw_id = getattr(time_reference, "id", None)
        candidate_id = getattr(candidate, "id", None)
        candidate_url = normalize_url(
            getattr(candidate, "normalized_url", None) or getattr(candidate, "url", "")
        )
        if (
            item_url
            and candidate_url == item_url
            and (
                raw_id is None
                or candidate_id is None
                or candidate_id < raw_id
            )
        ):
            ranked.append((1.0, candidate, "same_normalized_url"))
            continue
        candidate_title = normalize_text(getattr(candidate, "title", ""))
        if (
            item_title
            and candidate_title == item_title
            and _supports_title_duplicate_check(time_reference)
            and _supports_title_duplicate_check(candidate)
            # raw ID is monotonic, so the older row is always the canonical one.
            # This also prevents a version-bump reanalysis from making two rows
            # point at each other as duplicates.
            and (
                raw_id is None
                or candidate_id is None
                or candidate_id < raw_id
            )
            and _published_within_minutes(time_reference, candidate)
        ):
            ranked.append((1.0, candidate, "same_title_within_15_minutes"))
    ranked.sort(key=lambda row: row[0], reverse=True)

    duplicate_score, duplicate_candidate, duplicate_evidence = (
        ranked[0] if ranked else (0.0, None, None)
    )
    classifier_kind = (
        "company_cross_encoder_reranker" if company_reranker is not None else
        "fine_tuned_klue_multitask" if fine_tuned is not None else
        "hybrid_klue_nli" if ai_used else "rules_only"
    )
    details = {
        "relevance_evidence": relevance_evidence,
        "advertising_evidence": advertising_evidence,
        "duplicate_evidence": duplicate_evidence,
        "ai_relevance_score": ai_relevance,
        "ai_advertising_score": ai_advertising,
        "nli_label_scores": nli_labels,
        "classifier_model": (
            company_reranker.get("version")
            if company_reranker
            else config.classifier_model_name if ai_used else None
        ),
        "fine_tuned_model_version": fine_tuned.get("version") if fine_tuned else None,
        "target_company": str(getattr(company, "name", "")) or None,
        "relevance_input_schema": (
            company_reranker.get("input_schema")
            if company_reranker
            else local_relevance.get("input_schema") if local_relevance else None
        ),
        "company_reranker_version": (
            company_reranker.get("version") if company_reranker else None
        ),
        "company_reranker_score": (
            float(company_reranker["relevant"]) if company_reranker else None
        ),
        "company_reranker_thresholds": (
            {
                "accept": company_reranker.get("accept_threshold"),
                "reject": company_reranker.get("reject_threshold"),
            }
            if company_reranker
            else None
        ),
        "affiliate_only_target_mention": affiliate_only,
        "classifier_fallback_error": nli_error,
        "semantic_model": (
            config.semantic_model_name
            if ai_used and company_reranker is None
            else None
        ),
        "semantic_fallback_error": scorer.last_error if scorer and not ai_used else None,
        "topical_irrelevant_override": details_topical_override,
        "thresholds": {
            "duplicate": config.duplicate_threshold,
            "advertising_reject": config.advertising_reject_threshold,
            "advertising_review": config.advertising_review_threshold,
            "relevance_accept": config.relevance_accept_threshold,
            "relevance_reject": config.relevance_reject_threshold,
        },
    }

    # 중복, 광고, 관련성 순으로 배타적인 판정 우선순위를 적용한다.
    reason, decision = "accepted", "accepted"
    duplicate_of_raw_id: int | None = None
    if duplicate_candidate is not None and duplicate_score >= config.duplicate_threshold:
        decision, reason = "rejected", "duplicate"
        duplicate_of_raw_id = getattr(duplicate_candidate, "id", None)
        confidence = duplicate_score
    elif advertising_score >= config.advertising_reject_threshold:
        decision, reason = "rejected", "advertisement"
        confidence = advertising_score
    elif advertising_score >= config.advertising_review_threshold:
        decision, reason = "review_required", "advertisement"
        confidence = max(0.5, advertising_score)
    elif relevance_score >= config.relevance_accept_threshold:
        confidence = relevance_score
    elif relevance_score <= config.relevance_reject_threshold:
        decision, reason = "rejected", "irrelevant"
        confidence = 1.0 - relevance_score
    else:
        decision, reason = "review_required", "irrelevant"
        midpoint = (
            config.relevance_accept_threshold + config.relevance_reject_threshold
        ) / 2
        confidence = 0.5 + abs(relevance_score - midpoint)

    return FilterDecision(
        decision=decision,
        reason=reason,
        relevance_score=round(max(0.0, min(1.0, relevance_score)), 6),
        advertising_score=round(max(0.0, min(1.0, advertising_score)), 6),
        confidence=round(max(0.0, min(1.0, confidence)), 6),
        classifier_kind=classifier_kind,
        filter_version=config.version,
        duplicate_score=round(max(0.0, min(1.0, duplicate_score)), 6),
        duplicate_of_raw_id=duplicate_of_raw_id,
        details=details,
    )
