"""모니터링 기업의 등록·조회와 키워드 보강 API를 제공한다."""

from datetime import datetime, timezone
import re
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import Settings, get_settings
from app.models import (
    ArticleFilterResult,
    Company,
    CompanyArticleMatch,
    CompanyKeyword,
    Industry,
    NewsArticle,
    RawNewsArticle,
    CompanyFeatureWindow,
)
from app.schemas import (
    CompanyActivationRead,
    CompanyCreate,
    CompanyKeywordRead,
    CompanyRead,
    CompanyUpdate,
)
from app.services.monitoring_pipeline import (
    initialize_company_monitoring,
    refresh_company_monitoring,
)


router = APIRouter(prefix="/companies", tags=["companies"])


def _to_response(
    db: Session,
    company: Company,
    industry_name: str | None,
    keywords: list[CompanyKeyword],
    is_existing: bool = False,
    added_keyword_count: int = 0,
) -> CompanyRead:
    """기업 ORM 객체와 연관 정보를 API 응답 스키마로 변환한다."""
    settings = get_settings()
    accepted_article_count = db.scalar(
        select(func.count(CompanyArticleMatch.article_id)).where(
            CompanyArticleMatch.company_id == company.id
        )
    ) or 0
    valid_nonempty_window_count = db.scalar(
        select(func.count(CompanyFeatureWindow.id)).where(
            CompanyFeatureWindow.company_id == company.id,
            CompanyFeatureWindow.data_quality != "unavailable",
            CompanyFeatureWindow.article_count > 0,
        )
    ) or 0
    ready = (
        accepted_article_count >= settings.readiness_min_articles
        and valid_nonempty_window_count >= settings.readiness_min_nonempty_windows
    )
    readiness_status = (
        "active" if company.monitoring_status == "active" else
        "pending_approval" if ready else "preparing"
    )
    latest_window = db.scalar(
        select(CompanyFeatureWindow)
        .where(CompanyFeatureWindow.company_id == company.id)
        .order_by(CompanyFeatureWindow.window_start.desc())
        .limit(1)
    )
    return CompanyRead(
        id=company.id,
        name=company.name,
        ticker=company.ticker,
        industry_id=company.industry_id,
        industry_name=industry_name,
        backfill_days=company.backfill_days,
        monitoring_status=company.monitoring_status,
        analysis_status=company.analysis_status,
        analysis_error=company.analysis_error,
        monitoring_started_at=company.monitoring_started_at,
        last_collected_at=company.last_collected_at,
        baseline_ready_at=company.baseline_ready_at,
        keywords=[CompanyKeywordRead.model_validate(item) for item in keywords],
        created_at=company.created_at,
        is_existing=is_existing,
        added_keyword_count=added_keyword_count,
        readiness_status=readiness_status,
        accepted_article_count=accepted_article_count,
        valid_nonempty_window_count=valid_nonempty_window_count,
        activation_required=readiness_status == "pending_approval",
        model_state=latest_window.model_state if latest_window else "unavailable",
    )


