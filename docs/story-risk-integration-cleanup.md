# 스토리 위험 모델 연결과 코드 정리

2026-09-07 기준. `STORY_RISK_ENGINE_ENABLED=true`와 `STORY_RISK_MODEL_ENABLED=true`이면 기업·스토리별 Isolation Forest + LightGBM 모델이 최종 위험 점수를 계산한다. 아래의 “대체”는 새 운영 경로에서 역할이 바뀌었다는 뜻이며, 기존 파일을 삭제했다는 뜻은 아니다.

선택한 학습 결과는 [전체 데이터 v2 모델 폴더](../exports/model_artifacts/story-risk-full-v2-20260907/)의 `story-if-lgbm-full-20260907T034115Z.joblib`이다. 이 파일은 별도로 학습한 IF, LightGBM, 전처리기를 함께 담은 추론 묶음이다.

## 실제 판정과 화면 반영

1. 기업에 연결된 기사 중 같은 기업·기사에 `accepted` 필터 결과가 존재하는 기사만 스토리 입력으로 사용한다. 입력 선택은 기존 학습 내보내기와 같다.
2. 텍스트와 기사 수·출처·감성 등 수치 특징을 만든다. IF 이상치 점수와 백분위도 LightGBM 입력에 포함한다.
3. `StoryRiskScore`에 위험 확률, IF 점수, 임계값, 모델 버전, 파일 해시, 입력 해시와 스냅샷을 저장한다. AI 학습 라벨과 운영 예측은 별도 데이터다.
4. LightGBM의 판정과 기존 최소 기사 수 조건으로 `RiskEvent`를 생성·갱신한다. 그래프와 위험 목록은 이 사건 데이터를 조회한다. 비위험 목록은 `StoryRiskScore`의 점수와 버전을 조회한다.
5. 새 모델이 비위험으로 판단한 기존 자동 사건은 삭제하지 않고 `legacy_candidate`, `story_model_non_risk`로 보존한다. 운영 집계에서는 제외한다. 사람이 확정한 판정과 수동 종료는 보호한다.

모델 파일 교체만으로 기존 DB 점수가 갱신되지는 않는다. 과거 데이터 반영은 [story_model_backfill.py](../backend/app/services/story_model_backfill.py)의 `reapply_story_model`이 수행한다. 이 함수는 변경 전 사건·점수·근거를 백업하고, 기존 데이터의 자동 대응 초안과 알림 재발송을 억제하는 절차를 포함한다.

비위험 목록도 현재 `accepted` 기사 두 건 이상이 있어야 노출된다. 아직 점수가 없는 스토리를 정상으로 간주하지 않는다. 운영 모델을 사용할 수 있으면 그 모델 버전의 점수만 비위험 목록에 표시한다. 모델을 불러올 수 없는 상황에서는 저장된 과거 결과와 실제 과거 버전을 유지하며, 기업·모델 상태는 `unavailable`로 표시한다.

사람이 비위험으로 확정한 사건은 모델 점수가 높아도 자동 위험 목록으로 복원하지 않는다. 따라서 화면의 운영 분류와 원시 모델 점수가 다를 수 있다. 이는 사람의 판정을 보존한 결과다.

## 현재 학습 계약과 해석 범위

- **최초 24시간, 최대 8건:** 최초 기사 저장 시각부터 24시간 안에 이용 가능했던 기사 중 빠른 순서로 최대 8건을 사용한다. 24시간이 지나면 모델 입력 시간 범위는 고정된다. 전체 데이터 학습은 모든 기업·스토리 조합을 활용했다는 의미이며, 스토리의 모든 기사 본문을 제한 없이 입력한다는 뜻은 아니다.
- **후속 기사:** 이후 기사는 사건의 근거, 마지막 기사 시각, 최소 기사 수, 활동 상태를 갱신한다. 최초 24시간 이후 새롭게 발생한 악화 내용을 모델 확률에 반영하려면 스냅샷 계약을 바꾸고 재학습해야 한다.
- **최소 기사 두 건:** 모델이 한 기사에서 위험을 예측해도 운영 사건과 목록은 기존 `STORY_EVENT_MIN_ARTICLES=2` 조건을 유지한다. 모델 예측 수와 화면 사건 수는 다를 수 있다.
- **확률과 피해 규모:** 이진 모델은 위험 여부를 추정한다. 높은 위험 확률을 큰 피해로 해석하지 않는다. 새 자동 사건은 `warning`으로 생성하며, 확률만으로 `critical`로 올리는 기존 규칙을 사용하지 않는다.
- **AI 라벨:** 현재 모델은 사람이 검수하지 않은 AI 라벨로 학습했다. 운영 연결 후에도 상태는 `provisional`이다. 기존 평가 성능은 AI 라벨 기준 성능이며 사람 검수 성능을 뜻하지 않는다.
- **유형 설명:** 현재 학습된 IF/LightGBM에는 기사 위험 판정이나 생성된 정답 라벨을 입력하지 않는다. 기사 유형 점수와 키워드는 위험 유형·근거 설명을 위해 별도로 사용한다.

