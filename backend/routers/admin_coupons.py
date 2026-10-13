from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coupon_service import claim_url, new_token, share_url, token_hash
from database import get_db
from dependencies import get_catalog_manager, get_current_manager
from models import AuditLog, CouponCampaign, CouponClaimLink
from store_context import staff_store, store_scope_clause
from telegram_service import tr


router = APIRouter(prefix="/api/v1/admin/coupons", tags=["Admin Coupons"])


def _audit(db: AsyncSession, manager: dict, coupon: CouponCampaign, action: str, **details) -> None:
    db.add(AuditLog(
        store_id=coupon.store_id, actor_staff_id=manager["staff_id"], entity_type="coupon",
        entity_id=str(coupon.id), action=action,
        details={"source": "admin_console", "operator_name": manager.get("login_name"),
                 "operator_telegram_id": manager.get("telegram_user_id"),
                 "name": coupon.name, **details}))


class CouponCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    coupon_type: Literal["DISCOUNT", "FIXED"]
    distribution_mode: Literal["PRIVATE", "PUBLIC"]
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    min_spend_minor: int = Field(default=0, ge=0, le=100_000_000)
    discount_percent: int | None = Field(default=None, ge=1, le=100)
    discount_cap_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    fixed_discount_minor: int | None = Field(default=None, ge=1, le=100_000_000)
    starts_at: datetime
    ends_at: datetime
    per_user_claim_limit: int = Field(default=1, ge=1, le=1000)
    total_claim_limit: int | None = Field(default=None, ge=1, le=10_000_000)

    @model_validator(mode="after")
    def validate_rules(self):
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Coupon times must include a timezone")
        if self.ends_at <= self.starts_at:
            raise ValueError("Coupon end time must be after start time")
        if self.coupon_type == "DISCOUNT":
            if self.discount_percent is None:
                raise ValueError("Discount percentage is required")
            if self.fixed_discount_minor is not None:
                raise ValueError("Fixed discount is only valid for fixed coupons")
        else:
            if not self.fixed_discount_minor:
                raise ValueError("Fixed discount amount is required")
            if self.discount_percent is not None or self.discount_cap_minor is not None:
                raise ValueError("Percentage fields are only valid for discount coupons")
        return self


class CouponStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "PAUSED"]


def _currency_digits(currency: str) -> int:
    return 0 if str(currency or "").upper() in {"KHR", "JPY", "VND"} else 2


def _format_minor(minor: int, currency: str) -> str:
    """Format integer minor units without float conversion."""
    digits = _currency_digits(currency)
    value = max(0, int(minor or 0))
    if digits == 0:
        return f"{value:,} {currency}"
    scale = 10 ** digits
    whole, fraction = divmod(value, scale)
    return f"{whole:,}.{fraction:0{digits}d} {currency}"


def _share_text(coupon: CouponCampaign, language: str) -> str:
    if coupon.coupon_type == "DISCOUNT":
        return tr("coupon.shareTextDiscount", language, name=coupon.name,
                   percent=int(coupon.discount_percent or 0))
    return tr("coupon.shareTextFixed", language, name=coupon.name,
              amount=_format_minor(int(coupon.fixed_discount_minor or 0), coupon.currency))


def _share_urls(url: str | None, coupon: CouponCampaign) -> dict[str, str]:
    if not url:
        return {}
    return {language: share_url(url, _share_text(coupon, language))
            for language in ("zh-CN", "en", "km")}


async def _links(db: AsyncSession, coupon_id: int):
    result = await db.execute(select(CouponClaimLink).where(
        CouponClaimLink.coupon_id == coupon_id
    ).order_by(CouponClaimLink.created_at.desc(), CouponClaimLink.id.desc()))
    return result.scalars().all()


async def _links_for_coupons(db: AsyncSession, coupon_ids: list[int]) -> dict[int, list[CouponClaimLink]]:
    if not coupon_ids:
        return {}
    result = await db.execute(select(CouponClaimLink).where(
        CouponClaimLink.coupon_id.in_(coupon_ids)
    ).order_by(CouponClaimLink.created_at.desc(), CouponClaimLink.id.desc()))
    grouped: dict[int, list[CouponClaimLink]] = defaultdict(list)
    for link in result.scalars().all():
        grouped[link.coupon_id].append(link)
    return grouped


