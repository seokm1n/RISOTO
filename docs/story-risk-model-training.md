# 스토리용 Isolation Forest + LightGBM

기업별 15분 모델과 별도로, `기업 + 스토리`의 첫 24시간에 수집된 기사 중 최초 8건으로 위험을 예측한다. 학습 명령 자체는 후보 파일만 생성한다. 전체 데이터 v2는 2026-09-07 별도 연결 작업으로 프로젝트에 적용했으며, 사람 검증 전 상태를 유지한다. 현재 운영 경로와 재평가 결과는 [연결 문서](story-risk-integration-cleanup.md)를 참조한다.

## 데이터와 라벨

- DB의 정제 통과 기사와 현재 스토리 연결을 읽는다. 기존 기사 위험 확률·사건 개방 여부는 표본 선정이나 정답 생성에 사용하지 않는다.
- 군집 ID의 고정 해시로 표본을 추출하고, 같은 군집에 연결된 기업은 모두 유지한다.
- 기사 제목과 최대 1,000자의 요약을 별도 LLM에 전달해 `risk / normal / uncertain`, 확신 수준, 근거 기사 ID, 판정 이유를 생성한다. 회사 이름과 별칭도 제공한다.
- `ai_labels.jsonl`은 **AI 생성 라벨**이다. `human_reviewed=false`, 모델, 프롬프트 해시, 응답 ID, 입력 스냅샷 해시를 보존한다. 운영 DB의 사람 정답 테이블에는 쓰지 않는다.
- `ai_review.json`의 Codex 점검·수정 이력을 원본 라벨과 별도로 유지한다. 이번 실행의 수정은 학습 구간에만 허용하며, 이 수정도 사람 검수가 아니다.
- `uncertain`과 확신 수준 `low`는 학습과 평가에서 제외한다. 정상 기사도 위험 사건과 동일한 표본 추출 방식으로 포함한다.

## 모델

1. 기사 수·출처 다양성·동일 제목 비율·수집 시간 간격·감성·위험 키워드 특징을 집계한다. 감성은 스냅샷 시점까지 분석된 경우만 사용한다.
2. 기업명을 마스킹한 본문을 문자 TF-IDF와 32차원 SVD로 변환한다. 이 전처리기는 학습 분할에만 적합한다.
3. 학습 분할의 AI 정상 라벨 표본으로 RobustScaler와 300개 트리의 Isolation Forest를 학습한다.
4. 구조화 특징, 텍스트 특징, IF 이상 점수와 정상 학습 점수 대비 백분위를 LightGBM에 입력한다.
5. 검증 분할에서 조기 종료와 F1 기준 임계값 선택을 수행한다. 동일 조건의 IF 없는 LightGBM도 비교한다.
6. 테스트 분할은 최종 보고에만 사용한다. 보고서 수치는 사람이 확인한 실제 위험 탐지 정확도가 아니라 AI 라벨에 대한 일치도이다.

같은 군집·동일 기사·정규화된 동일 제목을 공유하는 표본은 한 그룹으로 묶는다. 수집 기간의 60%/80% 시점으로 학습·검증·테스트 시간대를 나누고, 경계를 걸치는 그룹은 제외한다. 현재 군집/필터 상태로 과거를 재구성하므로 완전한 실시간 백테스트는 아니다. 표현이 다른 동일 사건까지 모두 분리되었다고 보장하지 않는다.

## 실행

프로젝트 루트에서 실행한다. backend 컨테이너에 이미 설치된 학습 라이브러리와 설정된 OpenAI 라벨링 모델을 사용한다. 학습은 CPU로 수행한다.

```powershell
docker compose exec -T backend python -m app.training.story_models export --output training_data/story_model_v1 --limit 1600
docker compose exec -T backend python -m app.training.story_models label --output training_data/story_model_v1 --workers 4
docker compose exec -T backend python -m app.training.story_models train --output training_data/story_model_v1
```

최종 파일을 `exports`로 옮긴 후 같은 데이터로 재학습하려면 해당 폴더를 새 작업 폴더로 복사해 `train`을 실행한다. 모델 로딩에는 작업 폴더가 필요 없다.

라벨링이 일부 실패하면 같은 명령을 다시 실행해 완료된 결과를 재사용한다. 필요한 경우 `--batch-size 1`로 항목별 재시도할 수 있다. 신규 데이터 추출 시에는 새 출력 폴더를 사용한다.

최종 산출물은 프로젝트의 `exports/model_artifacts/story-risk-v1-20260907` 폴더에 보관한다. backend에서는 이 폴더가 `/app/model_artifacts/story-risk-v1-20260907`로 읽기 전용 연결된다.