정확한 입력 계약은 [story_model.py](../backend/app/services/story_model.py)의 `snapshot_articles`, `numeric_features`, `transform_stories`와 [story_model_runtime.py](../backend/app/services/story_model_runtime.py)의 `build_story_snapshot`에 정의돼 있다.

## 새 운영 경로에서 대체된 코드

| 파일·함수 | 기존 역할 | 현재 역할과 정리 판단 |
|---|---|---|
| [story_risk.py](../backend/app/services/story_risk.py) `_aggregate_story_event`의 `candidates`, `meets_event_threshold` 호출 | 기사별 위험 판정·기사 임계값으로 스토리 개방 여부 결정 | 새 모드에서는 `story_model_events.refresh_story_model_events`가 모델 판정과 최소 기사 수로 결정한다. 기존 분기는 모델 스위치를 끌 때 복귀할 수 있도록 남긴다. |
| 같은 함수의 `maximum + 출처 수·기사 수 보너스` 계산 | 스토리 최종 위험 확률을 수동 공식으로 계산 | 새 모드는 LightGBM 예측값을 그대로 저장한다. 이 보너스 공식은 새 모델에 중복 적용하지 않는다. |
| 같은 함수의 `latest_window.anomaly_score`, `feature_window_id` 연결 | 기업 15분 창의 이상치를 각 스토리에 차용 | 새 사건은 자체 IF 점수를 사용하고 `feature_window_id=None`으로 둔다. 관련 없는 스토리에 같은 기업 창 점수를 붙이지 않는다. |
| 같은 함수의 `probability >= 0.85 → critical` | 높은 판정 확률을 심각도로 전환 | 새 이진 모델에는 피해 규모 정답이 없어 사용하지 않는다. 별도의 피해 규모 판정이 필요하다. |
| [collection.py](../backend/app/routers/collection.py) `list_risk_judgments_page`의 기사 최대 위험 점수 | 비위험 스토리의 대표 확률을 기사 점수 최댓값으로 표시 | 새 모드는 `StoryRiskScore`를 사용한다. 기사 최대값 집계는 구 모드에서만 유지한다. |
| [companies.py](../backend/app/routers/companies.py) `_to_response`, [collection.py](../backend/app/routers/collection.py) `get_monitoring_summary` | 최신 15분 창으로 운영 모델 상태 표시 | 새 모드는 검증된 스토리 런타임의 상태·버전을 표시한다. |
| [story_risk.py](../backend/app/services/story_risk.py) `rebuild_recent_story_events`의 기사 위험 후보만 재집계하는 루프 | 기사 위험 후보가 있는 스토리만 재구축 | 새 모드의 전용 재적용 함수가 전체 기업·스토리 범위를 재평가한다. 기존 기사 판정이 낮아도 모델 양성이 될 수 있고, 기존 사건이 모델 음성으로 바뀔 수도 있다. |

## 구 모드 지원을 유지하는 동안 남길 코드

