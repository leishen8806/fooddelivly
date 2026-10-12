from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from coupon_service import CouponError, claim, list_customer_coupons
from database import get_db
from dependencies import get_current_customer


router = APIRouter(prefix="/api/v1/coupons", tags=["Customer Coupons"])


class CouponClaimRequest(BaseModel):
    code: str = Field(min_length=4, max_length=160)


@router.get("/mine")
async def get_my_coupons(customer_id: int = Depends(get_current_customer), db: AsyncSession = Depends(get_db)):
    return await list_customer_coupons(db, customer_id)


@router.post("/claim")
async def claim_coupon(
    req: CouponClaimRequest,
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
):
    try:
        result = await claim(
            db,
            token=req.code,
            customer_id=customer_id,
            claim_request_key=idempotency_key,
        )
        await db.commit()
        return {"claimed": True, "coupon": result}
    except CouponError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


