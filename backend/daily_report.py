"""每日经营报表：统计 + 投递 + 定时。

时间口径：**店铺时区的自然日**（默认 Asia/Phnom_Penh）。
「昨天的数据」= 昨天 00:00:00.000 至 23:59:59.999（含），
换算成 UTC 再去查库——`admin_stats` 用的是同一套口径，两处数字应当一致。

投递保证「一天一份」：
  1. 先往 `report_deliveries` 抢一条记录（(report_key, chat_id) 唯一）；
  2. 抢不到 -> 当天已经发过，直接返回 skipped；
  3. 发送失败 -> 删掉这条记录，下一分钟重试（宁可迟到，不能漏发）；
  4. 发送成功 -> 回填 message_id。

调度器与外部 cron 走的是同一个 `send_daily_report()`，所以两条路径天然互斥。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models import Customer, Order, ReportDelivery, Store, StoreSettings
from telegram_service import send_bot_message, tr

log = logging.getLogger("teacafe.report")

DEFAULT_TZ = "Asia/Phnom_Penh"
DEFAULT_HOUR = 8


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

async def store_timezone(db: AsyncSession, store_id: int | None = None) -> ZoneInfo:
    if store_id is not None:
        store = (await db.execute(select(Store).filter(Store.id == store_id))).scalars().first()
        name = store.timezone if store else DEFAULT_TZ
    else:
        result = await db.execute(select(StoreSettings).limit(1))
        settings = result.scalars().first()
        name = (settings.timezone if settings else None) or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("店铺时区 %s 无效，回退到 %s", name, DEFAULT_TZ)
        return ZoneInfo(DEFAULT_TZ)


async def build_daily_report(db: AsyncSession, report_date: date, tz: ZoneInfo | None = None,
                             store_id: int | None = None) -> dict:
    """统计 `report_date` 这一天（店铺时区自然日）的经营数据。

    `store_id` 非空时只统计该门店（门店群只应收到本店数据）；为空 = 总部视角。
    """
    store = None
    if store_id is not None:
        store = (await db.execute(select(Store).filter(Store.id == store_id))).scalars().first()
    # Use the same guarded resolver for store-specific timezones so an invalid
    # branch setting falls back cleanly instead of aborting the report job.
    tz = tz or await store_timezone(db, store_id)
    start_local = datetime.combine(report_date, time.min, tz)
    end_local = start_local + timedelta(days=1)          # 左闭右开：00:00:00 ~ 23:59:59.999
    start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    order_query = select(Order).filter(Order.created_at >= start_utc, Order.created_at < end_utc)
    if store_id is not None:
        order_query = order_query.filter(Order.store_id == store_id)
    orders = (await db.execute(order_query)).scalars().all()

    if store is not None:
        currency = store.currency or "USD"
    else:
        currency = "USD"
        settings = (await db.execute(select(StoreSettings).limit(1))).scalars().first()
        if settings and settings.currency:
            currency = settings.currency

    order_total = sum(o.total_minor for o in orders)
    by_state: dict[str, int] = {}
    for order in orders:
        by_state[order.order_status] = by_state.get(order.order_status, 0) + 1

    # 人工转账：以「审核通过的 PaymentReview」为准（与既有财务口径一致）
    manual_received = (await db.execute(
        text(
            """
            SELECT COALESCE(sum(COALESCE(o.external_due_minor, o.total_minor)), 0)
              FROM public.payment_reviews r
              JOIN public.orders o ON o.id = r.order_id
             WHERE r.decision = 'APPROVED'
               AND r.created_at >= :start_utc AND r.created_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR o.store_id = CAST(:store_id AS INTEGER))
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": store_id},
    )).scalar() or 0

    # 钱包抵扣：净额 = 实际钱包支付金额 - 已退金额；混合支付只统计钱包部分
    wallet_received = (await db.execute(
        text(
            """
            SELECT COALESCE(sum(COALESCE(p.amount, 0) - COALESCE(p.refunded_amount, 0)), 0)
              FROM public.orders o
              LEFT JOIN wallet.order_payments p ON p.biz_id = o.public_code
             WHERE o.payment_method IN ('WALLET', 'MIXED')
               AND o.created_at >= :start_utc AND o.created_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR o.store_id = CAST(:store_id AS INTEGER))
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": store_id},
    )).scalar() or 0

    refunded = (await db.execute(
        text(
            """
            SELECT COALESCE(sum(p.refunded_amount), 0)
              FROM public.orders o
              JOIN wallet.order_payments p ON p.biz_id = o.public_code
             WHERE o.created_at >= :start_utc AND o.created_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR o.store_id = CAST(:store_id AS INTEGER))
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": store_id},
    )).scalar() or 0

    # 钱包充值：以「入账时间」为准
    recharges = (await db.execute(
        text(
            """
            SELECT count(*)                                   AS credited_count,
                   COALESCE(sum(received_amount), 0)          AS credited_minor,
                   COALESCE(sum(bonus_amount), 0)             AS bonus_minor
              FROM wallet.recharge_orders
             WHERE status = 'credited'
               AND reviewed_at >= :start_utc AND reviewed_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR store_id = CAST(:store_id AS INTEGER))
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": store_id},
    )).mappings().first()

    pending_recharges = (await db.execute(
        text(
            """
            SELECT count(*) AS n FROM wallet.recharge_orders
             WHERE status IN ('awaiting_proof', 'under_review')
               AND created_at >= :start_utc AND created_at < :end_utc
               AND (CAST(:store_id AS INTEGER) IS NULL
                    OR store_id = CAST(:store_id AS INTEGER))
            """
        ),
        {"start_utc": start_utc, "end_utc": end_utc, "store_id": store_id},
    )).scalar() or 0

    new_customer_query = select(func.count()).select_from(Customer).filter(
        Customer.created_at >= start_utc, Customer.created_at < end_utc)
    if store_id is not None:
        new_customer_query = new_customer_query.filter(Customer.store_id == store_id)
    new_customers = (await db.execute(new_customer_query)).scalar() or 0

    # 遗留待处理：截至当天结束时仍未审核的收款凭证（不是当天新增，而是「还压着」）
    backlog_query = select(func.count()).select_from(Order).filter(
        Order.payment_status == "PROOF_SUBMITTED",
        Order.created_at >= start_utc, Order.created_at < end_utc)
    if store_id is not None:
        backlog_query = backlog_query.filter(Order.store_id == store_id)
    backlog_review = (await db.execute(backlog_query)).scalar() or 0

    return {
        "date": report_date.isoformat(),
        "store_id": store_id,
        "timezone": str(tz),
        "currency": currency,
        "orders": len(orders),
        "orders_total_minor": int(order_total),
        "orders_by_status": by_state,
        "received_manual_minor": int(manual_received),
        "received_wallet_minor": int(wallet_received),
        "received_total_minor": int(manual_received) + int(wallet_received),
        "refunded_minor": int(refunded),
        "recharges_credited": int(recharges["credited_count"]),
        "recharges_amount_minor": int(recharges["credited_minor"]),
        "recharges_bonus_minor": int(recharges["bonus_minor"]),
        "recharges_pending": int(pending_recharges),
        "new_customers": int(new_customers),
        "awaiting_payment_review": int(backlog_review),
    }


def format_daily_report(report: dict, language: str = "en") -> str:
    """渲染成 Telegram 文案。金额用最小单位整数除以 100 显示。"""
    currency = report.get("currency", "USD")

    def money(minor: int) -> str:
        digits = 0 if currency.upper() in {"KHR", "JPY", "VND"} else 2
        return f"{minor / (10 ** digits):,.{digits}f} {currency}"

    by_status = report.get("orders_by_status", {})
    orders_line = tr("report.daily.orders", language, count=report["orders"])
    if by_status:      # 没有订单时不要留一个孤零零的分隔符
        orders_line += " · " + " / ".join(
            f"{tr(f'order.status.{k.lower()}', language)} {v}"
            for k, v in sorted(by_status.items()))
    lines = [
        tr("report.daily.title", language),
        tr("report.daily.date", language, date=report["date"]),
        "━━━━━━━━━━━━━━",
        orders_line,
        tr("report.daily.orderTotal", language, amount=money(report["orders_total_minor"])),
        tr("report.daily.received", language, amount=money(report["received_total_minor"])),
        "  · " + tr("report.daily.receivedManual", language, amount=money(report["received_manual_minor"])),
        "  · " + tr("report.daily.receivedWallet", language, amount=money(report["received_wallet_minor"])),
    ]
    if report["refunded_minor"]:
        lines.append(tr("report.daily.refunded", language, amount=money(report["refunded_minor"])))
    lines += [
        "━━━━━━━━━━━━━━",
        tr("report.daily.recharges", language, count=report["recharges_credited"],
           amount=money(report["recharges_amount_minor"])),
        tr("report.daily.bonus", language, amount=money(report["recharges_bonus_minor"])),
        tr("report.daily.pendingRecharges", language, count=report["recharges_pending"]),
        tr("report.daily.awaitingReview", language, count=report["awaiting_payment_review"]),
        tr("report.daily.newCustomers", language, count=report["new_customers"]),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 投递
# ---------------------------------------------------------------------------

def _report_key(report_date: date, store_code: str | None = None) -> str:
    """一天一份**每店**。带上门店码，否则第二家店会被当成「已发送」直接跳过。"""
    suffix = f":{store_code}" if store_code else ""
    return f"daily_sales:{report_date.isoformat()}{suffix}"


async def daily_report_status(db: AsyncSession, report_date: date, chat_id: str | None,
                              store_code: str | None = None) -> dict | None:
    """返回当天的投递记录（用来在后台显示「已发送」）。"""
    filters = [ReportDelivery.report_key == _report_key(report_date, store_code)]
    if chat_id:
        filters.append(ReportDelivery.chat_id == chat_id)
    row = (await db.execute(select(ReportDelivery).filter(*filters).limit(1))).scalars().first()
    if row is None:
        return None
    return {"chat_id": row.chat_id, "message_id": row.message_id, "sent_at": row.sent_at}


async def send_daily_report(
    db: AsyncSession, report_date: date, *, chat_id: str, language: str = "en",
    force: bool = False, store_id: int | None = None, store_code: str | None = None,
) -> dict:
    """把某天的报表发到指定会话。**幂等**：同一天同一会话只会成功发送一次。

    `force=True` 时忽略已发送记录（后台「立即发送」用），但仍然会更新同一条记录。
    """
    report = await build_daily_report(db, report_date, store_id=store_id)
    key = _report_key(report_date, store_code)

    if not force:
        # 1) 抢占当天的投递记录；抢不到说明已经发过
        claim = ReportDelivery(report_key=key, report_date=report_date, chat_id=chat_id,
                               payload=report, store_id=store_id)
        db.add(claim)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = await daily_report_status(db, report_date, chat_id, store_code)
            log.info("每日报表 %s 已发送过（%s），跳过", key, existing)
            return {"sent": False, "skipped": "already_sent", "report": report, "delivery": existing}
    else:
        claim = (await db.execute(
            select(ReportDelivery).filter(ReportDelivery.report_key == key,
                                          ReportDelivery.chat_id == chat_id)
        )).scalars().first()
        if claim is None:
            claim = ReportDelivery(report_key=key, report_date=report_date, chat_id=chat_id,
                                   payload=report)
            db.add(claim)
        else:
            claim.payload = report
        await db.commit()

    # 2) 发送；失败就把占位记录删掉，让下一次（下一分钟或手动重试）还能发出去
    try:
        from telegram_service import _bot  # 局部导入，便于测试替换

        bot = _bot()
        if bot is None:
            raise RuntimeError("BOT_TOKEN 未配置")
        try:
            message = await bot.send_message(chat_id=chat_id,
                                            text=format_daily_report(report, language))
        finally:
            await bot.session.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("每日报表发送失败 %s -> %s: %s", key, chat_id, exc)
        if not force and claim is not None and claim.id is not None:
            await db.delete(claim)
            await db.commit()
        return {"sent": False, "error": str(exc), "report": report}

    # 3) 回填消息 id
    claim.message_id = str(getattr(message, "message_id", "")) or None
    claim.sent_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("每日报表已发送 %s -> %s (message_id=%s)", key, chat_id, claim.message_id)
    return {"sent": True, "report": report,
            "delivery": {"chat_id": chat_id, "message_id": claim.message_id,
                         "sent_at": claim.sent_at.isoformat()}}


# ---------------------------------------------------------------------------
# 进程内定时
# ---------------------------------------------------------------------------

def report_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("DAILY_REPORT_HOUR", str(DEFAULT_HOUR)))))
    except ValueError:
        return DEFAULT_HOUR


def report_enabled() -> bool:
    return os.getenv("DAILY_REPORT_ENABLED", "true").lower() not in {"0", "false", "no"}


async def _tick(db_factory) -> None:
    """到点就发昨天的报表——**每家门店各发各的群**。

    以前只有一家店的群和一个全局报表，多门店之后：
      * 门店群只能收到本店数据；
      * 每店的投递记录独立（report_key 带门店码），一家失败不影响其它家。
    """
    from database import AsyncSessionLocal

    async with (db_factory or AsyncSessionLocal)() as db:
        now = datetime.now(timezone.utc)
        rows = (await db.execute(
            select(Store).filter(Store.status == "ACTIVE").order_by(Store.id))).scalars().all()
        # 先把要用的值取出来：send_daily_report 里会 commit，
        # commit 之后 ORM 对象全部过期，再访问属性会触发同步懒加载 ->
        # MissingGreenlet（异步 session 里拿不到数据）。
        stores = [(row.id, row.code, row.timezone, row.telegram_staff_group_id,
                   row.staff_group_language) for row in rows]
        for store_id, code, tz_name, chat_id, language in stores:
            try:
                tz = ZoneInfo(tz_name or DEFAULT_TZ)
            except ZoneInfoNotFoundError:
                tz = ZoneInfo(DEFAULT_TZ)
            local_now = now.astimezone(tz)
            if local_now.hour != report_hour():
                continue
            if not chat_id:
                continue          # 这家店没配群就跳过（不是错误）
            yesterday = local_now.date() - timedelta(days=1)
            await send_daily_report(
                db, yesterday, chat_id=str(chat_id),
                language=(language or "en"),
                store_id=store_id, store_code=code,
            )


async def run_scheduler(db_factory=None) -> None:
    """每分钟醒一次；发送逻辑本身幂等，所以重启/多副本都不会重复发。"""
    log.info("每日报表调度器已启动（每天 %02d:00 店铺时区，报表区间=前一天 00:00:00-23:59:59）",
             report_hour())
    while True:
        try:
            await _tick(db_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 调度线程不能因为一次失败就退出
            log.warning("每日报表调度出错: %s", exc)
        await asyncio.sleep(60)


def start_scheduler(db_factory=None) -> asyncio.Task | None:
    if not report_enabled():
        log.info("DAILY_REPORT_ENABLED=false，不启动每日报表调度器")
        return None
    return asyncio.create_task(run_scheduler(db_factory))
