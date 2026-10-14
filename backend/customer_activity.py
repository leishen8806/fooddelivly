"""Record de-duplicated Mini App opens by the store's local calendar day."""
from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import CustomerDailyActivity

DEFAULT_TIMEZONE = "Asia/Phnom_Penh"
logger = logging.getLogger("teacafe.customer_activity")


def activity_date_for_store(now: datetime, timezone_name: str | None) -> date:
    """Return the store-local date for an aware UTC timestamp.

    Invalid or missing store time zones fall back to the configured product
    default so a bad store setting cannot make authentication fail.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return now.astimezone(zone).date()


async def upsert_customer_activity(
    db: AsyncSession,
    *,
    customer_id: int,
    store_id: int | None,
    store_timezone: str | None,
    now: datetime | None = None,
) -> None:
    """Upsert one activity row without committing the surrounding transaction."""
    seen_at = now or datetime.now(timezone.utc)
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=timezone.utc)
    activity_date = activity_date_for_store(seen_at, store_timezone)
    statement = pg_insert(CustomerDailyActivity).values(
        customer_id=customer_id,
        activity_date=activity_date,
        store_id=store_id,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[CustomerDailyActivity.customer_id, CustomerDailyActivity.activity_date],
        set_={
            "store_id": statement.excluded.store_id,
            "last_seen_at": func.greatest(
                CustomerDailyActivity.last_seen_at,
                statement.excluded.last_seen_at,
            ),
        },
    )
    await db.execute(statement)


async def record_customer_activity_best_effort(
    db: AsyncSession,
    *,
    customer_id: int,
    store_id: int | None,
    store_timezone: str | None,
) -> bool:
    """Record activity without making authentication depend on this table."""
    try:
        await upsert_customer_activity(
            db,
            customer_id=customer_id,
            store_id=store_id,
            store_timezone=store_timezone,
        )
        await db.commit()
        return True
    except Exception:  # noqa: BLE001 - best effort by contract
        await db.rollback()
        logger.exception("customer_daily_activity_upsert_failed customer_id=%s", customer_id)
        return False
