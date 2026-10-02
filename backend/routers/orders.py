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
from sale_window import describe, is_on_sale
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
    settings = (await db.execute(select(StoreSettings).limit(1))).scalars().first()
    return order_handoff(order, settings)

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
                "currency": existing_order.currency,
                "status": existing_order.order_status,
                "payment_status": existing_order.payment_status,
                "payment_method": existing_order.payment_method,
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

    # 售卖时间按**店铺时区**判断；这里取一次，整单用同一个时刻
    settings_result = await db.execute(select(StoreSettings).limit(1))
    _settings = settings_result.scalars().first()
    try:
        store_tz = ZoneInfo(_settings.timezone if _settings and _settings.timezone else "Asia/Phnom_Penh")
    except ZoneInfoNotFoundError:
        store_tz = ZoneInfo("Asia/Phnom_Penh")
    now_local = datetime.now(store_tz)

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
        windows = product.sale_windows
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

        unit_price = product.price_minor + option_delta
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
        
    try:
        new_order = Order(
            public_code=generate_public_code(),
            customer_id=customer_id,
            room_number=order_req.room_number,
            order_status="NEW",
            payment_status="UNPAID",
            payment_method="WALLET" if order_req.pay_with_wallet else "MANUAL",
            currency=currency,
            customer_language=customer.preferred_language or ("zh-CN" if (customer.language_code or "").lower().startswith("zh") else "km" if (customer.language_code or "").lower().startswith("km") else "en"),
            total_minor=total_minor,
            idempotency_key=idempotency_key,
            request_digest=request_digest
        )
        db.add(new_order)
        await db.flush() # get new_order.id

        for oi in order_items:
            oi.order_id = new_order.id
            db.add(oi)

        if order_req.pay_with_wallet:
            # 余额抵扣：与建单在**同一个事务**里，钱和订单要么都成功、要么都回滚。
            # 扣款幂等锚点是订单号，所以即使调用被重试也不会重复扣。
            from wallet_service import WalletError, spend_balance

            try:
                await spend_balance(
                    db,
                    customer_id=customer_id,
                    amount_minor=total_minor,
                    biz_id=new_order.public_code,
                    idem=f"pay:{new_order.public_code}",
                    currency=currency,
                    remark=f"下单余额支付 {new_order.public_code}",
                )
            except WalletError as exc:
                await db.rollback()
                raise HTTPException(status_code=exc.http_status, detail=exc.message)
            new_order.payment_status = "PAID_CONFIRMED"

        await db.commit()
        await db.refresh(new_order)
        settings_result = await db.execute(select(StoreSettings).limit(1))
        settings = settings_result.scalars().first()
        try:
            message_id = await notify_new_order(new_order, settings.telegram_staff_group_id if settings else None,
                                                settings.staff_group_language if settings else "en", order_items)
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
                    **await _handoff_for(db, existing_order),
                    "message": "Order already exists"
                }
        raise
    
    settings_result = await db.execute(select(StoreSettings).limit(1))
    settings = settings_result.scalars().first()
    return {
        "public_code": new_order.public_code,
        "total_minor": new_order.total_minor,
        "currency": new_order.currency,
        "status": new_order.order_status,
        "payment_status": new_order.payment_status,
        "payment_method": new_order.payment_method,
        **order_handoff(new_order, settings),
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
        "currency": order.currency, "total_minor": order.total_minor, "created_at": order.created_at,
        **await _handoff_for(db, order),
        "items": [{"name": x.product_name_snapshot, "quantity": x.quantity, "unit_price_minor": x.unit_price_minor,
                   "line_total_minor": x.line_total_minor, "options": x.options_json or {}} for x in items_result.scalars().all()],
        "proof_status": proof.review_status if proof else None,
    }