| 파일·함수/설정 | 남아 있는 사용처 | 정리 조건 |
|---|---|---|
| [risk_analysis.py](../backend/app/services/risk_analysis.py) `update_risk_events`, `_event_articles` | 스토리 엔진을 끈 경우 15분 창으로 사건을 생성·종료한다. | 창 기반 사건 모드를 제품에서 완전히 없앨 때 해당 분기와 테스트를 함께 정리한다. |
| `RISK_CLOSE_THRESHOLD`, `RISK_CLOSE_CONSECUTIVE_WINDOWS` | 위 창 기반 사건 종료 조건이다. | 새 스토리 모델의 종료 조건으로 쓰지 않는다. 창 모드를 없애기 전에는 삭제하지 않는다. |
| [risk_analysis.py](../backend/app/services/risk_analysis.py) `score_window`, `resolve_production_risk_detector`, `import_exported_models` | 15분 분석 결과와 기존 모델 등록·호환성 확인에 사용한다. | 스토리 모델로 교체했다고 전체 함수를 삭제하지 않는다. 15분 점수 기능도 폐지할 때 사용처를 다시 확인한다. |
| [training/risk_models.py](../backend/app/training/risk_models.py) `train_isolation_forest`, `train_risk_detector`, `_labeled_windows` | 15분 창용 IF/LightGBM 학습이다. `training.cli`의 `iforest`, `risk` 명령과 과거 정답 이관 스크립트가 사용한다. | 새 스토리 모델 재학습에는 `training/story_models.py`를 사용한다. 구 학습 경로는 별도 보관 대상으로 분류한다. |
| [story_risk.py](../backend/app/services/story_risk.py) `_llm_assessment`, `_risk_schema`, `ARTICLE_RISK_LLM_MAX_PER_RUN` | 애매한 기사에 대한 유형·위험 보완 판정과 구 스토리 경로에 사용한다. | 기사 설명 보완까지 없애려는 별도 결정 없이 삭제하지 않는다. 새 최종 모델 점수 계산에는 LLM 호출이 필요하지 않다. |
| [config.py](../backend/app/config.py) `article_risk_high_threshold`, Compose의 `ARTICLE_RISK_HIGH_THRESHOLD` | 현재 운영 코드에서는 읽지 않고 일부 테스트 설정에만 남아 있다. | 실제 미사용 설정이다. 추후 설정·문서·테스트에서 함께 제거할 수 있다. |

## 계속 필요한 코드

| 파일·함수 | 유지하는 이유 |
|---|---|
| [story_model_runtime.py](../backend/app/services/story_model_runtime.py) `resolve_story_risk_runtime`, `_validate_bundle`, `build_story_snapshot` | 신뢰하는 파일 해시 검증, 학습과 추론의 특징 계약, 캐시, 사용할 수 없는 모델의 명시적 처리다. |
| [story_model_events.py](../backend/app/services/story_model_events.py) `refresh_story_model_events` | 스토리 점수 저장, 최종 사건 판정, 기존 사건 이관, 사람 판정 보호를 담당한다. |
| [story_model.py](../backend/app/services/story_model.py), [training/story_models.py](../backend/app/training/story_models.py) | 현재 모델의 전처리·추론과 재현 가능한 내보내기·분할·재학습에 필요하다. |
| [story_risk.py](../backend/app/services/story_risk.py) `_local_assessment*`, `_save_assessment`, `_sync_event_evidence`, 출처 함수 | 기사 유형·근거·관련성·출처 정보를 제공한다. 기존 기사 위험 점수 자체가 새 LightGBM의 입력이라는 의미는 아니다. |
| [risk_analysis.py](../backend/app/services/risk_analysis.py) `classify_risk_types`, `resolve_risk_type_scores`, `resolve_article_risk_type_scores_batch` | 위험 유형과 근거 설명을 계속 제공한다. 이 파일 전체를 삭제하면 새 사건의 유형 설명도 깨진다. |
| [story_clustering.py](../backend/app/services/story_clustering.py) | 어떤 기사를 같은 사건으로 묶을지 결정한다. 모델 점수가 좋아도 군집이 틀리면 서로 다른 사건이 섞인다. |
| 기사 필터·감성 분석 | 기업에 실제 관련된 기사만 선정하고 현재 모델에 필요한 텍스트·감성 특징을 제공한다. |
| [risk_analysis.py](../backend/app/services/risk_analysis.py) `build_feature_window`, `update_daily_summary`, `update_company_readiness` | 15분 기사량·감성·수집 품질·준비 상태·일일 통계에 필요하다. |
| [story_risk.py](../backend/app/services/story_risk.py) `_reconcile_story_event_lifecycle`, `_has_authoritative_closure`, `_event_lock` | 서울 날짜 기준 활동 종료, 사람의 종료 판정 보존, 중복 사건 갱신 방지에 필요하다. |
| [risk_ground_truth.py](../backend/app/services/risk_ground_truth.py)와 사람 라벨 테이블 | 모델 예측과 독립된 검수·학습 정답을 보존한다. 모델 교체 시 덮어쓰지 않는다. |
| [dashboard.py](../backend/app/routers/dashboard.py), [operations.py](../backend/app/routers/operations.py), [collection.py](../backend/app/routers/collection.py)의 집계 | 모델을 브라우저에서 다시 실행하지 않고 DB 사건·점수를 일관된 목록과 그래프로 제공한다. 기사 감성·기사 위험 수는 스토리 위험 수와 구분된 통계다. |

## 검증

[test_story_model_listing.py](../backend/tests/test_story_model_listing.py)의 격리된 SQLite 테스트 5개를 Docker에서 실행해 모두 통과했다. 비위험 모델 점수·버전 표시, 미평가/기사 한 건 스토리 제외, 필터 철회 후 최소 기사 수, 모델 버전 교체, 과거 모드 호환성, 대시보드 사건 제외 조건, 기업·모니터링의 런타임 상태를 확인한다.

