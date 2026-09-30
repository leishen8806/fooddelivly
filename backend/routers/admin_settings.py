from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from database import get_db
from models import StoreSettings, AuditLog
from dependencies import get_current_manager, get_current_staff
from jose import jwt
import os

router = APIRouter(prefix="/api/v1/admin/settings", tags=["Admin Settings"])

class SettingsUpdate(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str = Field(min_length=1, max_length=64)
    aba_qr_asset_key: str | None = None
    payment_link: str | None = None
    telegram_staff_group_id: str | None = None
    staff_group_language: str = "en"
    open_hours: str | None = None

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

@router.get("/")
async def get_settings(staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoreSettings))
    settings = result.scalars().first()
    
    if not settings:
        return {"currency": "USD", "timezone": "Asia/Phnom_Penh", "aba_qr_asset_key": None,
                "payment_link": None, "telegram_staff_group_id": None, "staff_group_language": "en", "open_hours": None}
    return settings

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
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="store_settings", entity_id="1",
                    action="updated", details={"currency": req.currency, "timezone": req.timezone,
                                                "payment_link_configured": bool(req.payment_link),
                                                "qr_configured": bool(req.aba_qr_asset_key),
                                                "telegram_group_configured": bool(req.telegram_staff_group_id),
                                                "staff_group_language": req.staff_group_language}))
    await db.commit()
    await db.refresh(settings)
    return settings
