from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
import os
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database import get_db
from models import Customer, Staff
from auth_utils import validate_telegram_init_data, verify_password, create_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])

class TelegramLoginRequest(BaseModel):
    initData: str

class AdminLoginRequest(BaseModel):
    login_name: str
    password: str

@router.post("/telegram")
async def telegram_login(req: TelegramLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
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
    
    token = create_access_token({"sub": str(customer.id), "type": "customer"})
    
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax"
    )
    
    return {"message": "Login successful", "customer_id": customer.id}


@router.post("/admin/login")
async def admin_login(req: AdminLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Staff).filter(Staff.login_name == req.login_name))
    staff = result.scalars().first()
    
    if not staff or not verify_password(req.password, staff.password_hash) or not staff.active:
        raise HTTPException(status_code=401, detail="Invalid credentials or account disabled")
    
    token = create_access_token({"sub": str(staff.id), "type": "staff", "role": staff.role})
    
    response.set_cookie(
        key="admin_session_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax"
    )
    
    return {"message": "Login successful", "staff_id": staff.id, "role": staff.role}
