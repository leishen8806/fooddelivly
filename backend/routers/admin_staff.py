import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from auth_utils import get_password_hash
from database import get_db
from dependencies import get_current_manager
from store_context import can_access_store, store_scope_clause
from models import AuditLog, Staff

router = APIRouter(prefix="/api/v1/admin/staff", tags=["Admin Staff"])


class StaffCreate(BaseModel):
    login_name: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=72)
    role: str = "STAFF"
    telegram_user_id: str | None = None
    #: 归属门店；不传则继承创建者的门店（总部建人时显式指定）
    store_id: int | None = None

    @field_validator("login_name")
    @classmethod
    def validate_login(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("Use letters, numbers, dot, underscore, or hyphen")
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in {"STAFF", "MANAGER"}:
            raise ValueError("Role must be STAFF or MANAGER")
        return value

    @field_validator("telegram_user_id")
    @classmethod
    def validate_telegram_id(cls, value: str | None) -> str | None:
        if value is not None and not value.isdigit():
            raise ValueError("Telegram user ID must contain digits only")
        return value


class StaffUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    telegram_user_id: str | None = None
    password: str | None = Field(None, min_length=12, max_length=72)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str | None) -> str | None:
        if value is not None and value not in {"STAFF", "MANAGER"}:
            raise ValueError("Role must be STAFF or MANAGER")
        return value

    @field_validator("telegram_user_id")
    @classmethod
    def validate_telegram_id(cls, value: str | None) -> str | None:
        if value is not None and not value.isdigit():
            raise ValueError("Telegram user ID must contain digits only")
        return value


def _staff_row(staff: Staff) -> dict:
    return {"id": staff.id, "login_name": staff.login_name, "role": staff.role,
            "active": staff.active, "telegram_user_id": staff.telegram_user_id,
            "store_id": staff.store_id}          # None = 总部账号（可跨店）


@router.get("")
async def list_staff(manager: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    # 门店隔离：门店经理只看本店员工；总部账号看全部
    query = select(Staff).order_by(Staff.login_name)
    clause = store_scope_clause(Staff, manager)
    if clause is not None:
        query = query.filter(clause)
    result = await db.execute(query)
    return [_staff_row(row) for row in result.scalars().all()]


@router.post("")
async def create_staff(req: StaffCreate, manager: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    # 归属门店：门店经理只能建**本店**员工，且不能把 store_id 设成空
    # （空 = 总部账号 = 能看所有门店，是静默提权）。
    # 总部 MANAGE 才能显式把员工放到任意门店或总部。
    if manager.get("store_id") is not None:
        target_store = manager["store_id"]
        if req.store_id is not None and req.store_id != target_store:
            raise HTTPException(status_code=403, detail="You can only create staff in your own store")
    else:
        target_store = req.store_id      # 总部：None = 总部账号
    staff = Staff(login_name=req.login_name, password_hash=get_password_hash(req.password),
                  role=req.role, telegram_user_id=req.telegram_user_id, active=True,
                  store_id=target_store)
    db.add(staff)
    try:
        await db.flush()
        db.add(AuditLog(actor_staff_id=manager["staff_id"], entity_type="staff", entity_id=str(staff.id),
                        action="created", details={"login_name": staff.login_name, "role": staff.role,
                                                   "telegram_user_id": staff.telegram_user_id, "active": True,
                                                   "store_id": staff.store_id}))
        await db.commit()
        await db.refresh(staff)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Login name or Telegram user ID is already in use")
    return _staff_row(staff)


@router.patch("/{staff_id}")
async def update_staff(staff_id: int, req: StaffUpdate, manager: dict = Depends(get_current_manager), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Staff).filter(Staff.id == staff_id).with_for_update())
    staff = result.scalars().first()
    if not staff or not can_access_store(manager, staff.store_id):
        raise HTTPException(status_code=404, detail="Staff account not found")
    changes = req.model_dump(exclude_unset=True)
    # 门店经理不能把员工调去别店，也不能把自己的账号升成总部（提权）
    if manager.get("store_id") is not None:
        if changes.get("store_id") not in (None, manager["store_id"]):
            raise HTTPException(status_code=403, detail="You can only manage staff in your own store")
        changes.pop("store_id", None)
    if staff_id == manager["staff_id"] and (changes.get("active") is False or changes.get("role", "MANAGER") != "MANAGER"):
        raise HTTPException(status_code=409, detail="You cannot remove your own manager access")
    safe_changes = {key: value for key, value in changes.items() if key != "password"}
    if "password" in changes and changes["password"] is not None:
        staff.password_hash = get_password_hash(changes["password"])
    for key, value in safe_changes.items():
        setattr(staff, key, value)
    db.add(AuditLog(actor_staff_id=manager["staff_id"], entity_type="staff", entity_id=str(staff.id),
                    action="updated", details=safe_changes))
    try:
        await db.commit()
        await db.refresh(staff)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Telegram user ID is already assigned")
    return _staff_row(staff)
