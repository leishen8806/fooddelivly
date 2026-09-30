from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
import os
from pydantic import BaseModel, Field, field_validator
from database import get_db
from models import Order, OrderItem, Product
from models import StoreSettings, Customer, PaymentProof
from dependencies import get_current_customer
from telegram_service import notify_new_order
from urllib.parse import urlparse
import secrets
import string

router = APIRouter(prefix="/api/v1", tags=["Orders"])

# Schemas
class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=99, description="Quantity must be between 1 and 99")
    options: Optional[dict] = Field(None, description="Priced options/extras are temporarily disabled in MVP")

class OrderCreate(BaseModel):
    room_number: str = Field(min_length=1, max_length=32)
    items: List[OrderItemCreate] = Field(min_length=1, max_length=50)

    @field_validator("room_number")
    @classmethod
    def room_number_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Room number is required")
        return value

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
                **payment_handoff(existing_order.public_code, (await db.execute(select(StoreSettings).limit(1))).scalars().first()),
                "message": "Order already exists"
            }

    customer_result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = customer_result.scalars().first()
    if customer is None:
        raise HTTPException(status_code=401, detail="Customer session is no longer valid")

    total_minor = 0
    currency = None
    order_items = []
    
    for item in order_req.items:
        if item.options:
            # Temporarily reject orders attempting to use priced options/extras
            raise HTTPException(status_code=400, detail="Priced options/extras are temporarily disabled in MVP")

        result = await db.execute(select(Product).filter(Product.id == item.product_id))
        product = result.scalars().first()
        if not product or not product.available:
            raise HTTPException(status_code=400, detail=f"Product {item.product_id} not available")
            
        # Ensure consistent currency
        if currency is None:
            currency = product.currency
        elif currency != product.currency:
            raise HTTPException(status_code=400, detail="Mixed currencies in order are not allowed")
        
        unit_price = product.price_minor
        line_total = unit_price * item.quantity
        total_minor += line_total
        
        order_items.append(OrderItem(
            product_id=product.id,
            product_name_snapshot=product.name,
            unit_price_minor=unit_price,
            quantity=item.quantity,
            options_json=item.options,
            line_total_minor=line_total
        ))
        
    try:
        new_order = Order(
            public_code=generate_public_code(),
            customer_id=customer_id,
            room_number=order_req.room_number,
            order_status="NEW",
            payment_status="UNPAID",
            currency=currency,
            customer_language=("zh-CN" if (customer.language_code or "").lower().startswith("zh") else "km" if (customer.language_code or "").lower().startswith("km") else "en"),
            total_minor=total_minor,
            idempotency_key=idempotency_key,
            request_digest=request_digest
        )
        db.add(new_order)
        await db.flush() # get new_order.id
        
        for oi in order_items:
            oi.order_id = new_order.id
            db.add(oi)
            
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
                    **payment_handoff(existing_order.public_code, (await db.execute(select(StoreSettings).limit(1))).scalars().first()),
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
        **payment_handoff(new_order.public_code, settings),
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
             "payment_status": o.payment_status, "currency": o.currency, "total_minor": o.total_minor,
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
        "currency": order.currency, "total_minor": order.total_minor, "created_at": order.created_at,
        "items": [{"name": x.product_name_snapshot, "quantity": x.quantity, "unit_price_minor": x.unit_price_minor,
                   "line_total_minor": x.line_total_minor} for x in items_result.scalars().all()],
        "proof_status": proof.review_status if proof else None,
    }