통합 `.joblib` 파일이 추론의 진입점이다. LightGBM 텍스트 파일만으로는 동일한 예측을 재현할 수 없으며, 저장된 TF-IDF/SVD·스케일러·IF가 모두 필요하다.

```python
from app.services.story_model import predict_stories

# artifact: 신뢰할 수 있는 로컬 통합 joblib 경로
# snapshots: example_input.json과 동일한 구조의 리스트
predictions = predict_stories(artifact, snapshots)
```

CLI 추론도 지원한다.

```powershell
docker compose exec -T backend python -m app.training.story_models predict --output training_data/story_inference --artifact /app/model_artifacts/story-risk-v1-20260907/ARTIFACT.joblib --input /app/model_artifacts/story-risk-v1-20260907/example_input.json
```

`ARTIFACT.joblib`은 `report.json`의 `artifact` 이름으로 바꾼다. 모델 출력의 `candidate`와 `ai_generated_unreviewed`는 운영 승인 또는 사람 검수가 완료되었다는 뜻이 아니다. 위험 점수는 피해 심각도와 구분한다. 운영 적용 전에는 실제 기사 표본의 사람 검수와 전향적 비교가 필요하다.

## 전체 데이터 재학습 — 2026-09-07

`--all`은 현재 필터 통과 기사에 연결된 기업·스토리 조합 **10,576개 전체**를 추출한다. 첫 버전의 1,746개 표본과 구분된다. 비교 가능한 입력 계약을 유지하기 위해 스토리별 첫 24시간의 최초 8개 기사라는 제한은 동일하다. 따라서 전체 기업·스토리를 사용한다는 의미이며, 각 스토리의 모든 후속 기사를 하나의 입력에 넣는다는 의미는 아니다. 실제 포함 기사 수와 전체 기사·기업 연결 수는 `dataset_manifest.json`에 기록한다.

```powershell
docker compose exec -T backend python -m app.training.story_models export --output training_data/story_model_full_v2 --all
docker compose exec -T backend python -m app.training.story_models split --output training_data/story_model_full_v2
docker compose exec -T backend python -m app.training.story_annotation --output training_data/story_model_full_v2 --workers 8 --batch-size 8
docker compose exec -T backend python -m app.training.story_models train --output training_data/story_model_full_v2
docker compose exec -T backend python -m app.training.story_model_comparison --candidate training_data/story_model_full_v2 --baseline /app/model_artifacts/story-risk-v1-20260907
```

새 라벨링 기준은 직접 사건 당사자와 단순 출처·SNS 로그인 경로·스포츠팀·예방/구조 활동을 구별한다. 이전 라벨은 새 기준으로 다시 생성하며, 검수된 사람 정답으로 승격하지 않는다. 라벨링은 완료된 항목을 재사용하며 재개할 수 있고 프롬프트·모델·스냅샷 계약이 다른 라벨 혼합은 차단한다.

일부 응답이 같은 근거 기사 ID를 반복해 로컬 검증에 실패하면, 라벨링 프로세스가 종료된 뒤 아래 순서로 복구한다. 복구기는 원본 응답·입력·모델·프로토콜 해시를 검증하고 동일 ID의 반복만 제거한다. 판정·이유·확신도는 변경하지 않으며 원본 응답과 복구 이력을 별도로 보존한다. 다른 오류는 마지막 명령에서 누락 항목만 다시 요청한다.

```powershell
docker compose exec -T backend python -m app.training.story_annotation_recovery --output training_data/story_model_full_v2
docker compose exec -T backend python -m app.training.story_annotation --output training_data/story_model_full_v2 --workers 8 --batch-size 1
```

`split_plan.json`은 라벨을 보기 전에 고정한다. 불확실 표본을 제외하기 전의 모든 기사·동일 제목·군집 연결을 이용하므로, 제외되는 표본이 연결하던 사건도 서로 다른 분할에 섞이지 않는다. 일부 데이터는 평가용 또는 시간 경계 중복 제외용으로 남으며 모두를 학습 입력으로 쓰지는 않는다.

기존 모델과의 비교는 **새 모델의 테스트 중 기존 모델 학습·검증에 노출되지 않은 표본**에서 같은 새 라벨로 계산한다. 이전 보고서의 F1과 새 보고서의 F1을 직접 비교해 성능 향상으로 주장하지 않는다. 시간대와 회사별 위험 사건 수가 적으면 전체 표본 수가 늘어도 평가 불확실성은 남을 수 있다.

최종 전체 데이터 모델·라벨·보고서는 `exports/model_artifacts/story-risk-full-v2-20260907`에 저장한다. 학습 시점의 모델 카드·체크섬은 보존한다. 현재 운영 연결은 별도 `story_model_runtime.py`·`story_model_events.py`와 설정으로 관리하고, 다음 후보를 학습해도 자동으로 교체하지 않는다.
