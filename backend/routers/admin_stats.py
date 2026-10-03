from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, text

from database import get_db
from dependencies import get_current_staff
from store_context import default_store, get_store, visible_store_id
from models import Order, PaymentReview, StoreSettings

router = APIRouter(prefix="/api/v1/admin/analytics", tags=["Admin Stats"])


@router.get("/")
async def get_analytics(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    scope = visible_store_id(staff_info)
    scoped_store = await get_store(db, scope) if scope is not None else await default_store(db)
    if scoped_store is not None:
        timezone_name = scoped_store.timezone or "Asia/Phnom_Penh"
    else:
        settings_result = await db.execute(select(StoreSettings).limit(1))
        settings = settings_result.scalars().first()
        timezone_name = settings.timezone if settings else "Asia/Phnom_Penh"
    try:
        store_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=500, detail="Store timezone is invalid")

    today = datetime.now(store_tz).date()
    end_date = to_date or today
    start_date = from_date or (end_date - timedelta(days=6))
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="The start date must not be after the end date")
    start_local = datetime.combine(start_date, time.min, store_tz)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, store_tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    # 门店隔离：门店经理只能看本店财务（总部账号不过滤）。
    # 历史数据（store_id 为空）保持可见，与其它接口一致。
    def scoped(query):
        if scope is None:
            return query
        return query.filter((Order.store_id == scope) | (Order.store_id.is_(None)))

    result = await db.execute(scoped(
        select(Order).filter(Order.created_at >= start_utc, Order.created_at < end_utc)))
    orders = result.scalars().all()
    reviews_result = await db.execute(scoped(
        select(Order.currency, func.sum(func.coalesce(Order.external_due_minor, Order.total_minor)))
        .select_from(PaymentReview)
        .join(Order, PaymentReview.order_id == Order.id)
        .filter(PaymentReview.decision == "APPROVED", PaymentReview.created_at >= start_utc,
                PaymentReview.created_at < end_utc)
        .group_by(Order.currency)
    ))
    confirmed_by_currency = {currency: int(amount or 0) for currency, amount in reviews_result.all()}

    # 钱包抵扣的订单没有 PaymentReview 记录（钱包部分下单即扣款），
    # 只按 PaymentReview 汇总会让财务日报**漏掉这部分收入**。
    # 混合支付只统计钱包实际抵扣的部分；ABA 差额在人工确认后由上面的
    # PaymentReview 汇总。
    wallet_result = await db.execute(
        text(
            """
            SELECT o.currency,
                   sum(COALESCE(p.amount, 0) - COALESCE(p.refunded_amount, 0)) AS net_minor
              FROM public.orders o
              LEFT JOIN wallet.order_payments p ON p.biz_id = o.public_code
             WHERE o.payment_method IN ('WALLET', 'MIXED')
               AND o.created_at >= :start_utc AND o.created_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR o.store_id = CAST(:store_id AS INTEGER)
                    OR o.store_id IS NULL)
             GROUP BY o.currency
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": scope},
    )
    for currency, net in wallet_result.all():
        confirmed_by_currency[currency] = confirmed_by_currency.get(currency, 0) + int(net or 0)

    totals: dict[str, dict[str, int]] = {}
    for order in orders:
        bucket = totals.setdefault(order.currency, {"order_total_minor": 0, "in_review_minor": 0, "cancelled_total_minor": 0})
        bucket["order_total_minor"] += order.total_minor
        if order.payment_status == "PROOF_SUBMITTED":
            bucket["in_review_minor"] += order.external_due_minor if order.external_due_minor is not None else order.total_minor
        if order.order_status == "CANCELLED":
            bucket["cancelled_total_minor"] += order.total_minor
    for currency, bucket in totals.items():
        bucket["confirmed_receipts_minor"] = confirmed_by_currency.get(currency, 0)

    for currency, amount in confirmed_by_currency.items():
        totals.setdefault(currency, {"order_total_minor": 0, "in_review_minor": 0, "cancelled_total_minor": 0,
                                     "confirmed_receipts_minor": amount})

    return {
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "timezone": timezone_name,
        "order_volume": len(orders),
        "cancelled_count": sum(1 for order in orders if order.order_status == "CANCELLED"),
        "by_currency": [{"currency": currency, **values} for currency, values in sorted(totals.items())],
        "manual_review_basis": True,
    }
