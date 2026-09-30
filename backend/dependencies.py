from fastapi import Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from jose import jwt
import os
from typing import Dict, Any
from database import get_db
from models import Staff

def get_token_from_cookie(request: Request, cookie_name: str) -> str:
    token = request.cookies.get(cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token

def verify_token(token: str, expected_type: str = None) -> Dict[str, Any]:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET is not configured")
        
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if expected_type and payload.get("type") != expected_type:
            raise HTTPException(status_code=403, detail=f"Invalid token type. Expected {expected_type}")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_customer(request: Request) -> int:
    token = get_token_from_cookie(request, "session_token")
    payload = verify_token(token, "customer")
    return int(payload.get("sub"))

async def get_current_staff(request: Request, db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    token = get_token_from_cookie(request, "admin_session_token")
    payload = verify_token(token, "staff")
    staff_id = int(payload.get("sub"))
    
    result = await db.execute(select(Staff).filter(Staff.id == staff_id))
    staff = result.scalars().first()
    
    if not staff or not staff.active:
        raise HTTPException(status_code=401, detail="Staff account disabled or not found")
        
    return {
        "staff_id": staff.id,
        "role": staff.role
    }

async def get_current_manager(staff_info: dict = Depends(get_current_staff)) -> Dict[str, Any]:
    if staff_info.get("role") != "MANAGER":
        raise HTTPException(status_code=403, detail="Manager access required")
    return staff_info
