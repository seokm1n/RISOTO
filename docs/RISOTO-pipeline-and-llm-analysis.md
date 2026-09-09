# RISOTO 전체 흐름 · 모델 · LLM 사용 분석

분석 대상: `C:\python_project\RISOTO` (backend/frontend 전체)
작성일: 2026-09-09 / 기준: `main` 브랜치 작업본

---

## 0. 한 장 요약

RISOTO는 **기업 리스크 모니터링 → 위기 탐지 → 대응방안 초안 생성** 서비스다. 흐름은 크게 4단이다.

```
[1] 수집          네이버 / 다음(카카오) / Tavily / YouTube 댓글
                                       │  15분 주기 tick
[2] 정제·필터     중복 · 광고 · 관련성 판정 (규칙 + KLUE NLI + BGE reranker)
                                       │
[3] 위험 판정     스토리 군집화 → 기사별 위험 판정 → 스토리 단위 사건(RiskEvent) 개폐
                  (IsolationForest + LightGBM, 일부만 LLM 보완)
                                       │
[4] 대응 생성     RiskEvent → 13개 세부 유형 분류 → 대응 등급 → 근거 수집
                  → 관점별 시나리오 LLM 생성 → 자동 검증 10규칙 → ResponseDraft 저장
```

- **[1]~[3]은 대부분 로컬 모델·규칙**이고, **[4]가 LLM(OpenAI Responses API)의 주 무대**다.
- 진입점: [`backend/app/main.py`](../backend/app/main.py) → lifespan에서 `realtime_monitoring_loop` 상시 실행.
- 스택: Python 3.10 / FastAPI / SQLAlchemy 2 / Alembic / PostgreSQL 18.4 + pgvector 0.8.6 / React(Vite).
- 설정 단일 지점: [`backend/app/config.py`](../backend/app/config.py) (`.env` 주입, `get_settings()` 캐시).

---

## 1. 실행 구조

| 구성 | 위치 | 비고 |
|---|---|---|
| FastAPI 앱 조립 | [`app/main.py`](../backend/app/main.py) | 라우터 10개 등록, lifespan에서 모델 반입 + 초안 복구 + 실시간 루프 기동 |
| 설정 | [`app/config.py`](../backend/app/config.py) | 모든 임계값·모델명·기능 스위치가 여기 한 곳 |
| ORM 모델 | [`app/models.py`](../backend/app/models.py) (1,093줄) | 아래 테이블 계층 참고 |
| 라우터 | `app/routers/` | `collection`(1,391줄), `companies`, `governance`, `operations`, `dashboard`, `notifications`, `reviews`, `admin`, `auth`, `industries` |
| 서비스 | `app/services/` | 실제 파이프라인 로직 |
| 학습 | `app/training/` | 전부 수동 CLI. **자동 승격 없음** |
| 프런트 | `frontend/src/features/` | `analysis`, `risk-management`, `collection`, `models`, `dashboard` … |

