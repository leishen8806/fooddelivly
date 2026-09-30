from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from database import get_db
from models import AuditLog, Order, PaymentProof, PaymentReview, OrderEvent, Customer, StoreSettings
from dependencies import get_current_staff
import os
import httpx

router = APIRouter(prefix="/api/v1/admin/orders", tags=["Admin Orders"])

@router.get("")
async def list_orders(
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc()))
    orders = result.scalars().all()
    return [{
        "id": order.id, "public_code": order.public_code, "room_number": order.room_number,
        "order_status": order.order_status, "payment_status": order.payment_status,
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
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    transitions = {
        "ACCEPTED": order.order_status == "NEW",
        "READY": order.order_status == "PREPARING" and order.payment_status == "PAID_CONFIRMED",
        "DELIVERED": order.order_status == "READY",
        "COMPLETED": order.order_status == "DELIVERED",
        "CANCELLED": order.order_status not in {"CANCELLED", "COMPLETED"} and order.payment_status != "PAID_CONFIRMED",
    }
    if not transitions.get(req.status, False):
        raise HTTPException(status_code=409, detail="Order status transition is not allowed")
    if req.status == "CANCELLED" and not (req.reason and req.reason.strip()):
        raise HTTPException(status_code=422, detail="A cancellation reason is required")
    old_state = f"{order.order_status}/{order.payment_status}"
    order.order_status = req.status
    reason = req.reason.strip()[:500] if req.reason else None
    event_name = f"ORDER_{req.status}" + (f": {reason}" if reason else "")
    staff_id = staff_info["staff_id"]
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
    if not order:
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
