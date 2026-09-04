# 모델 성능 점검 개발 로그 (2026-09-04)

이번 세션에서 위험 판정·대응전략 생성 품질을 점검하고, 우선순위대로 고칠 수 있는 부분을 고쳤습니다.
아래는 항목별로 무엇을 발견했고, 무엇을 고쳤고, 무엇이 남았는지 정리한 내용입니다. 이어서 작업할
팀원은 "남은 일" 섹션부터 보시면 됩니다.

## 요약

| # | 항목 | 상태 |
|---|---|---|
| 1 | LightGBM 위험판정 모델 재검증 | ✅ 재학습·검증 완료 — **원인 재진단됨**(과적합 아님, 검수 데이터 부족) |
| 2 | 드리프트 경고 원인 조사 (기업 8, 9) | ✅ 원인 파악 완료 — 재검토 필요 항목 발견 |
| 3 | 검수 데이터 확보 | ✅ 죽어있던 검수 화면을 연결 완료, 실사용은 진행 중 |
| 4 | 대응초안 승인/반려 실사용 유도 | ⏭️ 이번 라운드에서 제외 (요청에 따라 스킵) |
| 5 | 법령 기한 매핑표 채우기 | ⚠️ **재진단**: 이미 잘 구축되어 있음. 실제 문제는 다른 곳 |
| 6 | `case_records` 채우기 | ❌ 미착수 — 별도 세션 필요한 규모의 작업 |
| 7 | reranker 회귀 테스트 2개 수정 | ✅ 완료 |
| - | (발견) company_reranker 마운트 경로 문제 | ✅ 긴급 수정 완료 |

---

## 0. (긴급, 세션 중 발견) company_reranker가 11시간 넘게 죽어있었음

**원인**: 어제(9/3) 모델 공유 파일을 `exports/model_artifacts/`에 풀어 넣었는데, 오늘(9/4) pull로
`compose.yaml`의 마운트 경로가 이 폴더로 바뀌면서 **9/3에 새로 학습된
`company-reranker-20260903T013454Z` 아티팩트를 덮어씌워 버렸습니다.** 그 결과 백엔드 컨테이너가
재기동된 9/4 새벽부터 지금까지, 전체 관련성 판정이 조용히 예전 규칙/NLI 폴백으로 돌아가고
있었습니다(`article_filter_results.classifier_kind`에 `company_cross_encoder_reranker`가 9/4
03:07 이후로 0건).

**조치**: 아티팩트를 `exports/model_artifacts/company-reranker-20260903T013454Z`로 복사해서
복구. 직접 스코어링 테스트(`predict_company_relevance`)로 정상 작동 확인.

**교훈**: `exports/` 폴더로 모델을 공유할 때, 그 폴더가 이제 실제 운영 마운트 경로이기도 하다는 걸
잊으면 안 됩니다. 앞으로 모델 아티팩트를 공유/반입할 때는 `docker compose exec backend ls
/app/model_artifacts`로 컨테이너 안에서 실제로 보이는지 먼저 확인하세요.

---

## 1. LightGBM 위험판정 모델 재검증

**한 일**: `docker compose exec backend python -m app.training.cli risk`로 새 후보 모델을 학습
(CPU만으로 가능, GPU 불필요). `model_versions` id=241, version `risk-lgbm-20260904T052405Z`로
등록됨(자동 승격 안 됨, `provisional` 상태로 대기 중).

**처음 발견**(지난 대화에서 보고): 운영 중인 모델과 동일한 아티팩트가 다른 버전 등록(폐기됨)에
남긴 지표를 보니 `train_recall=0.996`인데 `validation_recall=0.0, test_recall=0.0`이라 "과적합
확정"이라고 보고했습니다.

**⚠️ 정정**: 새로 학습해서 직접 분할 구성을 확인해보니, **validation/test 분할에 위험(positive)
사건이 단 한 건도 없었습니다** (`validation: pos=0/187`, `test: pos=0/198`). recall/PR-AUC 계산
코드가 분할에 positive가 없으면 수학적으로 0.0을 반환하는데, 이게 "모델이 하나도 못 잡음"과
구분이 안 됩니다. 즉 **과적합이 확정된 게 아니라, 최근 확정 라벨이 너무 적어서 검증 자체가
불가능한 상태**입니다. 지난 보고를 정정합니다 — 죄송합니다.

**진짜 원인**: `_labeled_windows()`(`backend/app/training/risk_models.py:151`)가 학습 데이터를
`RiskEventLabel`(사람이 확정한 라벨) 기준으로만 뽑는데, 확정 라벨이 전체 104건뿐이고 최근
것일수록 더 적습니다. → **이건 3번 항목(검수 데이터 확보)과 직접 연결됩니다.** 검수를 더 많이
할수록 이 문제가 자연히 풀립니다.

