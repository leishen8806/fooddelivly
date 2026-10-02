from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from database import get_db
from models import AuditLog, Order, PaymentProof, PaymentReview, OrderEvent, Customer, StoreSettings
from dependencies import get_current_staff
from store_context import can_access_store, store_scope_clause
import os
import httpx

router = APIRouter(prefix="/api/v1/admin/orders", tags=["Admin Orders"])

@router.get("")
async def list_orders(
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    # 门店隔离：门店员工只能看到本店订单（总部账号不过滤）
    query = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
    clause = store_scope_clause(Order, staff_info)
    if clause is not None:
        query = query.filter(clause)
    result = await db.execute(query)
    orders = result.scalars().all()
    return [{
        "id": order.id, "public_code": order.public_code, "room_number": order.room_number,
        "order_status": order.order_status, "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "currency": order.currency, "total_minor": order.total_minor, "created_at": order.created_at,
        "items": [{"name": item.product_name_snapshot, "quantity": item.quantity,
                   "options": item.options_json or {},
                   "line_total_minor": item.line_total_minor} for item in order.items],
    } for order in orders]

@router.get("/{order_id}/payment-proof")
async def get_payment_proof(order_id: int, staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PaymentProof).filter(PaymentProof.order_id == order_id).order_by(PaymentProof.submitted_at.desc()))
    proof = result.scalars().first()
    if not proof:
        raise HTTPException(status_code=404, detail="Payment proof not found")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Payment image service is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        file_result = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": proof.telegram_file_id})
        if file_result.status_code != 200 or not file_result.json().get("ok"):
            raise HTTPException(status_code=502, detail="Telegram could not provide this payment image")
        path = file_result.json().get("result", {}).get("file_path")
        if not path:
            raise HTTPException(status_code=404, detail="Payment image is unavailable")
        image = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        if image.status_code != 200 or len(image.content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=502, detail="Payment image could not be loaded")
        body = image.content
        # Telegram's file endpoint can label valid uploaded photos as
        # application/octet-stream. Determine the safe raster MIME from bytes.
        if body.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
        elif body.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
        elif body.startswith((b"GIF87a", b"GIF89a")):
            content_type = "image/gif"
        elif len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
            content_type = "image/webp"
        else:
            raise HTTPException(status_code=415, detail="Uploaded file is not an image")
        return Response(content=body, media_type=content_type, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })

class PaymentReviewRequest(BaseModel):
    decision: str # APPROVED or REJECTED
    reason: str | None = None

class OrderStatusRequest(BaseModel):
    status: str
    reason: str | None = None

