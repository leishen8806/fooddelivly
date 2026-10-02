import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from database import get_db
from models import AuditLog, Customer, Order, OrderEvent, PaymentProof, PaymentReview, Staff, StoreSettings
from store_context import can_access_store
from telegram_service import (
    answer_callback, notify_new_order, notify_new_recharge, notify_recharge_review,
    order_keyboard, recharge_instruction_text, recharge_proof_keyboard, send_bot_message,
    send_payment_proof_reply, tr, update_order_message, wallet_keyboard, wallet_text,
)
import wallet_service
from wallet_service import WalletError

router = APIRouter(prefix="/api/v1/telegram", tags=["Telegram Webhook"])

#: 充值档位（最小货币单位）。与 routers/wallet.py 的 DEFAULT_PRESETS 保持一致。
WALLET_PRESETS_MINOR = (500, 1000, 2000, 5000)

#: 还能补传凭证的充值单状态
PENDING_STATUSES = ("awaiting_proof", "under_review")

#: 数据库错误码 -> i18n key。没列到的直接用 wallet_service 给的中文兜底文案。
WALLET_ERROR_KEYS = {
    "AMOUNT_TOO_SMALL": "wallet.errorMinAmount",
    "AMOUNT_TOO_LARGE": "wallet.errorMaxAmount",
    "TOO_MANY_OPEN_ORDERS": "wallet.errorTooManyOpen",
    "DAILY_LIMIT_EXCEEDED": "wallet.errorDailyLimit",
    "PROOF_DUPLICATE": "wallet.errorProofDuplicate",
    "ORDER_EXPIRED": "wallet.errorExpired",
    "ORDER_STATE": "wallet.errorOrderState",
    "NOT_ORDER_OWNER": "error.permission",
    "ORDER_NOT_FOUND": "wallet.errorOrderState",
    "NOT_REVIEWER": "error.permission",
    "SELF_APPROVE_FORBIDDEN": "wallet.errorSelfApprove",
    "REASON_REQUIRED": "wallet.errorReasonRequired",
}


