import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from database import get_db
from models import Customer, Order, OrderEvent, PaymentProof, PaymentReview, Staff, StoreSettings
from telegram_service import answer_callback, notify_new_order, order_keyboard, send_bot_message, tr, update_order_message

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram Webhook"])


def _language(value: str | None) -> str:
    if value and value.lower().startswith("zh"):
        return "zh-CN"
    if value and value.lower().startswith("km"):
        return "km"
    return "en"


def _message_status(order: Order) -> str:
    return f"{order.order_status}/{order.payment_status}"


async def _handle_private_message(msg: dict, db: AsyncSession) -> None:
    sender = msg.get("from") or {}
    tg_user_id = sender.get("id")
    chat = msg.get("chat") or {}
    if tg_user_id is None or chat.get("type") != "private" or str(chat.get("id")) != str(tg_user_id):
        return
    tg_user_id = str(tg_user_id)
    result = await db.execute(select(Customer).filter(Customer.telegram_user_id == tg_user_id).with_for_update())
    customer = result.scalars().first()
    language = _language(customer.language_code if customer else sender.get("language_code"))
    text = (msg.get("text") or "").strip()
    parts = text.split()
    command = parts[0].split("@", 1)[0].lower() if parts else ""

    if command in {"/start", "/pay"}:
        argument = parts[1] if len(parts) > 1 else ""
        if command == "/start" and argument.startswith("pay_"):
            argument = argument[4:]
        if argument:
            if not customer:
                await send_bot_message(tg_user_id, tr("auth.openFromTelegram", language))
                return
            order_result = await db.execute(select(Order).filter(
                Order.public_code == argument.upper(), Order.customer_id == customer.id
            ).with_for_update())
            order = order_result.scalars().first()
            if not order or order.order_status in {"CANCELLED", "COMPLETED"} or order.payment_status not in {"UNPAID", "REJECTED"}:
                await send_bot_message(tg_user_id, tr("error.generic", language))
                return
            pending_result = await db.execute(select(PaymentProof.id).filter(
                PaymentProof.order_id == order.id, PaymentProof.review_status == "PENDING"
            ).limit(1))
            if pending_result.first():
                await send_bot_message(tg_user_id, tr("payment.pending", language))
                return
            customer.pending_payment_order_id = order.id
            await db.commit()
            await send_bot_message(tg_user_id, f"{tr('bot.sendProofPrompt', language, order=order.public_code)}\n{tr('payment.notConfirmed', language)}")
            return

        miniapp_url = os.getenv("MINI_APP_URL", "https://food.workline.ink/")
        if not miniapp_url.startswith("https://"):
            miniapp_url = "https://food.workline.ink/"
        keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(
            text=tr("nav.menu", language), web_app=WebAppInfo(url=miniapp_url)
        )]], resize_keyboard=True)
        await send_bot_message(tg_user_id, tr("bot.hello", language), keyboard)
        return

    if command in {"/help", "/support"}:
        await send_bot_message(tg_user_id, f"{tr('bot.help', language)}\n{tr('payment.instruction', language)}")
        return

    if command in {"/orders", "/myorders"}:
        if not customer:
            await send_bot_message(tg_user_id, tr("auth.openFromTelegram", language))
            return
        orders_result = await db.execute(select(Order).filter(Order.customer_id == customer.id).order_by(Order.created_at.desc()).limit(5))
        orders = orders_result.scalars().all()
        if not orders:
            await send_bot_message(tg_user_id, tr("cart.empty", language))
        else:
            messages = [f"{o.public_code} · {tr(f'order.status.{o.order_status.lower()}', language)} · {o.payment_status}" for o in orders]
            await send_bot_message(tg_user_id, "\n".join(messages))
        return

    if "photo" not in msg:
        return
    if not customer or not customer.pending_payment_order_id:
        await send_bot_message(tg_user_id, tr("payment.sendProof", language) + "\n" + tr("auth.openFromTelegram", language))
        return

    order_result = await db.execute(select(Order).options(selectinload(Order.items)).filter(
        Order.id == customer.pending_payment_order_id, Order.customer_id == customer.id
    ).with_for_update())
    order = order_result.scalars().first()
    if not order or order.order_status in {"CANCELLED", "COMPLETED"} or order.payment_status not in {"UNPAID", "REJECTED"}:
        customer.pending_payment_order_id = None
        await db.commit()
        await send_bot_message(tg_user_id, tr("error.generic", language))
        return

    existing_result = await db.execute(select(PaymentProof.id).filter(
        PaymentProof.order_id == order.id, PaymentProof.review_status == "PENDING"
    ).limit(1))
    if existing_result.first():
        customer.pending_payment_order_id = None
        await db.commit()
        await send_bot_message(tg_user_id, tr("payment.pending", language))
        return

    photos = msg.get("photo") or []
    if not photos:
        return
    old_state = _message_status(order)
    db.add(PaymentProof(order_id=order.id, telegram_file_id=photos[-1]["file_id"], submitted_by=customer.id))
    order.payment_status = "PROOF_SUBMITTED"
    customer.pending_payment_order_id = None
    db.add(OrderEvent(order_id=order.id, actor_type="CUSTOMER", actor_id=customer.id,
                      event="PAYMENT_PROOF_SUBMITTED", from_state=old_state, to_state=_message_status(order)))
    settings_result = await db.execute(select(StoreSettings).limit(1))
    settings = settings_result.scalars().first()
    await db.commit()

    await send_bot_message(tg_user_id, f"{tr('bot.paymentProofSubmitted', language)}\n{tr('payment.notConfirmed', language)}")
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items)
        except Exception:
            pass


