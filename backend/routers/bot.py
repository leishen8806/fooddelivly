from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database import get_db
from models import Order, PaymentProof, Customer, Staff, OrderEvent, StoreSettings
import os
import httpx

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram Webhook"])

async def edit_bot_message_reply_markup(chat_id: str, message_id: str, inline_keyboard: list):
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        return
    url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": inline_keyboard}
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

async def notify_customer(telegram_user_id: str, text: str):
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": telegram_user_id,
        "text": text
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

def get_order_inline_keyboard(order_id: int, status: str, payment_status: str):
    # Generates the appropriate buttons based on current state
    keyboard = []
    
    if status == "NEW":
        keyboard.append([{"text": "接单 (Accept)", "callback_data": f"accept_{order_id}"}])
    elif status == "ACCEPTED":
        if payment_status == "PROOF_SUBMITTED":
            keyboard.append([{"text": "确认支付 (Confirm Payment)", "callback_data": f"confirmpay_{order_id}"}])
        elif payment_status == "PAID_CONFIRMED":
            keyboard.append([{"text": "出餐 (Ready)", "callback_data": f"ready_{order_id}"}])
    elif status == "PREPARING":
        keyboard.append([{"text": "出餐 (Ready)", "callback_data": f"ready_{order_id}"}])
    elif status == "READY":
        keyboard.append([{"text": "已配送 (Delivered)", "callback_data": f"deliver_{order_id}"}])
    elif status == "DELIVERED":
        keyboard.append([{"text": "已完成 (Complete)", "callback_data": f"complete_{order_id}"}])
        
    return keyboard

@router.post("/webhook")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    bot_token = os.getenv("BOT_TOKEN")
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    expected_secret = os.getenv("WEBHOOK_SECRET", "dummy_secret")
    
    if secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="Invalid secret token")
        
    update = await request.json()
    
    # Get store settings for group ID validation
    result = await db.execute(select(StoreSettings))
    settings = result.scalars().first()
    staff_group_id = settings.telegram_staff_group_id if settings else None
    
    if "message" in update:
        msg = update["message"]
        tg_user_id = str(msg.get("from", {}).get("id"))
        chat_type = msg.get("chat", {}).get("type")
        
        if chat_type == "private" and "photo" in msg:
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
                    
                    # Notify customer
                    await notify_customer(tg_user_id, f"Payment proof received for order {order.public_code}. Please wait for confirmation.")
                    
                    # Update staff group message if possible
                    if order.telegram_group_message_id and staff_group_id:
                        kb = get_order_inline_keyboard(order.id, order.order_status, order.payment_status)
                        await edit_bot_message_reply_markup(staff_group_id, order.telegram_group_message_id, kb)

    elif "callback_query" in update:
        cb = update["callback_query"]
        tg_user_id = str(cb.get("from", {}).get("id"))
        chat_id = str(cb.get("message", {}).get("chat", {}).get("id"))
        message_id = str(cb.get("message", {}).get("message_id"))
        data = cb.get("data")
        
        # Validate Group ID
        if staff_group_id and chat_id != staff_group_id:
            return {"ok": True} # Ignore callbacks from unauthorized groups
        
        # Verify staff
        result = await db.execute(select(Staff).filter(Staff.telegram_user_id == tg_user_id, Staff.active == True))
        staff = result.scalars().first()
        
        if staff and data:
            try:
                action, order_id_str = data.split("_", 1)
                order_id = int(order_id_str)
            except ValueError:
                return {"ok": True}
            
            result = await db.execute(select(Order).filter(Order.id == order_id))
            order = result.scalars().first()
            
            if order:
                old_status = order.order_status
                new_status = old_status
                payment_status = order.payment_status
                
                if action == "accept" and old_status == "NEW":
                    new_status = "ACCEPTED"
                elif action == "confirmpay" and old_status == "ACCEPTED" and payment_status == "PROOF_SUBMITTED":
                    order.payment_status = "PAID_CONFIRMED"
                    new_status = "PREPARING"
                    # We should also log PaymentReview here ideally
                elif action == "ready" and (old_status == "PREPARING" or (old_status == "ACCEPTED" and payment_status == "PAID_CONFIRMED")):
                    new_status = "READY"
                elif action == "deliver" and old_status == "READY":
                    new_status = "DELIVERED"
                elif action == "complete" and old_status == "DELIVERED":
                    new_status = "COMPLETED"
                    
                if new_status != old_status or order.payment_status != payment_status:
                    order.order_status = new_status
                    
                    if new_status != old_status:
                        event = OrderEvent(
                            order_id=order.id,
                            actor_type="STAFF",
                            actor_id=staff.id,
                            event=f"STATE_CHANGED_TO_{new_status}",
                            from_state=old_status,
                            to_state=new_status
                        )
                        db.add(event)
                        
                        # Notify customer of status change
                        result_cust = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
                        cust = result_cust.scalars().first()
                        if cust:
                            await notify_customer(cust.telegram_user_id, f"Order {order.public_code} status changed to: {new_status}")
                    
                    await db.commit()
                    
                    # Update message reply markup
                    kb = get_order_inline_keyboard(order.id, order.order_status, order.payment_status)
                    await edit_bot_message_reply_markup(chat_id, message_id, kb)

    return {"ok": True}