def _wallet_error_text(exc: WalletError, language: str) -> str:
    key = WALLET_ERROR_KEYS.get(exc.code)
    return tr(key, language) if key else exc.message


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
    if tg_user_id is None:
        return
    tg_user_id = str(tg_user_id)
    text = (msg.get("text") or "").strip()
    parts = text.split()
    command = parts[0].split("@", 1)[0].lower() if parts else ""

    if chat.get("type") in {"group", "supergroup"}:
        if command != "/getgroupid":
            return
        staff_result = await db.execute(select(Staff).filter(
            Staff.telegram_user_id == tg_user_id,
            Staff.role == "MANAGER",
            Staff.active.is_(True),
        ))
        manager = staff_result.scalars().first()
        group_chat_id = str(chat.get("id", ""))
        if not manager or not group_chat_id.lstrip("-").isdigit():
            await send_bot_message(group_chat_id, tr("bot.groupIdManagerOnly", _language(sender.get("language_code"))))
            return
        try:
            await send_bot_message(tg_user_id, tr("bot.groupIdDelivered", _language(sender.get("language_code")), group_id=group_chat_id))
        except TelegramForbiddenError:
            await send_bot_message(group_chat_id, tr("bot.groupIdStartBot", _language(sender.get("language_code"))))
        return

    if chat.get("type") != "private" or str(chat.get("id")) != tg_user_id:
        return
    if command == "/myid":
        await send_bot_message(tg_user_id, tr("bot.myId", _language(sender.get("language_code")), telegram_id=tg_user_id))
        return

    result = await db.execute(select(Customer).filter(Customer.telegram_user_id == tg_user_id).with_for_update())
    customer = result.scalars().first()
    language = _language(customer.language_code if customer else sender.get("language_code"))

    if command in {"/start", "/pay"}:
        argument = parts[1] if len(parts) > 1 else ""
        if command == "/start" and argument.startswith("pay_"):
            argument = argument[4:]
        if argument.startswith("rc_"):
            # 钱包充值 deep link（Mini App / 网页上的「发给机器人」按钮）：?start=rc_<订单号>
            await _handle_recharge_deeplink(tg_user_id, argument[3:], customer, language, db)
            return
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
            # 两个「下一张截图属于谁」的指针必须互斥：用户既然从 pay_ 深链进来
            # 传餐费凭证，就不能再被残留的钱包充值指针劫持。
            customer.pending_recharge_order_id = None
            await db.commit()
            await send_bot_message(tg_user_id, f"{tr('bot.sendProofPrompt', language, order=order.public_code)}\n{tr('payment.notConfirmed', language)}")
            return

        miniapp_url = os.getenv("MINI_APP_URL", "https://food.workline.ink/")
        if not miniapp_url.startswith("https://"):
            miniapp_url = "https://food.workline.ink/"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=tr("nav.menu", language), web_app=WebAppInfo(url=miniapp_url)
        )]])
        await send_bot_message(tg_user_id, tr("bot.hello", language), keyboard)
        return

    if command in {"/help", "/support"}:
        await send_bot_message(tg_user_id, f"{tr('bot.help', language)}\n{tr('payment.instruction', language)}")
        return

    if command in {"/wallet", "/balance", "/topup"}:
        if not customer:
            await send_bot_message(tg_user_id, tr("auth.openFromTelegram", language))
            return
        await _send_wallet_card(db, customer, language)
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
    # 先看这张图是不是「钱包充值」的凭证：客户刚在 /wallet 里发起过充值。
    # 充值单已失效时返回 False，继续走下面的订单收款凭证路径。
    if customer and customer.pending_recharge_order_id:
        if await _handle_recharge_proof(tg_user_id, msg, customer, language, db):
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
    db.add(PaymentProof(order_id=order.id, telegram_file_id=photos[-1]["file_id"],
                        submitted_by=customer.id, store_id=order.store_id))
    order.payment_status = "PROOF_SUBMITTED"
    customer.pending_payment_order_id = None
    db.add(OrderEvent(order_id=order.id, actor_type="CUSTOMER", actor_id=customer.id,
                      event="PAYMENT_PROOF_SUBMITTED", from_state=old_state, to_state=_message_status(order)))
    # 群消息与语言按**订单所属门店**（多门店时不能都发到同一个群）
    settings = await _store_settings(db, order=order, customer=customer)
    await db.commit()

    await send_bot_message(tg_user_id, f"{tr('bot.paymentProofSubmitted', language)}\n{tr('payment.notConfirmed', language)}")
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        proof_state = _message_status(order)
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items)
        except Exception:
            pass
        try:
            shared_message = await send_payment_proof_reply(
                settings.telegram_staff_group_id, order.telegram_group_message_id,
                photos[-1]["file_id"], order.public_code, settings.staff_group_language,
            )
            event_name = "PAYMENT_PROOF_SHARED_TO_GROUP" if shared_message else "PAYMENT_PROOF_GROUP_SHARE_FAILED"
            db.add(OrderEvent(order_id=order.id, actor_type="SYSTEM", actor_id=None,
                              event=event_name, from_state=proof_state, to_state=proof_state))
            await db.commit()
        except Exception:
            await db.rollback()
            db.add(OrderEvent(order_id=order.id, actor_type="SYSTEM", actor_id=None,
                              event="PAYMENT_PROOF_GROUP_SHARE_FAILED",
                              from_state=proof_state, to_state=proof_state))
            await db.commit()


