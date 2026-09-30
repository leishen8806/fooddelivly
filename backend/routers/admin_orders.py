from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from database import get_db
from models import Order, PaymentProof, PaymentReview, OrderEvent
from dependencies import get_current_staff
from jose import jwt
import os

router = APIRouter(prefix="/api/v1/admin/orders", tags=["Admin Orders"])

@router.get("")
async def list_orders(
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).order_by(Order.created_at.desc()))
    return result.scalars().all()

class PaymentReviewRequest(BaseModel):
    decision: str # APPROVED or REJECTED
    reason: str = None

@router.post("/{order_id}/payment-review")
async def review_payment(
    order_id: int, 
    req: PaymentReviewRequest, 
    staff_info: dict = Depends(get_current_staff), 
    db: AsyncSession = Depends(get_db)
):
    staff_id = staff_info["staff_id"]

    # Fetch order
    result = await db.execute(select(Order).filter(Order.id == order_id))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if req.decision not in ["APPROVED", "REJECTED"]:
        raise HTTPException(status_code=400, detail="Invalid decision")

    if req.decision == "REJECTED" and not req.reason:
        raise HTTPException(status_code=400, detail="Reason required for rejection")

    if order.payment_status not in ["PROOF_SUBMITTED", "REJECTED"]:
        raise HTTPException(status_code=409, detail="Order is not awaiting payment review")

    if req.decision == "APPROVED":
        proof_result = await db.execute(
            select(PaymentProof).filter(
                PaymentProof.order_id == order.id,
                PaymentProof.review_status == "PENDING",
            )
        )
        proof = proof_result.scalars().first()
        if proof is None:
            raise HTTPException(status_code=409, detail="No pending payment proof for this order")

    # Create review
    review = PaymentReview(
        order_id=order.id,
        staff_id=staff_id,
        decision=req.decision,
        reason=req.reason
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
        proof_result = await db.execute(select(PaymentProof).filter(PaymentProof.order_id == order.id, PaymentProof.review_status == "PENDING"))
        proof = proof_result.scalars().first()
        if proof:
            proof.review_status = "REJECTED"

    db.add(OrderEvent(
        order_id=order.id,
        actor_type="STAFF",
        actor_id=staff_id,
        event=f"PAYMENT_{req.decision}",
        from_state=f"{old_order_status}/{old_payment_status}",
        to_state=f"{order.order_status}/{order.payment_status}",
    ))
    await db.commit()

    return {"message": "Review submitted successfully", "payment_status": order.payment_status}
