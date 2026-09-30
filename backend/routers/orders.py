from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from pydantic import BaseModel, Field
from database import get_db
from models import Order, OrderItem, Product
from models import StoreSettings
from dependencies import get_current_customer
from telegram_service import notify_new_order
import secrets
import string

router = APIRouter(prefix="/api/v1", tags=["Orders"])

# Schemas
class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, description="Quantity must be positive")
    options: Optional[dict] = Field(None, description="Priced options/extras are temporarily disabled in MVP")

class OrderCreate(BaseModel):
    room_number: str
    items: List[OrderItemCreate]

def generate_public_code():
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(8))

@router.post("/orders")
async def create_order(
    order_req: OrderCreate, 
    customer_id: int = Depends(get_current_customer),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
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
                "message": "Order already exists"
            }

    if not order_req.items:
        raise HTTPException(status_code=400, detail="Order must have items")

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
            message_id = await notify_new_order(new_order, settings.telegram_staff_group_id if settings else None)
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
                    "message": "Order already exists"
                }
        raise
    
    return {
        "public_code": new_order.public_code,
        "total_minor": new_order.total_minor,
        "currency": new_order.currency,
        "status": new_order.order_status
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
    return orders