def normalize_company_name(value: str) -> str:
    """중복 기업 판별을 위해 이름의 유니코드, 대소문자, 구분 문자를 정규화한다."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s·._-]+", "", normalized)


def _get_company_keywords(db: Session, company_id: int) -> list[CompanyKeyword]:
    """기업의 키워드를 유형과 값 순서로 정렬해 반환한다."""
    return list(
        db.scalars(
            select(CompanyKeyword)
            .where(CompanyKeyword.company_id == company_id)
            .order_by(CompanyKeyword.keyword_type, CompanyKeyword.value)
        )
    )


@router.get("", response_model=list[CompanyRead])
def list_companies(db: Session = Depends(get_db)) -> list[CompanyRead]:
    """등록된 기업과 산업·키워드 정보를 최신 등록순으로 조회한다."""
    companies = db.execute(
        select(Company, Industry.name)
        .outerjoin(Industry, Industry.id == Company.industry_id)
        .order_by(Company.created_at.desc())
    ).all()
    keyword_rows = db.scalars(
        select(CompanyKeyword).order_by(CompanyKeyword.keyword_type, CompanyKeyword.value)
    ).all()
    grouped: dict[int, list[CompanyKeyword]] = {}
    for keyword in keyword_rows:
        grouped.setdefault(keyword.company_id, []).append(keyword)
    return [
        _to_response(db, company, industry_name, grouped.get(company.id, []))
        for company, industry_name in companies
    ]


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(company_id: int, db: Session = Depends(get_db)) -> CompanyRead:
    """수정 화면에서 사용할 기업 기본 정보와 전체 키워드를 조회한다."""
    row = db.execute(
        select(Company, Industry.name)
        .outerjoin(Industry, Industry.id == Company.industry_id)
        .where(Company.id == company_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    company, industry_name = row
    return _to_response(db, company, industry_name, _get_company_keywords(db, company.id))


@router.post("", response_model=CompanyRead)
def create_or_update_company(
    payload: CompanyCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> CompanyRead:
    """기업을 새로 등록하거나 기존 기업에 키워드를 보강하고 모니터링을 예약한다."""
    industry = db.get(Industry, payload.industry_id)
    if industry is None:
        raise HTTPException(status_code=404, detail="선택한 산업군을 찾을 수 없습니다.")

    normalized_name = normalize_company_name(payload.name)
    company = db.scalar(
        select(Company).where(
            Company.normalized_name == normalized_name,
            Company.industry_id == industry.id,
        )
    )
    is_existing = company is not None
    now = datetime.now(timezone.utc)
    # 같은 산업의 정규화 기업명은 하나만 유지하고 재등록은 설정 보강으로 처리한다.
    if company is None:
        company = Company(
            name=payload.name,
            normalized_name=normalized_name,
            ticker=payload.ticker or None,
            industry_id=industry.id,
            backfill_days=7,
            monitoring_status="backfilling",
            analysis_status="pending",
            monitoring_started_at=now,
        )
        db.add(company)
    else:
        company.backfill_days = 7
        company.monitoring_started_at = company.monitoring_started_at or now
        if payload.ticker and not company.ticker:
            company.ticker = payload.ticker
        if company.monitoring_status in {"paused", "error"}:
            company.monitoring_status = "warming"

    # 기업과 키워드를 한 트랜잭션으로 저장해 일부 키워드만 반영되는 상태를 방지한다.
    try:
        db.flush()
        existing_keywords = list(
            db.scalars(
                select(CompanyKeyword).where(CompanyKeyword.company_id == company.id)
            )
        )
        seen = {
            (item.keyword_type, item.value.casefold())
            for item in existing_keywords
        }
        keywords = list(existing_keywords)
        added_keywords: list[CompanyKeyword] = []
        for item in payload.keywords:
            key = (item.keyword_type, item.value.casefold())
            if key in seen:
                continue
            seen.add(key)
            keyword = CompanyKeyword(
                company_id=company.id,
                keyword_type=item.keyword_type,
                value=item.value,
            )
            db.add(keyword)
            keywords.append(keyword)
            added_keywords.append(keyword)
        db.commit()
        db.refresh(company)
        for keyword in added_keywords:
            db.refresh(keyword)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 기업명 또는 종목코드입니다.") from exc

    # API 응답을 지연시키지 않도록 과거 수집과 모델 분석은 백그라운드에서 시작한다.
    background_tasks.add_task(
        initialize_company_monitoring,
        company.id,
        not is_existing,
        [item.id for item in added_keywords],
    )
    return _to_response(
        db,
        company,
        industry.name,
        keywords,
        is_existing=is_existing,
        added_keyword_count=len(added_keywords),
    )


@router.post("/{company_id}/activate", response_model=CompanyActivationRead)
def activate_company(
    company_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CompanyActivationRead:
    """기사·유효 창 기준을 충족한 기업만 사람 요청으로 활성화한다."""
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    article_count = db.scalar(
        select(func.count(CompanyArticleMatch.article_id)).where(
            CompanyArticleMatch.company_id == company_id
        )
    ) or 0
    window_count = db.scalar(
        select(func.count(CompanyFeatureWindow.id)).where(
            CompanyFeatureWindow.company_id == company_id,
            CompanyFeatureWindow.data_quality != "unavailable",
            CompanyFeatureWindow.article_count > 0,
        )
    ) or 0
    if (
        article_count < settings.readiness_min_articles
        or window_count < settings.readiness_min_nonempty_windows
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"PREPARING 상태입니다. 기사 {article_count}/{settings.readiness_min_articles}건, "
                f"유효 구간 {window_count}/{settings.readiness_min_nonempty_windows}개입니다."
            ),
        )
    activated_at = datetime.now(timezone.utc)
    company.monitoring_status = "active"
    company.analysis_status = "ready"
    company.baseline_ready_at = company.baseline_ready_at or activated_at
    db.commit()
    return CompanyActivationRead(
        company_id=company_id,
        readiness_status="active",
        monitoring_status="active",
        activated_at=activated_at,
    )


@router.put("/{company_id}", response_model=CompanyRead)
def update_company(
    company_id: int,
    payload: CompanyUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> CompanyRead:
    """기업 기본 정보와 키워드 목록을 저장된 설정 전체와 동기화한다."""
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")
    industry = db.get(Industry, payload.industry_id)
    if industry is None:
        raise HTTPException(status_code=404, detail="선택한 산업군을 찾을 수 없습니다.")

    normalized_name = normalize_company_name(payload.name)
    duplicate_company_id = db.scalar(
        select(Company.id).where(
            Company.id != company_id,
            Company.normalized_name == normalized_name,
            Company.industry_id == industry.id,
        )
    )
    if duplicate_company_id is not None:
        raise HTTPException(
            status_code=409,
            detail="같은 산업군에 동일한 이름의 기업이 이미 등록되어 있습니다.",
        )
    if payload.ticker and db.scalar(
        select(Company.id).where(
            Company.id != company_id,
            func.upper(Company.ticker) == payload.ticker,
        )
    ) is not None:
        raise HTTPException(status_code=409, detail="이미 등록된 종목코드입니다.")

    existing_keywords = _get_company_keywords(db, company_id)
    existing_by_key = {
        (item.keyword_type, item.value.casefold()): item for item in existing_keywords
    }
    requested_by_key = {}
    for item in payload.keywords:
        requested_by_key.setdefault(
            (item.keyword_type, item.value.casefold()),
            item,
        )

    # 유지되는 키워드는 ID를 보존하고, 누락 항목만 삭제하며 새 항목만 추가한다.
    added_keywords: list[CompanyKeyword] = []
    for key, keyword in existing_by_key.items():
        requested = requested_by_key.get(key)
        if requested is None:
            db.delete(keyword)
        else:
            keyword.value = requested.value
    for key, requested in requested_by_key.items():
        if key in existing_by_key:
            continue
        keyword = CompanyKeyword(
            company_id=company_id,
            keyword_type=requested.keyword_type,
            value=requested.value,
        )
        db.add(keyword)
        added_keywords.append(keyword)

    name_changed = company.normalized_name != normalized_name
    company.name = payload.name
    company.normalized_name = normalized_name
    company.ticker = payload.ticker
    company.industry_id = industry.id

    try:
        db.commit()
        db.refresh(company)
        keywords = _get_company_keywords(db, company_id)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="기업명, 종목코드 또는 키워드가 다른 설정과 충돌합니다.",
        ) from exc

    # 새 이름은 전체 검색어를, 새 키워드는 해당 검색어만 최근 7일 범위로 다시 수집한다.
    if company.monitoring_status not in {"paused", "archived"} and (
        name_changed or added_keywords
    ):
        background_tasks.add_task(
            refresh_company_monitoring,
            company.id,
            name_changed,
            [item.id for item in added_keywords],
        )
    return _to_response(
        db,
        company,
        industry.name,
        keywords,
        added_keyword_count=len(added_keywords),
    )


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(company_id: int, db: Session = Depends(get_db)) -> Response:
    """기업과 전용 수집·분석 자료를 삭제하고 공유되지 않은 기사도 정리한다."""
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="기업을 찾을 수 없습니다.")

    # 기업을 지우면 FK CASCADE로 키워드, 수집 작업, 필터 판정, 기사 매칭,
    # 기준선, 위험 이벤트와 기업 간 관계가 함께 삭제된다.
    db.delete(company)
    db.flush()

    # 기사는 여러 기업이 함께 수집할 수 있으므로 다른 기업의 연결이 전혀 없는
    # 정제 기사와 원문 기사만 삭제한다. 공유 기사는 보존해 다른 기업의 이력을 지킨다.
    db.execute(
        delete(NewsArticle).where(
            ~exists(
                select(CompanyArticleMatch.article_id).where(
                    CompanyArticleMatch.article_id == NewsArticle.id
                )
            ),
            ~exists(
                select(ArticleFilterResult.id).where(
                    ArticleFilterResult.curated_article_id == NewsArticle.id
                )
            ),
        )
    )
    db.execute(
        delete(RawNewsArticle).where(
            ~exists(
                select(ArticleFilterResult.id).where(
                    ArticleFilterResult.raw_article_id == RawNewsArticle.id
                )
            ),
            ~exists(
                select(NewsArticle.id).where(
                    NewsArticle.raw_article_id == RawNewsArticle.id
                )
            ),
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
