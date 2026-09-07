# 위험판정 공식 개발 로그 (2026-09-07)

`wongyeong` 브랜치에서 진행. 사람이 읽기 쉬운 버전은 [`docs/2026-09-07-signal-and-noise.html`](2026-09-07-signal-and-noise.html)(팀 공유용,
비전공자 대상 설명 포함)을 보시고, 이 문서는 이어서 작업할 엔지니어를 위한 기술 기록입니다.

## 요약

| # | 항목 | 상태 |
|---|---|---|
| 1 | window_v1 실시간 이벤트 생성 확인 | ✅ 이미 팀이 `story_risk_engine_enabled`로 꺼둔 상태였음 (조치 불필요) |
| 2 | 실시간 스토리 군집화에 문장 임베딩 모델 연결 | ✅ 완료 |
| 3 | window_v1 ↔ story_v2 신호 블렌드 검증 | ✅ 검증 후 껐음 (w=0.2 → 0.0) |
| 4 | 모델 승격이 재시작마다 초기화되는 버그 | ✅ 수정 — 단, 재시작 경쟁 조건 남아있음 (아래 "주의" 참고) |
| 5 | 위험유형(8종) 키워드 충돌 | ✅ 2건 수정, 4/9건 해소 |
| 6 | story_v2 확률식 가중치 재조정 | ✅ 완료 |
| 7 | LLM 대응전략에 정량 근거(attribution) 실제 연결 | ✅ 완료 |
| 8 | LightGBM 재학습 (stratified k-fold) | ✅ 완료, 신규 후보 승격 |

---

## 1. window_v1 vs story_v2 — 두 판정 엔진이 공존하는 구조 확인

기사를 15분 단위로 모아 보는 예전 방식(`window_v1`, `risk_analysis.py::score_window`)과 사건 단위로 묶어 보는
현재 방식(`story_v2`, `story_risk.py::_aggregate_story_event`)이 동시에 존재합니다. 실사용자가 보는 위험 경보는
전부 `story_v2`이고 `window_v1`은 현재 새 이벤트를 거의 안 만듭니다(전체 6,595건 중 524건, 최근 활성 사건은
0건). `monitoring_pipeline.py`가 이미 `update_events=not settings.story_risk_engine_enabled`로 이 부분을
꺼둔 상태였습니다 — 새로 만든 게 아니라 기존 팀 작업을 확인만 한 것입니다.

다만 window 자체의 스코어링(`score_window`, IsolationForest+LightGBM)은 계속 돌고 있고, `CompanyFeatureWindow`에
그 결과가 저장됩니다. 이게 이번 세션 후반부(7번 항목)에서 다시 쓰였습니다.

## 2. 실시간 스토리 군집화에 문장 임베딩 모델 연결

`story_clustering.py::assign_story_cluster`(실시간 경로, `monitoring_pipeline.py`에서 신규 기사마다 호출)는
`semantic_scorer` 인자가 항상 `None` 기본값으로 들어와서, 실시간 군집화가 어휘 유사도(Jaccard/Dice/SequenceMatcher)와
하드코딩된 한글 개념 사전만으로 이뤄지고 있었습니다. `match_story_articles`에 임베딩 유사도 조건이 이미 있는데도
실시간에서는 한 번도 안 쓰이고 있었던 것입니다.

`recluster_story_articles`(배치/CLI 재군집 경로)는 이미 `semantic_scorer is None and settings.article_filter_ai_enabled`일
때 스스로 `get_semantic_scorer(...)`를 만들어 쓰고 있었는데, 이 배치 경로는 CLI 수동 실행이나 기업명 변경 시에만
돌아서 상시 루프에는 연결이 안 돼 있었습니다.

**수정**: `assign_story_cluster`에도 같은 자동 생성 로직을 추가 ([story_clustering.py:391-405](../backend/app/services/story_clustering.py)).
검증: 임베딩 모델이 실제로 로드되고, "쿠팡 물류센터 화재" ↔ "쿠팡 물류센터에서 큰 불이 났다" 유사도 0.93,
↔ "오늘 날씨는 맑습니다" 유사도 0.43으로 정상 동작 확인.