**추가로 발견한 코드 개선점** (아직 안 고침): `train_risk_detector`의 metrics 계산 부분
(`risk_models.py:298-320`)이 분할에 positive가 0건일 때 `n_positive`를 같이 기록하지 않습니다.
`recall=0.0`이 "진짜 실패"인지 "애초에 평가 불가"인지 구분할 수 있게, 각 지표 옆에
`{split}_positive_count`도 같이 저장하도록 고치면 이런 오해를 앞으로 막을 수 있습니다.

**남은 일**:
- 확정 라벨이 최소 수십 건 이상 쌓이면 재학습 → 이번엔 validation/test에 진짜 positive가 섞여서
  의미 있는 recall/PR-AUC가 나올 것입니다.
- 위 metrics 코드에 `n_positive` 기록 추가 (작은 개선, 30분 이내 작업).
- 새 후보(id=241)는 지금 provisional 상태로 대기 중 — 운영 승격 여부는 사람이 판단해야 합니다
  (운영 관리 화면에서 "운영 승격" 버튼).

---

## 2. 드리프트 경고 원인 (기업 8=네이버, 9=쿠팡)

**발견**: `model_operation_checks` 최근 점검에 두 기업 모두
`negative_probability_robust_z` 드리프트 경고(임계값 3.5의 2~3.5배: 쿠팡 12.2, 네이버 7.6).

**원인 조사**: 실제 최근 48시간 부정감성 확률(`negative_probability`)은 절대값 기준으로는
0.15~0.32 정도로 그렇게 극단적이지 않았습니다. 그런데 드리프트 체크의 `recent_median`이 두
기업 모두 **정확히 20.0**(클리핑 상한)에 붙어 있었습니다 — 이는 "이 값이 그 회사의 과거
기준선(중앙값·MAD) 대비 압도적으로 벗어나 있다"는 뜻입니다. 즉 두 회사는 **역사적으로
부정감성 변동폭(MAD)이 매우 좁았는데**, 최근 실제로 어느 정도 부정 기사가 늘면서 상대적으로
극단적인 z-score가 찍힌 것으로 보입니다.

**왜 중요한가**: `negative_probability_robust_z`는 LightGBM/이상치 탐지 피처
(`BASE_FEATURE_NAMES`)에 실제로 들어갑니다. 이게 계속 클리핑 상한에 붙어 있으면, 이 두 기업은
당분간 이상치/위험 점수가 구조적으로 부풀려질 가능성이 있습니다 — **최근 쿠팡·네이버 위험 판정을
볼 때 이 점을 감안해서 보셔야 합니다.**

**남은 일**: 기준선(baseline) 계산 기간이 너무 좁거나 오래된 게 아닌지 확인 필요
(`model_operation_checks`의 baseline_start가 8/26로, 최근 8일치만 기준선으로 쓰고 있음 — 기업
성격상 원래 변동이 적은 기업이면 기준선 기간을 늘리는 게 나을 수 있습니다). 이건 도메인 판단이
필요해서 팀 논의를 권합니다.

---

## 3. 검수 데이터 확보

**발견**: 위험 사건 확정 라벨이 6,595건 중 103건(1.6%)뿐이었던 이유 — **검수 화면
(`frontend/src/features/analysis/AnalysisManagementPage.jsx`)이 이미 완성돼 있었는데 어느 메뉴에도
연결이 안 되어 있었습니다**(죽은 코드).

**조치**: 관리자 메뉴에 "위험 사건 검수" 탭 추가(`frontend/src/features/app/WorkspaceApp.jsx`,
경로 `/admin/risk-review`). 실제 로그인해서 라벨 하나 제출해보고 DB 저장까지 확인했습니다.

**남은 일**: 이제부터는 팀에서 실제로 써야 쌓입니다. 우선순위를 두고 싶다면(예: 모델이 애매하게
판단한 사건 위주로 큐 정렬), `backend/app/routers/reviews.py:182`의
`risk_review_candidates`가 지금은 `opened_at.desc()`(최신순)로만 정렬합니다 — 여기에
"risk_probability가 임계값(0.65) 근처인 것 우선" 같은 정렬을 추가하면 검수 효율이 올라갈 겁니다.

---

## 5. 법령 기한 매핑표 — 재진단

**처음 판단**(지난 보고): "법령 기한 매핑표가 비어서 21.6% 실패"라고 보고했습니다.

