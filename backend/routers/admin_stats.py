from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import case, cast, func, Integer, or_, text

from database import get_db
from dependencies import get_current_staff
from store_context import default_store, get_store, visible_store_id
from models import Order, OrderItem, OrderRefund, PaymentReview, StoreSettings, Customer, CustomerDailyActivity, Store
from refund_metrics import ZERO_REFUND_METRICS, refund_metrics

router = APIRouter(prefix="/api/v1/admin/analytics", tags=["Admin Stats"])
dashboard_router = APIRouter(prefix="/api/v1/admin", tags=["Admin Dashboard"])


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
    external_by_currency = {currency: int(amount or 0) for currency, amount in reviews_result.all()}

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
    wallet_by_currency = {currency: int(net or 0) for currency, net in wallet_result.all()}

    # 充值是现金流入和钱包负债增加，不是订单销售额。
    # 只统计已入账金额，赠送金另行展示，避免把未消费余额算成收入。
    recharge_result = await db.execute(
        text(
            """
            SELECT currency, COALESCE(sum(received_amount), 0) AS credited_minor
              FROM wallet.recharge_orders
             WHERE status = 'credited'
               AND reviewed_at >= :start_utc AND reviewed_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR store_id = CAST(:store_id AS INTEGER))
             GROUP BY currency
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": scope},
    )
    recharge_by_currency = {currency: int(amount or 0) for currency, amount in recharge_result.all()}

    totals: dict[str, dict[str, int]] = {}
    for order in orders:
        bucket = totals.setdefault(order.currency, {
            "order_total_minor": 0, "order_sales_minor": 0,
            "order_subtotal_minor": 0, "order_discount_minor": 0,
            "order_sales_subtotal_minor": 0, "order_sales_discount_minor": 0,
            "order_sales_delivery_fee_minor": 0, "order_sales_service_fee_minor": 0,
            "in_review_minor": 0, "cancelled_total_minor": 0,
        })
        bucket["order_total_minor"] += order.total_minor
        subtotal = int(order.subtotal_minor if order.subtotal_minor is not None else order.total_minor)
        discount = int(order.coupon_discount_minor or 0)
        bucket["order_subtotal_minor"] += subtotal
        bucket["order_discount_minor"] += discount
        if order.order_status != "CANCELLED":
            bucket["order_sales_minor"] += order.total_minor
            bucket["order_sales_subtotal_minor"] += subtotal
            bucket["order_sales_discount_minor"] += discount
            bucket["order_sales_delivery_fee_minor"] += int(order.delivery_fee_minor or 0)
            bucket["order_sales_service_fee_minor"] += int(order.service_fee_minor or 0)
        if order.payment_status == "PROOF_SUBMITTED":
            bucket["in_review_minor"] += order.external_due_minor if order.external_due_minor is not None else order.total_minor
        if order.order_status == "CANCELLED":
            bucket["cancelled_total_minor"] += order.total_minor
    for currency, bucket in totals.items():
        external_amount = external_by_currency.get(currency, 0)
        wallet_amount = wallet_by_currency.get(currency, 0)
        recharge_amount = recharge_by_currency.get(currency, 0)
        # confirmed_receipts_minor 保留原 API 兼容性；新字段拆开显示，防止
        # 使用者将充值、订单销售额和钱包抵扣误相加。
        bucket["external_order_receipts_minor"] = external_amount
        bucket["wallet_paid_minor"] = wallet_amount
        bucket["confirmed_receipts_minor"] = external_amount + wallet_amount
        bucket["recharge_credited_minor"] = recharge_amount
        bucket["cash_inflow_minor"] = external_amount + recharge_amount

    for currency in set(external_by_currency) | set(wallet_by_currency) | set(recharge_by_currency):
        totals.setdefault(currency, {"order_total_minor": 0, "order_sales_minor": 0,
                                     "order_subtotal_minor": 0, "order_discount_minor": 0,
                                     "order_sales_subtotal_minor": 0, "order_sales_discount_minor": 0,
            "order_sales_delivery_fee_minor": 0, "order_sales_service_fee_minor": 0,
                                     "in_review_minor": 0, "cancelled_total_minor": 0,
                                     "external_order_receipts_minor": external_by_currency.get(currency, 0),
                                     "wallet_paid_minor": wallet_by_currency.get(currency, 0),
                                     "confirmed_receipts_minor": external_by_currency.get(currency, 0) + wallet_by_currency.get(currency, 0),
                                     "recharge_credited_minor": recharge_by_currency.get(currency, 0),
                                     "cash_inflow_minor": external_by_currency.get(currency, 0) + recharge_by_currency.get(currency, 0)})

    # 订单退款（缺货删品 / 取消）：ABA 已转账的退款是真实现金流出，从净流入里扣除；
    # 钱包退款已体现在钱包扣款净额里，只展示；待处理退款是快照，不进现金流。
    refunds = await refund_metrics(db, start_utc=start_utc, end_utc=end_utc, store_id=scope,
                                   include_legacy_null_store=scope is not None)
    for currency in refunds:
        if currency not in totals:
            totals[currency] = {key: 0 for key in (
                "order_total_minor", "order_sales_minor", "order_subtotal_minor", "order_discount_minor",
                "order_sales_subtotal_minor", "order_sales_discount_minor", "order_sales_delivery_fee_minor",
                "order_sales_service_fee_minor", "in_review_minor", "cancelled_total_minor",
                "external_order_receipts_minor", "wallet_paid_minor", "confirmed_receipts_minor",
                "recharge_credited_minor", "cash_inflow_minor")}
    for currency, bucket in totals.items():
        bucket.update(refunds.get(currency, ZERO_REFUND_METRICS))
        bucket["net_cash_inflow_minor"] = bucket.get("cash_inflow_minor", 0) - bucket["refund_aba_done_minor"]

    return {
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "timezone": timezone_name,
        "order_volume": len(orders),
        "cancelled_count": sum(1 for order in orders if order.order_status == "CANCELLED"),
        "by_currency": [{"currency": currency, **values} for currency, values in sorted(totals.items())],
        "manual_review_basis": True,
    }


@dashboard_router.get("/dashboard")
async def get_dashboard(days: int = Query(7, ge=7, le=30), store: str | None = None,
                        staff_info: dict = Depends(get_current_staff),
                        db: AsyncSession = Depends(get_db)):
    """Single-request overview aggregation using the same store/date rules as finance."""
    if days not in {7, 30}:
        raise HTTPException(status_code=422, detail="days must be 7 or 30")
    scope = visible_store_id(staff_info)
    selected = await get_store(db, scope) if scope is not None else await default_store(db)
    if scope is None and store:
        selected = (await db.execute(select(Store).filter(Store.code == store, Store.status == "ACTIVE"))).scalars().first()
        if selected is None:
            raise HTTPException(status_code=404, detail="Store not found")
        scope = selected.id
    timezone_name = (selected.timezone if selected else None) or "Asia/Phnom_Penh"
    try:
        store_tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Store timezone is invalid") from exc
    today = datetime.now(store_tz).date()
    start_date = today - timedelta(days=days - 1)
    start_utc = datetime.combine(start_date, time.min, store_tz).astimezone(timezone.utc)
    end_utc = datetime.combine(today + timedelta(days=1), time.min, store_tz).astimezone(timezone.utc)

    def order_scope():
        return or_(Order.store_id == scope, Order.store_id.is_(None)) if scope is not None else True

    # Use bounded GROUP BY queries instead of loading every order/customer/item
    # into Python just to render the dashboard.
    local_day = func.date(func.timezone(timezone_name, Order.created_at))
    local_hour = cast(func.extract("hour", func.timezone(timezone_name, Order.created_at)), Integer)
    sales_case = case((Order.order_status != "CANCELLED", Order.total_minor), else_=0)
    series = {(start_date + timedelta(days=i)).isoformat(): {"orders": 0, "sales_minor": 0, "new_users": 0, "daily_active": 0} for i in range(days)}
    day_rows = await db.execute(select(local_day, func.count(Order.id), func.coalesce(func.sum(sales_case), 0)).where(
        Order.created_at >= start_utc, Order.created_at < end_utc, order_scope()).group_by(local_day))
    for day, count, amount in day_rows.all():
        if day.isoformat() in series:
            series[day.isoformat()]["orders"] = int(count or 0)
            series[day.isoformat()]["sales_minor"] = int(amount or 0)

    customer_day = func.date(func.timezone(timezone_name, Customer.created_at))
    customer_rows = await db.execute(select(customer_day, func.count(Customer.id)).where(
        Customer.created_at < end_utc,
        or_(Customer.store_id == scope, Customer.store_id.is_(None)) if scope is not None else True,
    ).group_by(customer_day))
    total_users = 0
    for day, count in customer_rows.all():
        total_users += int(count or 0)
        if day.isoformat() in series:
            series[day.isoformat()]["new_users"] = int(count or 0)

    activity_rows = await db.execute(select(CustomerDailyActivity.activity_date, func.count(func.distinct(CustomerDailyActivity.customer_id))).where(
        CustomerDailyActivity.activity_date >= start_date,
        CustomerDailyActivity.activity_date <= today,
        or_(CustomerDailyActivity.store_id == scope, CustomerDailyActivity.store_id.is_(None)) if scope is not None else True,
    ).group_by(CustomerDailyActivity.activity_date))
    for day, count in activity_rows.all():
        if day.isoformat() in series:
            series[day.isoformat()]["daily_active"] = int(count or 0)

    status_rows = await db.execute(select(Order.order_status, func.count(Order.id)).where(
        Order.created_at >= start_utc, Order.created_at < end_utc, order_scope()).group_by(Order.order_status))
    status_breakdown = {status: int(count or 0) for status, count in status_rows.all()}
    payment_rows = await db.execute(select(Order.payment_method, func.count(Order.id)).where(
        Order.created_at >= start_utc, Order.created_at < end_utc, order_scope()).group_by(Order.payment_method))
    payment_breakdown = {method or "UNKNOWN": int(count or 0) for method, count in payment_rows.all()}
    hourly_rows = await db.execute(select(local_hour, func.count(Order.id)).where(
        Order.created_at >= start_utc, Order.created_at < end_utc, order_scope()).group_by(local_hour))
    hourly = [0] * 24
    for hour, count in hourly_rows.all():
        if hour is not None and 0 <= int(hour) < 24:
            hourly[int(hour)] = int(count or 0)

    pending_orders = await db.scalar(select(func.count(Order.id)).where(Order.order_status == "NEW", order_scope())) or 0
    pending_review = await db.scalar(select(func.count(Order.id)).where(Order.payment_status == "PROOF_SUBMITTED", order_scope())) or 0
    name_expr = func.coalesce(OrderItem.product_name_snapshot.op("->>")("en"), "—")
    item_rows = await db.execute(select(name_expr, func.sum(OrderItem.quantity)).join(Order, OrderItem.order_id == Order.id).where(
        Order.created_at >= start_utc, Order.created_at < end_utc,
        Order.order_status != "CANCELLED", order_scope()).group_by(name_expr).order_by(func.sum(OrderItem.quantity).desc()).limit(10))
    top_products = [{"name": str(name), "quantity": int(quantity or 0)} for name, quantity in item_rows.all()]
    pending_refunds = await db.scalar(select(func.count(OrderRefund.id)).join(Order, OrderRefund.order_id == Order.id).where(
        OrderRefund.status == "PENDING", order_scope())) or 0
    sales = sum(item["sales_minor"] for item in series.values())
    return {"from": start_date.isoformat(), "to": today.isoformat(), "timezone": timezone_name,
            "currency": (selected.currency if selected else "USD"),
            "kpis": {"today_orders": series[today.isoformat()]["orders"], "today_sales_minor": series[today.isoformat()]["sales_minor"],
                     "pending_orders": int(pending_orders), "pending_review": int(pending_review),
                     "pending_refunds": int(pending_refunds), "total_users": total_users,
                     "new_users": series[today.isoformat()]["new_users"], "daily_active": series[today.isoformat()]["daily_active"]},
            "series": [{"date": key, **value} for key, value in series.items()],
            "status_breakdown": status_breakdown, "payment_breakdown": payment_breakdown,
            "hourly": [{"hour": hour, "orders": count} for hour, count in enumerate(hourly)],
            "top_products": top_products, "sales_minor": sales}