## 3. window_v1 ↔ story_v2 신호 블렌드 — 검증 후 폐기

**1차 시도**: window_v1의 LightGBM 신호를 story_v2 확률에 섞으면 도움이 될지 확인. 사람 라벨 82건(전부
`event_source='window_v1'`)으로 재구성 검증했을 때는 블렌드가 도움이 되는 것처럼 보였음(AUC 0.773→0.848).

**문제 발견**: 이 82건이 전부 window_v1 소속이라 story_v2 자체의 정확도는 검증한 적이 없었음. `risk_probability`
원점수는 이미 알람 뜬 창들에서만 보면 0.86+에 몰려 있어(p25=0.86), 그대로 블렌드하면 도움이 안 됨 → 전체
스코어링된 창(~8,500개, 대부분 평온) 대비 percentile로 정규화(`risk_analysis.py::risk_detector_percentile`,
학습 시 저장한 `reference_probabilities`와 비교)해야 의미가 생김. 5-fold 교차검증으로 w=0.2가 최적 근방임을
확인하고 우선 반영.

**최종 재검증**: `story_v2_label_candidates.csv`(117건, story_v2 사건 전용, 이번에 처음 확보한 사람 라벨)로
다시 보니 **블렌드가 오히려 정확도를 낮춤** (AUC 0.8343 → 0.8196, story_v2 단독이 더 나음). 표본이 작아서
(양성 25건) 확정적이진 않지만 방향이 뒤집혀서 `WINDOW_SIGNAL_BLEND_WEIGHT = 0.0`으로 되돌리고 짧은 회로
가드를 추가([story_risk.py:51](../backend/app/services/story_risk.py), [:609](../backend/app/services/story_risk.py))했습니다
— 기본값에서는 `resolve_production_risk_detector` 자체를 호출하지 않습니다.

관련 테스트: `test_window_signal_blends_into_story_probability`(가중치를 `@patch`로 강제로 켜서 메커니즘 자체는
계속 검증), `test_window_signal_is_a_noop_at_the_current_zero_weight`(현재 기본값에서 detector가 호출조차
안 되는지 확인).

## 4. 모델 승격이 재시작마다 초기화되는 버그

`risk_analysis.py::import_exported_models`(앱 시작 시 1회 실행, `main.py` lifespan)가 `exports/model_artifacts/`의
하드코딩된 파일(`risk-lgbm-20260826T034203Z.joblib` 등)을 매번 무조건 production으로 재등록하면서, 그 전에
있던 다른 production 모델을 전부 retired로 돌려버리고 있었습니다. 새 모델을 승격해도 다음 재시작(개발 중이면
핫리로드) 때 원래대로 돌아갔습니다(실측: 승격 후 40초 만에 원상복구).

**수정**: 해당 태스크에 이미 production이 있으면(자기 자신이 아닌 한) 건드리지 않도록 변경
([risk_analysis.py:446-470](../backend/app/services/risk_analysis.py)).

**⚠️ 남아있는 주의사항**: 수정 후에도, 개발 중 **연속으로 빠르게 여러 파일을 저장할 때** 핫리로드 사이클이
DB 커밋과 경합하면서 간헐적으로 다시 되돌아가는 게 관찰됐습니다(재현: 파일 수정 없이 두면 승격이 유지되지만,
바로 다음 핫리로드에서 다시 `lightgbm_auto_v3`로 돌아간 사례 있음 — 정확한 재현 조건은 못 찾음). 원인 후보:
① uvicorn `--reload`가 겹쳐 도는 타이밍 이슈, ② **이 DB는 팀 공유 외부 DB라, 다른 팀원이 자신의 로컬
백엔드를 이 코드 수정 없이 그 DB에 붙여 재시작하면 그쪽 프로세스가 옛 로직으로 되돌릴 수 있음**(가능성 높음
— 이 브랜치가 아직 master에 병합 안 됐으므로). **이 브랜치가 병합되기 전까지는, risk_detector production
모델이 의도한 것인지 재확인 후 배포/시연하세요.**

