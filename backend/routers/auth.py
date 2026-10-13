from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from typing import Literal
from datetime import datetime, timezone
import secrets
import os
import json
import logging
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from database import get_db
from models import Customer, Staff, Store
from auth_utils import validate_telegram_init_data, verify_password, create_access_token
from dependencies import get_current_customer, get_current_staff
from customer_activity import record_customer_activity_best_effort
from store_context import default_store, get_store

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
logger = logging.getLogger("teacafe.telegram_auth")

class TelegramLoginRequest(BaseModel):
    initData: str

class AdminLoginRequest(BaseModel):
    login_name: str
    password: str

class PasswordChangeRequest(BaseModel):
    password: str

class LanguagePreferenceRequest(BaseModel):
    language: Literal["zh-CN", "en", "km"]

def _cookie_options():
    return {
        "httponly": True,
        "secure": os.getenv("COOKIE_SECURE", "true").lower() == "true",
        "samesite": "lax",
        "path": "/",
        "max_age": 7 * 24 * 60 * 60,
    }

def _supported_language(code: str | None) -> str:
    if code and code.lower().startswith("zh"):
        return "zh-CN"
    if code and code.lower().startswith("km"):
        return "km"
    return "en"

@router.post("/telegram")
async def telegram_login(req: TelegramLoginRequest, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    if not req.initData:
        platform = request.headers.get("x-tc-tg-platform", "unknown")
        version = request.headers.get("x-tc-tg-version", "unknown")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", platform):
            platform = "unknown"
        if not re.fullmatch(r"[0-9.]{1,16}", version):
            version = "unknown"
        logger.warning(
            "telegram_initdata_empty webapp=%s platform=%s version=%s bridge=%s hash_param=%s query_param=%s unsafe_user=%s",
            request.headers.get("x-tc-tg-webapp") == "1",
            platform,
            version,
            request.headers.get("x-tc-tg-bridge") == "1",
            request.headers.get("x-tc-tg-hash") == "1",
            request.headers.get("x-tc-tg-query") == "1",
            request.headers.get("x-tc-tg-unsafe-user") == "1",
        )
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")
    
    parsed_data = validate_telegram_init_data(req.initData, bot_token)
    if not parsed_data:
        raise HTTPException(status_code=401, detail="Invalid or expired initData")
    
    user_str = parsed_data.get("user")
    if not user_str:
        raise HTTPException(status_code=401, detail="User data missing")
    
    user_data = json.loads(user_str)
    tg_id = str(user_data.get("id"))
    
    # Find or create customer
    result = await db.execute(select(Customer).filter(Customer.telegram_user_id == tg_id))
    customer = result.scalars().first()
    
    if not customer:
        customer = Customer(
            telegram_user_id=tg_id,
            display_name=user_data.get("first_name", "") + " " + user_data.get("last_name", ""),
            username=user_data.get("username"),
            language_code=user_data.get("language_code")
        )
        db.add(customer)
        await db.commit()
        await db.refresh(customer)
    else:
        customer.display_name = " ".join(filter(None, [user_data.get("first_name"), user_data.get("last_name")])) or customer.display_name
        customer.username = user_data.get("username")
        customer.language_code = user_data.get("language_code")
        await db.commit()
    
    # Activity writes are deliberately isolated from the login transaction:
    # an unavailable activity table must never turn a valid Telegram login into
    # a 500 response.
    try:
        activity_store = await get_store(db, customer.store_id) if customer.store_id else await default_store(db)
    except Exception:  # noqa: BLE001 - activity is best effort by contract
        await db.rollback()
        logger.exception("customer_daily_activity_store_resolution_failed customer_id=%s", customer.id)
    else:
        await record_customer_activity_best_effort(
            db,
            customer_id=customer.id,
            store_id=activity_store.id if activity_store else customer.store_id,
            store_timezone=activity_store.timezone if activity_store else None,
        )

    token = create_access_token({"sub": str(customer.id), "type": "customer"})
    
    response.set_cookie(key="session_token", value=token, **_cookie_options())
    response.headers["Cache-Control"] = "no-store"
    
    return {
        "message": "Login successful",
        "customer_id": customer.id,
        "language": customer.preferred_language or _supported_language(customer.language_code),
        "preferred_language": customer.preferred_language,
    }


@router.post("/admin/login")
async def admin_login(req: AdminLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Staff).filter(Staff.login_name == req.login_name))
    staff = result.scalars().first()

    # verify_password 在哈希格式不可用（历史数据/导入错误/字段被写坏）时会抛异常。
    # 不能让它冒成 500：
    #   1) 「账号存在 -> 500、账号不存在 -> 401」本身就是个账号探测 oracle；
    #   2) 500 会引导攻击者去猜哪些登录名是真的。
    # 一律按鉴权失败处理，只记日志。
    password_ok = False
    if staff is not None:
        try:
            password_ok = verify_password(req.password, staff.password_hash)
        except Exception:  # noqa: BLE001
            logger.warning("staff password hash unusable staff_id=%s", staff.id)
            password_ok = False

    if not staff or not password_ok or not staff.active:
        raise HTTPException(status_code=401, detail="Invalid credentials or account disabled")
    
    staff.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    token = create_access_token({"sub": str(staff.id), "type": "staff", "role": staff.role})
    
    response.set_cookie(key="admin_session_token", value=token, **_cookie_options())
    response.headers["Cache-Control"] = "no-store"
    
    return {"message": "Login successful", "staff_id": staff.id, "role": staff.role,
            "must_change_password": bool(staff.must_change_password)}

