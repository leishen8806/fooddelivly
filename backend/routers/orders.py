from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Literal
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import selectinload
from database import get_db
from product_options import OptionError, load_groups, resolve_selections
from store_context import effective_product, load_overrides, resolve_store, store_for_order
from sale_window import describe, is_on_sale, windows_from_json
from models import Order, OrderItem, Product
from models import StoreSettings, Customer, PaymentProof
from dependencies import get_current_customer
from telegram_service import notify_new_order
from urllib.parse import urlparse
import secrets
import string

router = APIRouter(prefix="/api/v1", tags=["Orders"])

# Schemas
class OptionSelection(BaseModel):
    """规格/附加选择。**只传 id**：价格由服务端回库计算，客户端传什么都不看。"""
    group_id: int
    option_id: int

class OrderItemOptions(BaseModel):
    sweetness: Optional[Literal[0, 25, 50, 75, 100]] = None
    selections: List[OptionSelection] = Field(default_factory=list, max_length=40)

class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=99, description="Quantity must be between 1 and 99")
    options: Optional[OrderItemOptions] = Field(None, description="Non-priced preparation options")

class OrderCreate(BaseModel):
    room_number: str = Field(min_length=1, max_length=32)
    items: List[OrderItemCreate] = Field(min_length=1, max_length=50)
    #: 用钱包余额直接支付：下单即扣款、即 PAID_CONFIRMED，不需要上传转账截图。
    #: 余额不足返回 409，订单不会创建（扣款与建单在同一个事务里）。
    pay_with_wallet: bool = False

    @field_validator("room_number")
    @classmethod
    def room_number_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Room number is required")
        return value

def product_localized_name(product: Product, language: str = "en") -> str:
    name = product.name if isinstance(product.name, dict) else {}
    for key in (language, "en", "zh-CN", "zh_CN", "km"):
        if name.get(key):
            return str(name[key])
    return next((str(v) for v in name.values() if v), f"#{product.id}")

def generate_public_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(8))

def payment_handoff(public_code: str, settings: StoreSettings | None) -> dict:
    payment_link = settings.payment_link if settings and settings.payment_link and urlparse(settings.payment_link).scheme == "https" else None
    qr_value = settings.aba_qr_asset_key if settings else None
    qr_url = qr_value if qr_value and (urlparse(qr_value).scheme == "https" or qr_value.startswith("/")) else None
    bot_username = (os.getenv("BOT_USERNAME") or "").lstrip("@")
    bot_deeplink = f"https://t.me/{bot_username}?start=pay_{public_code}" if bot_username else None
    return {"payment_link": payment_link, "payment_qr_url": qr_url, "bot_deeplink": bot_deeplink}


def order_handoff(order: Order, settings: StoreSettings | None) -> dict:
    """钱包支付的单已经付清了，就不要再给客户转账信息（否则会被重复付款）。"""
    if order.payment_method == "WALLET" and order.payment_status == "PAID_CONFIRMED":
        return {"payment_link": None, "payment_qr_url": None, "bot_deeplink": None}
    return payment_handoff(order.public_code, settings)


async def _handoff_for(db, order: Order) -> dict:
    # 收款信息按**订单所属门店**取：以前取全局设置，会把 A 店的收款码
    # 发给 B 店的客人（多门店时这是直接的资金风险）。
    store = await store_for_order(db, order)
    return order_handoff(order, store)