## 5. 위험유형(8종) 키워드 충돌

`story_v2_label_candidates.csv`의 25건(is_risk=TRUE) 중 9건이 8종 위험유형(`primary_type`) 오분류. 그중 4건이
같은 원인 2가지로 설명됨:

- `financial_governance`의 `"감사"` → "국정감사"/"국감"(국회 청문)까지 매칭 → `"회계감사"`로 좁힘
- `product_quality`의 `"품질"` → "배달 품질"/"서비스 품질"(reputation_consumer 소관)까지 매칭 → 제거,
  실제 결함 표현(리콜/불량/결함/하자/오작동)만 남김

수정: [risk_analysis.py:43-56](../backend/app/services/risk_analysis.py). 나머지 5건은 복합 주제 기사를
"주 유형 하나"로 압축하는 데서 오는 판단 차이(버그 아님). 1건(사건 3814)은 2026-09-03에 이미 고쳐진 제휴
탐지 로직 도입 이전(09-01)에 필터링된 낡은 데이터였음(재발 안 함, 조치 불필요).

## 6. story_v2 확률식 가중치 재조정

`_local_assessment_from_scores`의 공식이 `0.45×위험유형 + 0.35×감성부정 + 0.20×관련성`이었는데, 라벨 55건
(근거 데이터가 남아있는 부분집합, 양성 9)으로 세 성분을 분리 측정:

| 성분 | 옛 가중치 | 단독 AUC |
|---|---|---|
| 위험유형 확신도 | 45% | **0.500** (표준편차 0 — 완전 무정보) |
| 감성 부정확률 | 35% | 0.900 |
| 관련성 점수 | 20% | 0.615 |

원인: 위험 후보가 되려면 이미 `type_probability>=0.35`가 필요하고, 키워드 매칭(`enrich_risk_types_with_nli`의
`nli_score * max(keyword_score, 0.6)`)이 일단 걸리면 거의 항상 1.0 근처로 포화되어, 후보가 된 시점엔 진짜
위험이든 아니든 이 항목이 똑같이 만점.

그리드서치(합 1.0, 0.1 단위) 상위권(type 0.0~0.3 / negative 0.6~0.9 / relevance 0.1, AUC 0.90~0.91) 중
`type=0.20 / negative=0.70 / relevance=0.10`로 반영 ([story_risk.py:149-165](../backend/app/services/story_risk.py)).
결과: AUC 0.894→0.906, 임계값 0.65 기준 정밀도 16.4%→33.3%(재현율 100% 유지).

## 7. LLM 대응전략에 정량 근거(attribution) 실제 연결

`response_engine`의 `Evidence.attribution`(정량 근거 — "어떤 지표가 현재값·기준값과 함께")은 데이터 구조와
프롬프트 렌더링(`generate.py::build_user_prompt`)까지 다 있었는데, 실제로 채워 넣는 코드가 없어서 항상
빈 리스트였습니다. IsolationForest·LightGBM·감성분석 모델이 실제로 낸 숫자가 LLM에 하나도 전달되지
않고 있었습니다.

**수정**: `response_engine/service.py::_attribution_from_window` 신규 함수 ([:96-146](../backend/app/services/response_engine/service.py)).
`RiskEvent.feature_window_id`로 연결된 `CompanyFeatureWindow`에서:

- `anomaly_score` / `anomaly_percentile` — IsolationForest. window에 이미 저장된 값을 그대로 옮김(재계산 안 함)
- `risk_probability`(LightGBM) — 마찬가지로 이미 저장된 값을 옮기고, `percentile`만 `risk_detector_percentile`로
  새로 계산(모집단 대비 순위, 모델 재실행 없음)
- `negative_probability`(창 단위 감성 집계) — 같은 기업의 직전 7일 평균과 비교(새로 쿼리)

**주의해서 뺀 것**: 처음에 LightGBM 항목에 `baseline=decision_threshold`(결정 임계값)를 넣었는데,
프롬프트 템플릿이 `baseline`을 무조건 "직전 N일 평균"으로 렌더링해서 "지난 7일 평균이 0.65였다"는
**사실과 다른 문장**이 만들어짐. `percentile`만 남기고 제거함 ([service.py:119-130](../backend/app/services/response_engine/service.py)).

