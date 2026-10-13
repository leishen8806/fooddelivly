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
from telegram_service import _bot
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
    confirm_currency_change: bool = False
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
    unpaid_order_timeout_minutes: Optional[int] = Field(None, ge=0, le=1440)
    unpaid_order_reminder_minutes: Optional[int] = Field(None, ge=0, le=1440)
    shortage_customer_response_timeout_minutes: Optional[int] = Field(None, ge=0, le=10080)
    shortage_refund_review_minor: Optional[int] = Field(None, ge=0, le=100_000_000)

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


class TestNotificationRequest(BaseModel):
    message: str = Field(default="Tea Cafe test message", min_length=1, max_length=500)

async def _resolve_target_store(db: AsyncSession, staff_info: dict, store_code: str | None):
    """决定这次读写哪家门店。

    门店账号：只能是自己的店（传别的 code 直接 404，不泄露存在性）。
    总部账号：按 store_code 指定，缺省主店。
    """
    from store_context import default_store, get_store, get_store_by_code

    scope = (staff_info or {}).get("store_id")
    if scope:
        own = await get_store(db, scope)
        if store_code and own is not None and store_code != own.code:
            raise HTTPException(status_code=404, detail="Store not found")
        return own
    if store_code:
        found = await get_store_by_code(db, store_code)
        if found is None or (store_code and found.code != store_code):
            raise HTTPException(status_code=404, detail="Store not found")
        return found
    return await default_store(db)


def _store_payload(store) -> dict:
    """统一的门店配置结构（管理端设置页用的就是它）。"""
    return {
        "store_id": store.id,
        "store_code": store.code,
        "store_name": store.name,
        "currency": store.currency,
        "timezone": store.timezone,
        "aba_qr_asset_key": store.aba_qr_asset_key,
        "payment_link": store.payment_link,
        "telegram_staff_group_id": store.telegram_staff_group_id,
        "staff_group_language": store.staff_group_language,
        "open_hours": None,                     # 旧字段，已由 business_hours 取代
        "is_accepting_orders": bool(store.is_accepting_orders),
        "business_hours": store.business_hours or [],
        "min_order_minor": store.min_order_minor or 0,
        "delivery_fee_minor": store.delivery_fee_minor or 0,
        "service_fee_minor": store.service_fee_minor or 0,
        "unpaid_order_timeout_minutes": store.unpaid_order_timeout_minutes or 0,
        "unpaid_order_reminder_minutes": store.unpaid_order_reminder_minutes or 0,
        "shortage_customer_response_timeout_minutes": store.shortage_customer_response_timeout_minutes or 0,
        "shortage_refund_review_minor": store.shortage_refund_review_minor or 0,
        "commission_bps": store.commission_bps or 0,
        "commission_fixed_minor": store.commission_fixed_minor or 0,
        "settlement_cycle": store.settlement_cycle,
    }


def _summary_payload(payload: dict) -> dict:
    return {
        "business": {key: payload[key] for key in ("is_accepting_orders", "business_hours", "min_order_minor", "delivery_fee_minor", "service_fee_minor", "unpaid_order_timeout_minutes", "unpaid_order_reminder_minutes", "shortage_customer_response_timeout_minutes", "shortage_refund_review_minor")},
        "payment": {key: payload[key] for key in ("currency", "aba_qr_asset_key", "payment_link")},
        "notice": {key: payload[key] for key in ("telegram_staff_group_id", "staff_group_language")},
        "region": {key: payload[key] for key in ("timezone", "store_code", "store_name")},
    }


@router.get("", include_in_schema=False)
@router.get("/")
async def get_settings(store: str | None = None,
                       staff_info: dict = Depends(get_current_staff),
                       db: AsyncSession = Depends(get_db)):
    """读取**本门店**配置。

    设置不再是全局的：以前任何店长改一下，全平台的 ABA 收款码 / 员工群 /
    营业时间都会被覆盖。总部账号可以用 `?store=CODE` 指定目标门店。
    """
    target = await _resolve_target_store(db, staff_info, store)
    if target is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return _store_payload(target)


@router.get("/summary")
async def get_settings_summary(store: str | None = None,
                               staff_info: dict = Depends(get_current_staff),
                               db: AsyncSession = Depends(get_db)):
    target = await _resolve_target_store(db, staff_info, store)
    if target is None:
        raise HTTPException(status_code=404, detail="Store not found")
    payload = _store_payload(target)
    return {"settings": payload, "groups": _summary_payload(payload)}


