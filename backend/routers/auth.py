from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
import os
import json
import logging
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database import get_db
from models import Customer, Staff
from auth_utils import validate_telegram_init_data, verify_password, create_access_token
from dependencies import get_current_customer, get_current_staff

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
logger = logging.getLogger("teacafe.telegram_auth")

class TelegramLoginRequest(BaseModel):
    initData: str

class AdminLoginRequest(BaseModel):
    login_name: str
    password: str

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
    
    token = create_access_token({"sub": str(customer.id), "type": "customer"})
    
    response.set_cookie(key="session_token", value=token, **_cookie_options())
    response.headers["Cache-Control"] = "no-store"
    
    return {"message": "Login successful", "customer_id": customer.id, "language": _supported_language(customer.language_code)}


@router.post("/admin/login")
async def admin_login(req: AdminLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Staff).filter(Staff.login_name == req.login_name))
    staff = result.scalars().first()
    
    if not staff or not verify_password(req.password, staff.password_hash) or not staff.active:
        raise HTTPException(status_code=401, detail="Invalid credentials or account disabled")
    
    token = create_access_token({"sub": str(staff.id), "type": "staff", "role": staff.role})
    
    response.set_cookie(key="admin_session_token", value=token, **_cookie_options())
    response.headers["Cache-Control"] = "no-store"
    
    return {"message": "Login successful", "staff_id": staff.id, "role": staff.role}

@router.get("/admin/me")
async def admin_me(staff_info: dict = Depends(get_current_staff)):
    return staff_info

@router.post("/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie("admin_session_token", path="/", secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", httponly=True, samesite="lax")
    return {"message": "Logged out"}

@router.post("/logout")
async def customer_logout(response: Response):
    response.delete_cookie("session_token", path="/", secure=os.getenv("COOKIE_SECURE", "true").lower() == "true", httponly=True, samesite="lax")
    return {"message": "Logged out"}

@router.get("/me")
async def customer_me(customer_id: int = Depends(get_current_customer)):
    return {"customer_id": customer_id}
