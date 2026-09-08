"""서울 달력 기준 기간 집계에 공통으로 사용하는 SQL 표현식과 경계를 제공한다."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func


SEOUL = ZoneInfo("Asia/Seoul")


def seoul_date_range(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """양 끝 날짜를 포함하는 서울 달력 기간을 UTC 반개구간으로 반환한다."""
    if start_date > end_date:
        raise ValueError("종료일은 시작일 이후여야 합니다.")
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=SEOUL)
    end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=SEOUL)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def seoul_period_start(days: int, *, now: datetime | None = None) -> tuple[date, datetime]:
    """오늘을 포함한 최근 ``days``개 서울 달력 날짜의 시작을 반환한다."""
    current = now or datetime.now(SEOUL)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_start_date = current.astimezone(SEOUL).date() - timedelta(days=days - 1)
    local_start = datetime.combine(local_start_date, datetime.min.time(), tzinfo=SEOUL)
    return local_start_date, local_start.astimezone(timezone.utc)


def seoul_day_bucket(timestamp_expression):
    """시간대가 있는 DB 시각을 서울 현지 날짜의 자정 버킷으로 변환한다."""
    return func.date_trunc(
        "day",
        func.timezone("Asia/Seoul", timestamp_expression),
    )
