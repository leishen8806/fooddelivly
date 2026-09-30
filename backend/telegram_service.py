import os
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _bot() -> Bot | None:
    token = os.getenv("BOT_TOKEN")
    return Bot(token) if token else None


def order_keyboard(order_id: int, order_status: str, payment_status: str) -> InlineKeyboardMarkup:
    buttons = []
    if order_status == "NEW":
        buttons.append([InlineKeyboardButton(text="接单 / Accept", callback_data=f"accept_{order_id}")])
    elif order_status == "ACCEPTED" and payment_status == "PROOF_SUBMITTED":
        buttons.append([InlineKeyboardButton(text="确认支付 / Confirm payment", callback_data=f"confirm_{order_id}")])
    elif order_status == "PREPARING":
        buttons.append([InlineKeyboardButton(text="出餐 / Ready", callback_data=f"ready_{order_id}")])
    elif order_status == "READY":
        buttons.append([InlineKeyboardButton(text="已配送 / Delivered", callback_data=f"deliver_{order_id}")])
    elif order_status == "DELIVERED":
        buttons.append([InlineKeyboardButton(text="已完成 / Complete", callback_data=f"complete_{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def order_text(order, payment_status: str | None = None) -> str:
    payment = payment_status or order.payment_status
    return (
        f"TEA CAFE · ORDER {order.public_code}\n"
        f"Room: {order.room_number}\n"
        f"Amount: {order.total_minor / 100:.2f} {order.currency}\n"
        f"Status: {order.order_status}\n"
        f"Payment: {payment}"
    )


async def notify_new_order(order, group_id: str | None) -> str | None:
    if not group_id:
        return None
    bot = _bot()
    if not bot:
        return None
    try:
        message = await bot.send_message(
            chat_id=group_id,
            text=order_text(order),
            reply_markup=order_keyboard(order.id, order.order_status, order.payment_status),
        )
        return str(message.message_id)
    finally:
        await bot.session.close()


async def update_order_message(group_id: str, message_id: str, order) -> None:
    bot = _bot()
    if not bot:
        return
    try:
        await bot.edit_message_text(
            chat_id=group_id,
            message_id=int(message_id),
            text=order_text(order),
            reply_markup=order_keyboard(order.id, order.order_status, order.payment_status),
        )
    finally:
        await bot.session.close()


async def answer_callback(callback_id: str, text: str | None = None) -> None:
    bot = _bot()
    if not bot:
        return
    try:
        await bot.answer_callback_query(callback_id, text=text, show_alert=False)
    finally:
        await bot.session.close()