@router.patch("", include_in_schema=False)
@router.patch("/")
async def update_settings(req: SettingsUpdate, store: str | None = None,
                          manager_info: dict = Depends(get_current_manager),
                          db: AsyncSession = Depends(get_db)):
    """更新**本门店**配置（总部可用 `?store=CODE` 指定目标门店）。"""
    target = await _resolve_target_store(db, manager_info, store)
    if target is None:
        raise HTTPException(status_code=404, detail="Store not found")

    if req.business_hours is not None:
        for hour in req.business_hours:
            start, end = hour.as_pair()
            if start == end:
                raise HTTPException(status_code=422,
                                    detail="营业时间的开始与结束不能相同（全天营业请留空）")

    currency_before = target.currency
    if req.currency != target.currency:
        if manager_info.get("store_id") is not None:
            raise HTTPException(status_code=403, detail="Only headquarters can change currency")
        if not req.confirm_currency_change:
            raise HTTPException(status_code=422, detail="confirm_currency_change is required")
    target.currency = req.currency
    target.timezone = req.timezone
    target.aba_qr_asset_key = req.aba_qr_asset_key
    target.payment_link = req.payment_link
    target.telegram_staff_group_id = req.telegram_staff_group_id
    target.staff_group_language = req.staff_group_language
    if req.business_hours is not None:
        target.business_hours = [{"start": h.start, "end": h.end} for h in req.business_hours]
    if req.is_accepting_orders is not None:
        target.is_accepting_orders = req.is_accepting_orders
    if req.min_order_minor is not None:
        target.min_order_minor = req.min_order_minor
    if req.delivery_fee_minor is not None:
        target.delivery_fee_minor = req.delivery_fee_minor
    if req.service_fee_minor is not None:
        target.service_fee_minor = req.service_fee_minor
    if req.unpaid_order_timeout_minutes is not None:
        target.unpaid_order_timeout_minutes = req.unpaid_order_timeout_minutes
    if req.unpaid_order_reminder_minutes is not None:
        target.unpaid_order_reminder_minutes = req.unpaid_order_reminder_minutes
    if req.shortage_customer_response_timeout_minutes is not None:
        target.shortage_customer_response_timeout_minutes = req.shortage_customer_response_timeout_minutes
    if req.shortage_refund_review_minor is not None:
        target.shortage_refund_review_minor = req.shortage_refund_review_minor

    # 过渡期：主店配置同时镜像到旧的 store_settings，给还没迁移的读取方兜底。
    # 所有读取方都走 stores 之后会删掉这张表。
    if target.code == "MAIN":
        legacy = (await db.execute(select(StoreSettings).limit(1))).scalars().first()
        if legacy is None:
            legacy = StoreSettings(id=1)
            db.add(legacy)
        legacy.currency = target.currency
        legacy.timezone = target.timezone
        legacy.payment_link = target.payment_link
        legacy.aba_qr_asset_key = target.aba_qr_asset_key
        legacy.telegram_staff_group_id = target.telegram_staff_group_id
        legacy.staff_group_language = target.staff_group_language
        legacy.open_hours = req.open_hours
        legacy.is_accepting_orders = bool(target.is_accepting_orders)
        legacy.business_hours = target.business_hours or []
        legacy.min_order_minor = int(target.min_order_minor or 0)
        legacy.delivery_fee_minor = int(target.delivery_fee_minor or 0)
        legacy.service_fee_minor = int(target.service_fee_minor or 0)
        legacy.unpaid_order_timeout_minutes = int(target.unpaid_order_timeout_minutes or 0)
        legacy.unpaid_order_reminder_minutes = int(target.unpaid_order_reminder_minutes or 0)
        legacy.shortage_customer_response_timeout_minutes = int(target.shortage_customer_response_timeout_minutes or 0)
        legacy.shortage_refund_review_minor = int(target.shortage_refund_review_minor or 0)

    details = {"store_code": target.code, "currency_before": currency_before,
                             "currency_after": target.currency,
                             "timezone": target.timezone,
                             "payment_link_configured": bool(target.payment_link),
                             "qr_configured": bool(target.aba_qr_asset_key),
                             "telegram_group_configured": bool(target.telegram_staff_group_id),
                             "staff_group_language": target.staff_group_language,
                             "is_accepting_orders": target.is_accepting_orders,
                             "business_hours": target.business_hours,
                             "min_order_minor": target.min_order_minor,
                             "delivery_fee_minor": target.delivery_fee_minor,
                             "service_fee_minor": target.service_fee_minor,
                             "unpaid_order_timeout_minutes": target.unpaid_order_timeout_minutes,
                             "unpaid_order_reminder_minutes": target.unpaid_order_reminder_minutes,
                             "shortage_customer_response_timeout_minutes": target.shortage_customer_response_timeout_minutes,
                             "shortage_refund_review_minor": target.shortage_refund_review_minor}
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="store_settings",
                    entity_id=str(target.id), action="updated", store_id=target.id,
                    details=details))
    await db.commit()
    await db.refresh(target)
    return _store_payload(target)


@router.patch("/{section}")
async def update_settings_section(section: str, req: SettingsUpdate, store: str | None = None,
                                  manager_info: dict = Depends(get_current_manager),
                                  db: AsyncSession = Depends(get_db)):
    if section not in {"business", "payment", "notice", "region"}:
        raise HTTPException(status_code=404, detail="Settings section not found")
    # Keep one validation and audit path; section pages submit the full current
    # snapshot so legacy clients and new pages cannot diverge.
    return await update_settings(req, store, manager_info, db)


@router.post("/test-notification")
async def test_notification(req: TestNotificationRequest, store: str | None = None,
                            manager_info: dict = Depends(get_current_manager),
                            db: AsyncSession = Depends(get_db)):
    target = await _resolve_target_store(db, manager_info, store)
    if target is None:
        raise HTTPException(status_code=404, detail="Store not found")
    if not target.telegram_staff_group_id:
        return {"ok": False, "reason": "Telegram staff group is not configured"}
    bot = _bot()
    if bot is None:
        return {"ok": False, "reason": "Telegram bot is not configured"}
    try:
        await bot.send_message(chat_id=target.telegram_staff_group_id, text=req.message)
    except Exception as exc:  # noqa: BLE001 - return a safe provider reason
        return {"ok": False, "reason": f"Telegram send failed: {exc}"}
    finally:
        await bot.session.close()
    db.add(AuditLog(actor_staff_id=manager_info["staff_id"], entity_type="store_settings",
                    entity_id=str(target.id), action="test_notification", store_id=target.id,
                    details={"group_configured": True, "language": target.staff_group_language}))
    await db.commit()
    return {"ok": True, "group_id": target.telegram_staff_group_id}