@router.post("/orders")
async def create_order(
    order_req: OrderCreate, 
    customer_id: int = Depends(get_current_customer),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    db: AsyncSession = Depends(get_db)
):
    import hashlib
    # To properly enforce idempotency by request body, we hash the request payload.
    request_digest = None
    if idempotency_key:
        payload_bytes = order_req.model_dump_json().encode('utf-8')
        request_digest = hashlib.sha256(payload_bytes).hexdigest()
        
        # Scope idempotency check to the current customer to prevent cross-customer collisions
        result = await db.execute(
            select(Order)
            .filter(Order.idempotency_key == idempotency_key)
            .filter(Order.customer_id == customer_id)
        )
        existing_order = result.scalars().first()
        
        if existing_order:
            if existing_order.request_digest != request_digest:
                raise HTTPException(status_code=409, detail="Idempotency key already used for a different request")
                
            return {
                "public_code": existing_order.public_code,
                "total_minor": existing_order.total_minor,
                "subtotal_minor": existing_order.subtotal_minor if existing_order.subtotal_minor is not None else existing_order.total_minor,
                "delivery_fee_minor": existing_order.delivery_fee_minor or 0,
                "service_fee_minor": existing_order.service_fee_minor or 0,
                "currency": existing_order.currency,
                "status": existing_order.order_status,
                "payment_status": existing_order.payment_status,
                "payment_method": existing_order.payment_method,
                "wallet_paid_minor": existing_order.wallet_paid_minor or 0,
                "external_due_minor": existing_order.external_due_minor if existing_order.external_due_minor is not None else existing_order.total_minor,
                **await _handoff_for(db, existing_order),
                "message": "Order already exists"
            }

    customer_result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = customer_result.scalars().first()
    if customer is None:
        raise HTTPException(status_code=401, detail="Customer session is no longer valid")

    total_minor = 0
    currency = None
    order_items = []

    # 一次把这一单涉及菜品的规格分组读出来（避免每个条目查一次库）
    product_ids = [item.product_id for item in order_req.items]
    groups_by_product = await load_groups(db, product_ids)

    # 门店上下文：经营参数（营业时间/费用/起送）与菜品覆盖都以**门店**为准。
    # 主店在迁移时从 store_settings 复制而来，所以对现有单店行为没有变化。
    store_row = await resolve_store(db, customer=customer)
    if store_row is None:
        raise HTTPException(status_code=409, detail="门店未配置，无法下单")
    try:
        store_tz = ZoneInfo(store_row.timezone or "Asia/Phnom_Penh")
    except ZoneInfoNotFoundError:
        store_tz = ZoneInfo("Asia/Phnom_Penh")
    now_local = datetime.now(store_tz)
    _overrides = await load_overrides(db, store_row.id, product_ids)

    # 门店级经营校验：暂停接单 / 营业时间。同样必须在服务端做。
    if store_row.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="门店已停业，无法下单")
    if not store_row.is_accepting_orders:
        raise HTTPException(status_code=409, detail="店铺已暂停接单，请稍后再试")
    business_hours = windows_from_json(store_row.business_hours)
    if business_hours and not is_on_sale(business_hours, now_local.time()):
        span = " / ".join(f'{w["start"]}-{w["end"]}' for w in describe(business_hours))
        raise HTTPException(status_code=409, detail=f"当前不在营业时间内（{span}）")

    for item in order_req.items:
        result = await db.execute(
            select(Product)
            .filter(Product.id == item.product_id)
            .options(selectinload(Product.sale_windows)))
        product = result.scalars().first()
        if not product or not product.available:
            raise HTTPException(status_code=400, detail=f"Product {item.product_id} not available")

        # 售卖时间：不在时间段内不能下单。**必须在这里拦住**——
        # 前端把按钮藏起来只防误点，不防伪造请求。
        effective = effective_product(product, _overrides.get(product.id), store_row)
        if not effective.available:
            raise HTTPException(status_code=400, detail=f"Product {item.product_id} not available")
        windows = windows_from_json(effective.sale_windows)
        if not is_on_sale(windows, now_local.time()):
            hours = " / ".join(f'{w["start"]}-{w["end"]}' for w in describe(windows))
            raise HTTPException(
                status_code=409,
                detail=f"{product_localized_name(product)} 当前不在售卖时间内（{hours}）")

        if product.sweetness_enabled and (item.options is None or item.options.sweetness is None):
            raise HTTPException(status_code=422, detail=f"Sweetness selection is required for product {item.product_id}")
            
        # Ensure consistent currency
        if currency is None:
            currency = product.currency
        elif currency != product.currency:
            raise HTTPException(status_code=400, detail="Mixed currencies in order are not allowed")
        
        # 规格 / 附加：只认 id，价格回库算，客户端传来的价格一律不看
        selections = (item.options.selections if item.options is not None else None) or []
        try:
            option_delta, option_snapshot = resolve_selections(
                groups_by_product.get(product.id, []), selections)
        except OptionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

        unit_price = effective.price_minor + option_delta
        line_total = unit_price * item.quantity
        total_minor += line_total

        options_json: dict = {}
        if product.sweetness_enabled and item.options is not None and item.options.sweetness is not None:
            options_json["sweetness"] = item.options.sweetness
        if option_snapshot:
            options_json["selections"] = option_snapshot
        if option_delta:
            options_json["price_delta_minor"] = option_delta

        order_items.append(OrderItem(
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_minor=unit_price,
            quantity=item.quantity,
            options_json=options_json,
            line_total_minor=line_total
        ))
        
    # 最低起送按**商品小计**判断（不含配送费/服务费），再叠加费用得到应付总额
    subtotal_minor = total_minor
    min_order_minor = int(store_row.min_order_minor or 0)
    if min_order_minor and subtotal_minor < min_order_minor:
        raise HTTPException(
            status_code=409,
            detail=f"最低起送 {min_order_minor / 100:.2f} {currency}，当前商品小计 {subtotal_minor / 100:.2f} {currency}")
    delivery_fee_minor = int(store_row.delivery_fee_minor or 0)
    service_fee_minor = int(store_row.service_fee_minor or 0)
    total_minor = subtotal_minor + delivery_fee_minor + service_fee_minor

    wallet_paid_minor = 0
    try:
        new_order = Order(
            public_code=generate_public_code(),
            customer_id=customer_id,
            room_number=order_req.room_number,
            order_status="NEW",
            payment_status="UNPAID",
            payment_method="MANUAL",
            store_id=store_row.id,
            currency=currency,
            customer_language=customer.preferred_language or ("zh-CN" if (customer.language_code or "").lower().startswith("zh") else "km" if (customer.language_code or "").lower().startswith("km") else "en"),
            total_minor=total_minor,
            subtotal_minor=subtotal_minor,
            delivery_fee_minor=delivery_fee_minor,
            service_fee_minor=service_fee_minor,
            wallet_paid_minor=0,
            external_due_minor=total_minor,
            idempotency_key=idempotency_key,
            request_digest=request_digest
        )
        db.add(new_order)
        await db.flush() # get new_order.id

        for oi in order_items:
            oi.order_id = new_order.id
            db.add(oi)

        if order_req.pay_with_wallet:
            # 钱包优先：在同一事务内最多扣掉当前可用余额，剩余金额走 ABA。
            # 数据库函数会锁钱包行并记录实际扣款，避免先读余额再扣款的竞态。
            from wallet_service import WalletError, spend_up_to

            try:
                wallet_paid_minor = await spend_up_to(
                    db,
                    customer_id=customer_id,
                    max_amount_minor=total_minor,
                    biz_id=new_order.public_code,
                    idem=f"pay:{new_order.public_code}",
                    currency=currency,
                    remark=f"下单余额支付 {new_order.public_code}",
                )
            except WalletError as exc:
                await db.rollback()
                raise HTTPException(status_code=exc.http_status, detail=exc.message)
            new_order.wallet_paid_minor = wallet_paid_minor
            new_order.external_due_minor = max(total_minor - wallet_paid_minor, 0)
            if wallet_paid_minor >= total_minor:
                new_order.payment_method = "WALLET"
                new_order.payment_status = "PAID_CONFIRMED"
            elif wallet_paid_minor > 0:
                new_order.payment_method = "MIXED"

        await db.commit()
        await db.refresh(new_order)
        # 通知发到**该订单所属门店**的群（不是全局那一个群）
        notify_store = await store_for_order(db, new_order)
        try:
            message_id = await notify_new_order(
                new_order,
                notify_store.telegram_staff_group_id if notify_store else None,
                (notify_store.staff_group_language if notify_store else "en") or "en",
                order_items, customer)
            if message_id:
                new_order.telegram_group_message_id = message_id
                await db.commit()
        except Exception:
            # Telegram notification failure must not undo a paid-independent order.
            pass
    except IntegrityError as e:
        await db.rollback()
        # Handle unique constraint violation on idempotency_key caused by race conditions
        if "uq_order_idempotency" in str(e.orig):
            # Fetch the order that was inserted by the concurrent request
            result = await db.execute(
                select(Order)
                .filter(Order.idempotency_key == idempotency_key)
                .filter(Order.customer_id == customer_id)
            )
            existing_order = result.scalars().first()
            if existing_order:
                if existing_order.request_digest != request_digest:
                    raise HTTPException(status_code=409, detail="Idempotency key already used for a different request")
                return {
                    "public_code": existing_order.public_code,
                    "total_minor": existing_order.total_minor,
                    "currency": existing_order.currency,
                    "status": existing_order.order_status,
                    "payment_status": existing_order.payment_status,
                    "payment_method": existing_order.payment_method,
                    "wallet_paid_minor": existing_order.wallet_paid_minor or 0,
                    "external_due_minor": existing_order.external_due_minor if existing_order.external_due_minor is not None else existing_order.total_minor,
                    **await _handoff_for(db, existing_order),
                    "message": "Order already exists"
                }
        raise
    
    return {
        "public_code": new_order.public_code,
        "total_minor": new_order.total_minor,
        "subtotal_minor": new_order.subtotal_minor,
        "delivery_fee_minor": new_order.delivery_fee_minor or 0,
        "service_fee_minor": new_order.service_fee_minor or 0,
        "currency": new_order.currency,
        "status": new_order.order_status,
        "payment_status": new_order.payment_status,
        "payment_method": new_order.payment_method,
        "wallet_paid_minor": new_order.wallet_paid_minor or 0,
        "external_due_minor": new_order.external_due_minor if new_order.external_due_minor is not None else new_order.total_minor,
        **await _handoff_for(db, new_order),
    }