@router.get("/admin/me")
async def admin_me(staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    active_store_count = await db.scalar(select(func.count(Store.id)).filter(Store.status == "ACTIVE")) or 0
    catalog_write_allowed = (
        staff_info.get("role") == "MANAGER"
        and (staff_info.get("store_id") is None or active_store_count <= 1)
    )
    return {**staff_info, "catalog_write_allowed": catalog_write_allowed}

@router.post("/admin/change-password")
async def admin_change_password(req: PasswordChangeRequest,
                                staff_info: dict = Depends(get_current_staff),
                                db: AsyncSession = Depends(get_db)):
    if len(req.password) < 12 or len(req.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be 12 to 72 bytes")
    from auth_utils import get_password_hash
    result = await db.execute(select(Staff).filter(Staff.id == staff_info["staff_id"]).with_for_update())
    staff = result.scalars().first()
    if staff is None:
        raise HTTPException(status_code=401, detail="Staff account not found")
    staff.password_hash = get_password_hash(req.password)
    staff.must_change_password = False
    await db.commit()
    return {"message": "Password updated"}

@router.post("/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie("admin_session_token", path="/", secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", httponly=True, samesite="lax")
    return {"message": "Logged out"}

@router.post("/logout")
async def customer_logout(response: Response):
    response.delete_cookie("session_token", path="/", secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", httponly=True, samesite="lax")
    return {"message": "Logged out"}

@router.get("/me")
async def customer_me(customer_id: int = Depends(get_current_customer), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalars().first()
    if customer is None:
        raise HTTPException(status_code=401, detail="Customer session is no longer valid")
    return {
        "customer_id": customer.id,
        "display_name": customer.display_name,
        "username": customer.username,
        "language": customer.preferred_language or _supported_language(customer.language_code),
        "preferred_language": customer.preferred_language,
    }

@router.patch("/me/language")
async def set_customer_language(
    req: LanguagePreferenceRequest,
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalars().first()
    if customer is None:
        raise HTTPException(status_code=401, detail="Customer session is no longer valid")
    customer.preferred_language = req.language
    await db.commit()
    return {"language": customer.preferred_language, "preferred_language": customer.preferred_language}
