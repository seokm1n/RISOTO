// 백엔드 식별자를 화면 문구로 변환하는 표시 전용 매핑이다.
export const KEYWORD_LABELS = { alias: "기업 별칭·약칭", product: "제품·브랜드", risk: "키워드" };
export const COMPANY_KEYWORD_FIELDS = [
  { field: "aliases", type: "alias", label: "기업 별칭·약칭", hint: "기업을 식별할 다른 이름을 입력하세요" },
  { field: "products", type: "product", label: "제품·브랜드", usage: "관련성 판별에 사용", hint: "제품·서비스명을 입력하세요" },
  { field: "risks", type: "risk", label: "키워드", usage: "위험·이슈 표현", hint: "예: 리콜, 소송, 사고" },
];
export const COMPANY_SIZE_LABELS = { small_medium: "중소기업", mid_sized: "중견기업", large: "대기업" };
export const MONITORING_LABELS = {
  backfilling: "7일 과거 수집 중",
  warming: "분석 기준 준비 중",
  active: "실시간 모니터링 활성",
  paused: "모니터링 일시중지",
  archived: "보관됨",
  error: "설정 확인 필요",
};
export const SOURCE_LABELS = {
  naver_api_hub: "네이버 뉴스",
  tavily: "Tavily 뉴스",
  kakao_daum: "Daum 검색",
  youtube_comment: "YouTube 댓글",
  pipeline: "분석 파이프라인",
};
// 수집 결과가 아직 없어도 선택 목록에 알려 줄 전체 지원 출처다.
export const SUPPORTED_SOURCES = ["naver_api_hub", "tavily", "kakao_daum", "youtube_comment"];
export const FILTERED_DATA_MODE = "__filtered__";
export const REVIEW_DATA_MODE = "__review_required__";
export const FILTER_REASON_LABELS = {
  duplicate: "중복",
  advertisement: "광고",
  irrelevant: "무관",
  accepted: "통과",
};
export const READINESS_LABELS = {
  preparing: "데이터 준비 중",
  pending_approval: "활성화 승인 대기",
  active: "분석 통계 사용 가능",
};
export const DATA_QUALITY_LABELS = { complete: "수집 정상", partial: "일부 수집원 장애", unavailable: "수집 불가" };
export const LIGHTGBM_STATE_LABELS = { production: "LightGBM 운영 판정", provisional: "LightGBM 검증 판정", unavailable: "LightGBM 판정 대기" };
export const RISK_TYPE_LABELS = {
  product_quality: "제품·품질", safety_accident: "안전·사고", security_privacy: "보안·개인정보", legal_regulatory: "법률·규제",
  labor_hr: "노동·인사", financial_governance: "재무·지배구조", supply_operations: "공급·운영", reputation_consumer: "평판·소비자",
};
export const INCIDENT_STATUS_LABELS = { open: "장애 발생", retrying: "재시도 중", recovered: "복구됨", acknowledged: "확인 완료" };
export const HEALTH_STATUS_LABELS = { healthy: "정상", partial: "일부 장애", down: "장애", unknown: "확인 전" };
// 모델 버전 화면에 쓰는 표시 이름이다. "무엇을 판별/분류/탐지하는 모델인가"로 통일한다.
export const MODEL_TASK_LABELS = {
  article_filter: "광고·관련성 통합 판별",
  article_relevance: "광고·스팸 판별",
  topical_relevance: "기업 관련성 판별",
  sentiment: "감성 분석",
  risk_type_classifier: "위험 유형 분류",
  risk_detector: "최종 위험 판정",
  isolation_forest: "이상치 탐지",
};
// 목록에서 각 모델이 실제로 무슨 일을 하는지 한 줄로 보여주는 설명이다.
export const MODEL_TASK_DESCRIPTIONS = {
  article_filter: "기사가 광고성인지와 기업 관련성을 하나의 모델로 함께 판정합니다.",
  article_relevance: "기사 본문이 광고·스팸 문구인지 아닌지를 판별합니다.",
  topical_relevance: "기사가 실제로 그 기업 얘기인지, 이름만 겹치는 동명이인인지 구분합니다.",
  sentiment: "기사·댓글의 긍정·중립·부정 감성을 분석합니다.",
  risk_type_classifier: "위험 사건을 제품·법률·노동 등 8가지 유형으로 분류합니다.",
  risk_detector: "이상치 탐지 결과를 바탕으로 최종 위험 여부를 판정합니다.",
  isolation_forest: "평소와 다른 이상 징후를 탐지합니다.",
};
export const MODEL_STATUS_LABELS = { production: "운영 중", candidate: "후보", retired: "보관", failed: "실패", unavailable: "연결 대기" };
export const EMPTY_NOTIFICATIONS = { items: [], total: 0, risk_count: 0, model_promotion_count: 0 };