@router.get("/orders")
async def get_orders(
    customer_id: int = Depends(get_current_customer), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Order)
        .filter(Order.customer_id == customer_id)
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()
    return [{"public_code": o.public_code, "room_number": o.room_number, "order_status": o.order_status,
             "payment_status": o.payment_status, "payment_method": o.payment_method,
             "currency": o.currency, "total_minor": o.total_minor,
             "wallet_paid_minor": o.wallet_paid_minor or 0,
             "external_due_minor": o.external_due_minor if o.external_due_minor is not None else o.total_minor,
             "created_at": o.created_at} for o in orders]

@router.get("/orders/{public_code}")
async def get_order(public_code: str, customer_id: int = Depends(get_current_customer), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).filter(Order.public_code == public_code, Order.customer_id == customer_id))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    items_result = await db.execute(select(OrderItem).filter(OrderItem.order_id == order.id))
    proofs_result = await db.execute(select(PaymentProof).filter(PaymentProof.order_id == order.id).order_by(PaymentProof.submitted_at.desc()))
    proof = proofs_result.scalars().first()
    return {
        "public_code": order.public_code, "room_number": order.room_number,
        "order_status": order.order_status, "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "currency": order.currency, "total_minor": order.total_minor,
        "wallet_paid_minor": order.wallet_paid_minor or 0,
        "external_due_minor": order.external_due_minor if order.external_due_minor is not None else order.total_minor,
        "subtotal_minor": order.subtotal_minor if order.subtotal_minor is not None else order.total_minor,
        "delivery_fee_minor": order.delivery_fee_minor or 0,
        "service_fee_minor": order.service_fee_minor or 0,
        "created_at": order.created_at,
        **await _handoff_for(db, order),
        "items": [{"name": x.product_name_snapshot, "quantity": x.quantity, "unit_price_minor": x.unit_price_minor,
                   "line_total_minor": x.line_total_minor, "options": x.options_json or {}} for x in items_result.scalars().all()],
        "proof_status": proof.review_status if proof else None,
    }
