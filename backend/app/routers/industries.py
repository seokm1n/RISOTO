from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Industry
from app.schemas import IndustryRead


router = APIRouter(prefix="/industries", tags=["industries"])


@router.get("", response_model=list[IndustryRead])
def list_industries(db: Session = Depends(get_db)) -> list[Industry]:
    return list(db.scalars(select(Industry).order_by(Industry.name)).all())