async def _handle_callback(cb: dict, db: AsyncSession) -> None:
    callback_id = cb.get("id")
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    tg_user_id = (cb.get("from") or {}).get("id")
    settings_result = await db.execute(select(StoreSettings).limit(1))
    settings = settings_result.scalars().first()
    staff_group_id = str(settings.telegram_staff_group_id) if settings and settings.telegram_staff_group_id else None
    group_language = settings.staff_group_language if settings else "en"
    if not callback_id:
        return
    if not staff_group_id or str(chat.get("id")) != staff_group_id:
        await answer_callback(callback_id, tr("error.permission", group_language), alert=True)
        return
    if tg_user_id is None:
        await answer_callback(callback_id, tr("error.permission", group_language), alert=True)
        return

    staff_result = await db.execute(select(Staff).filter(
        Staff.telegram_user_id == str(tg_user_id), Staff.active.is_(True)
    ))
    staff = staff_result.scalars().first()
    if not staff:
        await answer_callback(callback_id, tr("error.permission", group_language), alert=True)
        return
    try:
        action, order_id_text = (cb.get("data") or "").split("_", 1)
        order_id = int(order_id_text)
    except (ValueError, AttributeError):
        await answer_callback(callback_id, tr("error.generic", group_language), alert=True)
        return

    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order:
        await answer_callback(callback_id, tr("error.generic", group_language), alert=True)
        return
    old_order_status = order.order_status
    old_payment_status = order.payment_status
    old_state = _message_status(order)
    proof = None

    if action == "accept" and order.order_status == "NEW":
        order.order_status = "ACCEPTED"
    elif action == "confirmpay" and order.order_status == "ACCEPTED" and order.payment_status == "PROOF_SUBMITTED":
        proof_result = await db.execute(select(PaymentProof).filter(
            PaymentProof.order_id == order.id, PaymentProof.review_status == "PENDING"
        ).order_by(PaymentProof.submitted_at.desc()).with_for_update())
        proof = proof_result.scalars().first()
        if not proof:
            await answer_callback(callback_id, tr("error.generic", group_language), alert=True)
            return
        order.payment_status = "PAID_CONFIRMED"
        order.order_status = "PREPARING"
        proof.review_status = "APPROVED"
        db.add(PaymentReview(order_id=order.id, staff_id=staff.id, decision="APPROVED", reason="Staff confirmed ABA receipt in Telegram group"))
    elif action == "ready" and order.order_status == "PREPARING" and order.payment_status == "PAID_CONFIRMED":
        order.order_status = "READY"
    elif action == "deliver" and order.order_status == "READY":
        order.order_status = "DELIVERED"
    elif action == "complete" and order.order_status == "DELIVERED":
        order.order_status = "COMPLETED"
    else:
        await answer_callback(callback_id, tr("error.generic", group_language), alert=True)
        return

    new_state = _message_status(order)
    db.add(OrderEvent(order_id=order.id, actor_type="STAFF", actor_id=staff.id,
                      event="PAYMENT_CONFIRMED" if action == "confirmpay" else f"ORDER_{order.order_status}",
                      from_state=old_state, to_state=new_state))
    customer_result = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
    customer = customer_result.scalars().first()
    await db.commit()

    status_text = tr(f"order.status.{order.order_status.lower()}", order.customer_language)
    if customer:
        try:
            await send_bot_message(customer.telegram_user_id, tr("bot.statusUpdated", order.customer_language, status=status_text))
        except Exception:
            pass
    try:
        await update_order_message(str(chat["id"]), str(message["message_id"]), order, group_language, order.items)
    except Exception:
        pass
    await answer_callback(callback_id, tr("common.save", group_language))


@router.post("/webhook")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected_secret = os.getenv("WEBHOOK_SECRET", "")
    if not expected_secret or not hmac.compare_digest(secret_token, expected_secret):
        raise HTTPException(status_code=401, detail="Invalid secret token")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Telegram update")
    if "message" in update:
        await _handle_private_message(update["message"], db)
    elif "callback_query" in update:
        await _handle_callback(update["callback_query"], db)
    return {"ok": True}
