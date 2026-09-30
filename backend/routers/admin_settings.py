from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from database import get_db
from models import StoreSettings
from dependencies import get_current_manager, get_current_staff
from jose import jwt
import os

router = APIRouter(prefix="/api/v1/admin/settings", tags=["Admin Settings"])

class SettingsUpdate(BaseModel):
    currency: str
    timezone: str
    aba_qr_asset_key: str = None
    payment_link: str = None
    telegram_staff_group_id: str = None
    open_hours: str = None

@router.get("/")
async def get_settings(staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoreSettings))
    settings = result.scalars().first()
    
    if not settings:
        settings = StoreSettings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        
    return settings

@router.patch("/")
async def update_settings(req: SettingsUpdate, manager_info: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoreSettings))
    settings = result.scalars().first()
    
    if not settings:
        settings = StoreSettings()
        db.add(settings)
        
    settings.currency = req.currency
    settings.timezone = req.timezone
    settings.aba_qr_asset_key = req.aba_qr_asset_key
    settings.payment_link = req.payment_link
    settings.telegram_staff_group_id = req.telegram_staff_group_id
    settings.open_hours = req.open_hours
    
    await db.commit()
    return settings
