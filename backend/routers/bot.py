from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database import get_db
from models import Order, PaymentProof, Customer, Staff, OrderEvent, StoreSettings
from telegram_service import answer_callback, update_order_message
import os
import json

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram Webhook"])

@router.post("/webhook")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    expected_secret = os.getenv("WEBHOOK_SECRET")
    
    if not expected_secret or secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
        
    update = await request.json()
    
    if "message" in update:
        msg = update["message"]
        tg_user_id = str(msg.get("from", {}).get("id"))
        
        if msg["chat"]["type"] == "private" and "photo" in msg:
            result = await db.execute(select(Customer).filter(Customer.telegram_user_id == tg_user_id))
            customer = result.scalars().first()
            
            if customer:
                result = await db.execute(
                    select(Order)
                    .filter(Order.customer_id == customer.id, Order.payment_status == "UNPAID")
                    .order_by(Order.created_at.desc())
                )
                order = result.scalars().first()
                
                if order:
                    file_id = msg["photo"][-1]["file_id"]
                    proof = PaymentProof(
                        order_id=order.id,
                        telegram_file_id=file_id,
                        submitted_by=customer.id
                    )
                    db.add(proof)
                    order.payment_status = "PROOF_SUBMITTED"
                    await db.commit()

    elif "callback_query" in update:
        cb = update["callback_query"]
        tg_user_id = str(cb.get("from", {}).get("id"))
        data = cb.get("data") # e.g. "accept_123", "ready_123"
        
        message = cb.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        settings_result = await db.execute(select(StoreSettings).limit(1))
        settings = settings_result.scalars().first()
        if not settings or not settings.telegram_staff_group_id or chat_id != str(settings.telegram_staff_group_id):
            raise HTTPException(status_code=403, detail="Callback is not from the configured staff group")

        # Verify staff
        result = await db.execute(select(Staff).filter(Staff.telegram_user_id == tg_user_id, Staff.active == True))
        staff = result.scalars().first()
        
        if not staff:
            raise HTTPException(status_code=403, detail="Staff access required")
        if staff and data:
            try:
                action, order_id_str = data.split("_", 1)
                order_id = int(order_id_str)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid callback data")
            
            result = await db.execute(select(Order).filter(Order.id == order_id))
            order = result.scalars().first()
            
            if order:
                old_status = order.order_status
                new_status = old_status
                
                if action == "accept" and old_status == "NEW":
                    new_status = "ACCEPTED"
                elif action == "confirm" and old_status == "ACCEPTED" and order.payment_status == "PROOF_SUBMITTED":
                    order.payment_status = "PAID_CONFIRMED"
                    new_status = "PREPARING"
                elif action == "ready" and old_status == "PREPARING":
                    new_status = "READY"
                elif action == "deliver" and old_status == "READY":
                    new_status = "DELIVERED"
                elif action == "complete" and old_status == "DELIVERED":
                    new_status = "COMPLETED"
                    
                if new_status != old_status:
                    order.order_status = new_status
                    
                    event = OrderEvent(
                        order_id=order.id,
                        actor_type="STAFF",
                        actor_id=staff.id,
                        event=f"STATE_CHANGED_TO_{new_status}",
                        from_state=old_status,
                        to_state=new_status
                    )
                    db.add(event)
                    if action == "confirm":
                        proof_result = await db.execute(select(PaymentProof).filter(PaymentProof.order_id == order.id, PaymentProof.review_status == "PENDING"))
                        proof = proof_result.scalars().first()
                        if proof:
                            proof.review_status = "APPROVED"
                    await db.commit()
                    if settings.telegram_staff_group_id and order.telegram_group_message_id:
                        await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id, order)
                    await answer_callback(cb.get("id", ""), f"Updated: {new_status}")

    return {"ok": True}
