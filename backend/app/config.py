"""환경 변수에서 RISOTO 실행 설정을 읽고 캐시하는 모듈."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """API, 데이터베이스, 수집기 및 분석 모델의 실행 설정을 정의한다."""

    # 서비스 및 데이터베이스 기본 설정
    app_name: str = "RISOTO API"
    app_env: str = "development"
    database_url: str
    cors_origins: str = "http://localhost:5173"
    model_artifact_dir: str = "/app/model_artifacts"
    session_cookie_name: str = "risoto_session"
    csrf_cookie_name: str = "risoto_csrf"
    session_ttl_seconds: int = 604800
    session_cookie_secure: bool = False

    # 외부 기사·검색·댓글 제공자 자격 증명
    naver_api_hub_client_id: str = ""
    naver_api_hub_client_secret: str = ""
    tavily_api_key: str = ""
    kakao_rest_api_key: str = ""
    serpapi_api_key: str = ""  # 향후 수집기 연결을 위해 예약된 미사용 키
    # 기업 등록 백필, 수동 수집 및 주기적 실시간 댓글 수집에 모두 사용한다.
    youtube_api_key: str = ""
    # 무료 등급 하루 10,000 유닛 기준: 검색 1회(100) + 영상당 댓글 조회(최대 5개 x 1) = 쿼리당 105.
    # 회사당 검색어 1개, 3시간 간격(하루 8회)이면 9개 회사 x 105 x 8 = 7,560/일로 여유를 둔다.
    youtube_daily_quota_units: int = 10000
    youtube_query_quota_units: int = 105
    youtube_realtime_interval_hours: int = 3
    youtube_max_queries_per_run: int = 1

    # 수집 장애 알림과 재시도
    collection_alert_webhook_url: str = ""
    collection_alert_webhook_timeout_seconds: float = 5.0
    collection_retry_delays_seconds: str = "60,300,900"
    partial_failure_consecutive_threshold: int = 2
    collection_window_minutes: int = 15

    # NLI 기반 감성 분석 모델 설정
    sentiment_model_name: str = "Huffon/klue-roberta-base-nli"
    sentiment_allow_model_download: bool = True
    pretrained_sentiment_model_path: str = "/app/local_models/klue_roberta_domain_finetuned"

    # 실시간 수집 일정과 이상 탐지 기준선 최소 조건
    realtime_interval_seconds: int = 900
    realtime_overlap_minutes: int = 60
    baseline_min_articles: int = 20
    baseline_min_days: int = 3

    # 기사 중복·광고·관련성 하이브리드 필터 설정
    article_filter_version: str = "hybrid-company-reranker-v5"
    article_filter_ai_enabled: bool = True
    article_filter_classifier_model: str = "Huffon/klue-roberta-base-nli"
    article_filter_semantic_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    article_filter_allow_model_download: bool = True
    # 기존 환경 변수명은 유지하지만 이 normal/filter 모델은 광고·스팸 판정에 사용한다.
    pretrained_relevance_model_path: str = "/app/local_models/klue_roberta_spam_finetuned_v2"
    external_lightgbm_model_path: str = ""
    import_exported_models: bool = True
    article_filter_duplicate_threshold: float = 0.92
    article_filter_advertising_reject_threshold: float = 0.85
    article_filter_advertising_review_threshold: float = 0.55
    article_filter_relevance_accept_threshold: float = 0.70
    article_filter_relevance_reject_threshold: float = 0.30
    # 대상 기업과 기사를 함께 입력받는 공용 cross-encoder. 승격된 아티팩트만 서빙한다.
    company_reranker_enabled: bool = True
    company_reranker_base_model: str = "BAAI/bge-reranker-v2-m3"
    company_reranker_max_length: int = 512
    company_reranker_batch_size: int = 8

    # 스토리 군집: 최근 후보는 복합 판정, 오래된 후속 보도는 강한 동일성만 허용한다.
    story_cluster_recent_hours: int = 168
    story_cluster_followup_hours: int = 720
    story_cluster_candidate_limit: int = 1500
    story_cluster_semantic_candidate_limit: int = 80
    story_cluster_embedding_batch_size: int = 256
    readiness_min_articles: int = 50
    readiness_min_nonempty_windows: int = 40
    risk_default_threshold: float = 0.65
    risk_close_threshold: float = 0.45
    risk_close_consecutive_windows: int = 2
    risk_type_nli_enabled: bool = True

    # 기사·스토리 중심 운영 사건 엔진. 15분 특징은 확산 신호와 대시보드용으로 유지한다.
    story_risk_engine_enabled: bool = True
    # 위험 이벤트 발생 시 대응방안을 자동 생성할지. LLM을 부르므로 비용이 붙는다.
    # 자동 경로에만 걸리고 담당자가 버튼으로 요청하는 수동 생성은 이 값과 무관하다.
    # 생성 경로가 story_risk와 risk_analysis 두 갈래인데 서로 배타적이라(한쪽을 끄면
    # 다른 쪽이 켜진다) 엔진 스위치로는 멈출 수 없어 전용 스위치를 둔다.
    response_draft_auto_enabled: bool = True
    article_risk_candidate_threshold: float = 0.65
    article_risk_high_threshold: float = 0.80
    article_risk_uncertain_low: float = 0.35
    article_risk_llm_max_per_run: int = 20
    story_event_min_articles: int = 2
    # 마지막 관련 기사 날짜 다음 날부터 빈 날짜 3일이 모두 지나면 종료한다.
    story_event_inactivity_days: int = 3
    story_event_rebuild_hours: int = 72

    # 후보 재학습 준비 신호와 일일 운영 점검. 학습·승격은 별도 GPU 작업과 관리자 승인으로 남긴다.
    retrain_min_new_article_labels: int = 200
    retrain_min_new_risk_event_labels: int = 20
    model_drift_robust_z_threshold: float = 3.5
    model_drift_recent_hours: int = 24
    model_drift_baseline_days: int = 7

    # 근거 기반 대응 초안. 프로바이더가 준비되지 않으면 결정적 템플릿 초안으로 안전하게 폴백한다.
    openai_api_key: str = ""
    response_model_name: str = "gpt-5.6-luna"
    # "openai"(API 키 필요, 유료) 또는 "ollama"(로컬 무료 모델, API 키 불필요).
    response_generation_provider: str = "openai"

    # 수집된 기사에 대한 LLM 자동 라벨링(사람 대신 1차 정답지 생성)과 월간 표본 검수 목표치.
    # 사람이 매 건 검수하지 않고, LLM이 독립적으로 판단한 라벨을 바로 confirmed로 저장한다.
    llm_labeling_enabled: bool = True
    # "openai"(API 키 필요, 유료) 또는 "ollama"(로컬 무료 모델, API 키 불필요).
    llm_labeling_provider: str = "openai"
    llm_labeling_model_name: str = "gpt-4o-mini"
    llm_labeling_batch_size: int = 20
    llm_labeling_audit_sample_size: int = 20
    # 백엔드 컨테이너에서 호스트의 Ollama 서버로 접근하는 주소.
    ollama_base_url: str = "http://host.docker.internal:11434"

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """쉼표로 구분된 CORS 설정을 정리해 유효한 출처 목록으로 반환한다."""
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def collection_retry_delays(self) -> list[int]:
        """쉼표 구분 재시도 지연을 양의 초 목록으로 정규화한다."""
        delays: list[int] = []
        for value in self.collection_retry_delays_seconds.split(","):
            try:
                delay = int(value.strip())
            except ValueError:
                continue
            if delay > 0:
                delays.append(delay)
        return delays or [60, 300, 900]


@lru_cache
def get_settings() -> Settings:
    """환경 변수를 반영한 애플리케이션 설정 객체를 한 번 생성해 재사용한다."""
    return Settings()
