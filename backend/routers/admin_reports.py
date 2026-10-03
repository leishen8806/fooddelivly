"""每日经营报表 API。

    GET  /api/v1/admin/reports/daily?date=YYYY-MM-DD   预览（不发送）
    POST /api/v1/admin/reports/daily/send              发送到员工群（MANAGER 或 cron）

发送是**幂等**的：同一天同一会话只会成功发一次，重复调用返回 `skipped`。
外部 cron 只需要带 `X-Cron-Secret`（对应环境变量 `CRON_SECRET`），
不用去伪造一个管理员会话；没配 `CRON_SECRET` 时这条路径直接关闭。
"""
from __future__ import annotations

import hmac
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import daily_report
from database import get_db
from store_context import staff_store
from dependencies import get_current_staff
from models import Store, StoreSettings
from sqlalchemy.future import select

router = APIRouter(prefix="/api/v1/admin/reports", tags=["Admin Reports"])


async def require_report_sender(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """允许两种身份：带正确 X-Cron-Secret 的定时任务，或 MANAGER 会话。"""
    secret = os.getenv("CRON_SECRET")
    provided = request.headers.get("x-cron-secret") or ""
    if secret and provided and hmac.compare_digest(provided, secret):
        return {"staff_id": None, "role": "CRON", "login_name": "cron"}
    staff = await get_current_staff(request, db)
    if staff.get("role") != "MANAGER":
        raise HTTPException(status_code=403, detail="Manager access required")
    return staff


def _parse_date(value: Optional[str]) -> date:
    if not value:
        return datetime.now().date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")


@router.get("/daily")
async def preview_daily_report(
    date_str: Optional[str] = Query(None, alias="date"),
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """预览某天的报表（默认今天）。任何在职员工都能看，不会发送。"""
    report_date = _parse_date(date_str)
    # 门店隔离：报表按调用者门店统计（门店经理只应看到本店数据）
    store = await staff_store(db, staff_info)
    chat_id = str(store.telegram_staff_group_id) if store and store.telegram_staff_group_id else None
    report = await daily_report.build_daily_report(
        db, report_date, store_id=store.id if store else None)
    return {
        "report": report,
        "store": ({"id": store.id, "code": store.code, "name": store.name} if store else None),
        "text": daily_report.format_daily_report(
            report, (store.staff_group_language if store else "en") or "en"),
        "delivery": await daily_report.daily_report_status(
            db, report_date, chat_id, store.code if store else None),
        "target_chat_id": chat_id,
        "scheduled_hour": daily_report.report_hour(),
    }


class SendDailyReport(BaseModel):
    date: Optional[str] = None      # 缺省 = 昨天
    force: bool = False             # true = 忽略「已发送」，重发一次


@router.post("/daily/send")
async def send_daily_report_now(
    payload: SendDailyReport,
    sender: dict = Depends(require_report_sender),
    db: AsyncSession = Depends(get_db),
):
    """把某天的报表发到员工群。默认发**昨天**，重复调用不会重复发。"""
    # 外部 cron 没有员工会话，按每家活动门店分别投递；不能再落到全局配置。
    if sender.get("role") == "CRON":
        stores = (await db.execute(
            select(Store).filter(Store.status == "ACTIVE").order_by(Store.id)
        )).scalars().all()
        deliveries = []
        for store in stores:
            if not store.telegram_staff_group_id:
                continue
            if payload.date:
                report_date = _parse_date(payload.date)
            else:
                local_date = datetime.now(
                    await daily_report.store_timezone(db, store.id)
                ).date()
                report_date = local_date - timedelta(days=1)
            result = await daily_report.send_daily_report(
                db, report_date, chat_id=str(store.telegram_staff_group_id),
                language=store.staff_group_language or "en", force=payload.force,
                store_id=store.id, store_code=store.code,
            )
            deliveries.append({"store": store.code, "date": report_date.isoformat(), **result})
        if not deliveries:
            raise HTTPException(status_code=409, detail="No active store staff group is configured")
        return {"reports": deliveries}

    if payload.date:
        report_date = _parse_date(payload.date)
    else:
        tz = await daily_report.store_timezone(db)
        report_date = datetime.now(tz).date() - timedelta(days=1)

    sender_info = sender if isinstance(sender, dict) else {}
    store = await staff_store(db, sender_info) if sender_info.get("store_id") or sender_info.get("staff_id") else None
    chat_id = str(store.telegram_staff_group_id) if store and store.telegram_staff_group_id else None
    if not chat_id:
        raise HTTPException(status_code=409, detail="Staff group is not configured")

    result = await daily_report.send_daily_report(
        db, report_date, chat_id=chat_id,
        language=(store.staff_group_language if store else "en") or "en",
        force=payload.force,
        store_id=store.id if store else None,
        store_code=store.code if store else None,
    )
    return {"date": report_date.isoformat(), **result}
