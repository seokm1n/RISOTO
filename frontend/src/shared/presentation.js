// 백엔드 식별자를 화면 문구로 변환하는 표시 전용 매핑이다.
export const KEYWORD_LABELS = { alias: "기업 별칭·약칭", peer: "유사기업", product: "제품·브랜드", risk: "키워드" };
export const COMPANY_KEYWORD_FIELDS = [
  { field: "aliases", type: "alias", label: "기업 별칭·약칭", hint: "기업을 식별할 다른 이름을 입력하세요" },
  { field: "peers", type: "peer", label: "유사기업", usage: "대응책 생성에 사용", hint: "비교 대상으로 삼을 기업을 입력하세요" },
  { field: "products", type: "product", label: "제품·브랜드", usage: "관련성 판별에 사용", hint: "제품·서비스명을 입력하세요" },
  { field: "risks", type: "risk", label: "키워드", usage: "위험·이슈 표현", hint: "예: 리콜, 소송, 사고" },
];
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
  active: "실시간 모니터링 활성",
};
export const DATA_QUALITY_LABELS = { complete: "수집 정상", partial: "일부 수집원 장애", unavailable: "수집 불가" };
export const LIGHTGBM_STATE_LABELS = { production: "LightGBM 운영 판정", provisional: "LightGBM 검증 판정", unavailable: "LightGBM 판정 대기" };
export const RISK_TYPE_LABELS = {
  product_quality: "제품·품질", safety_accident: "안전·사고", security_privacy: "보안·개인정보", legal_regulatory: "법률·규제",
  labor_hr: "노동·인사", financial_governance: "재무·지배구조", supply_operations: "공급·운영", reputation_consumer: "평판·소비자",
};
export const INCIDENT_STATUS_LABELS = { open: "장애 발생", retrying: "재시도 중", recovered: "복구됨", acknowledged: "확인 완료" };
export const HEALTH_STATUS_LABELS = { healthy: "정상", partial: "일부 장애", down: "장애", unknown: "확인 전" };
export const MODEL_TASK_LABELS = {
  article_filter: "기사 관련성·광고 판정",
  sentiment: "감성 분석",
  risk_type_classifier: "위험 유형 분류",
  risk_detector: "최종 위험 판정",
  isolation_forest: "이상치 탐지",
};
export const MODEL_STATUS_LABELS = { production: "운영 중", candidate: "후보", retired: "보관", failed: "실패" };
export const EMPTY_NOTIFICATIONS = { items: [], total: 0, risk_count: 0, model_promotion_count: 0 };

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
export const isRiskDetectionAvailable = (status) => status?.risk_detection_status === "available";
