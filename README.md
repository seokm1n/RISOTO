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
- `company_keywords`: 기업 별칭, 유사기업, 제품·브랜드, 위험 키워드

## 기업 등록 화면

React 화면에서 기업명을 입력하고 산업군을 선택한 뒤 유사기업과 제품·브랜드명을
여러 개의 태그로 추가할 수 있습니다. 입력값은 FastAPI를 거쳐 PostgreSQL에 저장됩니다.

```powershell
docker compose up --build -d
```

접속 주소:

- 기업 등록 화면: `http://localhost:5173`
- 기업 API: `http://localhost:8000/api/v1/companies`
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

첫 번째 수집 버전은 NAVER API HUB 뉴스 검색과 Tavily 검색을 사용합니다. SerpAPI와
YouTube 키는 다음 수집 어댑터를 위해 환경변수 연결만 준비되어 있습니다.
