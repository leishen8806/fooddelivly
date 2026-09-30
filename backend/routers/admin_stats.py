from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from database import get_db
from models import Order
from dependencies import get_current_staff
from jose import jwt
import os
from datetime import datetime

router = APIRouter(prefix="/api/v1/admin/analytics", tags=["Admin Stats"])

@router.get("/")
async def get_analytics(
    start_date: str = None, 
    end_date: str = None, 
    staff_info: dict = Depends(get_current_staff), 
    db: AsyncSession = Depends(get_db)
):
    # In MVP, just return basic aggregates
    # We would use start_date and end_date to filter Order.created_at
    
    query = select(Order)
    result = await db.execute(query)
    orders = result.scalars().all()
    
    total_orders = len(orders)
    total_amount = sum(o.total_minor for o in orders)
    
    confirmed_orders = [o for o in orders if o.payment_status == "PAID_CONFIRMED"]
    confirmed_amount = sum(o.total_minor for o in confirmed_orders)
    
    pending_review_orders = [o for o in orders if o.payment_status == "PROOF_SUBMITTED"]
    pending_review_amount = sum(o.total_minor for o in pending_review_orders)
    
    cancelled_orders = [o for o in orders if o.order_status == "CANCELLED"]
    cancelled_count = len(cancelled_orders)
    
    return {
        "order_volume": total_orders,
        "order_total_minor": total_amount,
        "confirmed_receipts_minor": confirmed_amount,
        "in_review_minor": pending_review_amount,
        "cancelled_count": cancelled_count,
        "manual_review_basis": True
    }
