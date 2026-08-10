from datetime import datetime, timezone
import re
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Company, CompanyKeyword, Industry
from app.schemas import CompanyCreate, CompanyKeywordRead, CompanyRead
from app.services.monitoring_pipeline import initialize_company_monitoring


router = APIRouter(prefix="/companies", tags=["companies"])


def _to_response(
    company: Company,
    industry_name: str | None,
    keywords: list[CompanyKeyword],
    is_existing: bool = False,
    added_keyword_count: int = 0,
) -> CompanyRead:
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
    )


def normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s·._-]+", "", normalized)


@router.get("", response_model=list[CompanyRead])
def list_companies(db: Session = Depends(get_db)) -> list[CompanyRead]:
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
        _to_response(company, industry_name, grouped.get(company.id, []))
        for company, industry_name in companies
    ]


@router.post("", response_model=CompanyRead)
def create_or_update_company(
    payload: CompanyCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> CompanyRead:
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

    background_tasks.add_task(
        initialize_company_monitoring,
        company.id,
        not is_existing,
        [item.id for item in added_keywords],
    )
    return _to_response(
        company,
        industry.name,
        keywords,
        is_existing=is_existing,
        added_keyword_count=len(added_keywords),
    )
