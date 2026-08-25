# RISOTO

기업 리스크 분석 서비스입니다. 현재 개발 인프라는 PostgreSQL 18.4와 pgvector 0.8.6으로
고정되어 있습니다.

## 데이터베이스 실행

Docker Desktop을 실행한 뒤 프로젝트 루트에서 다음 명령을 사용합니다.

```powershell
docker compose config
docker compose up --build -d db
docker compose ps
```

버전을 확인합니다.

```powershell
docker compose exec db psql -U risoto_app -d risoto -c `
  "SELECT current_setting('server_version') AS postgres_version, extversion AS pgvector_version FROM pg_extension WHERE extname='vector';"
```

로컬 DB 접속 정보:

```text
Host: localhost
Port: 5432
Database: risoto
Username: risoto_app
Password: .env의 POSTGRES_PASSWORD
```

향후 FastAPI 컨테이너에서는 `localhost`가 아니라 Compose 서비스 이름 `db`를 사용합니다.

```text
postgresql+psycopg://risoto_app:<password>@db:5432/risoto
```

FastAPI를 Docker 밖의 Windows 가상환경에서 실행할 때는 호스트에 공개한 5432 포트를
사용합니다.

```text
postgresql+psycopg://risoto_app:<password>@localhost:5432/risoto
```

DB를 중지하되 데이터를 유지하려면:

```powershell
docker compose down
```

`docker compose down -v`는 `risoto_postgres_data` 볼륨과 모든 로컬 DB 데이터를 삭제하므로
초기화가 명확히 필요한 경우에만 사용해야 합니다.

## FastAPI 백엔드

백엔드는 Python 3.10.5, FastAPI, SQLAlchemy 2, Psycopg 3, Alembic을 사용합니다.

```powershell
docker compose up --build -d backend
docker compose ps
Invoke-RestMethod http://localhost:8000/health
```

접속 주소:

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- 상태 확인: `http://localhost:8000/health`

개발 환경에서는 백엔드 컨테이너가 시작될 때 `alembic upgrade head`를 자동 실행합니다.
현재 스키마 상태와 모델 차이를 확인하려면 다음 명령을 사용합니다.

```powershell
docker compose exec backend alembic current
docker compose exec backend alembic check
```

초기 마이그레이션은 다음 테이블을 생성합니다.

- `industries`: 상위·하위 산업군
- `companies`: 기업, 종목코드, 과거 수집 기간, 모니터링 상태
- `company_peers`: 기업별 경쟁사와 비교 가중치
- `company_keywords`: 기업 별칭, 유사기업, 제품·브랜드, 키워드

## 기업 등록 및 관리 화면

React 화면에서 기업명, 종목코드, 산업군을 입력한 뒤 기업 별칭·약칭, 유사기업,
제품·브랜드와 키워드를 여러 개의 태그로 추가할 수 있습니다. 입력값은 FastAPI를 거쳐
PostgreSQL에 저장됩니다.
상단의 `수집 기업 관리` 화면에서는 등록 기업을 선택해 기업명, 종목코드, 산업군과
별칭·유사기업·제품·키워드를 같은 형식으로 수정할 수 있습니다. 삭제한 키워드는 이후 수집부터
제외되며 기존 기사와 분석 이력은 보존됩니다.

```powershell
docker compose up --build -d
```

접속 주소:

- 기업 등록 화면: `http://localhost:5173`
- 기업 목록·등록 API: `GET/POST http://localhost:8000/api/v1/companies`
- 기업 상세·수정 API: `GET/PUT http://localhost:8000/api/v1/companies/{company_id}`
- 산업군 API: `http://localhost:8000/api/v1/industries`

기본 산업군은 두 번째 Alembic 마이그레이션에서 생성됩니다. 유사기업명은 아직 시스템에
등록된 실제 기업 관계가 아니므로 `peer` 키워드로 저장되고, 제품명은 `product` 키워드로
저장됩니다.

## 뉴스 수집 API 키

실제 인증 정보는 프로젝트 루트의 `.env`에만 입력하고 소스 코드에는 넣지 않습니다.

