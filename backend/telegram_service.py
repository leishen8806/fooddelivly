import json
import os
from functools import lru_cache
from pathlib import Path

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


@lru_cache(maxsize=1)
def _locales():
    path = Path(os.getenv("LOCALES_PATH", Path(__file__).resolve().parents[1] / "docs" / "ui" / "locales.json"))
    if not path.is_file():
        path = Path(__file__).resolve().parent / "locales.json"
    return json.loads(path.read_text(encoding="utf-8"))


def tr(key: str, language: str = "en", **values) -> str:
    lang = language if language in {"en", "zh-CN", "km"} else "en"
    text = _locales().get(lang, {}).get(key) or _locales()["en"].get(key) or key
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _bot() -> Bot | None:
    token = os.getenv("BOT_TOKEN")
    return Bot(token) if token else None


def _currency_amount(minor: int, currency: str) -> str:
    digits = 0 if currency.upper() in {"KHR", "JPY", "VND"} else 2
    amount = minor / (10 ** digits)
    return f"{amount:,.{digits}f} {currency}"


def order_keyboard(order_id: int, order_status: str, payment_status: str, language: str = "en") -> InlineKeyboardMarkup:
    buttons = []
    if order_status == "NEW":
        buttons.append([InlineKeyboardButton(text=tr("order.accept", language), callback_data=f"accept_{order_id}")])
    elif order_status == "ACCEPTED" and payment_status == "PROOF_SUBMITTED":
        buttons.append([InlineKeyboardButton(text=tr("order.confirmPayment", language), callback_data=f"confirmpay_{order_id}")])
    elif order_status == "PREPARING":
        buttons.append([InlineKeyboardButton(text=tr("order.markReady", language), callback_data=f"ready_{order_id}")])
    elif order_status == "READY":
        buttons.append([InlineKeyboardButton(text=tr("order.markDelivered", language), callback_data=f"deliver_{order_id}")])
    elif order_status == "DELIVERED":
        buttons.append([InlineKeyboardButton(text=tr("order.complete", language), callback_data=f"complete_{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def order_text(order, payment_status: str | None = None, language: str = "en", items=None) -> str:
    payment = payment_status or order.payment_status
    status_label = tr(f"order.status.{order.order_status.lower()}", language)
    payment_key = "order.status.paid" if payment == "PAID_CONFIRMED" else "order.status.paymentReview" if payment == "PROOF_SUBMITTED" else "payment.rejected" if payment == "REJECTED" else "order.status.unpaid"
    lines = [
        f"TEA CAFE · {tr('payment.order', language)} {order.public_code}",
        f"{tr('bot.room', language)}: {order.room_number}",
    ]
    if items:
        for item in items:
            name = item.product_name_snapshot
            if isinstance(name, dict):
                name = name.get(language) or name.get("en") or next(iter(name.values()), "")
            lines.append(f"• {item.quantity} × {name}")
    lines.extend([
        f"{tr('common.total', language)}: {_currency_amount(order.total_minor, order.currency)}",
        f"{tr('nav.orders', language)}: {status_label}",
        f"{tr('payment.title', language)}: {tr(payment_key, language)}",
    ])
    return "\n".join(lines)


async def notify_new_order(order, group_id: str | None, language: str = "en", items=None) -> str | None:
    if not group_id:
        return None
    bot = _bot()
    if not bot:
        return None
    try:
        message = await bot.send_message(
            chat_id=group_id,
            text=order_text(order, language=language, items=items),
            reply_markup=order_keyboard(order.id, order.order_status, order.payment_status, language),
        )
        return str(message.message_id)
    finally:
        await bot.session.close()


async def update_order_message(group_id: str, message_id: str, order, language: str = "en", items=None) -> None:
    bot = _bot()
    if not bot:
        return
    try:
        await bot.edit_message_text(
            chat_id=group_id,
            message_id=int(message_id),
            text=order_text(order, language=language, items=items),
            reply_markup=order_keyboard(order.id, order.order_status, order.payment_status, language),
        )
    finally:
        await bot.session.close()


async def send_bot_message(chat_id: str, text: str, reply_markup=None) -> None:
    bot = _bot()
    if not bot:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    finally:
        await bot.session.close()


async def answer_callback(callback_id: str, text: str | None = None, alert: bool = False) -> None:
    bot = _bot()
    if not bot:
        return
    try:
        await bot.answer_callback_query(callback_id, text=text, show_alert=alert)
    finally:
        await bot.session.close()


async def notify_payment_review(customer, order, decision: str, reason: str | None = None) -> None:
    result = tr("payment.proofReceived", order.customer_language)
    if decision == "APPROVED":
        result = tr("payment.confirmed", order.customer_language)
    else:
        result = f"{tr('payment.rejected', order.customer_language)}: {reason or ''}\n{tr('payment.resubmit', order.customer_language)}"
    await send_bot_message(customer.telegram_user_id, f"{order.public_code} · {result}")