앱 부팅 순서 ([`main.py:34-47`](../backend/app/main.py#L34-L47)):

1. `import_exported_models()` — `exports/model_artifacts`의 joblib을 `model_versions`에 반입
2. `recover_interrupted_response_drafts()` — 서버가 죽어 `generating`에 멈춘 초안 재큐잉
3. `realtime_monitoring_loop()` — 15분 tick 백그라운드 태스크

---

## 2. 데이터 계층 (3단 보관 원칙)

정제 결과를 **덮어쓰지 않고 계층으로 쌓는다.** 감사 가능성이 설계 원칙이다.

```
raw_news_articles        API 원본 그대로 (title / summary / url / raw JSON)
       │  classify_article()
article_filter_results   기업별 판정 + 제외 사유 + 점수 + 사용 모델 + 기준 버전
       │  통과분만
news_articles            분석 대상 (감성·이상탐지·위험·대시보드는 이 계층만 사용)
       │  assign_story_cluster()
story_clusters / story_cluster_articles
       │  process_company_risk_articles()
article_risk_assessments → risk_events (+ risk_event_articles, risk_event_types)
       │  generate_response_draft()
response_drafts          content(JSON) + evidence_urls + approval_state
```

부가 테이블: `company_feature_windows`(15분 특징), `company_daily_summaries`, `story_risk_scores`,
`article_labels`(정답 라벨), `risk_event_labels`(사람 확정 라벨), `model_versions`, `model_operation_checks`,
`case_records` / `case_sources`(**현재 0행**), `collection_jobs / attempts / incidents`, `notification_deliveries`.

> `review_required`와 제외 데이터도 삭제하지 않는다. 재분류는 `ARTICLE_FILTER_VERSION`을 올려 이력을 구분한다.

---

## 3. 단계별 상세 흐름

### 3-1. 수집 — [`app/services/news_collectors.py`](../backend/app/services/news_collectors.py)

수집기 4종: `NaverNewsCollector`, `TavilyNewsCollector`, `KakaoDaumSearchCollector`, `YouTubeCommentCollector`.

- 조정자: [`app/services/monitoring_pipeline.py`](../backend/app/services/monitoring_pipeline.py) (1,500줄, 이 프로젝트에서 가장 큰 파일)
  - `run_realtime_tick()` → `run_collection()` → 필터 → 감성 → 특징창 → 위험판정
  - 서울시간 `:00/:15/:30/:45` 정렬. **방금 끝난 구간만** 분석한다(수집 중인 미래 구간 점수화 금지).
- YouTube는 쿼터가 커서 `youtube_realtime_interval_hours=3`, `youtube_max_queries_per_run=1`로 스로틀.
- 수집 상태 3분류: `complete` / `partial` / `unavailable`
  - 전체 실패 → 기사 0건으로 대체하지 않고 **점수 자체를 만들지 않음**, 60/300/900초 3회 재시도
  - 부분 실패가 2개 구간 연속 → Webhook 알림 큐잉 ([`collection_health.py`](../backend/app/services/collection_health.py))

### 3-2. 정제·필터 — [`app/services/article_filtering.py`](../backend/app/services/article_filtering.py)

`classify_article()`이 단일 판정 함수. **규칙 + AI 하이브리드**이며, 모델 로드에 실패해도 보수적 규칙으로 계속 동작하고 `classifier_kind=rules_only`로 기록한다.

| 축 | 방식 | 임계값(config) |
|---|---|---|
| 완전 중복 | 추적 파라미터 제거한 **정규화 URL이 같을 때만** 병합 | — |
| 유사 기사 | 삭제하지 않고 `story_clusters`로만 연결 | `article_filter_duplicate_threshold=0.92` |
| 광고 | 협찬·제휴·할인·상거래 URL 신호 점수화 | 제외 `>=0.85` / 검토 `0.55~0.85` |
| 관련성 | 기업명·종목코드·별칭·제품명 실제 등장 + **BGE cross-encoder 재판정** | 통과 `>=0.70` / 제외 `<=0.30` |
| 제휴 고지 | "쿠팡 파트너스 활동의 일환" 같은 고지문에만 등장하면 통과시키지 않음 | `strip_affiliate_boilerplate()` |

- 제목 완전 일치 + 발행시각 15분 이내면 동일 기사로 처리. 단 **YouTube 댓글은 제목 중복 판정에서 제외**한다(같은 영상의 다른 댓글이 제목을 공유하므로).

### 3-3. 감성 — [`sentiment.py`](../backend/app/services/sentiment.py) / [`klue_nli.py`](../backend/app/services/klue_nli.py)

KLUE-RoBERTa NLI로 긍정·부정 가설을 비교하고 차이가 작으면 중립. 미세조정 아티팩트가 있으면 그쪽 우선([`fine_tuned_text.py`](../backend/app/services/fine_tuned_text.py)).

### 3-4. 스토리 군집 — [`app/services/story_clustering.py`](../backend/app/services/story_clustering.py)

- 옛 방식(제목 Jaccard 0.72 단일 컷)은 폐기. v2는 **제목 + 요약 문맥 + 행위·기관·인물 + 다국어 문장 임베딩**을 함께 본다.
- 장소는 의도적으로 사용하지 않는다(피드 결측이 많음).
- 최근 7일(`story_cluster_recent_hours=168`)은 복합 근거로 판정, 이후 30일(`followup_hours=720`)까지는 **강한 동일성**이 있을 때만 연결.

### 3-5. 위험 판정 — 두 엔진이 공존한다 (주의)

| | `window_v1` | `story_v2` (현행) |
|---|---|---|
| 파일 | [`risk_analysis.py::score_window`](../backend/app/services/risk_analysis.py) | [`story_risk.py::_aggregate_story_event`](../backend/app/services/story_risk.py) |
| 단위 | 15분 특징 창 | 기업 × 스토리 군집 |
| 모델 | IsolationForest + LightGBM | 스토리 IF+LightGBM 통합 joblib |
| 상태 | 새 이벤트 거의 생성 안 함 (6,595건 중 524건, 최근 활성 0건) | 실사용 경보 전부 |

- 스위치: `story_risk_engine_enabled=true`, `story_risk_model_enabled=true` + `STORY_RISK_MODEL_PATH` / `_SHA256` 검증.
- **15분 창 스코어링은 계속 돈다.** 대시보드 확산 신호로 쓰이고 확률에 재가산하지 않는다. 다만 그 값들이 대응 초안의 `[정량 근거]`로 흘러간다(→ 5-4).
- 사건 개방 조건: 정제 통과 기사 `>= story_event_min_articles(2)`. 언론사 수는 조건이 아니다.
- 사건 종료: 마지막 근거 기사 다음 날부터 빈 날짜가 `story_event_inactivity_days(3)` 연속.
- 사람이 확정한 라벨(`risk_event_labels`)은 자동 갱신이 덮어쓰지 않는다([`risk_ground_truth.py`](../backend/app/services/risk_ground_truth.py)).

### 3-6. 대응 초안 생성 — `app/services/response_engine/` (2부에서 상세)

---

## 4. 모델 인벤토리

### 4-1. 로컬 텍스트 모델 (`exports/local_models/local_models` → 컨테이너 `/app/local_models`)

| 용도 | 기본 / 경로 | 사용처 |
|---|---|---|
| 관련성·감성 폴백 NLI | `Huffon/klue-roberta-base-nli` | `klue_nli.py`, `sentiment.py`, `article_filtering.py` |
| 감성 미세조정 | `klue_roberta_domain_finetuned` | `PRETRAINED_SENTIMENT_MODEL_PATH` |
| 광고·스팸 미세조정 | `klue_roberta_spam_finetuned_v2` | `PRETRAINED_RELEVANCE_MODEL_PATH` (**이름과 달리 광고·스팸 판정용**) |
| 기업 인지 재순위 | `BAAI/bge-reranker-v2-m3` | `company_reranker.py` — 대상 기업 + 기사 쌍 cross-encoder |
| 근접 중복 임베딩 | `paraphrase-multilingual-MiniLM-L12-v2` | `LocalSemanticScorer`, 스토리 군집 |

> 관련성 모델 라벨 해석: `normal`=관련, `filter`=제외. 감성 모델은 아티팩트 `config.json`의 `negative / neutral / positive` 순서를 그대로 사용한다.

### 4-2. 수치 모델 (`exports/model_artifacts`, joblib)

| 모델 | 학습 | 서빙 |
|---|---|---|
| IsolationForest (15분 창) | `app/training/risk_models.py::train_isolation_forest` | `risk_analysis.score_window` |
| LightGBM 위험탐지 (15분 창) | `train_risk_detector` (StratifiedGroupKFold k=5) | `resolve_production_risk_detector` |
| 스토리 IF+LightGBM 통합 | `app/training/story_models.py` (export → label → train) | `story_model_runtime.py`, SHA256 고정 검증 |
| 위험유형 멀티라벨 KLUE | `app/training/risk_types.py` | `fine_tuned_text.predict_risk_types` |

- 최신 승격 예: `risk-lgbm-20260907T022507Z.joblib`
- **모든 학습 CLI는 `candidate`만 등록한다.** 승격은 `POST /api/v1/model-versions/{id}/promote` 수동.
- GPU 학습은 API 서버와 분리된 `trainer` 프로파일 컨테이너에서 수행한다.

---

## 5. LLM 사용 지점 — 전체 목록

호출 어댑터가 **두 갈래로 나뉘어 있다는 점**이 중요하다.

| # | 위치 | 모델 설정 | 어댑터 | 목적 |
|---|---|---|---|---|
| 1 | [`llm_labeling.py`](../backend/app/services/llm_labeling.py) | `llm_labeling_model_name` (기본 `gpt-4o-mini`) | 자체 (`openai` / `ollama` 분기) | 수집 기사 **자동 정답 라벨링** (관련성·광고·감성) |
| 2 | `llm_labeling.review_article_filter()` | 동일 | 동일 | 필터 화면의 "LLM 재검토" 버튼 |
| 3 | [`story_risk.py::_llm_assessment`](../backend/app/services/story_risk.py#L239) | 동일 | 자체 | 로컬 판정이 `uncertain`일 때만 기사 위험 보완 |
| 4 | **`response_engine/*`** | `response_model_name` (기본 `gpt-5.6-luna`) | [`_llm.py::structured_call`](../backend/app/services/response_engine/_llm.py) | 유형 분류 · 시나리오 생성 · 재생성 · 사례 인사이트 · 영향 판단 · 추천 |
| 5 | `response_engine/rag/embed.py` | `embedding_model_name` (기본 `text-embedding-3-small`) | `_llm.embed_texts` | RAG 색인·질의 임베딩 |

### 5-1. 자동 라벨링 루프 (주의 요망)

[`llm_labeling.py`](../backend/app/services/llm_labeling.py) docstring 요지:

> 모델 자기 예측을 쓰면 기존 맹점이 굳어지므로, **독립 프롬프트의 LLM**이 판정한 라벨을 `article_labels`에 곧바로 `confirmed`로 저장한다. 사람은 월 표본만 교차 확인한다(`audit_sample_candidates`).

- 라벨 값역: relevance `relevant | incidental | irrelevant | uncertain`, advertisement `yes | no | uncertain`, sentiment 6종
- 저장 주체는 `llm:` 접두 annotator ([`review_identity.py`](../backend/app/services/review_identity.py))
- **이 라벨이 `app/training/text_models.py`의 학습 입력이 된다.** 즉 *LLM이 만든 라벨로 KLUE를 학습시키는 구조*라 LLM 편향이 로컬 모델로 전이될 수 있다. 방어선은 월간 표본 검수(`llm_labeling_audit_sample_size=20`)뿐이다.
- 사람이 직접 매긴 정답지는 별도로 있다: `backend/training_data/HUMAN_LABELS.md`, `human_relevance_labels.csv`(관련성 600건), `human_risk_event_labels.csv`(위기 사건 82건).

### 5-2. 기사 단위 LLM 위험 보완 (비용 게이트)

- 조건: 로컬 판정 `decision == "uncertain"` **그리고** 예산 잔여 ([`story_risk.py:362-366`](../backend/app/services/story_risk.py#L362))
- 예산: `article_risk_llm_max_per_run=20`
- **`story_risk_model_enabled=true`면 `llm_remaining=0`으로 강제**한다 — 스토리 모델 모드에서는 기사 LLM 보완을 아예 부르지 않는다 ([`story_risk.py:770-772`](../backend/app/services/story_risk.py#L770))
- 출력 스키마 `_risk_schema()`: `is_risk`, `risk_probability`, `primary_type`(8종 or null), `type_scores`(8종 전부 required), `reason`

### 5-3. 응답 엔진 어댑터 특성 — [`_llm.py`](../backend/app/services/response_engine/_llm.py)

```python
client.responses.create(
    model=...,
    input=f"{system}\n\n---\n\n{user}",
    text={"format": {"type": "json_schema", "strict": True, "schema": ...}},
)
```

- **Chat Completions가 아니라 Responses API**를 쓴다. system/user는 문자열로 이어붙인다.
- **temperature를 쓰지 않는다** — `gpt-5.6-luna`가 미지원(400 Unsupported parameter). 시나리오 다양성은 temperature가 아니라 **stance를 프롬프트에 명시**해 확보한다. 덕분에 같은 입력에 같은 결과가 나와 재현성이 좋다.
- 타임아웃 120초, `max_retries=1`. 실패는 상위 워커가 `failed`로 기록한다.
- `app.config` import 실패 시 `.env`를 직접 읽는 독립 실행 폴백 경로가 있다(`_STANDALONE`).

### 5-4. 정량 근거 연결 (2026-09-07 수정분)

`Evidence.attribution`은 구조와 프롬프트 렌더링만 있고 **항상 비어 있었다.** IsolationForest·LightGBM·감성 모델이 낸 숫자가 LLM에 하나도 전달되지 않고 있었다.
→ [`service.py::_attribution_from_window`](../backend/app/services/response_engine/service.py#L105)에서 `RiskEvent.feature_window_id`로 연결된 `CompanyFeatureWindow` 값을 옮긴다.

- `anomaly_score` / `anomaly_percentile` (IF) — 저장값 그대로
- `lightgbm_risk_probability` — 저장값 + `risk_detector_percentile()`로 백분위만 새로 계산
- `negative_probability` — 같은 기업의 직전 7일 평균과 비교해 새로 계산
- 주의: LightGBM의 `baseline`에 결정 임계값을 넣었더니 프롬프트가 "지난 7일 평균이 0.65였다"는 **사실과 다른 문장**을 만들어 제거했다. `percentile`만 남겼다.

---

# 2부. LLM 위기 유형 분류 · 대응방안 생성

## 6. 모듈 지도 — `backend/app/services/response_engine/`

| 파일 | 줄수 | 역할 | 중요도 |
|---|---|---|---|
| `service.py` | 948 | **진입점.** RiskEvent 로드 → 페이로드 조립 → 단계 오케스트레이션 → ResponseDraft 저장 | ★★★ |
| `classify.py` | 347 | **위기 유형 분류**(8→13). LLM 호출 | ★★★ |
| `risk_types.py` | 132 | 유형 체계 정의 (8 상위 / 13 하위 + 담당주체 · 민감도) | ★★★ |
| `generate.py` | 576 | **시나리오 프롬프트 조립 + 호출 + 재생성 + 중복 병합** | ★★★ |
| `verify.py` | 339 | 자동 검증 10규칙 | ★★★ |
| `schema.py` | 238 | 입력 페이로드 `AlertPayload` / `Mention` / `Attribution` (별칭 관대 파싱) | ★★ |
| `report_schema.py` | 131 | 출력 strict JSON 스키마 + 전략·책임 enum + 상한 | ★★ |
| `tier.py` | 90 | 대응 등급 3단 매트릭스 | ★★ |
| `evidence.py` | 127 | 근거 4종 수집 조립 | ★★ |
| `principles.py` (+ `principles_data.json` 45KB) | 217 | 유형별 대응 원칙 · 주체별 지침 (정적 텍스트) | ★★ |
| `retrieval.py` (+ `regulations_data.json` 137KB) | 204 | 사례·법령 검색기 인터페이스 + `KoreanRegulationMapper` | ★★ |
| `case_search.py` | 270 | 유사 사례 웹검색 + LLM 인사이트 추출 | ★★ |
| `rag/{store,provider,embed}.py` + `rag/index/` | — | 원칙 **보충** RAG (36문서 3,847청크, 약 28MB) | ★★ |
| `impact.py` | 169 | 동종기업 경로: 영향 판단 | ★ |
| `recommend.py` (+ `recommend_schema.py`) | 391 | 동종기업 경로: 추천 생성 | ★ |
| `keywords.py` | 72 | 유형별 키워드 사전 + `looks_negative()` | ★ |
| `preview.py` | 135 | **DB 없이 돌려보는 도구** (`--dry-run`이면 프롬프트만 출력) | ★★ |
| `INTEGRATION.md` | — | 연결 안내 · v2→v3 대조표 · 미해결 목록 | ★★★ |

호출부 (반드시 둘 다 같은 엔진을 봐야 함):

- 수동: [`app/routers/governance.py:257`](../backend/app/routers/governance.py#L257) `POST /risk-events/{id}/response-drafts`
- 자동: `story_risk.py:862`, `risk_analysis.py:1202`, `story_model_backfill.py:113`, `story_model_events.py` → `enqueue_response_draft(..., auto=True)`

## 7. 유형 체계 — 왜 2계층인가

[`risk_types.py`](../backend/app/services/response_engine/risk_types.py)

**탐지 8개(상위)** = `app/risk_taxonomy.py::RISK_TYPES`와 코드 동일 (학습 모델·라벨·프런트가 쓰는 값):
`product_quality`, `safety_accident`, `security_privacy`, `legal_regulatory`, `labor_hr`, `financial_governance`, `supply_operations`, `reputation_consumer`

**대응 13개(하위)** — 각 하위의 상위는 **정확히 하나**(엄격한 계층):

| 코드 | 라벨 | 상위 | 1차 대상 | 민감도 |
|---|---|---|---|---|
| R01 | 품질·결함 | product_quality | 소비자 | 중 |
| R02 | 소비자 안전사고 | safety_accident | 소비자 | **높음** |
| R03 | 산업재해 | safety_accident | 노동자·라이더 | **높음** |
| R04 | 개인정보·보안 | security_privacy | 소비자(+규제기관) | **높음** |
| R05 | 가격·약관·표시 | legal_regulatory | 소비자 | 낮음 |
| R06 | 규제제재·소송 | legal_regulatory | 규제기관·투자자 | 중 |
| R07 | 노무·고용 | labor_hr | 노동자 | 중 |
| R08 | 재무·지배구조 | financial_governance | 규제기관·투자자 | 중 |
| R09 | 배송·물류 | supply_operations | 소비자 | 낮음 |
| R10 | 서비스장애·기술 | supply_operations | 소비자 | 낮음 |
| R11 | 정산·거래조건 | supply_operations | **판매자·입점사** | 중 |
| R12 | 고객대응·환불 | reputation_consumer | 소비자 | 낮음 |
| R13 | 평판·루머 | reputation_consumer | 일반대중 | 중 |

설계 근거(코드 주석에 명시):

- 탐지는 학습 모델이라 클래스를 늘리면 클래스당 표본이 줄어 성능이 떨어진다. 대응은 학습이 아니라 **원칙 블록 조회**라 늘려도 비용이 없다.
- R02 vs R03: 같은 `safety_accident`지만 대응이 정반대다(소비자 사용중단 고지 ↔ 작업중지·노동부 보고).
- R09/R10 vs R11: 같은 "운영 차질"이지만 **대응 상대가 소비자냐 판매자냐**로 갈린다. 뭉뚱그리면 판매자에게 소비자용 사과문을 보내게 된다.
- R13은 **부인(`부인_반박`)이 적절한 유일한 유형**이다. 같은 상위의 R12(환불 분쟁)에서 부인은 최악이라 반드시 분리해야 한다.
- 담당 주체(stakeholder)는 별도 판정 축이 아니라 **유형에서 파생되는 조회값**이다.

## 8. 분류 단계 상세 — [`classify.py`](../backend/app/services/response_engine/classify.py)

### 8-1. 실제로 쓰이는 함수는 `refine()` 하나다

- `classify.classify()` (키워드 게이트 `MIN_TOP_HITS=3`, `TOP_RATIO=2.0` → 미달 시 LLM 승격)는 **어디서도 호출되지 않는 사실상 사문 코드**다. `_SYSTEM_PROMPT`도 그쪽 전용.
- 실제 경로: `service._build_content` → `classify.refine(payload, _detection_scores(event))` → `_refine_llm()` (`_REFINE_PROMPT` 사용).

### 8-2. `refine()` 동작

```
detection (상위 유형 문자열 or {유형: 점수})
   │ _upstream_pick()   1·2위 격차 < AMBIGUOUS_MARGIN(0.15)이면 2위도 후보로 기록
   │ 후보 = 13개 전체    ← 상위의 자식만 주던 방식은 폐기
   │ _refine_llm()      대표 원문 5건 × 150자 + 키워드 히트 상위 3개 힌트
   │ _wrap()            확신도 하향 승계 / 상위 불일치 판정 / 검토필요 플래그
```

- **후보를 13개 전체로 준 이유**(주석 실측): 탐지 신엔진 이벤트의 **72%가 유형 공백**이고, 표본 12건 중 3건이 상위 오분류였다. 상위 자식만 주면 상위 오류에서 구조적으로 회복할 수 없다.
  → 상위는 프롬프트에 **힌트로만** 넣고, 어긋남은 코드가 `parent_mismatch`로 기록해 탐지 단계 오류율을 계속 측정한다.
- **확신도 승계**: `confidence = min(세부 확신도, 상단 확신도)`. "세부 판정에 불확실성이 없다"와 "유형이 확실하다"는 다른 말이다.
- `upstream_confidence < UPSTREAM_REVIEW_BELOW(0.5)`면 무조건 `needs_review`.
- **`event_title` 우선 규정**: 원문이 댓글처럼 단편적일 때는 스토리 대표 제목이 사안을 규정한다.
  (실측 배경: 배터리 화재 사건의 근거 10건이 전부 영상 댓글이었고 계정 해킹 댓글이 근거 1위여서 개인정보·보안으로 오분류됐다 — [`schema.py`](../backend/app/services/response_engine/schema.py)의 `event_title` 주석)

### 8-3. 안전장치 2개 (유형 선택과 분리되어 있음)

| 플래그 | 뜻 | 걸리면 |
|---|---|---|
| `is_actionable` | 이 기업이 대응해야 할 사안인가 (동명 구단·동명이인, 타사 사건에 곁들여 언급, 단순 시세·제품 소개 등) | **`_safety_hold()` → `status: "대응불필요_종료"`로 저장, 시나리오 생성 안 함** |
| `evidence_sufficient` | 이 원문만으로 대응 방향을 정할 수 있는가 | `needs_review=True` |

- 두 플래그는 유형 선택을 바꾸지 않는다(false여도 `risk_type`은 하나 고르게 한다).
- 유형 불일치(`parent_mismatch`)는 **더 이상 보류 사유가 아니다**(기록만 한다).
- 대응 단계에서 상위 분류기 오류를 덮지 않는다 — 덮으면 상위를 고쳤을 때 좋아졌는지 잴 수 없다.

### 8-4. 근거 0건 처리

`payload.mentions`가 비면 **LLM을 아예 부르지 않고** `status: "근거부족_보류"`로 저장한다 ([`service.py::_no_evidence_content`](../backend/app/services/response_engine/service.py#L340)).
근거 기사가 하나도 없는 이벤트가 실재한다(공유 DB 실측 123건, 최고 확률 0.94 critical 포함). 근거 없는 판정을 정상 산출물로 저장하는 것이 가장 나쁜 결과라는 판단이다.

## 9. 대응 등급 — [`tier.py`](../backend/app/services/response_engine/tier.py)

확률 구간 × 유형 민감도 매트릭스. 등급이 3단인 이유는 **알림 채널이 3개**이기 때문이다.

| 확률 \ 민감도 | 높음 | 중간 | 낮음 |
|---|---|---|---|
| 상 (>=0.8) | T3_긴급 | T3_긴급 | T2_주시 |
| 중 (>=0.6) | T3_긴급 | T2_주시 | T2_주시 |
| 하 | T2_주시 | T1_관찰 | T1_관찰 |

- `crisis_probability`가 없으면 **보수적으로 '중'** 처리(낮게 잡아 놓치는 비용이 더 크다).
- `spread_stage == "언론보도"`면 한 단계 상향 — 다만 **`spread_stage` 산출 로직이 아직 없어 이 규칙은 현재 미작동**이고, 그 사실을 `notes`에 명시적으로 남긴다.
- 정책: T1 대시보드 / T2 담당자 알림·24h / T3 즉시 알림·2인 + 법무 검토
- **T3는 시나리오 3개, 그 외는 2개** ([`service.py`](../backend/app/services/response_engine/service.py#L578)).
- 유형 가중치를 위기 확률 안에 섞지 않는다 — 같은 신호를 두 번 반영하지 않고, 정책이 바뀌면 매트릭스 칸만 고치면 되게 하려고.

## 10. 근거 수집 — [`evidence.py`](../backend/app/services/response_engine/evidence.py)

**보고서를 쓰기 전에 근거를 모은다.** 생성 후 첨부하면 LLM이 근거 없이 쓰고, 나중에 붙인 자료와 본문이 어긋나며, 인용 검증 대상도 없어진다.

| 종류 | 방식 | 현황 |
|---|---|---|
| 원문 | **부정 강도 상위 절반 + 확산 규모 상위 절반** 혼합, 최대 10건 | 동작 |
| 과거 사례 | `TeamCaseRetriever` — 검수 사례 우선, 부족분만 웹검색 | **`case_records` 0행 → 100% 웹검색** |
| 정량 근거 | `payload.attribution` 그대로 | 2026-09-07부터 실제로 채워짐 |
| 적용 법령 | `KoreanRegulationMapper` — **유사도가 아니라 결정적 매핑 조회** | 104건 중 **검증 62건만 서빙** |

- 혼합 선별 이유: 부정 강도만 뽑으면 극단적 소수의견만, 확산만 뽑으면 자극적인 것만 올라온다.
- 법령을 유사도로 찾지 않는 이유: 개인정보 유출이면 통지·신고 의무가 유사도와 무관하게 무조건 적용되는데, top-k로 뽑으면 반드시 적용돼야 할 조항이 우연히 빠질 수 있다.
- 자산이 없으면 `no_case_mode` / `no_regulation_mode` 플래그가 서고 → `generate.py`가 인용을 **금지**하고 → `verify.py` 규칙 1이 "정말 인용 0건인지"를 검사한다. **빈 상태가 조용히 넘어가지 않고 파이프라인 전체에 전파된다.**

### 10-1. 사례 검색 — [`case_search.py`](../backend/app/services/response_engine/case_search.py)

**검색과 추론을 분리한다.** 모델에 검색 권한을 주면 존재하지 않는 URL을 만들어낸다.

1. 팀 수집기(네이버·Tavily)가 반환한 기사만 허용 집합에 넣고
2. 그 기사들만 읽혀 교훈을 뽑게 한다 (`_INSIGHT_SCHEMA`: `outcome` = 성공/실패/혼재/미상, what·response·result·lesson)

모델이 URL을 만들어낼 경로 자체가 없으므로 사후 URL 실재 검증이 필요 없다. `provenance`가 `curated`(사람 검수) / `web_search`(런타임)로 구분되어 프롬프트·검증·화면에서 다르게 다뤄진다. 조회 범위 `LOOKBACK_YEARS=10`, `MAX_ARTICLES=8`.

### 10-2. 원칙과 RAG — [`principles.py`](../backend/app/services/response_engine/principles.py) / [`rag/`](../backend/app/services/response_engine/rag/)

```
프롬프트 = 공통 베이스(정적) + 유형 원칙(정적) + 주체 지침(정적) + RAG 보충(선택)
                  ↑ 없으면 안 되는 것(가드레일)                ↑ 있으면 좋은 것
```

- 원칙 블록(`principles_data.json`의 must / must_not)은 **RAG로 대체하지 않는다.** 가드레일이라 항상 같은 것이 들어가야 하고, 검색 실패로 비면 LLM이 기준 없이 쓴다.
- RAG는 **상황별 보충만** 얹는다. `RagPrincipleProvider(top_k=3, min_score=0.30)`.
- **검색은 유형으로 먼저 좁힌 뒤** 그 안에서만 유사도를 본다(다른 유형 지침 혼입 방지 — 법령을 결정적 매핑으로 조회하는 것과 같은 이유).
- 각 청크는 **사용 제약(`caution`)을 달고 다닌다** — 리콜 자료는 미국 CPSC 절차를, 사이버 자료는 NIST 용어를 담고 있어 그대로 넣으면 국내 기업 보고서에 미국 절차가 실린다.
- 색인이 없거나 임베딩·검색이 실패해도 **정적 원칙만으로 계속 동작**한다(예외를 전부 삼킴).
- 원칙 13개 중 **R03·R07·R11 3개는 아직 근거 없는 draft 텍스트**다(`grounded: false` → `principles.py`의 `TYPE_PRINCIPLES` 폴백).
- `PROMPT_VERSION = "principles-v2.6-hierarchical"` — 반려 사유를 원칙 버전에 매핑하는 추적 키. 문구를 의미 있게 고칠 때마다 올려야 반려율 변화를 볼 수 있다.
- 원문(`sources/*` 약 50개 PDF/HWPX/DOCX)은 용량 때문에 저장소에서 제외돼 있다. 어느 문서가 어느 유형의 근거인지는 `principles_data.json`의 `sources`가 `file` 키로 고정한다(파일명 유사도에 맡기면 제목이 겹치는 자료끼리 뒤바뀐다).

## 11. 시나리오 생성 — [`generate.py`](../backend/app/services/response_engine/generate.py)

### 11-1. 프롬프트 조립 순서

```
system : 역할 + 읽는 사람 정의 + 유형/대상 + 공통 원칙 + 유형 원칙 + 주체 지침 + RAG 보충 + 작성 규칙 + stance 블록
user   : 맥락 + 정량 근거 + 원문 + 사례 + 법령
```

원칙을 **system에 두는 이유**: 근거 텍스트가 길어져도 원칙이 뒤로 밀려 희석되지 않게 하기 위해서다.

### 11-2. 관점(stance)으로 다양성 확보

`DEFAULT_STANCES = ("선제_공개", "사실확인_우선", "피해구제_중심")` — T3면 3개, 아니면 앞 2개.
temperature 대신 관점을 명시하므로 **같은 입력에 같은 결과**가 나온다(재현성).

### 11-3. 프롬프트에 박힌 "읽는 사람" 규칙 (품질 이슈의 핵심)

보고서 독자는 **IR/주가 담당자와 마케팅·홍보 담당자**이지 데이터 분석가가 아니다. 그래서 금지 사항이 명시돼 있다:

- 내부 필드명(`negative_ratio_7d`, `daily_growth_rate`, `anomaly_score` 등) 본문 사용 금지 → 뜻으로 풀어 쓸 것
- 원문 식별자(`[m_1]`) 본문 사용 금지 → `cited_mention_ids` 필드에만 넣고 본문에서는 내용으로 지칭
- URL 본문 사용 금지 → 출처의 성격만 밝힐 것
- 비교 수치는 **기간을 밝힐 것** ("평소 대비 4배"가 아니라 "직전 7일 평균 대비 약 4배")
- `summary_points`는 숫자 없이 `짧은 라벨: 내용` 형태 2~4개, 수치는 `judgment_basis`에만

### 11-4. 출력 스키마 — [`report_schema.py`](../backend/app/services/response_engine/report_schema.py)

| 필드 | 검증 연결 |
|---|---|
| `scenario_headline` / `_contrast` / `_recommendation` / `_tradeoff` | 화면 탭 · 현재 권고 문구 |
| `summary_points` | 요점 2~4개, 숫자 금지 |
| `judgment_basis` | 수치는 여기에만 |
| `strategies[].strategy_type` (8종) | 규칙 3 |
| `strategies[].target_stakeholder` | 규칙 6 |
| `checklist[].deadline_hours` | 규칙 5 (1~72h) |
| `cited_mention_ids` / `cited_case_ids` | 규칙 2 / 규칙 1 |

- `RESPONSIBILITY_VALUES = ("피해자", "사고", "예방가능")`
- `FORBIDDEN_WHEN_PREVENTABLE = ("부인_반박",)` — 책임이 '예방가능'인데 부인하면 여론 역풍
- 상한 `MAX_STRATEGIES=3`, `MAX_PRIMARY_RISKS=2`: **OpenAI strict 모드가 maxItems / minItems를 지원하지 않아 코드로 검사한다**(규칙 4가 존재하는 이유)

### 11-5. T3 3회 생성의 실제 구현

문서 원안은 "3회 독립 생성 후 합의"지만, **문장 단위 다수결은 자연어에서 신뢰하기 어려워** 합의 대신 **'검증 통과'를 선별 기준**으로 쓴다. 통과한 것 중 첫 번째를 채택하고 나머지는 후보로 남긴다(사람이 3안을 비교할 수 있게 버리지 않는다).

## 12. 자동 검증 10규칙 — [`verify.py`](../backend/app/services/response_engine/verify.py)

규칙은 **레지스트리**로 관리되며, 각 규칙이 스스로 "지금 검사 가능한가(`is_active`)"를 판단한다. 자산이 없으면 FAIL이 아니라 **SKIP**으로 결과에 남고, 자산이 채워지면 코드 수정 없이 자동으로 켜진다.

| # | 이름 | 활성 조건 | 검사 내용 |
|---|---|---|---|
| 1 | 사례 인용 | 항상 | 없는 사례 인용 차단 / `no_case_mode`면 인용 0건인지 |
| 2 | 원문 인용 | 항상 | 선별 목록에 없는 `mention_id` 인용 차단 |
| 3 | 책임-전략 상충 | 항상 | 책임=예방가능 + `부인_반박` 조합 금지 |
| 4 | 개수 상한 | 항상 | 전략 <=3, 주 리스크 <=2 |
| 5 | 기한 범위 | 항상 | checklist 1~72시간 |
| 6 | 전략 대상 | 항상 | 전략 대상이 유형의 담당 주체와 일치 |
| 7 | 법령 기한 | `not no_regulation_mode` | 필수 조문이 체크리스트에 반영됐는가 |
| 8 | 사례 정리 | 사례 있을 때 | 사례 출처 형식 |
| 9 | 사례 활용 | 사례 있을 때 | 수집 사례가 실제로 쓰였는가 |
| 10 | 독자 친화 표기 | 항상 | 본문에 mention_id / URL / 내부 필드명 누출 검사 |

**실패 시**: 위반 항목을 구체적으로 지정해 `regenerate_with_feedback()`로 **1회만** 재생성 → 재검증.
전부 실패하면 `status: "검증실패"`로 저장하되 **버리지 않고** 담당자에게 보여준다.
마지막으로 `dedupe_scenarios()`가 사실상 같은 시나리오를 병합하고 `merged_stances`에 기록한다.

## 13. 저장 포맷 — `response_drafts.content` (schema_version = 3)

```jsonc
{
  "engine": "response_engine",
  "status": "생성완료" | "검증실패" | "근거부족_보류" | "대응불필요_종료" | "영향없음_종료",
  "risk_type": "R11", "risk_type_label": "정산·거래조건",
  "detection_type": "supply_operations",     // 상위 8개 중 하나
  "stakeholder": "판매자·입점사",
  "classification": { "confidence": 0.0, "refine_confidence": 0.0, "upstream_confidence": 0.0,
                      "route": "llm", "reason": "...", "needs_review": false,
                      "parent_mismatch": false, "hit_counts": {} },
  "tier": "T3_긴급", "tier_policy": {}, "tier_notes": [],
  "generation_kind": "main_response" | "competitor_impact",
  "selected_stance": "선제_공개",
  "scenarios": [
    { "stance": "...", "tradeoff": "...", "merged_stances": [],
      "report": {}, "verification": { "passed": true, "summary": "...",
                                      "rules": [], "violations": [], "skipped": [] } }
  ],
  "evidence": [], "precedents": [], "regulations": [],
  "principle_version": "principles-v2.6-hierarchical",
  "usage": { "input_tokens": 0, "output_tokens": 0, "calls": 0 }
}
```

`evidence_urls` 컬럼에는 허용 URL 집합(근거 원문 + 사례 출처)이 저장된다. **입력에 없는 URL과 인용 없는 대응 문구는 제거**되고, URL 근거 기사가 없으면 초안 자체를 만들지 않는다. 승인 전에는 외부 전송이나 실제 대응을 실행하지 않는다.

### v2 → v3 대조 (프런트 작업 시 필수) — [`INTEGRATION.md`](../backend/app/services/response_engine/INTEGRATION.md)

| v2 키 | v3 위치 |
|---|---|
| `content.risk_summary` | `scenarios[i].report.summary_points[]` (배열) |
| `content.scenarios[i].title` | `scenarios[i].stance` / `.tradeoff` |
| `scenario.recommended_actions` | `scenarios[i].report.strategies[]` + `.checklist[]` |
| `content.uncertainty` | `scenarios[i].verification` (규칙별 통과·스킵 결과) |

- **v2 키를 v3가 하나도 물려받지 않는다.** 렌더러를 분기해야 한다.
- `ActionGroups`는 `{immediate, within_24h, within_7d}` 형태를 전제하는데 v3의 `checklist[]`는 `{task, owner, deadline_hours}` 평면 목록이라 그대로 못 쓴다.
- 렌더 지점은 `ResponseDraftContent` 컴포넌트인데 **경로가 3번 바뀌었으므로** `grep -rn "function ResponseDraftContent" frontend/src`로 찾을 것. v3 메인 경로는 `MainResponseContent.jsx`로 분리되어 `schema_version === 3 && generation_kind !== "competitor_impact"`에서 갈라진다.
- 동종 경로(`content_kind: "peer_recommendation"`)는 **또 다른 구조**다 — `scenarios`가 없고 `content.recommendation`(`headline`, `recommendations[]`, `avoid[]`)과 `content.impact`를 읽어야 하며, `status: "영향없음_종료"`면 `recommendation`이 `null`이다.

## 14. 동종 기업(경쟁사) 경로

메인 경로가 "무엇이 문제인가(유형) → 어떻게 대응하나(방안)"라면, 동종 경로는 **"남의 일이 우리에게 오는가(영향) → 그렇다면 무엇을 하나(추천)"**다.

- [`impact.py`](../backend/app/services/response_engine/impact.py): **유형 판정과 영향 판단을 LLM 한 호출로 동시 확정**한다. 대다수가 '영향 없음'으로 버려질 건에 LLM을 두 번 쓰지 않기 위해, 키워드 1차 분류(무료)까지만 돌려 잠정 유형을 힌트로 넘긴다.
  - `IMPACT_DIRECTIONS = ("부정적_파급", "반사이익", "영향_없음")` — **반사이익을 '영향없음'으로 뭉개지 않는다.** 비교사 배송 사고 → 고객 유입은 마케팅이 알아야 할 상황이고, 그 국면의 공격적 마케팅은 역풍을 부르기 쉬워 그 자체가 추천 대상이다.
  - `IMPACT_CHANNELS`: 규제_조사_확대 / 소비자_인식_전이 / 투자자_주가_동조 / 동일_취약점_보유 / 공급망_협력사_공유 / 고객_유입_기회
- `영향_없음`이면 **사례 검색·추천 생성을 아예 호출하지 않는다** — 이 경로의 비용 통제 지점.
- [`recommend.py`](../backend/app/services/response_engine/recommend.py): 방향으로 리포트를 통째로 가르지 않고 **채널마다 권고를 매단다.** 실측: 쿠팡 정산 이슈에서 방향은 반사이익인데 채널에 `고객_유입_기회`와 `규제_조사_확대`가 같이 나왔다 — 방향으로 갈랐다면 규제 대응이 통째로 빠졌을 것이다.
- 체크리스트·시나리오·입장문 초안이 **없다.** 동종사 담당자에게 필요한 건 대응 전략 전문이 아니라 "우리도 위험한가, 뭘 확인해야 하나"에 대한 짧은 답이다.

## 15. 트리거 · 동시성 · 상태머신

### 15-1. 두 경로

| 경로 | 함수 | 스위치 |
|---|---|---|
| 자동 | `enqueue_response_draft(id, auto=True)` | `response_draft_auto_enabled` (기본 true) |
| 수동 | `generate_response_draft(id, force=...)` | **스위치 무관** (담당자 의도가 명확하므로 막지 않음) |

생성 트리거: 최초 사건 확정, 위험 등급 상승, 위험 확률의 의미 있는 상승, 공식 출처 추가.

### 15-2. 중복 방지 2중 잠금 ([`service.py::enqueue_response_draft`](../backend/app/services/response_engine/service.py#L827))

- 프로세스 내: `_active_job_ids` set + Lock
- 프로세스 간: PostgreSQL `pg_try_advisory_lock` — **기다리지 않고 시도만** 한다(이미 누가 만들고 있으면 곧 저장되므로 줄 설 이유가 없고, 워커를 1분 넘게 묶어 두면 다른 이벤트가 밀린다)
- 배경: 워커 2개짜리 스레드풀 + **팀원들이 각자 백엔드를 띄운 채 같은 공유 DB를 본다.** LLM 호출이 1분을 넘겨 "기존 초안 있나" 검사와 저장 사이가 넓게 벌어진다.

### 15-3. `RiskEvent.response_generation_status`

`idle → pending → generating → generated | failed | deferred`

- `deferred`: 자동 생성 스위치가 꺼져 있음
- 서버 재시작 시 `pending | generating`을 `recover_interrupted_response_drafts()`가 재큐잉한다(severity critical → 확률 순).

### 15-4. 재생성 스킵 조건

기존 v3 초안이 있고 + 사람 확정 라벨 이후 생성됐고 + `detection_type`이 그대로고 + `last_response_revision >= evidence_revision`이면 **재생성하지 않는다**(같은 답이 나오므로 비용만 든다). 유형이 바뀌었으면 원칙·법령·시나리오가 통째로 달라지므로 다시 만든다.
`status`가 `dismissed` / `legacy_candidate`면 아예 생성하지 않는다(실측: v3 초안 66건 중 39건이 이 상태의 이벤트에 붙어 있었다).

## 16. DB 없이 돌려보기 (가장 빠른 이해 경로)

```powershell
cd backend

# 프롬프트 조립만 확인 (LLM 미호출, API 키 불필요, 비용 0)
python -m app.services.response_engine.preview --input app/services/response_engine/samples/alert_sample.json --dry-run

# 메인 경로 전체를 실제 모델로 (유형 → 등급 → 근거 → 시나리오 → 검증)
python -m scripts.run_main_response tests/data/alert_peer_downside.json --dry-run
python -m scripts.run_main_response tests/data/alert_peer_downside.json --out-dir out/

# 동종 경로
python -m scripts.run_peer_impact ...
python -m scripts.run_peer_recommend ...
```

`--dry-run`은 조립된 프롬프트의 글자 수를 세어 호출 횟수와 함께 보여준다 — **자동 생성 비용을 미리 추산하는 근거**다.
`_build_content`가 db를 쓰는 곳은 `TeamCaseRetriever` 하나뿐이고 `db=None`이면 검수 사례 조회를 건너뛴다(현재 `case_records`가 0행이라 실제 동작도 같다).

관련 테스트:
`tests/test_response_engine_wiring.py`(연결), `test_response_engine_verify.py`(검증 규칙), `test_response_engine_recommend.py`(492줄, 동종 경로), `test_llm_labeling.py`, `test_filter_llm_review.py`, `test_story_risk.py`(532줄)

---

## 17. 반드시 알아야 할 점

### 안전 설계 (의도된 것 — 건드리기 전에 주석부터 읽을 것)

1. **근거 없으면 생성하지 않는다.** mentions 0건 → `근거부족_보류`, LLM 미호출.
2. **URL을 모델이 만들어낼 경로가 없다.** 검색 API 반환분만 허용 집합에 넣고 그 안에서만 인용하게 한다.
3. **자산 부재가 조용히 넘어가지 않는다.** `no_case_mode` / `no_regulation_mode`가 프롬프트(인용 금지)와 검증(SKIP 기록)에 전파된다.
4. **대응 단계가 탐지 오류를 덮지 않는다.** `parent_mismatch`를 기록만 하고 고치지 않는다.
5. **승인 전에는 외부 전송·실제 대응을 실행하지 않는다.**
6. 사람이 확정한 라벨은 자동 갱신이 덮어쓰지 않는다.
7. 기사 원문을 외부 분류 API로 보내지 않는다(필터·감성 추론은 백엔드 컨테이너 내부에서). LLM에 나가는 것은 대응 생성 단계의 선별된 원문 발췌다.

### 미완성 / 주의 (작업할 때 걸릴 것들)

| 항목 | 상태 | 근거 |
|---|---|---|
| `case_records` | **0행** → 유사 사례가 100% 웹검색 경로 | `INTEGRATION.md`, devlog 2026-09-04 §6 |
| 법령 매핑 | 104건 중 **검증 62건만 서빙.** 미검증 조문은 `KoreanRegulationMapper`가 반환조차 하지 않음 | `INTEGRATION.md` |
| 법령 반영률 | 데이터 문제가 아니라 **생성 신뢰성 문제** — 매핑표에 있는 필수 법령을 LLM이 체크리스트에 안 넣는다. 재생성 피드백이 "빠진 법령을 반드시 추가하라"를 충분히 강제하는지 확인 필요 | devlog 2026-09-04 §5 (미해결) |
| 대응 원칙 | R03 · R07 · R11 3개는 근거 없는 **draft 텍스트** | `principles.py::TYPE_PRINCIPLES` |
| R13 법령 0건 | **의도된 결과.** 언론중재법·명예훼손은 회사의 의무가 아니라 권리라 이 표의 성격과 맞지 않음 | `INTEGRATION.md` |
| `spread_stage` | 산출 로직 자체가 없음 → tier 상향 규칙 미작동 | `tier.py`, `schema.py` |
| 이력 4종 (`days_since_last_alert` 등) | 컬럼 자체가 없음 | `schema.py::missing_fields()` |
| `classify.classify()` | 호출부 없는 사문 코드 (`refine()`만 사용) | grep 결과 |
| 분류·등급 임계값 | `MIN_TOP_HITS=3`, `TOP_RATIO=2.0`, `AMBIGUOUS_MARGIN=0.15`, `PROB_HIGH/MID` 전부 **라벨셋으로 튜닝해야 하는 잠정값** | 각 파일 주석 |
| `window_v1` / `story_v2` | 두 판정 엔진 공존. 실사용은 story_v2, window는 대시보드 신호 + attribution 공급 | devlog 2026-09-07 §1 |
| 프런트 v2 / v3 | 키를 하나도 공유하지 않아 분기 렌더링 필요. 동종 경로는 또 다른 구조 | `INTEGRATION.md` |
| LLM 자기학습 루프 | LLM이 만든 라벨(`confirmed`)로 KLUE를 학습 → 편향 전이 가능. 방어선은 월 표본 20건 검수뿐 | `llm_labeling.py` docstring |
| 모델 승격 경쟁 조건 | 재시작 시 승격 초기화 버그는 수정됐으나 경쟁 조건 잔존 | devlog 2026-09-07 §4 |
| RAG 원문 | `sources/*`는 저장소에서 제외(용량). 색인 재생성 시 `pypdf`, `python-docx` 필요(런타임에는 불필요) | `INTEGRATION.md` |
| 문서 §N 표기 | 코드 주석의 "문서 §3", "문서 §11" 등은 저장소에 없는 외부 워크플로우 문서(v5)를 가리킴 | 전 모듈 |

### 비용 통제 지점 (LLM 과금이 붙는 곳)

- `RESPONSE_DRAFT_AUTO_ENABLED=false` — 자동 생성 전면 차단(수동 버튼은 계속 동작)
- `ARTICLE_RISK_LLM_MAX_PER_RUN=20` — 기사 위험 보완 회당 상한
- `story_risk_model_enabled=true`면 기사 LLM 보완 **0회**
- 이력 재구성(`rebuild-story-events --all`)은 **대응 초안을 자동 생성하지 않는다.** `--generate-drafts --draft-limit N`으로 명시해야 한다
- T3만 시나리오 3개(그 외 2개), 검증 실패 재생성은 **1회만**(최악 비용을 호출 2회로 묶음)
- 동종 경로는 `영향_없음`에서 즉시 종료
- 분류 프롬프트에는 대표 원문 5건 × 150자만 넣는다(길게 넣을수록 비용만 늘고 판단이 흐려진다)

---

## 18. 읽을 파일 우선순위

### 위기 유형 분류 + 대응 생성만 볼 때 (권장 순서)

1. `backend/app/services/response_engine/INTEGRATION.md` — 전체 맥락 · v2/v3 대조 · 미해결 목록
2. `backend/app/services/response_engine/risk_types.py` — 유형 체계 (짧고 주석이 설계 근거)
3. `backend/app/services/response_engine/service.py::_build_content` (545~680줄) — **파이프라인 전체가 이 함수 하나에 다 보인다**
4. `backend/app/services/response_engine/classify.py::refine` — 유형 분류 본체
5. `backend/app/services/response_engine/generate.py` 1~200줄 — 프롬프트 전문
6. `backend/app/services/response_engine/verify.py` 290~340줄 — 규칙 레지스트리
7. `backend/app/services/response_engine/report_schema.py` — 출력 계약
8. `backend/app/services/response_engine/tier.py`, `evidence.py` — 짧고 완결적
9. `backend/app/services/response_engine/schema.py` — 입력 계약(별칭 파싱)과 결측 필드 목록
10. `backend/app/services/response_engine/_llm.py` — Responses API · temperature 제약
11. `backend/app/routers/governance.py` 257~440줄 — API 표면(생성·조회·승인·반려)
12. `backend/app/services/response_engine/{impact,recommend}.py` — 동종 기업 경로

### 파이프라인 전반을 볼 때

1. `README.md` (22KB, 이 저장소에서 가장 정확한 운영 문서)
2. `backend/app/config.py` — 모든 임계값·스위치
3. `backend/app/main.py` — 부팅 순서
4. `backend/app/services/monitoring_pipeline.py::run_realtime_tick`
5. `backend/app/services/article_filtering.py::classify_article`
6. `backend/app/services/story_clustering.py::assign_story_cluster`
7. `backend/app/services/story_risk.py::process_company_risk_articles`, `_aggregate_story_event`
8. `backend/app/services/risk_analysis.py::build_feature_window`, `score_window`
9. `backend/app/models.py` — 테이블 계층
10. `docs/2026-09-04-model-quality-devlog.md`, `docs/2026-09-07-risk-formula-devlog.md` — **최근 무엇이 왜 바뀌었는지**
11. `docs/story-risk-integration-cleanup.md`, `docs/story-risk-model-training.md`

---

## 19. 코드를 읽을 때의 팁

- **이 저장소의 주석은 설계 근거와 실측 수치를 담고 있다.** 대부분 "왜 이렇게 하지 않았는가"를 적어 두었으므로, 바꾸기 전에 해당 주석을 읽으면 이미 시도했다 폐기한 방식인지 알 수 있다.
- 모듈 docstring이 사실상 설계 문서다. 특히 `risk_types.py`, `retrieval.py`, `rag/__init__.py`, `impact.py`, `verify.py`, `case_search.py`의 밀도가 높다.
- 임계값은 `config.py` 또는 각 모듈 상단 상수에 모여 있고 하드코딩된 매직 넘버는 드물다.
- 파일 크기 상위: `monitoring_pipeline.py`(1,500) > `routers/collection.py`(1,391) > `risk_analysis.py`(1,374) > `story_risk.py`(1,284) > `models.py`(1,093) > `response_engine/service.py`(948).

---

# 3부. 수학적 알고리즘 · 가중치 전수

**요약**: 딥러닝 모델의 가중치는 학습된 것(joblib / safetensors)이라 코드에 없지만, **그 위·아래를 감싸는 수식과 상수는 전부 코드에 하드코딩되어 있다.** 크게 6종류다 — ① 강건 통계 정규화(median/MAD, median/IQR) ② Isolation Forest 경험적 백분위 ③ LightGBM + 표본 가중치 + F-beta 임계값 탐색 ④ 집합 유사도 계열(Jaccard/Containment/Dice/SequenceMatcher)의 가중 합 ⑤ 코사인 유사도(mean-pooling, L2 정규화) ⑥ 손으로 정한 선형 결합 가중치.

## 20. 강건 통계 정규화 (Robust statistics)

### 20-1. Robust Z-score — [`risk_analysis.py::_robust_z`](../backend/app/services/risk_analysis.py#L340)

같은 기업의 과거 창(최대 672개 = 7일 × 96창)과 비교해 "평소와 얼마나 다른가"를 재는 값이다.

```
median = median(history)
MAD    = median(|history - median|)
scale  = 1.4826 × MAD
z      = clip((value - median) / scale, -20, +20)
```

- **1.4826의 의미**: 정규분포에서 `MAD × 1.4826 ≈ 표준편차`다. 정확히는 `1 / Φ⁻¹(0.75) = 1/0.6745 ≈ 1.4826`. 이 상수를 곱해야 MAD가 표준편차와 같은 척도가 되고, 그래야 결과를 z-score처럼 "몇 시그마"로 읽을 수 있다.
- **왜 평균·표준편차가 아니라 median·MAD인가**: 뉴스 기사량은 평소 0~2건이다가 사건 하나에 수백 건으로 튄다. 평균과 표준편차는 그 이상치 하나에 통째로 끌려가서, 정작 그 이상치를 "정상"으로 만들어 버린다(breakdown point 0%). median/MAD는 표본의 절반이 오염돼도 버틴다(breakdown point 50%).
- **3중 폴백**:
  1. 이력 8개 미만 → `0.0` (기준선을 못 만듦)
  2. MAD가 0 (절반 이상이 같은 값, 예: 대부분 기사 0건) → **IQR 기반으로 전환**: `scale = (Q75 - Q25) / 1.349`
     (1.349 = 정규분포의 IQR 폭 `2 × 0.6745`. 역시 표준편차 척도로 환산하는 상수)
  3. IQR도 0 (전부 동일값) → 값이 같으면 0, 다르면 `sign × 10.0`
- **clip ±20**: 분모가 아주 작을 때 z가 발산해 하류 모델의 피처 스케일을 망가뜨리는 것을 막는다.
- 사용처: `article_count_robust_z`, `story_count_robust_z`, `negative_probability_robust_z` 3개 피처. 일일 드리프트 점검(`model_drift_robust_z_threshold=3.5`)에도 같은 통계를 쓴다.

### 20-2. 기업별 RobustScaler — [`risk_models.py::_scaler`](../backend/app/training/risk_models.py#L74)

Isolation Forest에 넣기 전 **기업마다 따로** 정규화한다.

```
center = median(X, axis=0)
scale  = Q75 - Q25   (IQR, 0이면 1.0으로 대체)
X'     = (X - center) / scale
```

- sklearn `RobustScaler`의 기본 동작과 동일한 수식을 직접 구현한 것이다(joblib 의존을 줄이고 `company_scalers` dict로 저장하기 위해).
- **왜 기업별인가**: 쿠팡은 평소 15분당 기사 20건, 에이블리는 0.5건이다. 전역 정규화 하나로 묶으면 IF가 "쿠팡은 항상 이상치"라고 학습한다. 기업 ID를 피처로 넣지 않는 대신(`_numeric_features` 주석: *company ID is deliberately absent*) **스케일러에 기업 정체성을 흡수시킨다.**
- 서빙 시 `_scale_features()`가 `scalers[company_id] → scalers["global"] → 항등`  순으로 폴백한다. 신규 기업은 전역 스케일러를 쓴다.

## 21. Isolation Forest — 원리와 이 코드에서의 변형

### 21-1. 원리

- 랜덤하게 피처와 분할값을 골라 트리를 키우면, **이상치는 몇 번 안 잘라도 혼자 남는다.** 정상점은 밀집 구역에 있어 고립되기까지 깊이 들어가야 한다.
- 점수는 평균 경로 길이 `E[h(x)]`를 표본 수 `n`의 기대 경로 길이 `c(n)`으로 정규화한 것:
  `s(x, n) = 2^( −E[h(x)] / c(n) )`, 여기서 `c(n) = 2H(n−1) − 2(n−1)/n` (H는 조화수).
  1에 가까울수록 이상, 0.5 근처면 정상.
- sklearn의 `decision_function`은 부호가 반대(**클수록 정상**)라 코드가 전부 **`-decision_function()`** 으로 뒤집어 "클수록 이상"으로 만든다.

### 21-2. 하이퍼파라미터 (두 모델 공통)

```python
IsolationForest(n_estimators=300, max_samples="auto", contamination="auto",
                random_state=42, n_jobs=-1)
```
- `max_samples="auto"` = 256. 원 논문이 권하는 값으로, 표본을 적게 쓸수록 swamping/masking이 줄어든다.
- `contamination="auto"`: 이상치 비율을 가정하지 않는다. **애초에 이진 판정으로 쓰지 않고 raw score만 뽑아 LightGBM의 피처로 넘기기 때문에** 임계값이 필요 없다.

### 21-3. 경험적 백분위(empirical CDF)로 바꾸는 부분 — 여기가 핵심

raw anomaly score는 스케일이 해석 불가능하다. 그래서 학습 시 점수 분포를 통째로 저장해 두고, 서빙 때 **경험적 누적분포**로 매핑한다.

```python
# 학습: training_scores = (-decision_function(matrix)).tolist()   ← 전체 분포를 아티팩트에 저장
# 서빙:
percentile = float(np.mean(reference <= raw_score))     # = F̂(x), 0~1
```

- `mean(reference <= x)`는 **경험적 CDF** 그 자체다: 학습 모집단 중 이 값 이하인 비율.
- 스토리 모델은 정렬된 배열에 이진탐색으로 같은 계산을 한다(더 빠름):
  `percentiles = np.searchsorted(reference, scores, side="right") / len(reference)` ([`story_model.py:102`](../backend/app/services/story_model.py#L102))
- **LightGBM 백분위도 같은 방식**([`risk_analysis.py::risk_detector_percentile`](../backend/app/services/risk_analysis.py#L688)). 주석의 근거가 중요하다:
  > 사람 라벨 사건은 전부 탐지기가 이미 플래그한 창에서 뽑혀 raw 확률이 1.0 근처에 몰려 있다(p25=0.86). 전체 ~8,500창 모집단(대부분 조용해서 0쪽으로 크게 치우침) 대비 순위로 바꾸면 같은 신호가 쓸 만해진다.

  즉 **단조 변환(monotone transform)으로 분포를 펴는 것**이다. 순위는 보존되고 스케일만 균등해진다.
- IF 점수와 그 백분위 **2개 모두** LightGBM의 입력 피처로 들어간다(`anomaly_score`, `anomaly_percentile`).

## 22. LightGBM — 두 모델의 하이퍼파라미터와 임계값 결정

### 22-1. 15분 창 탐지기 — [`risk_models.py::train_risk_detector`](../backend/app/training/risk_models.py)

```python
LGBMClassifier(objective="binary", n_estimators=250, learning_rate=0.03,
               num_leaves=15, min_child_samples=10, subsample=0.85,
               colsample_bytree=0.85, reg_lambda=1.0, random_state=42)
```

- 전형적인 **소표본용 보수 설정**이다: 얕은 트리(`num_leaves=15`), 낮은 학습률(0.03) + 많은 트리(250), L2 정규화(`reg_lambda=1.0`), 행·열 서브샘플링 0.85.
- **표본 가중치 — 이중 역빈도(inverse frequency)**:
  ```python
  w_i = 1 / (같은 사건의 창 수) / (같은 기업의 창 수)
  w  *= len(w) / sum(w)          # 평균 1로 재정규화
  ```
  기사 수백 건짜리 대형 사건 하나가 소형 사건 수십 개를 압도하는 것과, 쿠팡 데이터가 에이블리를 압도하는 것을 **동시에** 막는다. 재정규화는 가중치 합을 표본 수와 맞춰 유효 학습률이 흔들리지 않게 한다.
- **교차검증: `StratifiedGroupKFold(n_splits=min(5, 양성사건수), shuffle=True, random_state=42)`**
  - `Group` = **사건 ID**. 같은 사건의 15분 창들이 학습/검증에 쪼개져 들어가면 거의 동일한 행이 양쪽에 있어 성능이 부풀려진다(누수). 사건 단위로 통째로 묶어 나눈다.
  - `Stratified` = 양성 비율 유지. 시간순 분할을 쓰던 시절 **양성 사건이 전부 앞쪽에 몰려 검증 구간이 100% 음성**이 되는 사고가 있었다(devlog 2026-09-04 §1).
  - 예측은 **OOF(out-of-fold)** 로 모아 평가·임계값 결정에 쓴다.

### 22-2. 결정 임계값 — F2 최적화 ([`_best_f2_threshold`](../backend/app/training/risk_models.py#L187))

```python
for t in np.linspace(0.2, 0.9, 71):        # 0.01 간격 격자 탐색
    score = fbeta_score(y, p >= t, beta=2)
best_threshold = argmax(score)             # 초기값 0.65
```

- **`F_β = (1+β²)·P·R / (β²·P + R)`, β=2** → **재현율을 정밀도보다 4배(β²) 무겁게** 본다.
  위기 탐지에서 놓친 사건(FN)의 비용이 오탐(FP)보다 훨씬 크다는 판단이다. FP는 담당자가 초안을 보고 기각하면 끝이지만, FN은 위기를 통째로 놓친다.
- **전역 임계값 + 기업별 임계값**을 둘 다 계산해 아티팩트에 넣는다. 서빙에서 `per_company[company_id] → global → risk_default_threshold(0.65)` 순으로 폴백([`score_window`](../backend/app/services/risk_analysis.py#L736)).
- 지표는 `oof_pr_auc`(average precision), `oof_macro_f1`, ROC-AUC, recall을 함께 기록한다. **불균형 데이터라 PR-AUC가 주 지표**다.

### 22-3. 스토리 모델 — [`story_models.py::train`](../backend/app/training/story_models.py#L344)

```python
LGBMClassifier(n_estimators=400, learning_rate=0.035, num_leaves=15, max_depth=5,
               min_child_samples=20, reg_lambda=5, colsample_bytree=0.85,
               deterministic=True, force_col_wise=True)
model.fit(x, y, eval_set=[(xv, y_val)], eval_metric="binary_logloss",
          callbacks=[lgb.early_stopping(35)])
threshold = argmax over np.linspace(.1,.9,81) of f1_score(y_val, p >= t)
```

- 창 모델보다 **더 강한 정규화**(`reg_lambda=5`, `max_depth=5`)와 **early stopping(35라운드)**.
- 임계값은 **F1**(창 모델은 F2)이고 **검증 세트에서만** 고른다 — 테스트 라벨은 절대 쓰지 않는다(주석에 명시).
- `deterministic=True, force_col_wise=True, random_state=SEED` — **완전 재현성**을 강제한다. 아티팩트를 SHA256으로 고정 검증하는 운영 방식과 짝을 이룬다.
- **IF 기여도를 측정한다**: `fit(include_if=True)`와 `fit(include_if=False)`(마지막 2열 제거) 두 모델을 모두 학습해 리포트에 나란히 남긴다. IF 피처가 실제로 도움이 되는지 매 학습마다 검증하는 구조다.
- 특징 중요도는 `importance_type="gain"`으로 기록한다(분할 횟수가 아니라 **손실 감소량** 기준).

### 22-4. 스토리 모델의 피처 벡터 구성 — [`story_model.py`](../backend/app/services/story_model.py)

```
[ 수치 피처 13 + 키워드 통계 2×|PATTERNS| ] ⊕ [ TF-IDF → SVD 32차원 ] ⊕ [ IF score, IF percentile ]
```

- **수치 피처**: `log1p(기사수)`, `log1p(언론사수)`, 언론사/기사 비율, **제목 중복 비율**(`1 − 고유제목수/n`), 기사 시간 스팬(시간), **최근 6시간 비율**, 부정확률의 mean/max/std, 감성 결측 비율, **공식 출처 비율**(`.go.kr`/`.gov` 도메인), 기업명 언급 비율, 평균 본문 길이 + 유형별 키워드 히트의 mean/max
  - `log1p(x) = ln(1+x)`: 기사 수는 롱테일 분포라 로그로 눌러야 트리가 상위 구간에만 분할을 낭비하지 않는다. `1+`는 0건 처리용.
- **텍스트**: `TfidfVectorizer(analyzer="char", ngram_range=(2,4), min_df=3, max_features=18000, sublinear_tf=True)` → `TruncatedSVD(32)`
  - **문자 n-gram(2~4)을 쓰는 이유**: 한국어는 교착어라 형태소 분석기 없이 단어 토큰화를 하면 "배송이/배송을/배송은"이 전부 다른 토큰이 된다. 문자 n-gram은 어미 변화에 강하다.
  - `sublinear_tf=True` → `tf = 1 + log(tf)`. 같은 단어의 반복 등장이 선형으로 기여하지 않게 눌러 준다.
  - `TruncatedSVD` = 희소행렬용 **LSA(잠재의미분석)**. 18,000차원 희소 벡터를 32차원 밀집 벡터로 줄여 LightGBM이 쓸 수 있게 만든다(PCA와 달리 평균 중심화를 안 해 희소성을 보존).
- **RobustScaler는 정상(y==0) 표본으로만 fit**한다: `scaler = RobustScaler().fit(numeric[y == 0])`, IF도 정상 표본만으로 학습(`isolation.fit(normal)`). 이상 탐지의 정석대로 **"정상이 무엇인지"만 학습**시키는 것.

## 23. 손으로 정한 선형 결합 가중치 (학습되지 않은 값들)

이 프로젝트에서 **가장 자주 손대게 될 숫자들**이다. 전부 코드 상수이고 근거가 주석에 있다.

### 23-1. 기사 위험 확률 — [`story_risk.py::_local_assessment_from_scores`](../backend/app/services/story_risk.py#L141)

```python
probability = clamp01(0.20 × type_probability + 0.70 × negative + 0.10 × relevance)
```

**이 가중치가 어떻게 정해졌는지가 문서화되어 있다** (2026-09-07, 사람 라벨 55건 `story_v2_label_candidates.csv`):

| 신호 | 단독 AUC | 판정 |
|---|---|---|
| `type_probability` | **0.500** (표준편차 0, 항상 1.000) | 완전 무정보 — 후보가 되려면 이미 `>=0.35`를 통과해야 해서 포화됨 |
| `negative` | **0.900** | 가장 강한 신호인데 옛 가중치 0.35가 희석시키고 있었음 |
| `relevance` | — | 보조 |

그리드서치 상위권(type 0.0~0.3 / negative 0.6~0.9 / relevance 0.1, AUC 0.90~0.91)에서 **type을 완전히 죽이지 않는 보수적 지점**을 골랐다. 결과: 전체 AUC **0.894 → 0.906**.

이어지는 판정 규칙:
```
type_probability < 0.20  또는  probability < 0.35   → non_risk
probability >= 0.65  그리고  type_probability >= 0.35 → risk
그 외                                                 → uncertain   ← LLM 보완 대상
```

### 23-2. 스토리 사건 확률 — [`story_risk.py::_aggregate_story_event`](../backend/app/services/story_risk.py#L613)

```python
probability = clamp01(
    max(후보 기사들의 risk_probability)                    # 최댓값 기반
    + min(0.15,                                            # 보너스 상한
          0.05 × max(0, 고유_출처도메인수 − 1)
        + 0.02 × max(0, 후보기사수 − 1))
)
severity = "critical" if probability >= 0.85 else "warning"
```

- **평균이 아니라 최댓값**을 쓴다: 스토리 안에 약한 기사가 아무리 많아도 강한 기사 하나의 신호를 희석시키면 안 된다.
- **확산 보너스**: 서로 다른 언론사 도메인 1개 추가마다 +0.05, 기사 1건 추가마다 +0.02, **합계 상한 0.15**. 상한이 없으면 대형 사건에서 전부 1.0으로 포화되어 순위 정보가 사라진다.
- `-1`이 붙은 이유: 첫 출처·첫 기사는 이미 `max()`에 반영돼 있으므로 **추가분만** 센다.

### 23-3. window_v1 ↔ story_v2 블렌드 — **현재 꺼져 있음**

```python
WINDOW_SIGNAL_BLEND_WEIGHT = 0.0          # story_risk.py:56
probability = (1 − w) × story_prob + w × window_percentile
```
w=0.2로 검증했다가 개선이 없어 0.0으로 되돌렸다(devlog 2026-09-07 §3). **코드는 남아 있고 상수 하나만 바꾸면 다시 켜진다.**

### 23-4. 유형 점수 = NLI × 키워드 (max가 아니라 곱) — [`risk_analysis.py::enrich_risk_types_with_nli`](../backend/app/services/risk_analysis.py#L141)

```python
score = nli_score × max(keyword_score, 0.6)
```

- **옛 공식은 `max(keyword, 0.6 × nli)`** 였는데, 그러면 우연한 부분문자열 하나("사고"가 무관한 단어 안에 들어 있는 경우)로 유형 점수가 **1.0까지** 치솟았다. NLI가 명백히 반대해도 막을 수 없었다.
- 곱셈으로 바꾸면 **NLI가 거부권을 갖는다**: 키워드만 맞고 NLI가 낮으면 결과가 낮다. `max(keyword, 0.6)` 하한 덕분에 키워드가 0이어도 NLI 단독 기여(×0.6)는 남는다.
- 키워드 점수 자체는 단순 비율: `classify_risk_types` → `해당 패턴이 걸린 기사 수 / 전체 기사 수`.
- NLI 게이트: 키워드가 걸렸거나 `negative >= 0.55`인 기사만 NLI를 태운다(비용 절약). 배치 상한 24건, `max_length=256`.

### 23-5. 그 밖의 손튜닝 상수

| 값 | 위치 | 의미 |
|---|---|---|
| `engagement = like + 2×reply` | `evidence.py::_engagement` | 확산 규모 대리 지표. 답글을 좋아요의 2배로 침(답글이 더 강한 관여) |
| 출처 신뢰도 0.95/0.65/0.40/0.35 | `story_risk.py::source_credibility` | 정부(.go.kr) 0.95 / 일반 0.65 / 블로그 0.40 / YouTube·미상 0.35 |
| 규칙 관련성 0.96/0.84/0.82/0.68 (+0.05) | `article_filtering.py::_rule_relevance` | 기업명 제목/요약, 제품명 제목/요약. 위험 키워드 동반 시 +0.05 |
| tier 매트릭스 | `tier.py::_MATRIX` | 확률 3구간 × 민감도 3단 → 9칸 룩업 (수식 아님, 정책 표) |
| `confidence = min(세부, 상단)` | `classify.py::_wrap` | 확신도 하향 승계 |
| 3-3-4 / 4-3-3 규칙 | — | 해당 없음 |

## 24. 텍스트 유사도 — 집합 계열 4종의 가중 합

### 24-1. 기본 4개 지표 — [`story_clustering.py`](../backend/app/services/story_clustering.py#L136)

| 지표 | 수식 | 특성 |
|---|---|---|
| **Containment** | `\|A ∩ B\| / min(\|A\|, \|B\|)` | 길이 차이에 관대 — 짧은 속보와 긴 해설 기사를 매칭 |
| **Jaccard** | `\|A ∩ B\| / \|A ∪ B\|` | 표준 집합 유사도. 길이 차이에 엄격 |
| **Dice** | `2\|A ∩ B\| / (\|A\| + \|B\|)` | Jaccard보다 완만 (`D = 2J/(1+J)`, 항상 J 이상) |
| **SequenceMatcher.ratio** | `2M / T` (M=매칭 문자 수, T=전체 길이) | Ratcliff/Obershelp — **어순**을 본다. 집합 지표가 못 보는 순서 정보 |

### 24-2. 조합 — [`story_similarity`](../backend/app/services/story_clustering.py#L164)

```python
title_score = max( 0.52×containment + 0.48×dice(char_3gram),
                   0.55×sequence_ratio + 0.45×jaccard )      # 두 갈래 중 좋은 쪽
body_score  = max( 0.55×containment + 0.45×jaccard,
                   dice(char_3gram) )
final       = min(1.0, 0.78×title_score + 0.22×body_score)
```

- **`max`로 두 조합을 경쟁시키는 구조**가 특징이다. 한쪽은 "어휘 겹침"(containment+dice), 다른 쪽은 "표현 유사"(sequence+jaccard)를 본다. 어느 한 방식으로 잡히면 통과.
- **제목 0.78 : 요약 0.22** — 제목이 사안을 규정한다는 이 시스템 전반의 전제와 일치한다(분류 프롬프트의 `event_title` 우선 규정과 같은 철학).
- 제목이 문자 단위로 완전히 같으면 즉시 1.0으로 단락(short-circuit).

### 24-3. 최종 매칭 판정 — [`match_story_articles`](../backend/app/services/story_clustering.py#L238)

**하나의 임계값이 아니라 다중 조건 OR**이다. 옛 "제목 유사도 0.72" 단일 컷을 대체한 것.

```
최근 7일 이내:
  lexical >= 0.66
  or (lexical >= 0.43 and 고신호개념 >= 1 and 개념가중 >= 1.8)
  or (lexical >= 0.30 and 고신호개념 >= 2 and 개념가중 >= 3.0)
  or (semantic >= 0.80 and lexical >= 0.34 and (고신호 >= 1 or 공통제목어 >= 2))
  or (semantic >= 0.58 and lexical >= 0.28 and 고신호 >= 2 and 개념가중 >= 3.0)
  or (semantic >= 0.58 and lexical >= 0.15 and 고신호 >= 3 and 개념가중 >= 4.0)

7~30일 후속 보도 (더 엄격):
  lexical >= 0.78
  or (semantic >= 0.82 and lexical >= 0.30 and 고신호 >= 2 and 개념가중 >= 3.0)
```

- **어휘 유사도가 낮을수록 더 많은 개념 증거를 요구**하는 사다리 구조다. 임베딩만 믿지도, 어휘만 믿지도 않는다.
- `개념가중 = Σ min(left[k], right[k])` — 공통 개념의 **가중치 교집합**(fuzzy set intersection). 행위·기관 패턴은 높은 가중치, 날짜/수치 사실은 0.35.
- 보고용 점수는 별도 공식: `score = min(1.0, 0.60×lexical + 0.30×max(0,semantic) + 0.10×min(1, 개념가중/5))`
  **매칭 판정과 표시 점수가 다른 수식**이라는 점에 주의. 판정은 위의 OR 사다리, 점수는 이 가중합이다.
- 임베딩 입력은 제목을 **두 번** 반복해 가중치를 준다: `f"{title}. {title}. {summary[:1200]}"` ([`story_article_text`](../backend/app/services/story_clustering.py#L321))

## 25. 신경망 추론부의 수식

### 25-1. NLI 함의 확률 — [`klue_nli.py::score_hypotheses`](../backend/app/services/klue_nli.py)

제로샷 분류의 표준 기법이다.

```
각 (전제, 가설_i) 쌍을 NLI 모델에 넣어 entailment 로짓만 뽑는다
   → logits[:, entailment_id]
그룹 내 가설들의 entailment 로짓에 softmax
   → softmax(logit_1, ..., logit_k)  = 가설별 확률
```

- **핵심**: 3-class(entailment/neutral/contradiction) softmax가 아니라, **여러 가설의 entailment 로짓끼리** softmax를 건다. 그래서 "이 문서가 어느 가설에 가장 부합하는가"의 확률분포가 나온다.
- 위험 유형 분류에서는 8개 유형 가설 + **"이 글은 기업 위험 사건과 관련이 없다"** 라는 널 가설(null hypothesis)을 하나 더 넣는다. 널 가설이 없으면 확률이 8개 유형에 강제 배분되어 무관한 글도 어딘가에 높은 점수가 붙는다.
- `label2id`에서 entailment 인덱스를 **찾아서** 쓴다(하드코딩 안 함). KLUE-RoBERTa는 `type_vocab_size=1`이라 `token_type_ids`를 제거하는 처리도 들어 있다.

### 25-2. 문장 임베딩 — [`article_filtering.py::LocalSemanticScorer`](../backend/app/services/article_filtering.py#L218)

```python
hidden  = model(**encoded).last_hidden_state            # (B, L, H)
mask    = attention_mask.unsqueeze(-1).expand(...)
vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)   # mean pooling
vectors = F.normalize(vectors, p=2, dim=1)                        # L2 정규화
score   = vectors[1:] @ vectors[0]                                # 코사인 = 정규화 후 내적
```

- **attention mask를 곱한 평균 풀링**: 패딩 토큰을 평균에서 제외한다. 이걸 빼먹으면 짧은 문장의 임베딩이 패딩에 희석된다. `sentence-transformers`의 기본 풀링과 동일한 수식을 직접 구현한 것.
- L2 정규화 후 내적 = 코사인 유사도. `‖a‖=‖b‖=1`이면 `a·b = cos θ`.
- 결과를 `[-1, 1]`로 클립.

### 25-3. RAG 검색 — [`rag/store.py::VectorStore.search`](../backend/app/services/response_engine/rag/store.py)

```python
self.vectors = vectors / clip(‖vectors‖, 1e-9, None)   # 색인 시 1회 정규화
q      = query / max(‖query‖, 1e-9)
scores = self.vectors @ q                               # 내적 한 번 = 전체 코사인
scores[~mask] = -1.0                                    # 유형 필터 밖은 배제
```

**2단 임계값**이 이 코드의 고유한 부분이다:

```python
picked = [상위 top_k 중 score >= min_score(0.30)]
cutoff = picked[0][1] × relative(0.92)      # 1위 점수의 92%
return [1위의 92% 이상인 것만]
```

- **왜 절대 임계값만으로는 안 되는가** (주석의 실측): 국내 자료는 한국어 질의와 코사인 0.6대가 나오지만, 영어 원문·번역본은 같은 적합도에서도 0.37 수준이다(R04 0.62 / R13 0.37). 절대값 하나로 자르면 **영어 자료 유형은 보충이 통째로 사라진다.**
- `relative=0.92`는 **유형별 점수 분포에 자동으로 적응하는 상대 컷**이다. 1위만 적합하고 2·3위가 어긋난 경우(실측 R03) 그 둘을 떨어뜨린다.
- 미리 정규화해 두므로 검색이 **행렬-벡터 곱 한 번**으로 끝난다(3,847 × 1536 내적).

### 25-4. Reranker 점수 구간 재매핑 — [`article_filtering.py::_calibrated_reranker_score`](../backend/app/services/article_filtering.py#L424)

모델마다 다른 임계값을 필터의 고정 밴드(0.30 / 0.70)로 옮기는 **구간별 선형 사상(piecewise linear map)**이다.

```
s <= reject         →  0.30 × s / reject
reject < s < accept →  0.30 + 0.40 × (s − reject) / (accept − reject)
s >= accept         →  0.70 + 0.30 × (s − accept) / (1 − accept)
```

- 모델의 `reject_threshold`·`accept_threshold`가 어디에 있든, **결과는 항상 `reject → 0.30`, `accept → 0.70`에 정확히 대응**한다.
- 단조증가이고 구간 경계에서 연속이다. 순위는 보존하면서 **의사결정 경계만 표준 위치로 옮기는** 캘리브레이션.
- 덕분에 리랭커를 교체해도 `article_filter_relevance_accept_threshold=0.70` 같은 운영 설정을 그대로 쓸 수 있다.

## 26. "가중치"의 소재 정리

| 종류 | 어디에 있나 | 버전 관리 |
|---|---|---|
| KLUE-RoBERTa / BGE 신경망 가중치 | `exports/local_models/**` (약 1.3GB, safetensors) | git 제외, 팀 Drive `local_models.tar.gz` |
| IF 트리 · LightGBM 부스터 · TF-IDF 어휘 · SVD 성분 · RobustScaler 중심/스케일 | `exports/model_artifacts/*.joblib` | git 제외, `model_versions` 테이블에 등록 + SHA256 |
| 기업별 스케일러(`company_scalers`) · IF 참조 점수 분포(`training_scores`) · LightGBM 참조 확률(`reference_probabilities`) · 임계값(`per_company`/`global`) | **같은 joblib 안에 함께 직렬화** | 아티팩트와 원자적으로 이동 |
| 선형 결합 가중치(0.20/0.70/0.10, 0.78/0.22, 0.52/0.48 …) · 임계값 사다리 · tier 매트릭스 | **소스 코드 상수** | git |
| 대응 원칙 텍스트 · 법령 매핑 | `principles_data.json`(45KB), `regulations_data.json`(137KB) | git |
| RAG 임베딩 | `rag/index/vectors.npy` (3,847 × 1536) | git (약 28MB) |

**아티팩트 무결성**: 스토리 모델은 `STORY_RISK_MODEL_SHA256`으로 해시를 고정 검증하고, 불일치하면 **새 판정을 중단하고 이전 점수를 보존한다.** `predict_stories`에도 *"신뢰할 수 있는 로컬 아티팩트만 로드하고 신뢰할 수 없는 joblib은 절대 로드하지 않는다"* 는 주석이 달려 있다(joblib은 pickle 기반이라 임의 코드 실행 위험).

## 27. 통계적으로 눈여겨볼 지점

1. **`type_probability`가 사실상 무정보**다(단독 AUC 0.500, 표준편차 0). 가중치를 0.20까지 낮췄지만 **여전히 피처로 남아 있다.** 이 항목을 살리려면 후보 게이트(`>=0.35`)와의 순환 구조부터 끊어야 한다.
2. **사람 라벨 55건으로 튜닝한 가중치**다(`story_v2_label_candidates.csv`). 그리드서치 상위권이 AUC 0.90~0.91로 평평했다는 건 **이 표본 크기에서 세밀한 가중치 차이는 통계적으로 구분되지 않는다**는 뜻이다. 라벨이 늘면 재탐색이 필요하다.
3. **F2(창) vs F1(스토리)** 임계값 기준이 서로 다르다. 의도된 것인지 확인해 볼 값어치가 있다 — 두 모델이 같은 운영 파이프라인의 앞뒤에 있는데 재현율 선호도가 다르다.
4. **IF 백분위와 raw 점수를 둘 다** LightGBM에 넣는다. 단조 변환 관계라 트리 모델에서는 원칙적으로 중복이지만(트리는 단조 변환에 불변), 분할 지점 탐색의 해상도가 달라져 실제로는 미묘한 차이가 난다. `feature_importance(gain)`로 확인 가능.
5. **스토리 군집의 매칭 조건이 6갈래 OR**이라 어느 조건으로 붙었는지 로그가 없으면 디버깅이 어렵다. `StoryMatch`가 `lexical_similarity`, `semantic_similarity`, `shared_concepts`를 반환하므로 이걸 저장해 두면 조건별 기여도를 사후 분석할 수 있다.
6. **경험적 CDF는 학습 시점 분포에 고정**된다. 수집량이 크게 늘거나 기업이 추가되면 `reference_probabilities`가 실제 모집단과 어긋난다 — 재학습 없이 오래 쓰면 백분위가 서서히 왜곡된다.