// 블라인드 기사 라벨링 화면의 선택지 표시 문구다.
export const RELEVANCE_LABEL_OPTIONS = [
  { value: "relevant", label: "관련 있음 (기업 핵심 내용)" },
  { value: "incidental", label: "부수적 언급" },
  { value: "irrelevant", label: "무관함" },
  { value: "uncertain", label: "판단 보류" },
];
export const ADVERTISEMENT_LABEL_OPTIONS = [
  { value: "no", label: "광고 아님" },
  { value: "yes", label: "광고/홍보성" },
  { value: "uncertain", label: "판단 보류" },
];
export const SENTIMENT_LABEL_OPTIONS = [
  { value: "positive", label: "긍정" },
  { value: "neutral", label: "중립" },
  { value: "negative", label: "부정" },
  { value: "mixed", label: "혼재" },
  { value: "uncertain", label: "판단 보류" },
  { value: "not_applicable", label: "해당 없음(무관 기사)" },
];

// 숫자를 한국어 로캘의 천 단위 구분 형식으로 표시한다.
export const formatNumber = (value) => new Intl.NumberFormat("ko-KR").format(value ?? 0);
// 날짜 값을 월·일·시·분 형식의 한국어 문자열로 변환한다.
export const formatDate = (value) => value ? new Date(value).toLocaleString("ko-KR", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}) : "-";
// 남은 초를 음수가 되지 않는 분·초 카운트다운으로 바꾼다.
export const formatCountdown = (seconds) => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safeSeconds / 60)}분 ${String(safeSeconds % 60).padStart(2, "0")}초`;
};
// API의 영문·한글 감성 레이블을 화면 스타일용 네 가지 상태로 통일한다.
export const sentimentKind = (label) => ["positive", "긍정"].includes(label) ? "positive" : ["negative", "부정"].includes(label) ? "negative" : ["neutral", "중립"].includes(label) ? "neutral" : "pending";
// 감성 상태를 사용자에게 보여 줄 한국어 문구로 변환한다. null/미분석만 분석 대기로 표시한다.
export const sentimentText = (label) => {
  const kind = sentimentKind(label);
  return kind === "positive" ? "긍정" : kind === "negative" ? "부정" : kind === "neutral" ? "중립" : "분석 대기";
};
// 필터 점수를 일관된 소수점 두 자리 문자열로 표시한다.
export const formatScore = (value) => Number.isFinite(value) ? value.toFixed(2) : "-";
export const formatPercent = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "-";
export const formatRiskProbability = (value) => Number.isFinite(value) ? formatPercent(value) : "판정 대기";
// 사건명은 기사 제목이 아닌 스토리 군집의 대표 제목을 우선한다.
export const riskEventTitle = (risk) => risk?.summary
  || risk?.evidence_articles?.[0]?.title
  || risk?.article_title
  || (risk?.id ? `위험 이벤트 #${risk.id}` : "위험 이벤트");
export const isRiskDetectionAvailable = (status) => status?.risk_detection_status === "available";