`generate.py::_FEATURE_LABELS`에 `lightgbm_risk_probability`, `negative_probability` 한글 라벨 추가.

**검증** (실제 사건으로 프롬프트 렌더링까지 확인):
```
[정량 근거]
- 평소와 다른 정도(이상 점수): 현재 -0.02179 / 과거 이력에서 상위 99.1% 수준
- 위험 탐지 모델이 낸 확률: 현재 0.02589 / 과거 이력에서 상위 99.5% 수준
```

`cases`(과거 유사 사례)는 여전히 비어있습니다 — 사례 DB 자체가 없어서 이번 범위 밖(별도 작업 필요).

## 8. LightGBM 재학습 (참고, 이전 커밋에서 이미 완료)

`StratifiedGroupKFold`(k=5)로 재학습, `reference_probabilities`(전체 창 모집단 대비 확률 분포)를 아티팩트에
새로 저장(7번 항목의 percentile 계산에 사용). oof_recall 0.871, oof_roc_auc 0.870. 신규 후보
`risk-lgbm-20260907T022507Z`(model_versions id=278)를 production으로 승격.

---

## 오늘 변경된 파일

```
backend/app/services/story_clustering.py     (실시간 군집화에 임베딩 스코어러 연결)
backend/app/services/story_risk.py           (블렌드 끔, 확률식 가중치 재조정, 테스트 2건 추가)
backend/app/services/risk_analysis.py        (승격 버그 수정, 위험유형 키워드 2건 수정)
backend/app/services/response_engine/service.py   (attribution 실제 연결)
backend/app/services/response_engine/generate.py  (신규 피처 한글 라벨)
backend/app/training/risk_models.py          (reference_probabilities 저장)
backend/tests/test_story_risk.py             (블렌드 테스트 2건)
backend/training_data/story_v2_label_candidates.csv  (사람 라벨 117건, 완료분)
backend/training_data/build_story_v2_label_candidates.py  (라벨 후보 추출 스크립트)
docs/2026-09-07-signal-and-noise.html        (팀 공유용 비전공자 설명 문서)
```

기존 테스트 전체(165개) 통과.

## 팀에게 필요한 액션

1. **이 브랜치를 최대한 빨리 master에 병합해주세요** — 4번 항목의 승격 버그 수정이 이 브랜치에만 있어서,
   병합 전까지는 누구든 옛 코드로 백엔드를 재시작하면(공유 DB에 붙여서) 모델 승격이 되돌아갈 수 있습니다.
2. **`exports/model_artifacts/risk-lgbm-20260907T022507Z.joblib`를 팀 Drive에 공유해주세요** — git에는
   안 올라가는 파일이라 각자 로컬에 받아야 합니다. 아래 "모델 파일 공유" 참고.
3. **story_v2 라벨을 더 모아주세요** — 지금 117건(양성 25)으로는 6번 항목의 가중치나 3번 항목의 블렌드
   결론을 확정 짓기엔 표본이 작습니다.
4. **`cases`(과거 유사 사례) 데이터베이스 착수 여부 논의** — 지금 대응전략 생성에 안 쓰이고 있는 마지막
   설계-only 항목입니다.

## 모델 파일 공유

`exports/`는 git에 안 올라갑니다(`.gitignore`). 오늘 새로 만든 모델 파일 하나만 늘었습니다:

```
exports/model_artifacts/risk-lgbm-20260907T022507Z.joblib   (약 350KB)
```

기존 `model_artifacts.tar.gz`(3GB, 팀 Drive에 이미 있는 것)를 통째로 다시 만들 필요는 없습니다 — 이 파일
하나만 같은 위치(Drive의 `model_artifacts` 폴더)에 추가로 올려두고, 팀원들에게 "`exports/model_artifacts/`에
이 파일만 추가로 받아서 넣으면 된다"고 안내하면 됩니다. README `팀원 온보딩` 섹션에도 이 내용을 추가해뒀습니다.
