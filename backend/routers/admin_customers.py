import re

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from database import get_db
from dependencies import get_current_manager
from models import Customer, Order

router = APIRouter(prefix="/api/v1/admin/customers", tags=["Admin Customers"])


@router.get("")
async def list_customers(
    _manager: dict = Depends(get_current_manager),
    db: AsyncSession = Depends(get_db),
):
    completed_count = func.count(case((Order.order_status == "COMPLETED", 1)))
    result = await db.execute(
        select(Customer, func.count(Order.id), completed_count)
        .outerjoin(Order, Order.customer_id == Customer.id)
        .group_by(Customer.id)
        .order_by(Customer.created_at.desc(), Customer.id.desc())
    )
    rows = result.all()
    customers = []
    for customer, order_count, completed_order_count in rows:
        username = (customer.username or "").lstrip("@")
        chat_url = (
            f"https://t.me/{username}"
            if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username)
            else f"tg://user?id={customer.telegram_user_id}"
        )
        customers.append({
            "id": customer.id,
            "telegram_user_id": customer.telegram_user_id,
            "display_name": customer.display_name,
            "username": username or None,
            "telegram_chat_url": chat_url,
            "order_count": order_count,
            "completed_order_count": completed_order_count,
            "created_at": customer.created_at,
        })
    return customers