**⚠️ 정정**: `regulations_data.json`(2,186줄)을 직접 열어보니 **이미 상당히 잘 구축되어
있었습니다** — R01~R13대까지, 조문 원문·시행일·verified 플래그·source_sha·적용 대상 주석까지
갖춘, 사람이 실제로 법조문을 대조 확인한 흔적이 있는 데이터입니다(예: "자본시장법 제161조 제1항"도
이미 `KR-R08-001`로 등록되어 있고 `verified: true`).

**진짜 원인**: 검증 실패 사례(초안 108, 113)를 다시 보니, 실패한 규정들이 **매핑표에 이미
있는데 LLM이 생성한 체크리스트에 반영을 안 한 것**이었습니다. 즉 이건 데이터 문제가 아니라
`response_engine`의 생성/프롬프트가 매핑표에 있는 필수 법령을 항상 빠짐없이 반영하도록
강제하지 못하는 **생성 신뢰성 문제**입니다.

**남은 일**: `backend/app/services/response_engine/generate.py`,
`backend/app/services/response_engine/service.py`에서 체크리스트 생성 프롬프트가 `필수 법령
목록(checklist_enforce=true인 것)`을 얼마나 강하게 지시하는지 확인 필요. 재생성 로직(검증
실패 시 1회 재시도)이 "빠진 법령을 반드시 추가하라"는 구체적 피드백을 주고 있는지도 확인해볼
가치가 있습니다. 이건 프롬프트 엔지니어링 작업이라 이번 세션에서는 손대지 않았습니다.

---

## 6. `case_records` (판례 RAG) — 미착수

이건 손대지 않았습니다. 이유:
- `backend/app/services/response_engine/retrieval.py` 상단 주석에 팀이 이미 "pgvector로 임베딩
  검색하는 CaseRetriever 구현"을 TODO로 명시해뒀습니다 — 새 DB 테이블, 임베딩 파이프라인, 검색
  통합까지 필요한 별도 규모의 작업입니다.
- 실제 판례/사례 내용을 제가 채워 넣는 건 하지 않는 게 맞다고 판단했습니다 — 지어낸 판례 데이터를
  법적 대응 초안에 근거로 쓰게 하는 건 위험합니다. 실제 데이터 소스(사내 축적 사례, 법률 DB
  구독 등)가 있어야 제대로 채울 수 있는 영역입니다.

**남은 일**: 별도 세션/스프린트로 스코프 잡는 걸 권합니다. `retrieval.py` 주석에 이미 설계
방향(pgvector 기반 CaseRetriever)이 적혀 있어 시작점은 있습니다.

---

## 7. reranker 관련 회귀 테스트 수정

`test_klue_nli_can_block_an_ambiguous_company_name`,
`test_local_relevance_model_receives_target_company`
(`backend/tests/test_article_filtering.py`) 둘 다 reranker가 없다고 가정하고 짠 테스트라, 0번
항목을 고쳐서 reranker가 다시 살아나니 실패로 바뀌었습니다. `classify_article`에 이미 있던
`precomputed_company_reranker=None` 옵션으로 명시적으로 폴백 경로만 타도록 고쳐서 통과시켰습니다.

---

## 오늘 변경된 파일

```
backend/app/services/risk_analysis.py       (지난 세션: 키워드/NLI 블렌드 공식 수정)
backend/app/services/story_risk.py          (지난 세션: severity 리셋 버그 수정)
backend/app/services/risk_ground_truth.py   (지난 세션: severity 리셋 버그 수정)
frontend/src/features/app/WorkspaceApp.jsx  (지난 세션: 위험 사건 검수 탭 연결)
backend/tests/test_article_filtering.py     (이번: reranker mock 경로 수정)
exports/model_artifacts/                    (이번: company-reranker 아티팩트 복구,
                                              risk-lgbm-20260904T052405Z 신규 후보 추가)
```

기존 테스트 전체(36개, story_risk/risk_events_db/risk_visibility/risk_event_page/
risk_ground_truth/article_filtering/company_reranker) 통과 확인.

## 팀에게 필요한 액션

1. **위험 사건 검수를 실제로 해주세요** (`/admin/risk-review`) — 1번·5번 항목 모두 결국 여기로
   귀결됩니다. 검수 데이터가 늘어야 재검증도, 재학습도 의미가 생깁니다.
2. **드리프트 경고 난 쿠팡/네이버 기준선을 검토해주세요** — 최근 위험 판정을 볼 때 감안 필요.
3. **`case_records`(판례 DB) 착수 여부를 논의해주세요** — 별도 스프린트급 작업입니다.
4. **response_engine 체크리스트 생성 프롬프트 검토** — 법령 매핑표는 이미 좋은데 생성이 못
   따라가는 상황입니다.