실행 명령: `docker exec risoto-backend-1 python -m unittest tests.test_story_model_listing -v`

## 전체 데이터 적용 기록

[실제 DB 재적용 보고서](../backend/training_data/story_model_applications/20260907_project_connection/report.json)는 2026-09-07 14:22 KST 시작 기준 다음 결과를 기록한다. 변경 전 DB 상태는 같은 폴더의 `before.json`에 보관한다. 기존 학습 모델 카드와 학습 아카이브는 수정하지 않았다.

| 항목 | 건수 |
|---|---:|
| 새 모델로 점수를 저장한 기업·스토리 | 10,576 |
| 모델 자체의 위험 예측 | 1,545 |
| 최소 기사 조건을 충족해 생성·갱신한 위험 사건 | 216 |
| 위 사건 중 새로 생성 | 95 |
| 비위험 또는 근거 조건 미달로 운영 집계에서 제외한 기존 자동 사건 | 2,465 |
| 이전 15분 창 사건 이관 | 34 |
| 기사 활동 종료 규칙으로 종료한 사건 | 78 |
| 새 기사 라벨 생성·기사 LLM 호출·자동 대응 초안 생성 | 각각 0 |

**모델 양성 1,545건이 화면에 위험 사건 1,545건으로 표시되는 것은 아니다.** 모델 점수는 기사 한 건인 스토리에도 저장하지만 운영 사건은 현재 승인된 기사 두 건 이상이어야 한다. 216은 이번 실행에서 위험 사건으로 생성·갱신한 수이며 진행 중인 사건 수만을 의미하지 않는다. 기간 필터와 활동 종료 상태에 따라 활성 목록·과거 목록의 수는 달라진다.

최종 [API 검증](../exports/model_applications/story-risk-full-v2-20260907/verification.json)에서는 16개 기업 모두 그래프와 목록의 사건 수가 같았다. 전체 위험 사건은 216건(진행 중 19건, 과거 197건), 비위험 목록은 938건이다. 목록 표본 987건의 확률·IF 점수·모델 버전을 DB 점수와 대조해 일치했다. 숫자는 적용 시점의 전체 계정 합계이며, 화면에서는 로그인 계정·선택 기업·기간으로 제한된다.

[재실행 검증](../exports/model_applications/story-risk-full-v2-20260907/idempotence.json)에서는 10,576건을 다시 평가했을 때 사건 변경·생성·제외와 활동 종료 변경이 모두 0건이었다. 저장된 입력의 예측 재현 오차도 0이었다. 학습 당시 24시간이 지나지 않았던 27개 스냅샷은 평가 시점 경과로 최근 6시간 비율이 달라졌지만 판정 변경은 없었다. 시점을 맞추면 특징과 예측이 정확히 재현됐다.

모델·라벨링·비교·런타임·목록·상태 테스트 44개, 실제 PostgreSQL 사건 테스트 9개가 통과했다. 기존 동작 테스트도 별도 스키마와 롤백으로 확인했고 프런트엔드 빌드가 성공했다. Docker 백엔드·프런트엔드는 healthy이며 HTTP 상태 확인도 200이었다.

## 이번에 실제 제거하거나 교체한 화면 코드

| 위치 | 조치와 이유 |
|---|---|
| `frontend/src/shared/presentation.js`의 `LIGHTGBM_STATE_LABELS` | 사용처가 없고 미검증 모델을 검증된 것처럼 표현하던 상수를 제거했다. 현재 모델 범위와 실제 상태를 표시하는 함수로 대체했다. |
| `DashboardPage.jsx`의 `company.model_state === "production"` 조건 | 유효한 스토리 판정을 가리던 기존 창 모델 상태 조건을 제거하고 현재 스토리 런타임 가용성을 사용한다. |
| `ModelManagementPage.jsx`의 사용 가능 → `production` 변환 | 적용 여부와 사람 검증 상태를 분리해 실제 `provisional` 상태를 표시한다. |
| `governance.py`의 최종 상태를 15분 모델로만 해석하던 분기 | 스토리 모드에서는 스토리 런타임의 실제 버전·임계값·체크섬을 반환한다. |

모델 적용 후 코드와 학습 아카이브는 서로 다르다. `exports/model_artifacts`의 기존 모델 카드는 생성 당시 후보 상태를 기록한 보존 자료이며, 현재 운영 상태는 본 문서와 `exports/model_applications`의 실행 기록을 기준으로 확인한다.
