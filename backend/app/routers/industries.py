"""기업 등록 화면에서 사용할 산업군 조회 API를 제공한다."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Industry
from app.schemas import IndustryRead


router = APIRouter(prefix="/industries", tags=["industries"])


@router.get("", response_model=list[IndustryRead])
def list_industries(db: Session = Depends(get_db)) -> list[Industry]:
    """선택 가능한 산업 목록을 이름순으로 반환한다."""
    return list(db.scalars(select(Industry).order_by(Industry.name)).all())