def _payload(coupon: CouponCampaign, links: list[CouponClaimLink]) -> dict:
    public = next((link for link in links if link.mode == "PUBLIC"), None)
    public_url = claim_url(public.token_value, "PUBLIC") if public else None
    share_urls = _share_urls(public_url, coupon)
    return {
        "id": coupon.id,
        "store_id": coupon.store_id,
        "name": coupon.name,
        "description": coupon.description,
        "currency": coupon.currency,
        "coupon_type": coupon.coupon_type,
        "distribution_mode": coupon.distribution_mode,
        "min_spend_minor": coupon.min_spend_minor,
        "discount_percent": coupon.discount_percent,
        "discount_cap_minor": coupon.discount_cap_minor,
        "fixed_discount_minor": coupon.fixed_discount_minor,
        "starts_at": coupon.starts_at,
        "ends_at": coupon.ends_at,
        "per_user_claim_limit": coupon.per_user_claim_limit,
        "total_claim_limit": coupon.total_claim_limit,
        "total_claimed_quantity": coupon.total_claimed_quantity,
        "status": coupon.status,
        "published_at": coupon.published_at,
        "public_claim_url": public_url,
        # Keep the English URL for backward compatibility. New clients choose
        # the URL matching the current UI language from this map.
        "public_share_url": share_urls.get("en"),
        "public_share_urls": share_urls,
        "private_link_count": sum(1 for link in links if link.mode == "PRIVATE"),
        "private_claimed_count": sum(1 for link in links if link.mode == "PRIVATE" and link.status == "CLAIMED"),
    }


async def _get_coupon(db: AsyncSession, manager: dict, coupon_id: int) -> CouponCampaign:
    query = select(CouponCampaign).where(CouponCampaign.id == coupon_id)
    clause = store_scope_clause(CouponCampaign, manager)
    if clause is not None:
        query = query.where(clause)
    coupon = (await db.execute(query)).scalars().first()
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return coupon


async def _ensure_public_link(db: AsyncSession, coupon: CouponCampaign, manager: dict) -> CouponClaimLink:
    existing = (await db.execute(select(CouponClaimLink).where(
        CouponClaimLink.coupon_id == coupon.id,
        CouponClaimLink.mode == "PUBLIC",
    ))).scalars().first()
    if existing:
        return existing
    raw = new_token()
    link = CouponClaimLink(
        coupon_id=coupon.id,
        token_value=raw,
        token_hash=token_hash(raw),
        mode="PUBLIC",
        status="ACTIVE",
        created_by=manager["staff_id"],
    )
    db.add(link)
    await db.flush()
    return link


