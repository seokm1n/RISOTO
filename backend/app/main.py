import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.routers import collection, companies, dashboard, industries
from app.schemas import HealthResponse
from app.services.monitoring_pipeline import realtime_monitoring_loop


settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop_event = asyncio.Event()
    monitoring_task = asyncio.create_task(realtime_monitoring_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await monitoring_task

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(industries.router, prefix="/api/v1")
app.include_router(companies.router, prefix="/api/v1")
app.include_router(collection.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "version": "0.1.0"}


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    result = db.execute(
        text(
            """
            SELECT
                current_database() AS database,
                current_user AS database_user,
                current_setting('server_version') AS postgres_version,
                (
                    SELECT extversion
                    FROM pg_extension
                    WHERE extname = 'vector'
                ) AS pgvector_version
            """
        )
    ).mappings().one()

    pgvector_version = result["pgvector_version"]
    return HealthResponse(
        status="ok" if pgvector_version else "degraded",
        app=settings.app_name,
        environment=settings.app_env,
        database=result["database"],
        database_user=result["database_user"],
        postgres_version=result["postgres_version"],
        pgvector_version=pgvector_version or "not installed",
    )