```dotenv
NAVER_API_HUB_CLIENT_ID=
NAVER_API_HUB_CLIENT_SECRET=
TAVILY_API_KEY=
KAKAO_REST_API_KEY=
SERPAPI_API_KEY=
YOUTUBE_API_KEY=
```

`.env` 변경 후 백엔드를 재생성합니다.

```powershell
docker compose up -d --force-recreate backend
```

설정된 수집원을 확인합니다.

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/collection/providers
```

수집기는 NAVER API HUB 뉴스 검색, Kakao REST API의 Daum 웹문서 검색, Tavily 검색과
YouTube 영상 댓글 수집을 지원합니다. `YOUTUBE_API_KEY`가 설정되어 있으면 기업 등록 시
7일 과거 수집, 수동 수집 및 15분 주기 실시간 수집에서 모두 YouTube 댓글을 수집합니다.
YouTube 검색 API는 쿼터 사용량이 크므로 운영 환경에서는 수집 주기와 검색어 수를 함께
고려해야 합니다.
SerpAPI 키는 다음 수집 어댑터를 위해 환경변수 연결만 준비되어 있습니다.

## 기사 정제 파이프라인

API 응답은 바로 분석하지 않고 다음 세 계층으로 보관합니다.

1. `raw_news_articles`: API가 반환한 제목, 요약, URL, 원본 JSON을 보존합니다.
2. `article_filter_results`: 기업별 판정, 제외 사유, 점수, 사용 모델과 기준 버전을 기록합니다.
3. `news_articles`: 정제를 통과했거나 기존 기사에 병합된 콘텐츠만 저장합니다. 감성 분석,
   이상 탐지, 위험 이벤트와 대시보드는 이 계층만 사용합니다.

필터는 규칙과 로컬 AI를 함께 사용합니다.

- 완전 중복: 추적 파라미터를 제거한 **정규화 URL이 같을 때만** 하나의 기사로 병합합니다.
- 유사 기사: 다른 URL은 삭제하지 않고 기사량에 포함하며 `story_clusters`로만 연결합니다.
- 광고: 협찬·제휴·할인·구매·상담·연락처·상거래 URL 신호를 점수화합니다.
- 관련성: 기업명, 종목코드, 별칭, 제품명이 실제 제목·요약에 등장하는지 확인하고,
  KLUE-RoBERTa NLI 모델이 실질 기사·부수 언급·무관 가설을 비교합니다.

기본 판정 기준은 다음과 같습니다.

- 정규화 URL 동일: 새 분석 기사로 만들지 않고 기존 기사 및 검색 적중 이력에 병합
- 광고 점수 `>= 0.85`: 제외
- 광고 점수 `0.55 ~ 0.85`: 검토 필요
- 관련성 점수 `>= 0.70`: 통과
- 관련성 점수 `<= 0.30`: 무관 콘텐츠로 제외
- 그 사이: 검토 필요

`review_required`와 제외 데이터도 삭제하지 않습니다. AI 모델을 내려받거나 불러올 수
없으면 보수적인 규칙 기반 판정으로 계속 동작하고 `classifier_kind=rules_only` 및 실패
사유를 기록합니다. AI가 사용되면 `classifier_kind=hybrid_klue_nli`와 모델명이 남습니다.
관련성 분류의 초기 폴백 모델은 `Huffon/klue-roberta-base-nli`입니다. 기사 원문은 외부
분류 API로 전송하지 않고 백엔드 컨테이너에서 추론합니다. 유사도는 중복 삭제가 아니라
스토리 군집과 `article_count`/`story_count`/`amplification_count` 분리에만 사용합니다.

판정 통계와 상세 결과는 다음 API로 확인합니다.

```text
GET /api/v1/companies/{company_id}/filter-summary
GET /api/v1/companies/{company_id}/filter-results
GET /api/v1/companies/{company_id}/filter-results?decision=review_required
GET /api/v1/companies/{company_id}/filter-results?reason=advertisement
```

임계값과 AI 사용 여부는 `.env`의 `ARTICLE_FILTER_*` 설정으로 변경할 수 있습니다.
기준을 변경할 때는 `ARTICLE_FILTER_VERSION`도 올려야 같은 원본을 새 기준으로 다시
판정한 이력을 구분할 수 있습니다. 마이그레이션 이전의 기존 기사는 데이터와 위험 이벤트를
보존하기 위해 `legacy-import-v1`의 승인 결과로 이관되며 자동 재분류하지 않습니다.

감성분석과 기사 관련성 분류는 현재 `Huffon/klue-roberta-base-nli` 체크포인트를 공유합니다.
이 체크포인트는 `klue/roberta-base`를 한국어 NLI로 미세조정한 모델입니다. 감성분석은
긍정·부정 가설의 차이가 작은 기사를 중립으로 처리하고, 필터는 한국어 기업 관련성
가설을 비교합니다. 별도 미세조정과 모델 반입 기능은 추후 구현하며, 현재 운영 화면에서는
학습이나 승격을 수행하지 않습니다.

## 기업 실시간 위험 분석

초기 운영 대상은 쿠팡, 네이버, 카카오, 무신사, 올리브영, 마켓컬리, SSG, 에이블리,
11번가입니다. 서울 시간 `:00/:15/:30/:45`에 맞춘 15분 특징 창을 만들며, 각 실행은
방금 끝난 구간을 분석해 아직 수집 중인 미래 구간을 점수화하지 않습니다. 다음 상태를
구분합니다.

- `complete`: 모든 수집원이 성공했습니다. 검색 결과가 0건이어도 유효한 무기사 구간입니다.
- `partial`: 일부 수집원만 실패했습니다. 수집된 데이터로 제한적 분석을 계속합니다.
- `unavailable`: 모든 수집원이 실패했습니다. 기사 0건으로 대체하지 않고 IF·LightGBM
  점수를 생성하지 않습니다.

전체 실패는 즉시 수집 장애로 기록하고 1분, 5분, 15분 뒤 최대 3회 재시도합니다. 부분
실패는 첫 회부터 대시보드에 남고 같은 수집원이 두 개의 서로 다른 구간에서 연속 실패하면
Webhook 알림을 큐에 넣습니다. 같은 구간·원인·수집원의 장애는 여러 기업을 하나의 사건으로
병합합니다. Webhook 실패는 `notification_deliveries`에 저장해 재시도하며 수집 작업을
실패시키지 않습니다.

```dotenv
COLLECTION_ALERT_WEBHOOK_URL=
COLLECTION_RETRY_DELAYS_SECONDS=60,300,900
PARTIAL_FAILURE_CONSECUTIVE_THRESHOLD=2
```

주요 운영 API:

```text
GET  /api/v1/notifications
GET  /api/v1/risk-detection-status
GET  /api/v1/collection-health
GET  /api/v1/collection-incidents
POST /api/v1/collection-incidents/{id}/acknowledge
GET  /api/v1/companies/{id}/feature-windows
GET  /api/v1/companies/{id}/daily-summaries
GET  /api/v1/companies/{id}/risk-events
```

화면 상단의 종 배지는 읽지 않은 알림 수가 아니라 현재 확인이 필요한 위험 사건
(`open`, `monitoring`) 수입니다. 종을 누르면 운영 관리의 위험 알림 영역으로 이동합니다.

신규 기업은 기사 50건과 비어 있지 않은 유효 특징 창 40개가 모일 때까지
`PREPARING`으로 수집만 수행합니다. 조건을 충족해도 자동 활성화하지 않습니다.

```text
POST /api/v1/companies/{id}/activate
```

## 현재 모델 운영 방식

- 기사 관련성·광고 보조 판정과 감성 분석: 기본 KLUE/RoBERTa NLI와 명시적 규칙 사용
- 위험 유형 근거 점수: 위험 키워드와 기본 KLUE NLI 사용
- 최종 위험 판정: 공통 LightGBM 필수
- 이상치 특징: 기업별 RobustScaler를 적용한 공통 Isolation Forest 사용

기본 KLUE 결과는 최종 위험 확률을 대신하지 않습니다. 운영 LightGBM과 그 모델이 요구하는
Isolation Forest가 모두 등록되고 아티팩트·특징 순서·의존 버전 계약이 일치할 때만 15분
위험도, 위험 이벤트와 위험 알림을 생성합니다. 준비 전에는 수집·기사 분석·스토리 군집·15분
특징 생성까지만 수행하며 위험 0건이 아니라 `판정 대기`로 표시합니다.

웹의 `운영 관리` 화면에는 위험 알림, 기본 분석/LightGBM 상태, 위험 사건 확인과
수집·분석 품질 점검만 노출합니다. 기사 1,500건 정답 CSV 제작과 모델 학습·반입·승격은
별도 후속 작업입니다.

서울 날짜 기준 하루 한 번 최근 24시간의 수집 완전성, 확정 라벨 분포, 앞선 7일 기준선 대비
특징 중앙값의 Robust Z-score를 점검해 `model_operation_checks`에 저장합니다. 점검 실패는
수집 파이프라인을 중단시키지 않으며 필요할 때 운영 API로 다시 계산할 수 있습니다.

학습 기능을 다시 활성화할 때 사용할 내부 CLI와 모델 레지스트리 API는 서버에 보존되어
있지만 현재 웹에서는 노출하지 않습니다. CPU에서 IF 후보를 만드는 명령은 다음과 같습니다.

```powershell
docker compose exec backend python -m app.training.cli iforest
```

KLUE 미세조정은 API 서버와 분리된 CUDA 컨테이너에서 실행합니다. Docker Desktop의 GPU
지원과 NVIDIA Container Toolkit이 준비된 환경에서 다음 명령을 사용합니다.

```powershell
docker compose --profile training build trainer
docker compose --profile training run --rm trainer filter --epochs 4
docker compose --profile training run --rm trainer sentiment --epochs 4
docker compose --profile training run --rm trainer risk-types --epochs 4
docker compose --profile training run --rm trainer risk
```

명령은 모두 `candidate`만 등록합니다. 아래 API는 추후 모델 운영 기능에서 사용합니다.

```text
GET  /api/v1/model-versions
POST /api/v1/model-versions/{id}/promote
GET  /api/v1/model-training-readiness
GET  /api/v1/model-monitoring
POST /api/v1/model-monitoring/check
```

## 위험 사건과 대응 초안

LightGBM 임계값을 처음 넘은 구간에서 사건을 즉시 열고, 같은 사건의 연속 구간은 하나로
병합합니다. 위험 확률이 하한 임계값 아래인 구간이 두 번 연속 나타날 때 닫습니다. 위험
유형은 제품·품질, 안전·사고, 보안·개인정보, 법률·규제, 노동·인사, 재무·지배구조,
공급·운영, 평판·소비자의 다중 라벨이며 초기에는 키워드와 KLUE NLI를 결합합니다.

최초 사건 개방 또는 주요 유형 변경 시 대응 초안을 백그라운드에서 만듭니다. 현재 근거
기사와 검증 사례를 우선 사용하고 부족하면 국내 사례 검색 결과를 `candidate`로 캐시합니다.
입력에 없는 URL과 인용 없는 대응 문구는 제거하며, URL 근거 기사가 없으면 초안 자체를
생성하지 않습니다. 승인 전에는 외부 전송이나 실제 대응을 실행하지 않습니다.

```text
POST /api/v1/risk-events/{id}/response-drafts
GET  /api/v1/risk-events/{id}/response-drafts
POST /api/v1/response-drafts/{id}/approve
POST /api/v1/response-drafts/{id}/reject
```

## 검증

```powershell
docker compose exec backend python -m unittest discover -s tests -v
docker compose exec backend alembic check
docker compose exec frontend npm run build
```

테스트는 URL 정규화, 다른 URL의 유사 기사 보존, 정상 무기사/부분 장애/전체 장애 구분,
자격 증명 마스킹, 동일 원인 장애 병합, 2개 구간 부분 장애 임계값, Webhook 비차단 실패,
시간·스토리·사건 그룹 분할, 위험 사건 즉시 개방과 하한 히스테리시스, 대응 URL 근거 제한을
검증합니다.
