from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import time
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from database import get_db
from models import StoreSettings, AuditLog
from store_context import default_store as load_default_store
from dependencies import get_current_manager, get_current_staff
from jose import jwt
import os

router = APIRouter(prefix="/api/v1/admin/settings", tags=["Admin Settings"])

class BusinessHour(BaseModel):
    start: str = Field(description="HH:MM（店铺时区）")
    end: str = Field(description="HH:MM；小于 start 表示跨午夜")

    @field_validator("start", "end")
    @classmethod
    def validate_hhmm(cls, value: str) -> str:
        try:
            time.fromisoformat(value.strip())
        except ValueError:
            raise ValueError("Time must be HH:MM")
        return value.strip()

    def as_pair(self) -> tuple[time, time]:
        return (time.fromisoformat(self.start).replace(second=0, microsecond=0),
                time.fromisoformat(self.end).replace(second=0, microsecond=0))


class SettingsUpdate(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str = Field(min_length=1, max_length=64)
    aba_qr_asset_key: str | None = None
    payment_link: str | None = None
    telegram_staff_group_id: str | None = None
    staff_group_language: str = "en"
    open_hours: str | None = None
    # 经营参数（可选：不传则保持不变，避免老前端把新字段清空）
    is_accepting_orders: Optional[bool] = None
    business_hours: Optional[List[BusinessHour]] = None
    min_order_minor: Optional[int] = Field(None, ge=0, le=100_000_000)
    delivery_fee_minor: Optional[int] = Field(None, ge=0, le=10_000_000)
    service_fee_minor: Optional[int] = Field(None, ge=0, le=10_000_000)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Timezone must be a valid IANA timezone")
        return value

    @field_validator("staff_group_language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        if value not in {"en", "zh-CN", "km"}:
            raise ValueError("Unsupported group language")
        return value

    @field_validator("payment_link")
    @classmethod
    def validate_payment_link(cls, value: str | None) -> str | None:
        if value and urlparse(value).scheme != "https":
            raise ValueError("Payment links must use HTTPS")
        return value

    @field_validator("aba_qr_asset_key")
    @classmethod
    def validate_qr_url(cls, value: str | None) -> str | None:
        if value and urlparse(value).scheme != "https" and not value.startswith("/"):
            raise ValueError("QR image must be an HTTPS URL or a same-origin path")
        return value

    @field_validator("telegram_staff_group_id")
    @classmethod
    def validate_group_id(cls, value: str | None) -> str | None:
        if value and not value.lstrip("-").isdigit():
            raise ValueError("Telegram group ID must be numeric")
        return value

@router.get("", include_in_schema=False)
@router.get("/")
async def get_settings(staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoreSettings))
    settings = result.scalars().first()
    
    if not settings:
        return {"currency": "USD", "timezone": "Asia/Phnom_Penh", "aba_qr_asset_key": None,
                "payment_link": None, "telegram_staff_group_id": None, "staff_group_language": "en",
                "open_hours": None, "is_accepting_orders": True, "business_hours": [],
                "min_order_minor": 0, "delivery_fee_minor": 0, "service_fee_minor": 0}
    return {
        "currency": settings.currency,
        "timezone": settings.timezone,
        "aba_qr_asset_key": settings.aba_qr_asset_key,
        "payment_link": settings.payment_link,
        "telegram_staff_group_id": settings.telegram_staff_group_id,
        "staff_group_language": settings.staff_group_language,
        "open_hours": settings.open_hours,
        "is_accepting_orders": bool(settings.is_accepting_orders),
        "business_hours": settings.business_hours or [],
        "min_order_minor": settings.min_order_minor or 0,
        "delivery_fee_minor": settings.delivery_fee_minor or 0,
        "service_fee_minor": settings.service_fee_minor or 0,
    }

@router.patch("", include_in_schema=False)
@router.patch("/")
async def update_settings(req: SettingsUpdate, manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoreSettings))
    settings = result.scalars().first()
    
    if not settings:
        settings = StoreSettings(id=1)
        db.add(settings)
        
    settings.currency = req.currency
    settings.timezone = req.timezone
    settings.aba_qr_asset_key = req.aba_qr_asset_key
    settings.payment_link = req.payment_link
    settings.telegram_staff_group_id = req.telegram_staff_group_id
    settings.staff_group_language = req.staff_group_language
    settings.open_hours = req.open_hours

    # 经营参数：只在显式传入时更新（PATCH 语义），并对营业时间做校验
    if req.business_hours is not None:
        pairs = [hour.as_pair() for hour in req.business_hours]
        for start, end in pairs:
            if start == end:
                raise HTTPException(status_code=422,
                                    detail="营业时间的开始与结束不能相同（全天营业请留空）")
        settings.business_hours = [{"start": h.start, "end": h.end} for h in req.business_hours]
    if req.is_accepting_orders is not None:
        settings.is_accepting_orders = req.is_accepting_orders
    if req.min_order_minor is not None:
        settings.min_order_minor = req.min_order_minor
    if req.delivery_fee_minor is not None:
        settings.delivery_fee_minor = req.delivery_fee_minor
    if req.service_fee_minor is not None:
        settings.service_fee_minor = req.service_fee_minor

    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="store_settings", entity_id="1",
                    action="updated", details={"currency": req.currency, "timezone": req.timezone,
                                                "payment_link_configured": bool(req.payment_link),
                                                "qr_configured": bool(req.aba_qr_asset_key),
                                                "telegram_group_configured": bool(req.telegram_staff_group_id),
                                                "staff_group_language": req.staff_group_language,
                                                "is_accepting_orders": settings.is_accepting_orders,
                                                "business_hours": settings.business_hours,
                                                "min_order_minor": settings.min_order_minor,
                                                "delivery_fee_minor": settings.delivery_fee_minor,
                                                "service_fee_minor": settings.service_fee_minor}))
    # 多商家过渡期：经营参数以**门店**为准（下单读门店），这里同步一份到主店，
    # 否则管理端改了设置但下单不生效。P3 会把 store_settings 彻底退休。
    store = await load_default_store(db)
    if store is not None:
        store.currency = settings.currency
        store.timezone = settings.timezone
        store.payment_link = settings.payment_link
        store.aba_qr_asset_key = settings.aba_qr_asset_key
        store.telegram_staff_group_id = settings.telegram_staff_group_id
        store.staff_group_language = settings.staff_group_language
        store.is_accepting_orders = bool(settings.is_accepting_orders)
        store.business_hours = settings.business_hours or []
        store.min_order_minor = int(settings.min_order_minor or 0)
        store.delivery_fee_minor = int(settings.delivery_fee_minor or 0)
        store.service_fee_minor = int(settings.service_fee_minor or 0)

    await db.commit()
    await db.refresh(settings)
    return {
        "currency": settings.currency,
        "timezone": settings.timezone,
        "aba_qr_asset_key": settings.aba_qr_asset_key,
        "payment_link": settings.payment_link,
        "telegram_staff_group_id": settings.telegram_staff_group_id,
        "staff_group_language": settings.staff_group_language,
        "open_hours": settings.open_hours,
        "is_accepting_orders": bool(settings.is_accepting_orders),
        "business_hours": settings.business_hours or [],
        "min_order_minor": settings.min_order_minor or 0,
        "delivery_fee_minor": settings.delivery_fee_minor or 0,
        "service_fee_minor": settings.service_fee_minor or 0,
    }
