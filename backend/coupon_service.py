"""Coupon claiming and checkout validation.

All limits and discount calculations live here so the Mini App cannot bypass
the campaign rules by changing the request body.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CouponCampaign, CouponClaimLink, CustomerCoupon, Order


class CouponError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_token(value: str) -> str:
    token = (value or "").strip()
    for prefix in ("claim_private_", "coupon_public_", "claim_", "coupon_"):
        if token.startswith(prefix):
            return token[len(prefix):]
    return token


def token_hash(token: str) -> str:
    return hashlib.sha256(normalize_token(token).encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(12)


def bot_username() -> str:
    return (os.getenv("BOT_USERNAME") or "").strip().lstrip("@")


def claim_url(token: str, mode: str) -> str | None:
    username = bot_username()
    if not username:
        return None
    prefix = "claim_private_" if mode == "PRIVATE" else "coupon_public_"
    return f"https://t.me/{username}?startapp={prefix}{token}"


def share_url(url: str, text: str) -> str:
    from urllib.parse import quote

    return f"https://t.me/share/url?url={quote(url, safe='')}&text={quote(text, safe='')}"


def discount_for(coupon: CouponCampaign, subtotal_minor: int) -> int:
    if subtotal_minor < int(coupon.min_spend_minor or 0):
        raise CouponError("Coupon minimum spend is not met", 409)
    if coupon.coupon_type == "DISCOUNT":
        percent = int(coupon.discount_percent or 0)
        discount = (subtotal_minor * percent + 50) // 100
        if coupon.discount_cap_minor is not None:
            discount = min(discount, int(coupon.discount_cap_minor))
    elif coupon.coupon_type == "FIXED":
        discount = int(coupon.fixed_discount_minor or 0)
    else:
        raise CouponError("Unsupported coupon type", 422)
    return max(0, min(discount, subtotal_minor))


def ensure_campaign_usable(
    coupon: CouponCampaign,
    now: datetime | None = None,
    *,
    check_claim_limit: bool = True,
) -> None:
    current = now or now_utc()
    if coupon.status != "ACTIVE":
        raise CouponError("Coupon is not available", 409)
    if current < coupon.starts_at or current > coupon.ends_at:
        raise CouponError("Coupon is expired or not started", 409)
    if check_claim_limit and coupon.distribution_mode == "PUBLIC" and coupon.total_claim_limit is not None and coupon.total_claimed_quantity >= coupon.total_claim_limit:
        raise CouponError("Coupon has been fully claimed", 409)


async def _claim_count(db: AsyncSession, coupon_id: int, customer_id: int) -> int:
    return int(await db.scalar(
        select(func.count(CustomerCoupon.id)).where(
            CustomerCoupon.coupon_id == coupon_id,
            CustomerCoupon.customer_id == customer_id,
            CustomerCoupon.status != "REVOKED",
        )
    ) or 0)


def coupon_payload(coupon: CouponCampaign, customer_coupon: CustomerCoupon, *, discount_minor: int | None = None) -> dict:
    display_status = customer_coupon.status
    if display_status == "AVAILABLE" and now_utc() > coupon.ends_at:
        display_status = "EXPIRED"
    return {
        "id": customer_coupon.id,
        "coupon_id": coupon.id,
        "name": coupon.name,
        "description": coupon.description,
        "currency": coupon.currency,
        "coupon_type": coupon.coupon_type,
        "min_spend_minor": coupon.min_spend_minor,
        "discount_percent": coupon.discount_percent,
        "discount_cap_minor": coupon.discount_cap_minor,
        "fixed_discount_minor": coupon.fixed_discount_minor,
        "starts_at": coupon.starts_at,
        "ends_at": coupon.ends_at,
        "status": display_status,
        "discount_minor": discount_minor,
    }


async def claim(
    db: AsyncSession,
    *,
    token: str,
    customer_id: int,
    claim_request_key: str | None = None,
) -> dict:
    if claim_request_key:
        existing = (await db.execute(select(CustomerCoupon).where(
            CustomerCoupon.customer_id == customer_id,
            CustomerCoupon.claim_request_key == claim_request_key,
        ))).scalars().first()
        if existing:
            coupon = await db.get(CouponCampaign, existing.coupon_id)
            if coupon:
                return coupon_payload(coupon, existing)

    link = (await db.execute(select(CouponClaimLink).where(
        CouponClaimLink.token_hash == token_hash(token)
    ).with_for_update())).scalars().first()
    if link is None:
        raise CouponError("Coupon link is invalid", 404)
    coupon = (await db.execute(select(CouponCampaign).where(
        CouponCampaign.id == link.coupon_id
    ).with_for_update())).scalars().first()
    if coupon is None:
        raise CouponError("Coupon is not available", 404)
    ensure_campaign_usable(coupon)
    if link.mode == "PRIVATE" and link.status != "ACTIVE":
        existing_private = (await db.execute(select(CustomerCoupon).where(
            CustomerCoupon.claim_link_id == link.id,
            CustomerCoupon.customer_id == customer_id,
        ))).scalars().first()
        if existing_private:
            payload = coupon_payload(coupon, existing_private)
            payload["already_claimed"] = True
            return payload
        raise CouponError("This private coupon link has already been claimed", 409)

    # Public links can be claimed repeatedly by the same user up to the
    # campaign limit. Private links are single-use links; each newly generated
    # link is an independent grant for the recipient.
    if link.mode == "PUBLIC":
        user_count = await _claim_count(db, coupon.id, customer_id)
        if user_count >= coupon.per_user_claim_limit:
            existing_public = (await db.execute(select(CustomerCoupon).where(
                CustomerCoupon.coupon_id == coupon.id,
                CustomerCoupon.customer_id == customer_id,
                CustomerCoupon.status == "AVAILABLE",
            ).order_by(CustomerCoupon.id.desc()))).scalars().first()
            if existing_public:
                payload = coupon_payload(coupon, existing_public)
                payload["already_claimed"] = True
                return payload
            raise CouponError("You have reached the claim limit for this coupon", 409)

    customer_coupon = CustomerCoupon(
        coupon_id=coupon.id,
        customer_id=customer_id,
        claim_link_id=link.id,
        claim_request_key=claim_request_key,
        status="AVAILABLE",
    )
    db.add(customer_coupon)
    coupon.total_claimed_quantity = int(coupon.total_claimed_quantity or 0) + 1
    if link.mode == "PRIVATE":
        link.status = "CLAIMED"
        link.claimed_by = customer_id
        link.claimed_at = now_utc()
    await db.flush()
    return coupon_payload(coupon, customer_coupon)


async def list_customer_coupons(db: AsyncSession, customer_id: int) -> list[dict]:
    result = await db.execute(
        select(CustomerCoupon, CouponCampaign)
        .join(CouponCampaign, CouponCampaign.id == CustomerCoupon.coupon_id)
        .where(CustomerCoupon.customer_id == customer_id)
        .order_by(CustomerCoupon.claimed_at.desc(), CustomerCoupon.id.desc())
    )
    return [coupon_payload(coupon, customer_coupon) for customer_coupon, coupon in result.all()]


async def reserve_for_order(
    db: AsyncSession,
    *,
    customer_id: int,
    customer_coupon_id: int | None,
    subtotal_minor: int,
    currency: str,
) -> tuple[CustomerCoupon | None, CouponCampaign | None, int]:
    if customer_coupon_id is None:
        return None, None, 0
    result = await db.execute(select(CustomerCoupon, CouponCampaign).join(
        CouponCampaign, CouponCampaign.id == CustomerCoupon.coupon_id
    ).where(
        CustomerCoupon.id == customer_coupon_id,
        CustomerCoupon.customer_id == customer_id,
    ).with_for_update())
    row = result.first()
    if row is None:
        raise CouponError("Coupon not found", 404)
    customer_coupon, coupon = row
    if customer_coupon.status != "AVAILABLE":
        raise CouponError("Coupon has already been used", 409)
    if coupon.currency != currency:
        raise CouponError("Coupon currency does not match the order", 409)
    # The campaign's total claim cap only limits new claims. Users who already
    # hold a coupon can still redeem it while the campaign remains active.
    ensure_campaign_usable(coupon, check_claim_limit=False)
    discount = discount_for(coupon, subtotal_minor)
    return customer_coupon, coupon, discount


async def release_for_cancel(db: AsyncSession, order: Order) -> bool:
    """Return the coupon reserved by a cancelled order.

    A coupon that is already past its campaign end remains EXPIRED and cannot
    be used again.  The row is locked so a concurrent cancellation/retry cannot
    release another order's coupon.
    """
    if not order.coupon_id:
        return False
    result = await db.execute(
        select(CustomerCoupon, CouponCampaign)
        .join(CouponCampaign, CouponCampaign.id == CustomerCoupon.coupon_id)
        .where(CustomerCoupon.id == order.coupon_id)
        .with_for_update()
    )
    row = result.first()
    if row is None:
        return False
    customer_coupon, campaign = row
    if customer_coupon.used_order_id not in (None, order.id):
        return False
    if customer_coupon.status != "USED":
        return False
    customer_coupon.status = "EXPIRED" if now_utc() > campaign.ends_at else "AVAILABLE"
    customer_coupon.used_order_id = None
    customer_coupon.used_at = None
    return True