async def _handle_callback(cb: dict, db: AsyncSession) -> None:
    callback_id = cb.get("id")
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    sender = cb.get("from") or {}
    tg_user_id = sender.get("id")
    # 群里按钮的回复语言按群所属门店；找不到门店时退回主店
    settings = await _store_settings(db)
    staff_group_id = str(settings.telegram_staff_group_id) if settings and settings.telegram_staff_group_id else None
    group_language = (settings.staff_group_language if settings else "en") or "en"
    if not callback_id:
        return
    if chat.get("type") == "private" and tg_user_id is not None:
        # 私聊里的按钮 = 客户自己的钱包操作（充值档位 / 上传凭证 / 取消）
        await _handle_private_callback(cb, db, str(tg_user_id))
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

    if action in {"walletok", "walletno"}:
        # 钱包充值到账审核（与订单收款审核同一批员工、同一个群）
        await _handle_wallet_review(db, action, order_id, staff, sender, chat,
                                    group_language, callback_id)
        return

    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    # 门店隔离：群里的订单按钮只能操作本店订单（跨店按「无效」处理，不泄露存在性）
    if not order or not can_access_store(
            {"staff_id": staff.id, "role": staff.role, "store_id": staff.store_id}, order.store_id):
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
        db.add(PaymentReview(order_id=order.id, staff_id=staff.id, decision="APPROVED",
                             reason="Staff confirmed ABA receipt in Telegram group",
                             store_id=order.store_id))
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
    db.add(AuditLog(
        actor_staff_id=staff.id,
        entity_type="order",
        entity_id=str(order.id),
        action="payment_approved" if action == "confirmpay" else "status_changed",
        details={
            "source": "telegram_group",
            "operator_name": staff.login_name,
            "operator_telegram_id": str(tg_user_id),
            "operator_telegram_username": sender.get("username"),
            "operator_first_name": sender.get("first_name"),
            "group_id": str(chat.get("id")),
            "public_code": order.public_code,
            "room_number": order.room_number,
            "action": action,
            "from_state": old_state,
            "to_state": new_state,
        },
    ))
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


# ===========================================================================
# 钱包 / 充值
# ===========================================================================

async def _settings(db: AsyncSession):
    result = await db.execute(select(StoreSettings).limit(1))
    return result.scalars().first()


async def _store_settings(db: AsyncSession, *, order=None, customer=None):
    """按门店取配置（员工群 / 语言 / 收款信息）。

    订单通知必须发到**该订单所属门店**的群：以前所有店共用一个群，
    多门店之后 A 店的单会通知到 B 店的群里。
    """
    from store_context import default_store, get_store, resolve_store

    store = None
    if order is not None and getattr(order, "store_id", None):
        store = await get_store(db, order.store_id)
    if store is None:
        store = await resolve_store(db, customer=customer)
    if store is None:
        store = await default_store(db)
    return store


def _currency(settings) -> str:
    return settings.currency if settings and settings.currency else "USD"


async def _send_wallet_card(db: AsyncSession, customer: Customer, language: str) -> None:
    settings = await _settings(db)
    summary = await wallet_service.get_summary(db, customer.id, _currency(settings))
    await send_bot_message(
        customer.telegram_user_id,
        f"{wallet_text(summary, language)}\n\n{tr('wallet.chooseAmount', language)}",
        wallet_keyboard(WALLET_PRESETS_MINOR, language),
    )


async def _create_recharge(db: AsyncSession, customer: Customer, amount_minor: int,
                           token: str, language: str, chat_id: str):
    """建充值单并给出转账指引。

    `token` 由调用方给：按钮点击用「承载按钮的那条消息」的身份，
    所以同一条消息上连点两次只会建出一张单（幂等键在数据库上有唯一约束）。
    """
    settings = await _settings(db)
    try:
        order = await wallet_service.start_recharge(
            db, customer_id=customer.id, amount_minor=amount_minor,
            idem=f"recharge:{customer.id}:{token}", currency=_currency(settings),
            store_id=customer.store_id,
        )
    except WalletError as exc:
        await db.rollback()
        await send_bot_message(chat_id, _wallet_error_text(exc, language))
        return None

    customer.pending_recharge_order_id = order["id"]
    customer.pending_payment_order_id = None   # 互斥：见 pay_ 深链处的说明
    await db.commit()
    await send_bot_message(chat_id, recharge_instruction_text(order, language),
                           recharge_proof_keyboard(order["id"], language))
    return order