@router.post("/{order_id}/status")
async def change_order_status(order_id: int, req: OrderStatusRequest,
                             staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")
    staff_id = staff_info["staff_id"]
    wallet_paid = order.payment_method == "WALLET" and order.payment_status == "PAID_CONFIRMED"
    transitions = {
        "ACCEPTED": order.order_status == "NEW",
        "READY": order.order_status == "PREPARING" and order.payment_status == "PAID_CONFIRMED",
        "DELIVERED": order.order_status == "READY",
        "COMPLETED": order.order_status == "DELIVERED",
        # 钱包支付的订单：只在「餐还没做出去」的阶段允许取消并退款
        # （NEW/ACCEPTED/PREPARING）。到了 READY/DELIVERED 说明已经出餐/送出，
        # 这时候把钱退回去应该走单独的、仅 MANAGER 的退款接口，而不是点一下「取消」。
        # 人工转账的已确认付款订单仍然禁止取消（退款走线下）。
        "CANCELLED": order.order_status not in {"CANCELLED", "COMPLETED"} and (
            order.payment_status != "PAID_CONFIRMED"
            or (wallet_paid and order.order_status in {"NEW", "ACCEPTED", "PREPARING"})),
    }
    if not transitions.get(req.status, False):
        raise HTTPException(status_code=409, detail="Order status transition is not allowed")
    if req.status == "CANCELLED" and not (req.reason and req.reason.strip()):
        raise HTTPException(status_code=422, detail="A cancellation reason is required")
    old_state = f"{order.order_status}/{order.payment_status}"
    order.order_status = req.status
    reason = req.reason.strip()[:500] if req.reason else None

    if req.status == "ACCEPTED" and order.payment_status == "PAID_CONFIRMED":
        # 下单即已付清（钱包余额抵扣）：接单后直接进入制作。
        # 这与「员工确认收款后 PAID_CONFIRMED + PREPARING」的既有行为保持一致。
        order.order_status = "PREPARING"

    if req.status == "CANCELLED" and wallet_paid:
        # 退款与状态变更在同一个事务里：要么都成功，要么都回滚
        from wallet_service import WalletError, refund_payment

        try:
            await refund_payment(
                db, biz_id=order.public_code, amount_minor=order.total_minor,
                idem=f"refund:{order.public_code}", operator_staff_id=staff_id,
                reason=reason or "Order cancelled",
            )
        except WalletError as exc:
            await db.rollback()
            raise HTTPException(status_code=exc.http_status, detail=exc.message)
        order.payment_status = "REFUNDED"

    event_name = f"ORDER_{req.status}" + (f": {reason}" if reason else "")
    db.add(OrderEvent(order_id=order.id, actor_type="STAFF", actor_id=staff_id, event=event_name,
                      from_state=old_state, to_state=f"{order.order_status}/{order.payment_status}"))
    db.add(AuditLog(actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id), action="status_changed",
                    details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                             "operator_telegram_id": staff_info.get("telegram_user_id"), "public_code": order.public_code,
                             "room_number": order.room_number, "from_state": old_state,
                             "to_state": f"{order.order_status}/{order.payment_status}", "reason": reason}))
    customer_result = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
    customer = customer_result.scalars().first()
    settings_result = await db.execute(select(StoreSettings).limit(1))
    settings = settings_result.scalars().first()
    await db.commit()
    if customer:
        from telegram_service import send_bot_message, tr
        try:
            await send_bot_message(customer.telegram_user_id,
                                   tr("bot.statusUpdated", order.customer_language,
                                      status=tr(f"order.status.{req.status.lower()}", order.customer_language)))
        except Exception:
            pass
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        from telegram_service import update_order_message
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items)
        except Exception:
            pass
    return {"order_status": order.order_status, "payment_status": order.payment_status}


class RefundRequest(BaseModel):
    reason: str
    amount_minor: int | None = None


@router.post("/{order_id}/refund")
async def refund_wallet_order(order_id: int, req: RefundRequest,
                              staff_info: dict = Depends(get_current_staff),
                              db: AsyncSession = Depends(get_db)):
    """把已用钱包余额支付的订单退款回钱包。

    为什么单独开接口而不是复用「取消」：
      * 取消 = 单子没做成；退款 = 钱要退回去。出餐后（READY/DELIVERED）不该还能
        一点「取消」就把钱退回去，那是典型的内部勾结路径；
      * 所以这条路径**仅 MANAGER**，且必须填原因，全程进 audit_logs；
      * 幂等：同一订单重复调用由 `refund:{订单号}` 的幂等键兜底，只退一次。
    """
    if staff_info.get("role") != "MANAGER":
        raise HTTPException(status_code=403, detail="Manager access required")
    reason = (req.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A refund reason is required")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_method != "WALLET" or order.payment_status != "PAID_CONFIRMED":
        raise HTTPException(status_code=409, detail="Order was not paid from the wallet balance")

    staff_id = staff_info["staff_id"]
    amount = req.amount_minor or order.total_minor
    if amount <= 0 or amount > order.total_minor:
        raise HTTPException(status_code=422, detail="Refund amount is invalid")

    from wallet_service import WalletError, refund_payment

    old_state = f"{order.order_status}/{order.payment_status}"
    try:
        payment = await refund_payment(
            db, biz_id=order.public_code, amount_minor=amount,
            idem=f"refund:{order.public_code}", operator_staff_id=staff_id, reason=reason,
        )
    except WalletError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.message)

    order.payment_status = "REFUNDED"
    db.add(OrderEvent(order_id=order.id, actor_type="STAFF", actor_id=staff_id,
                      event=f"ORDER_REFUNDED: {reason}", from_state=old_state,
                      to_state=f"{order.order_status}/{order.payment_status}"))
    db.add(AuditLog(actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id),
                    action="wallet_refund",
                    details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                             "operator_telegram_id": staff_info.get("telegram_user_id"),
                             "public_code": order.public_code, "amount_minor": amount,
                             "reason": reason, "from_state": old_state,
                             "to_state": f"{order.order_status}/{order.payment_status}"}))
    await db.commit()
    return {
        "order_status": order.order_status,
        "payment_status": order.payment_status,
        "refunded_amount_minor": payment["refunded_amount"],
    }

