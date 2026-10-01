import json
import os
from functools import lru_cache
from pathlib import Path

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters


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
    payment_key = "order.status.refunded" if payment == "REFUNDED" else "order.status.paid" if payment == "PAID_CONFIRMED" else "order.status.paymentReview" if payment == "PROOF_SUBMITTED" else "payment.rejected" if payment == "REJECTED" else "order.status.unpaid"
    lines = [
        f"TEA CAFE · {tr('payment.order', language)} {order.public_code}",
        f"{tr('bot.room', language)}: {order.room_number}",
    ]
    if items:
        for item in items:
            name = item.product_name_snapshot
            if isinstance(name, dict):
                name = name.get(language) or name.get("en") or next(iter(name.values()), "")
            line = f"• {item.quantity} × {name}"
            sweetness = (getattr(item, "options_json", None) or {}).get("sweetness")
            if sweetness is not None:
                line += f" · {tr('order.sweetnessValue', language, value=sweetness)}"
            lines.append(line)
    lines.extend([
        f"{tr('common.total', language)}: {_currency_amount(order.total_minor, order.currency)}",
        f"{tr('nav.orders', language)}: {status_label}",
        f"{tr('payment.title', language)}: {tr(payment_key, language)}",
    ])
    if getattr(order, "payment_method", "MANUAL") == "WALLET":
        lines.append(f"{tr('payment.method', language)}: {tr('payment.method.wallet', language)}")
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


async def send_payment_proof_reply(group_id: str, message_id: str, file_id: str, order_code: str, language: str = "en"):
    bot = _bot()
    if not bot:
        return None
    try:
        return await bot.send_photo(
            chat_id=group_id,
            photo=file_id,
            caption=tr("bot.groupPaymentProof", language, order=order_code),
            reply_parameters=ReplyParameters(message_id=int(message_id)),
            protect_content=True,
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


# ===========================================================================
# 钱包 / 充值
# ===========================================================================

def _recharge_language(customer) -> str:
    return getattr(customer, "preferred_language", None) or "en"


def wallet_text(summary, language: str = "en") -> str:
    """钱包卡片：本金 / 赠送 / 赠送到期时间。"""
    lines = [
        f"💰 {tr('wallet.title', language)}",
        f"{tr('wallet.principal', language)}: {_currency_amount(summary['principal_minor'], summary['currency'])}",
        f"{tr('wallet.bonus', language)}: {_currency_amount(summary['bonus_minor'], summary['currency'])}",
        f"{tr('wallet.total', language)}: {_currency_amount(summary['total_minor'], summary['currency'])}",
    ]
    if summary["bonus_minor"] and summary.get("bonus_expire_at"):
        lines.append(tr("wallet.bonusExpire", language, date=summary["bonus_expire_at"].strftime("%Y-%m-%d %H:%M")))
    if summary["bonus_minor"]:
        lines.append(tr("wallet.bonusFirst", language))
    return "\n".join(lines)


def wallet_keyboard(presets_minor, language: str = "en") -> InlineKeyboardMarkup:
    """充值档位按钮。金额只是「档位下标」，实际金额永远回库校验。"""
    rows = [[InlineKeyboardButton(
        text=_currency_amount(minor, "USD"), callback_data=f"wamount_{minor}"
    )] for minor in presets_minor]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recharge_instruction_text(recharge, language: str = "en") -> str:
    """转账指引：**备注必须填订单号**，否则人工对不上账。"""
    lines = [
        tr("wallet.orderCreated", language,
           order=recharge["order_no"],
           amount=_currency_amount(recharge["amount"], recharge["currency"])),
    ]
    if recharge["bonus_amount"]:
        lines.append(tr("wallet.bonusPreview", language,
                        bonus=_currency_amount(recharge["bonus_amount"], recharge["currency"])))
    lines.append(tr("wallet.remarkHint", language, order=recharge["order_no"]))
    lines.append(tr("wallet.sendProofPrompt", language, order=recharge["order_no"]))
    return "\n".join(lines)


def recharge_proof_keyboard(order_id: int, language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr("wallet.uploadProof", language), callback_data=f"wproof_{order_id}"),
    ], [
        InlineKeyboardButton(text=tr("wallet.cancelOrder", language), callback_data=f"wcancel_{order_id}"),
    ]])


def recharge_review_text(recharge, customer, language: str = "en") -> str:
    state = "wallet.groupProof" if recharge["proof_count"] else "wallet.groupWaiting"
    lines = [
        f"💰 {tr('wallet.groupTitle', language, order=recharge['order_no'])}",
        f"{tr('wallet.groupCustomer', language)}: {getattr(customer, 'display_name', None) or customer.telegram_user_id}",
        f"{tr('wallet.groupAmount', language)}: {_currency_amount(recharge['amount'], recharge['currency'])}",
    ]
    if recharge["bonus_amount"]:
        lines.append(f"{tr('wallet.groupBonus', language)}: "
                     f"{_currency_amount(recharge['bonus_amount'], recharge['currency'])}")
    lines.append(tr(state, language))
    return "\n".join(lines)


def recharge_group_keyboard(order_id: int, language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr("wallet.approve", language), callback_data=f"walletok_{order_id}"),
        InlineKeyboardButton(text=tr("wallet.reject", language), callback_data=f"walletno_{order_id}"),
    ]])


async def notify_new_recharge(group_id: str | None, recharge, customer, language: str = "en",
                               file_id: str | None = None) -> str | None:
    """把充值单推到员工群（带截图 + 审核按钮）。推送失败不影响用户流程。"""
    if not group_id:
        return None
    bot = _bot()
    if not bot:
        return None
    text = recharge_review_text(recharge, customer, language)
    keyboard = recharge_group_keyboard(recharge["id"], language)
    try:
        if file_id:
            message = await bot.send_photo(chat_id=group_id, photo=file_id, caption=text,
                                           reply_markup=keyboard, protect_content=True)
        else:
            message = await bot.send_message(chat_id=group_id, text=text, reply_markup=keyboard)
        return str(message.message_id)
    except Exception:  # noqa: BLE001 - 通知失败不影响用户
        return None
    finally:
        await bot.session.close()


async def notify_recharge_review(customer, recharge, decision: str) -> None:
    """审核结果通知客户。"""
    language = _recharge_language(customer)
    if decision == "APPROVED":
        amount = recharge["received_amount"] or recharge["amount"]
        text = tr("wallet.credited", language, order=recharge["order_no"],
                  amount=_currency_amount(amount, recharge["currency"]))
        if recharge["bonus_amount"]:
            text += "\n" + tr("wallet.creditedWithBonus", language,
                              bonus=_currency_amount(recharge["bonus_amount"], recharge["currency"]))
    else:
        text = tr("wallet.rejected", language, order=recharge["order_no"],
                  reason=recharge["reject_reason"] or "")
    await send_bot_message(customer.telegram_user_id, text)