async def _handle_recharge_deeplink(tg_user_id: str, order_no: str, customer,
                                    language: str, db: AsyncSession) -> None:
    """`?start=rc_<订单号>`：把用户带到「发截图」这一步。"""
    if customer is None:
        await send_bot_message(tg_user_id, tr("auth.openFromTelegram", language))
        return
    order = await wallet_service.get_recharge_by_order_no(db, order_no)
    if order is None or order["customer_id"] != customer.id:
        await send_bot_message(tg_user_id, tr("wallet.errorOrderState", language))
        return
    if order["status"] not in PENDING_STATUSES:
        await send_bot_message(tg_user_id, tr("wallet.errorOrderState", language))
        return
    customer.pending_recharge_order_id = order["id"]
    customer.pending_payment_order_id = None   # 互斥：见 pay_ 深链处的说明
    await db.commit()
    await send_bot_message(tg_user_id, recharge_instruction_text(order, language),
                           recharge_proof_keyboard(order["id"], language))


async def _handle_private_callback(cb: dict, db: AsyncSession, tg_user_id: str) -> None:
    """私聊按钮：充值档位 / 上传凭证 / 取消充值单。"""
    callback_id = cb.get("id")
    data = cb.get("data") or ""
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or tg_user_id)
    message_id = message.get("message_id")

    result = await db.execute(select(Customer).filter(
        Customer.telegram_user_id == tg_user_id).with_for_update())
    customer = result.scalars().first()
    if customer is None:
        await answer_callback(callback_id, tr("auth.openFromTelegram", "en"), alert=True)
        return
    language = _language(customer.language_code)

    try:
        if data == "wwallet":
            await answer_callback(callback_id)
            await _send_wallet_card(db, customer, language)
            return

        if data.startswith("wamount_"):
            raw = data.split("_", 1)[1]
            amount = int(raw) if raw.isdigit() else 0
            if amount not in WALLET_PRESETS_MINOR:
                # callback_data 是用户可以伪造的：金额只认服务端白名单
                await answer_callback(callback_id, tr("error.generic", language), alert=True)
                return
            await answer_callback(callback_id)
            await _create_recharge(db, customer, amount,
                                   f"{chat_id}:{message_id}:a{amount}", language, chat_id)
            return

        if data.startswith("wproof_"):
            order_id = int(data.split("_", 1)[1])
            order = await wallet_service.get_recharge_for_customer(db, order_id, customer.id)
            if order is None or order["status"] not in PENDING_STATUSES:
                await answer_callback(callback_id, tr("wallet.errorOrderState", language), alert=True)
                return
            customer.pending_recharge_order_id = order_id
            customer.pending_payment_order_id = None   # 互斥：见 pay_ 深链处的说明
            await db.commit()
            await answer_callback(callback_id)
            await send_bot_message(chat_id, tr("wallet.sendProofPrompt", language, order=order["order_no"]))
            return

        if data.startswith("wcancel_"):
            order_id = int(data.split("_", 1)[1])
            try:
                order = await wallet_service.cancel_recharge(
                    db, order_id=order_id, customer_id=customer.id)
            except WalletError as exc:
                await db.rollback()
                await answer_callback(callback_id, _wallet_error_text(exc, language), alert=True)
                return
            if customer.pending_recharge_order_id == order_id:
                customer.pending_recharge_order_id = None
            await db.commit()
            await answer_callback(callback_id, tr("wallet.cancelledShort", language))
            await send_bot_message(chat_id, tr("wallet.cancelled", language, order=order["order_no"]))
            return

        await answer_callback(callback_id, tr("error.generic", language), alert=True)
    except Exception:  # noqa: BLE001 - 回调不能把 webhook 打成 500
        await db.rollback()
        await answer_callback(callback_id, tr("error.generic", language), alert=True)