@router.post("/{order_id}/payment-review")
async def review_payment(
    order_id: int, 
    req: PaymentReviewRequest, 
    staff_info: dict = Depends(get_current_staff), 
    db: AsyncSession = Depends(get_db)
):
    staff_id = staff_info["staff_id"]

    # Fetch order
    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")

    if req.decision not in ["APPROVED", "REJECTED"]:
        raise HTTPException(status_code=400, detail="Invalid decision")

    if req.decision == "REJECTED" and not (req.reason and req.reason.strip()):
        raise HTTPException(status_code=400, detail="Reason required for rejection")

    if order.payment_status != "PROOF_SUBMITTED":
        raise HTTPException(status_code=409, detail="Order is not awaiting payment review")

    if order.order_status != "ACCEPTED":
        raise HTTPException(status_code=409, detail="Order must be accepted before payment review")

    proof_result = await db.execute(
        select(PaymentProof).filter(
            PaymentProof.order_id == order.id,
            PaymentProof.review_status == "PENDING",
        ).order_by(PaymentProof.submitted_at.desc()).with_for_update()
    )
    proof = proof_result.scalars().first()
    if proof is None:
        raise HTTPException(status_code=409, detail="No pending payment proof for this order")

    # Create review
    review = PaymentReview(
        order_id=order.id,
        staff_id=staff_id,
        decision=req.decision,
        reason=req.reason.strip() if req.reason else None
    )
    db.add(review)

    # Update order payment status
    old_payment_status = order.payment_status
    old_order_status = order.order_status
    if req.decision == "APPROVED":
        order.payment_status = "PAID_CONFIRMED"
        proof.review_status = "APPROVED"
        if order.order_status == "ACCEPTED":
            order.order_status = "PREPARING"
    else:
        order.payment_status = "REJECTED"
        proof.review_status = "REJECTED"

    db.add(OrderEvent(
        order_id=order.id,
        actor_type="STAFF",
        actor_id=staff_id,
        event=f"PAYMENT_{req.decision}",
        from_state=f"{old_order_status}/{old_payment_status}",
        to_state=f"{order.order_status}/{order.payment_status}",
    ))
    db.add(AuditLog(actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id),
                    action=f"payment_{req.decision.lower()}",
                    details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                             "operator_telegram_id": staff_info.get("telegram_user_id"), "public_code": order.public_code,
                             "room_number": order.room_number,
                             "from_state": f"{old_order_status}/{old_payment_status}",
                             "to_state": f"{order.order_status}/{order.payment_status}",
                             "reason": req.reason.strip() if req.reason else None}))
    customer_result = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
    customer = customer_result.scalars().first()
    settings_result = await db.execute(select(StoreSettings).limit(1))
    settings = settings_result.scalars().first()
    await db.commit()
    if customer:
        from telegram_service import notify_payment_review
        try:
            await notify_payment_review(customer, order, req.decision, req.reason)
        except Exception:
            pass
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        from telegram_service import update_order_message
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items)
        except Exception:
            pass

    return {"message": "Review submitted successfully", "payment_status": order.payment_status}
