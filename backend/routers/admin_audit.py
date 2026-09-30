from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from database import get_db
from dependencies import get_current_manager
from models import AuditLog, Staff

router = APIRouter(prefix="/api/v1/admin/audit-logs", tags=["Admin Audit Logs"])


@router.get("")
async def list_audit_logs(
    _manager: dict = Depends(get_current_manager),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total = await db.scalar(select(func.count(AuditLog.id))) or 0
    result = await db.execute(
        select(AuditLog, Staff.login_name, Staff.telegram_user_id)
        .outerjoin(Staff, Staff.id == AuditLog.actor_staff_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = []
    for entry, login_name, staff_telegram_id in result.all():
        details = entry.details or {}
        items.append({
            "id": entry.id,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "action": entry.action,
            "operator_name": details.get("operator_name") or login_name or "—",
            "operator_telegram_id": details.get("operator_telegram_id") or staff_telegram_id,
            "source": details.get("source", "admin_console"),
            "details": details,
            "created_at": entry.created_at,
        })
    return {"items": items, "total": total, "limit": limit, "offset": offset}