async def _handle_recharge_proof(tg_user_id: str, msg: dict, customer: Customer,
                                 language: str, db: AsyncSession) -> bool:
    """客户发来的是「钱包充值」的转账截图。

    返回 True 表示这张图已经被充电流程消费掉；返回 False 表示充值单已失效，
    调用方应该继续按「订单收款凭证」处理——否则用户为餐费发的截图会被悄悄吞掉。
    """
    order_id = customer.pending_recharge_order_id
    photos = msg.get("photo") or []
    if not photos:
        return True
    order = await wallet_service.get_recharge_for_customer(db, order_id, customer.id)
    if order is None or order["status"] not in PENDING_STATUSES:
        customer.pending_recharge_order_id = None
        await db.commit()
        return False

    photo = photos[-1]
    try:
        updated = await wallet_service.submit_proof(
            db, order_id=order_id, customer_id=customer.id,
            file_id=photo["file_id"],
            file_unique_id=photo.get("file_unique_id") or photo["file_id"],
        )
    except WalletError as exc:
        await db.rollback()
        await send_bot_message(tg_user_id, _wallet_error_text(exc, language))
        return True

    customer.pending_recharge_order_id = None
    await db.commit()
    await send_bot_message(tg_user_id, tr("wallet.proofReceived", language, order=updated["order_no"]))

    recharge_store = await _store_settings(db, customer=customer)
    if recharge_store and recharge_store.telegram_staff_group_id:
        await notify_new_recharge(
            recharge_store.telegram_staff_group_id, updated, customer,
            recharge_store.staff_group_language or "en", file_id=photo["file_id"],
        )
    return True


async def _handle_wallet_review(db: AsyncSession, action: str, order_id: int, staff,
                                sender: dict, chat: dict, group_language: str,
                                callback_id: str) -> None:
    """员工在群里点「确认到账 / 驳回」。"""
    order = await wallet_service.get_recharge(db, order_id)
    # 门店隔离：充值审核按钮也只能审本店的单
    if order is None or not can_access_store(
            {"staff_id": staff.id, "role": staff.role, "store_id": staff.store_id},
            order.get("store_id")):
        await answer_callback(callback_id, tr("error.generic", group_language), alert=True)
        return
    old_status = order["status"]
    staff_id = staff.id
    try:
        if action == "walletok":
            updated = await wallet_service.approve_recharge(
                db, order_id=order_id, staff_id=staff_id,
                remark="Staff confirmed ABA receipt in Telegram group")
        else:
            updated = await wallet_service.reject_recharge(
                db, order_id=order_id, staff_id=staff_id,
                reason="Staff rejected the top-up in Telegram group")
    except WalletError as exc:
        await db.rollback()
        await answer_callback(callback_id, _wallet_error_text(exc, group_language), alert=True)
        return

    db.add(AuditLog(
        actor_staff_id=staff_id, entity_type="wallet_recharge", entity_id=str(order_id),
        action="recharge_approved" if action == "walletok" else "recharge_rejected",
        details={
            "source": "telegram_group", "operator_name": staff.login_name,
            "operator_telegram_id": str(sender.get("id")),
            "group_id": str(chat.get("id")), "order_no": updated["order_no"],
            "amount_minor": updated["amount"], "received_minor": updated["received_amount"],
            "from_status": old_status, "to_status": updated["status"],
        },
    ))
    customer_result = await db.execute(
        select(Customer).filter(Customer.id == updated["customer_id"]))
    customer = customer_result.scalars().first()
    await db.commit()

    # 大额双人复核：只凑到一位时订单仍是 under_review，必须说清楚「还差一位」，
    # 否则员工会以为没生效而反复点。
    done, required = await wallet_service.approval_progress(db, order_id)
    if action == "walletok" and done < required:
        await answer_callback(callback_id, tr("wallet.needSecondApprover", group_language,
                                              done=done, required=required), alert=True)
        try:
            await send_bot_message(
                str(chat.get("id")),
                tr("wallet.groupAwaitingSecond", group_language, order=updated["order_no"],
                   done=done, required=required),
            )
        except Exception:  # noqa: BLE001
            pass
        return

    if customer:
        try:
            await notify_recharge_review(customer, updated,
                                         "APPROVED" if action == "walletok" else "REJECTED")
        except Exception:  # noqa: BLE001
            pass
    await answer_callback(callback_id, tr("common.save", group_language))
    try:
        await send_bot_message(
            str(chat.get("id")),
            tr("wallet.groupReviewDone", group_language,
               order=updated["order_no"], status=updated["status"]),
        )
    except Exception:  # noqa: BLE001
        pass


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
