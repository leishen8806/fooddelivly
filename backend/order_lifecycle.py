"""Shared order cancellation, coupon release and unpaid-order expiry rules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coupon_service import release_for_cancel
from models import AuditLog, Customer, Order, OrderEvent, OrderItem, OrderIssue, OrderRefund, PaymentReview, Store


async def _refund_totals(db: AsyncSession, order_id: int) -> tuple[int, int]:
    """Return reserved/done wallet and ABA refund totals for an order.

    PENDING rows are included because they reserve money that must not be
    generated again by a later cancellation.  The wallet ledger is checked by
    callers as well; this table is the idempotency record for manual and
    approval-gated refunds.
    """
    result = await db.execute(
        select(OrderRefund.method, func.coalesce(func.sum(OrderRefund.amount_minor), 0))
        .where(OrderRefund.order_id == order_id, OrderRefund.status.in_(("DONE", "PENDING")))
        .group_by(OrderRefund.method)
    )
    totals = {method: int(amount or 0) for method, amount in result.all()}
    return totals.get("WALLET", 0), totals.get("ABA_MANUAL", 0)


async def _refund_breakdown(db: AsyncSession, order_id: int) -> dict[str, dict[str, int]]:
    result = await db.execute(
        select(OrderRefund.method, OrderRefund.status,
               func.coalesce(func.sum(OrderRefund.amount_minor), 0))
        .where(OrderRefund.order_id == order_id)
        .group_by(OrderRefund.method, OrderRefund.status)
    )
    out = {"WALLET": {"DONE": 0, "PENDING": 0}, "ABA_MANUAL": {"DONE": 0, "PENDING": 0}}
    for method, status, amount in result.all():
        if method in out and status in out[method]:
            out[method][status] = int(amount or 0)
    return out

log = logging.getLogger("teacafe.order_lifecycle")


def customer_can_cancel(order_status: str, payment_status: str) -> bool:
    """Customer cancellation is limited to a new order without confirmed payment."""
    return order_status == "NEW" and payment_status in {"UNPAID", "REJECTED"}


def unpaid_order_expired(created_at: datetime, timeout_minutes: int, now: datetime) -> bool:
    """Return whether an unpaid order is past its configured timeout."""
    return timeout_minutes > 0 and created_at <= now - timedelta(minutes=timeout_minutes)


async def cancel_order_record(
    db: AsyncSession,
    order: Order,
    *,
    actor_type: str,
    actor_id: int | None,
    reason: str,
) -> dict:
    """Cancel an order inside the caller's transaction.

    Wallet amounts are refunded in the same transaction.  For a confirmed
    payment, any remaining ABA amount becomes one PENDING finance record.  All
    amounts are calculated after previously generated refunds so a shortage
    adjustment followed by cancellation cannot refund the same money twice.
    """
    old_state = f"{order.order_status}/{order.payment_status}"
    wallet_paid = int(order.wallet_paid_minor or 0)
    paid_confirmed = int(order.paid_confirmed_minor or 0)
    refund_breakdown = await _refund_breakdown(db, order.id)
    wallet_refund_rows = sum(refund_breakdown["WALLET"].values())
    aba_refund_rows = sum(refund_breakdown["ABA_MANUAL"].values())
    wallet_ledger_refunded = 0
    if wallet_paid > 0:
        from wallet_service import get_payment
        existing_payment = await get_payment(db, order.public_code)
        wallet_ledger_refunded = int(existing_payment["refunded_amount"] or 0) if existing_payment else 0
    wallet_actual_refunded = max(refund_breakdown["WALLET"]["DONE"], wallet_ledger_refunded)
    wallet_already_reserved = max(wallet_refund_rows, wallet_ledger_refunded)
    wallet_remaining = max(wallet_paid - wallet_already_reserved, 0)
    wallet_refund_done = 0
    if wallet_remaining > 0:
        from wallet_service import WalletError, refund_payment

        try:
            await refund_payment(
                db,
                biz_id=order.public_code,
                amount_minor=wallet_remaining,
                idem=f"refund:{order.public_code}:cancel:{wallet_already_reserved}:{wallet_remaining}",
                operator_staff_id=actor_id if actor_type == "STAFF" else None,
                reason=reason or "Order cancelled",
            )
        except WalletError:
            raise
        wallet_refund_done = wallet_remaining
        db.add(OrderRefund(
            store_id=order.store_id, order_id=order.id, amount_minor=wallet_remaining,
            method="WALLET", status="DONE", note="Order cancellation wallet refund",
            done_at=datetime.now(timezone.utc), done_by=actor_id if actor_type == "STAFF" else None,
        ))

    external_paid = max(paid_confirmed - wallet_paid, 0)
    aba_remaining = max(external_paid - aba_refund_rows, 0)
    if aba_remaining > 0:
        db.add(OrderRefund(
            store_id=order.store_id, order_id=order.id, amount_minor=aba_remaining,
            method="ABA_MANUAL", status="PENDING",
            note="Order cancellation; manual ABA refund required",
        ))

    order.order_status = "CANCELLED"
    # REFUNDED is reserved for a cancelled order whose entire confirmed
    # payment is already refunded.  A mixed order with an outstanding ABA
    # amount is always PARTIALLY_REFUNDED, even when its wallet leg is done.
    if paid_confirmed > 0:
        wallet_total_refunded = wallet_actual_refunded + wallet_refund_done
        all_aba_actual = refund_breakdown["ABA_MANUAL"]["PENDING"] == 0
        if wallet_total_refunded >= wallet_paid and aba_remaining == 0 and all_aba_actual:
            order.payment_status = "REFUNDED"
        elif wallet_total_refunded > 0 or aba_refund_rows > 0 or aba_remaining > 0:
            order.payment_status = "PARTIALLY_REFUNDED"
    released = await release_for_cancel(db, order)
    new_state = f"{order.order_status}/{order.payment_status}"
    db.add(OrderEvent(
        order_id=order.id,
        actor_type=actor_type,
        actor_id=actor_id,
        event=f"ORDER_CANCELLED:{reason[:200]}",
        from_state=old_state,
        to_state=new_state,
    ))
    if actor_type == "STAFF" and actor_id is not None:
        db.add(AuditLog(
            store_id=order.store_id,
            actor_staff_id=actor_id,
            entity_type="order",
            entity_id=str(order.id),
            action="cancelled",
            details={"source": "admin_console", "public_code": order.public_code,
                     "reason": reason, "coupon_released": released,
                     "from_state": old_state, "to_state": new_state},
        ))
    return {"coupon_released": released, "from_state": old_state, "to_state": new_state,
            "wallet_refunded_minor": wallet_refund_done, "aba_pending_minor": aba_remaining}


async def notify_cancelled(db: AsyncSession, order: Order, reason_key: str) -> None:
    """Best-effort post-commit customer and staff-group cancellation notice."""
    try:
        from telegram_service import send_bot_message, tr, update_order_message
        from store_context import store_for_order
        customer = (await db.execute(select(Customer).filter(Customer.id == order.customer_id))).scalars().first()
        if customer:
            await send_bot_message(customer.telegram_user_id, tr(reason_key, order.customer_language,
                                                                order=order.public_code))
        settings = await store_for_order(db, order)
        if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
            items = (await db.execute(select(OrderItem).filter(OrderItem.order_id == order.id))).scalars().all()
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, items, customer)
    except Exception as exc:  # Telegram is not part of the cancellation transaction
        log.warning("cancellation notification failed for %s: %s", getattr(order, "public_code", "?"), exc)


async def notify_issue_resolved(db: AsyncSession, order: Order, issue, refund_amount_minor: int = 0) -> None:
    """Refresh group card and tell staff how a shortage was resolved."""
    try:
        from telegram_service import _currency_amount, send_bot_message, tr, update_order_message
        from store_context import store_for_order
        customer = (await db.execute(select(Customer).filter(Customer.id == order.customer_id))).scalars().first()
        settings = await store_for_order(db, order)
        if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
            from models import OrderItem
            items = (await db.execute(select(OrderItem).filter(OrderItem.order_id == order.id))).scalars().all()
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, items, customer)
            await send_bot_message(settings.telegram_staff_group_id,
                                   tr("order.shortageResolved", settings.staff_group_language,
                                      order=order.public_code,
                                      amount=_currency_amount(refund_amount_minor, order.currency)))
    except Exception as exc:
        log.warning("shortage notification failed for %s: %s", getattr(order, "public_code", "?"), exc)


async def notify_shortage_escalated(db: AsyncSession, order: Order, *, reminder: bool = False) -> None:
    """Tell the customer and the store group that a shortage needs attention."""
    try:
        from telegram_service import send_bot_message, tr
        from store_context import store_for_order
        customer = (await db.execute(select(Customer).filter(Customer.id == order.customer_id))).scalars().first()
        settings = await store_for_order(db, order)
        customer_key = "order.shortageReminder" if reminder else "order.shortageEscalated"
        group_key = "order.shortageEscalatedGroup"
        if customer:
            await send_bot_message(customer.telegram_user_id,
                                   tr(customer_key, order.customer_language, order=order.public_code))
        if not reminder and settings and settings.telegram_staff_group_id:
            await send_bot_message(settings.telegram_staff_group_id,
                                   tr(group_key, settings.staff_group_language, order=order.public_code))
    except Exception as exc:
        log.warning("shortage escalation notification failed for %s: %s", getattr(order, "public_code", "?"), exc)


async def _expiry_anchor(db: AsyncSession, order: Order) -> datetime:
    if order.payment_status == "REJECTED":
        review = (await db.execute(select(PaymentReview).filter(
            PaymentReview.order_id == order.id, PaymentReview.decision == "REJECTED"
        ).order_by(PaymentReview.created_at.desc()).limit(1))).scalars().first()
        if review and review.created_at:
            return review.created_at
    return order.created_at or datetime.now(timezone.utc)


async def expire_unpaid_orders(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Cancel NEW unpaid/rejected orders past each store's configured timeout."""
    now = now or datetime.now(timezone.utc)
    stores = (await db.execute(select(Store).filter(Store.status == "ACTIVE"))).scalars().all()
    expired = 0
    reminders: list[tuple[Order, int]] = []
    cancelled: list[Order] = []
    shortage_reminders: list[Order] = []
    shortage_escalations: list[Order] = []
    for store in stores:
        minutes = max(0, int(store.unpaid_order_timeout_minutes or 0))
        if minutes <= 0:
            continue
        result = await db.execute(
            select(Order).where(
                Order.store_id == store.id,
                Order.order_status == "NEW",
                Order.payment_status.in_(("UNPAID", "REJECTED")),
            ).with_for_update()
        )
        for order in result.scalars().all():
            anchor = await _expiry_anchor(db, order)
            expiry_at = anchor + timedelta(minutes=minutes)
            reminder_minutes = max(0, int(store.unpaid_order_reminder_minutes or 0))
            if reminder_minutes and now >= expiry_at - timedelta(minutes=reminder_minutes) and now < expiry_at:
                seen = (await db.execute(select(OrderEvent.id).filter(
                    OrderEvent.order_id == order.id, OrderEvent.event == "EXPIRY_REMINDER_SENT"
                ).limit(1))).first()
                if not seen:
                    db.add(OrderEvent(order_id=order.id, actor_type="SYSTEM", actor_id=None,
                                      event="EXPIRY_REMINDER_SENT",
                                      from_state=f"{order.order_status}/{order.payment_status}",
                                      to_state=f"{order.order_status}/{order.payment_status}"))
                    reminders.append((order, minutes))
            if now < expiry_at:
                continue
            await cancel_order_record(
                db, order, actor_type="SYSTEM", actor_id=None,
                reason=f"Unpaid order expired after {minutes} minutes",
            )
            expired += 1
            cancelled.append(order)
    # A paid order with an open shortage must not remain stuck forever when a
    # customer never opens the Mini App.  Remind at half the configured window
    # and escalate to the staff group at the full window; manager resolution
    # remains an explicit action, so no silent refund or cancellation occurs.
    for store in stores:
        timeout = max(0, int(store.shortage_customer_response_timeout_minutes or 0))
        if timeout <= 0:
            continue
        issue_rows = (await db.execute(
            select(OrderIssue, Order).join(Order, Order.id == OrderIssue.order_id).where(
                OrderIssue.store_id == store.id,
                OrderIssue.status == "OPEN",
                Order.order_status.in_(("ACCEPTED", "PREPARING")),
                Order.payment_status.in_(("PAID_CONFIRMED", "PARTIALLY_REFUNDED", "REFUNDED")),
            ).with_for_update()
        )).all()
        for issue, order in issue_rows:
            created_at = issue.created_at or order.created_at or now
            age = now - created_at
            if age >= timedelta(minutes=timeout):
                seen = (await db.execute(select(OrderEvent.id).filter(
                    OrderEvent.order_id == order.id, OrderEvent.event == "SHORTAGE_ESCALATED"
                ).limit(1))).first()
                if not seen:
                    db.add(OrderEvent(order_id=order.id, actor_type="SYSTEM", actor_id=None,
                                      event="SHORTAGE_ESCALATED",
                                      from_state=f"{order.order_status}/{order.payment_status}",
                                      to_state=f"{order.order_status}/{order.payment_status}"))
                    shortage_escalations.append(order)
            elif age >= timedelta(minutes=max(1, timeout // 2)):
                seen = (await db.execute(select(OrderEvent.id).filter(
                    OrderEvent.order_id == order.id, OrderEvent.event == "SHORTAGE_CUSTOMER_REMINDER_SENT"
                ).limit(1))).first()
                if not seen:
                    db.add(OrderEvent(order_id=order.id, actor_type="SYSTEM", actor_id=None,
                                      event="SHORTAGE_CUSTOMER_REMINDER_SENT",
                                      from_state=f"{order.order_status}/{order.payment_status}",
                                      to_state=f"{order.order_status}/{order.payment_status}"))
                    shortage_reminders.append(order)
    if expired or reminders or shortage_reminders or shortage_escalations:
        await db.commit()
    for order, reminder_timeout in reminders:
        try:
            from telegram_service import send_bot_message, tr
            customer = (await db.execute(select(Customer).filter(Customer.id == order.customer_id))).scalars().first()
            if customer:
                await send_bot_message(customer.telegram_user_id,
                                       tr("order.expiryReminder", order.customer_language,
                                          order=order.public_code, minutes=reminder_timeout))
        except Exception as exc:
            log.warning("expiry reminder failed: %s", exc)
    for order in cancelled:
        await notify_cancelled(db, order, "order.cancelledExpired")
    for order in shortage_reminders:
        await notify_shortage_escalated(db, order, reminder=True)
    for order in shortage_escalations:
        await notify_shortage_escalated(db, order, reminder=False)
    return expired


async def run_expiry_scheduler(db_factory=None) -> None:
    from database import AsyncSessionLocal

    while True:
        try:
            async with (db_factory or AsyncSessionLocal)() as db:
                count = await expire_unpaid_orders(db)
                if count:
                    log.info("expired %d unpaid orders", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one failed tick must not stop expiry
            log.warning("unpaid order expiry failed: %s", exc)
        await asyncio.sleep(60)


def start_expiry_scheduler(db_factory=None) -> asyncio.Task:
    return asyncio.create_task(run_expiry_scheduler(db_factory))