@router.get("")
async def list_coupons(manager: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    query = select(CouponCampaign).order_by(CouponCampaign.created_at.desc(), CouponCampaign.id.desc())
    clause = store_scope_clause(CouponCampaign, manager)
    if clause is not None:
        query = query.where(clause)
    coupons = (await db.execute(query)).scalars().all()
    links_by_coupon = await _links_for_coupons(db, [coupon.id for coupon in coupons])
    return [_payload(coupon, links_by_coupon.get(coupon.id, [])) for coupon in coupons]


@router.post("", status_code=201)
async def create_coupon(req: CouponCreate, manager: dict = Depends(get_catalog_manager), db: AsyncSession = Depends(get_db)):
    store = await staff_store(db, manager)
    if store is None:
        raise HTTPException(status_code=409, detail="Store not configured")
    currency = req.currency or store.currency
    coupon = CouponCampaign(
        store_id=store.id,
        created_by=manager["staff_id"],
        name=req.name.strip(),
        description=req.description.strip() if req.description else None,
        currency=currency,
        coupon_type=req.coupon_type,
        distribution_mode=req.distribution_mode,
        min_spend_minor=req.min_spend_minor,
        discount_percent=req.discount_percent,
        discount_cap_minor=req.discount_cap_minor,
        fixed_discount_minor=req.fixed_discount_minor,
        starts_at=req.starts_at,
        ends_at=req.ends_at,
        per_user_claim_limit=req.per_user_claim_limit,
        total_claim_limit=req.total_claim_limit,
        status="DRAFT",
    )
    db.add(coupon)
    await db.flush()
    _audit(db, manager, coupon, "created", coupon_type=coupon.coupon_type,
           distribution_mode=coupon.distribution_mode, currency=coupon.currency)
    await db.commit()
    await db.refresh(coupon)
    return _payload(coupon, [])


@router.patch("/{coupon_id}")
async def update_coupon(coupon_id: int, req: CouponCreate,
                        manager: dict = Depends(get_catalog_manager),
                        db: AsyncSession = Depends(get_db)):
    coupon = await _get_coupon(db, manager, coupon_id)
    if coupon.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Published coupons cannot be edited")
    store = await staff_store(db, manager)
    if store is None:
        raise HTTPException(status_code=409, detail="Store not configured")
    coupon.name = req.name.strip()
    coupon.description = req.description.strip() if req.description else None
    coupon.currency = req.currency or store.currency
    coupon.coupon_type = req.coupon_type
    coupon.distribution_mode = req.distribution_mode
    coupon.min_spend_minor = req.min_spend_minor
    coupon.discount_percent = req.discount_percent
    coupon.discount_cap_minor = req.discount_cap_minor
    coupon.fixed_discount_minor = req.fixed_discount_minor
    coupon.starts_at = req.starts_at
    coupon.ends_at = req.ends_at
    coupon.per_user_claim_limit = req.per_user_claim_limit
    coupon.total_claim_limit = req.total_claim_limit
    _audit(db, manager, coupon, "updated", coupon_type=coupon.coupon_type,
           distribution_mode=coupon.distribution_mode, currency=coupon.currency)
    await db.commit()
    return _payload(coupon, await _links(db, coupon.id))


@router.post("/{coupon_id}/publish")
async def publish_coupon(coupon_id: int, manager: dict = Depends(get_catalog_manager), db: AsyncSession = Depends(get_db)):
    coupon = await _get_coupon(db, manager, coupon_id)
    if coupon.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft coupons can be published")
    if coupon.ends_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="Coupon has already expired")
    coupon.status = "ACTIVE"
    coupon.published_at = datetime.now(timezone.utc)
    if coupon.distribution_mode == "PUBLIC":
        await _ensure_public_link(db, coupon, manager)
    _audit(db, manager, coupon, "published")
    await db.commit()
    return _payload(coupon, await _links(db, coupon.id))


@router.post("/{coupon_id}/status")
async def update_coupon_status(coupon_id: int, req: CouponStatusUpdate, manager: dict = Depends(get_catalog_manager), db: AsyncSession = Depends(get_db)):
    coupon = await _get_coupon(db, manager, coupon_id)
    if coupon.status == "DRAFT":
        raise HTTPException(status_code=409, detail="Publish the coupon before changing its online status")
    old_status = coupon.status
    coupon.status = req.status
    _audit(db, manager, coupon, "status_changed", from_status=old_status, to_status=req.status)
    await db.commit()
    return _payload(coupon, await _links(db, coupon.id))


@router.post("/{coupon_id}/private-link")
async def create_private_link(coupon_id: int, manager: dict = Depends(get_catalog_manager), db: AsyncSession = Depends(get_db)):
    coupon = await _get_coupon(db, manager, coupon_id)
    if coupon.distribution_mode != "PRIVATE":
        raise HTTPException(status_code=409, detail="This coupon uses a public link")
    if coupon.status == "DRAFT":
        raise HTTPException(status_code=409, detail="Publish the coupon first")
    raw = new_token()
    link = CouponClaimLink(
        coupon_id=coupon.id,
        token_value=raw,
        token_hash=token_hash(raw),
        mode="PRIVATE",
        status="ACTIVE",
        created_by=manager["staff_id"],
    )
    db.add(link)
    await db.flush()
    _audit(db, manager, coupon, "private_link_created", link_id=link.id)
    await db.commit()
    private_claim_url = claim_url(raw, "PRIVATE")
    share_urls = _share_urls(private_claim_url, coupon)
    return {
        "id": link.id,
        "status": link.status,
        "claim_url": private_claim_url,
        "share_url": share_urls.get("en"),
        "share_urls": share_urls,
    }


@router.get("/{coupon_id}/links")
async def list_coupon_links(coupon_id: int, manager: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    coupon = await _get_coupon(db, manager, coupon_id)
    links = await _links(db, coupon.id)
    return [{
        "id": link.id,
        "mode": link.mode,
        "status": link.status,
        "claim_url": claim_url(link.token_value, link.mode),
        "created_at": link.created_at,
        "claimed_at": link.claimed_at,
    } for link in links]
