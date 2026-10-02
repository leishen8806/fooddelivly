"""钱包 / 充值 API。

客户侧（Cookie: session_token）：
    GET    /api/v1/wallet                        余额 + 可选充值档位
    GET    /api/v1/wallet/ledger                 交易明细
    GET    /api/v1/wallet/recharges              我的充值单
    POST   /api/v1/wallet/recharges              创建充值单（需 Idempotency-Key）
    GET    /api/v1/wallet/recharges/{id}         充值单详情
    POST   /api/v1/wallet/recharges/{id}/cancel  取消未付款的充值单

员工侧（Cookie: admin_session_token）：
    GET    /api/v1/admin/recharges               充值单列表 / 待审核
    GET    /api/v1/admin/recharges/{id}/proof    凭证原图（代理 Telegram）
    POST   /api/v1/admin/recharges/{id}/approve  审核通过并入账
    POST   /api/v1/admin/recharges/{id}/reject   驳回（必填原因）
    POST   /api/v1/admin/recharges/{id}/received 审核前暂存实收金额
    GET    /api/v1/admin/wallets/{customer_id}   查某客户钱包
    POST   /api/v1/admin/wallets/{customer_id}/adjust  手动调账（仅 MANAGER）

所有金额字段单位都是「最小货币单位」（USD cents），与 orders.total_minor 一致。
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import wallet_service as wallet
from store_context import can_access_store, default_store, visible_store_id
from database import get_db
from dependencies import get_current_customer, get_current_staff
from models import AuditLog, Customer, StoreSettings, Staff
from sqlalchemy.future import select

router = APIRouter(prefix="/api/v1", tags=["Wallet"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["Admin Wallet"])

PENDING_STATUSES = ("awaiting_proof", "under_review")

#: 单次资金操作的金额上界（最小货币单位）。数据库是 BIGINT，但业务上不需要更大；
#: 卡在 API 层可以避免「误输入一个天文数字」或 BIGINT 溢出变成 500。
MAX_AMOUNT_MINOR = 10_000_000  # $100,000.00

#: 默认充值档位（最小货币单位）。运营以后可以从 wallet.recharge_rules 读，
#: 这里先给一组固定档位，前端/Bot 都用它渲染按钮。
DEFAULT_PRESETS = (500, 1000, 2000, 5000)


def _http(exc: wallet.WalletError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.message)


async def _currency(db: AsyncSession) -> str:
    result = await db.execute(select(StoreSettings).limit(1))
    settings = result.scalars().first()
    return (settings.currency if settings and settings.currency else wallet.DEFAULT_CURRENCY)


def _order_json(row) -> dict:
    return {
        "id": row["id"],
        "order_no": row["order_no"],
        "currency": row["currency"],
        "amount_minor": row["amount"],
        "bonus_amount_minor": row["bonus_amount"],
        "status": row["status"],
        "received_amount_minor": row["received_amount"],
        "pending_received_amount_minor": row.get("pending_received_amount"),
        "proof_count": row["proof_count"],
        "pay_reference": row["pay_reference"],
        "expires_at": row["expires_at"],
        "submitted_at": row["submitted_at"],
        "reviewed_at": row["reviewed_at"],
        "reject_reason": row["reject_reason"],
        "created_at": row["created_at"],
    }


# ===========================================================================
# 客户侧
# ===========================================================================

@router.get("/wallet")
async def get_wallet(
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    currency = await _currency(db)
    summary = await wallet.get_summary(db, customer_id, currency)
    limits = {
        "min_recharge_minor": await wallet.cfg_int(db, "min_recharge_amount", 100),
        "max_recharge_minor": await wallet.cfg_int(db, "max_recharge_amount", 50000),
        "max_open_orders": await wallet.cfg_int(db, "max_open_orders", 3),
    }
    return {
        "currency": summary["currency"],
        # 余额 = 本金 + 赠送 + 冻结；可用 = 本金 + 赠送（能消费的部分）
        "balance_minor": summary["balance_minor"],
        "available_minor": summary["available_minor"],
        "frozen_minor": summary["frozen_minor"],
        "principal_minor": summary["principal_minor"],
        "bonus_minor": summary["bonus_minor"],
        "total_minor": summary["total_minor"],
        "bonus_expire_at": summary["bonus_expire_at"],
        "presets_minor": list(DEFAULT_PRESETS),
        **limits,
    }


@router.get("/wallet/ledger")
async def get_wallet_ledger(
    limit: int = 30,
    offset: int = 0,
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
        raise HTTPException(status_code=422,
                            detail="limit must be 1..100 and offset must be 0..100000")
    rows = await wallet.list_ledger(db, customer_id, limit=limit, offset=offset)
    return {
        "entries": [
            {
                "id": r["id"],
                "currency": r["currency"],
                "bucket": r["bucket"],
                "direction": r["direction"],
                "amount_minor": r["amount"],
                "balance_after_minor": r["balance_after"],
                "entry_type": r["entry_type"],
                "biz_type": r["biz_type"],
                "biz_id": r["biz_id"],
                "remark": r["remark"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@router.get("/wallet/recharges")
async def list_my_recharges(
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    rows = await wallet.list_recharges(db, customer_id)
    result = []
    for row in rows:
        item = _order_json(row)
        item["approval_count"] = row.get("approval_count", 0)
        item["required_approvals"] = row.get("required_approvals", 1)
        # 未完成的单要能在列表里直接拿到「去机器人发截图」的深链，
        # 否则用户建完单离开页面后就找不回来了。
        if row["status"] in PENDING_STATUSES:
            item["bot_deeplink"] = _recharge_deeplink(row)
        result.append(item)
    return {"recharges": result}


class RechargeCreate(BaseModel):
    amount_minor: int = Field(gt=0, le=MAX_AMOUNT_MINOR, strict=True,
                              description="充值金额，最小货币单位（整数，不接受字符串/布尔）")


@router.post("/wallet/recharges")
async def create_recharge(
    payload: RechargeCreate,
    customer_id: int = Depends(get_current_customer),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    db: AsyncSession = Depends(get_db),
):
    """创建充值单。

    幂等：`idempotency_key` 由调用方给（前端用随机 UUID，机器人在重复点击时用
    「同一条消息」的身份），同名重复请求返回同一张单，不会重复建单。
    金额上下限、未完成单数量、单日累计都在数据库里校验。
    """
    currency = await _currency(db)
    # 收款门店：客户绑定的门店（未绑定则落主店）。结算与门店隔离都要用。
    customer_row = (await db.execute(select(Customer).filter(Customer.id == customer_id))).scalars().first()
    store_id = customer_row.store_id if customer_row else None
    if store_id is None:
        default = await default_store(db)
        store_id = default.id if default else None
    try:
        row = await wallet.start_recharge(
            db,
            customer_id=customer_id,
            amount_minor=payload.amount_minor,
            idem=f"recharge:{customer_id}:{idempotency_key}",
            currency=currency,
            store_id=store_id,
        )
    except wallet.WalletError as exc:
        raise _http(exc)
    await db.commit()

    from routers.orders import payment_handoff  # 复用同一套收款信息渲染

    result = await db.execute(select(StoreSettings).limit(1))
    settings = result.scalars().first()
    handoff = payment_handoff(row["order_no"], settings)
    return {
        **_order_json(row),
        "payment_link": handoff.get("payment_link"),
        "payment_qr_url": handoff.get("payment_qr_url"),
        "bot_deeplink": _recharge_deeplink(row),
        "instruction": "请按订单号转账，然后把截图发给机器人；员工确认后到账。",
    }


def _recharge_deeplink(row) -> str | None:
    bot_username = (os.getenv("BOT_USERNAME") or "").lstrip("@")
    if not bot_username:
        return None
    return f"https://t.me/{bot_username}?start=rc_{row['order_no']}"


@router.get("/wallet/recharges/{order_id}")
async def get_my_recharge(
    order_id: int,
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    row = await wallet.get_recharge_for_customer(db, order_id, customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recharge not found")
    proofs = await wallet.list_proofs(db, order_id)
    return {
        **_order_json(row),
        "bot_deeplink": _recharge_deeplink(row),
        "proofs": [
            {"id": p["id"], "created_at": p["created_at"]} for p in proofs
        ],
    }


@router.post("/wallet/recharges/{order_id}/cancel")
async def cancel_my_recharge(
    order_id: int,
    customer_id: int = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    """取消尚未提交凭证的充值单（幂等）。已提交凭证的只能由员工驳回。"""
    try:
        row = await wallet.cancel_recharge(db, order_id=order_id, customer_id=customer_id)
    except wallet.WalletError as exc:
        raise _http(exc)
    await db.commit()
    return _order_json(row)


# ===========================================================================
# 员工侧
# ===========================================================================

async def _ensure_recharge_in_scope(db: AsyncSession, order_id: int, staff_info: dict) -> dict:
    """门店隔离：跨店审核一律按「不存在」处理。

    返回订单行，调用方可以直接用。
    """
    row = await wallet.get_recharge(db, order_id)
    if not row or not can_access_store(staff_info, row.get("store_id")):
        raise HTTPException(status_code=404, detail="Recharge not found")
    return row


@admin_router.get("/recharges")
async def admin_list_recharges(
    status: Optional[str] = None,
    limit: int = 50,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    if status and status not in {"awaiting_proof", "under_review", "credited",
                                 "rejected", "expired", "cancelled"}:
        raise HTTPException(status_code=422, detail="Unknown status filter")
    limit = max(1, min(limit, 200))
    # 门店隔离：门店员工只看本店充值单（总部账号不过滤）
    rows = await wallet.list_recharges_admin(db, status, limit=limit,
                                             store_id=visible_store_id(staff_info))
    return [
        {
            **_order_json(r),
            "customer_id": r["customer_id"],
            "telegram_user_id": r["telegram_user_id"],
            "display_name": r["display_name"],
            "username": r["username"],
            # 客户**当前**账户状态，审核时用来判断这个客户
            # balance = 本金 + 赠送 + 冻结；available = 本金 + 赠送
            "balance_minor": r["balance_minor"],
            "available_minor": r["available_minor"],
            "frozen_minor": r["frozen_minor"],
            "approval_count": r["approval_count"],
            "required_approvals": r["required_approvals"],
            "principal_minor": r["principal_minor"],
            "bonus_minor": r["bonus_minor"],
        }
        for r in rows
    ]


@admin_router.get("/recharges/{order_id}/proof")
async def admin_recharge_proof(
    order_id: int,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """把客户上传的转账截图代理回来（Telegram 的 file_id 不能直接给浏览器）。"""
    await _ensure_recharge_in_scope(db, order_id, staff_info)
    proofs = await wallet.list_proofs(db, order_id)
    if not proofs:
        raise HTTPException(status_code=404, detail="Recharge proof not found")
    body, content_type = await fetch_telegram_photo(proofs[-1]["tg_file_id"])
    return Response(content=body, media_type=content_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


async def fetch_telegram_photo(file_id: str) -> tuple[bytes, str]:
    """下载 Telegram 文件并判定真实图片类型（Telegram 常把 jpeg 标成 octet-stream）。"""
    import httpx

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Payment image service is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        file_result = await client.get(
            f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}
        )
        if file_result.status_code != 200 or not file_result.json().get("ok"):
            raise HTTPException(status_code=502, detail="Telegram could not provide this image")
        path = file_result.json().get("result", {}).get("file_path")
        if not path:
            raise HTTPException(status_code=404, detail="Image is unavailable")
        image = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        if image.status_code != 200 or len(image.content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=502, detail="Image could not be loaded")
    body = image.content
    if body.startswith(b"\xff\xd8\xff"):
        return body, "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return body, "image/png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return body, "image/gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return body, "image/webp"
    raise HTTPException(status_code=415, detail="Uploaded file is not an image")


class RechargeApprove(BaseModel):
    received_minor: Optional[int] = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR, strict=True,
                                          description="实收金额；缺省时按暂存值 / 订单金额")
    remark: Optional[str] = None


class RechargeReject(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReceivedAmount(BaseModel):
    amount_minor: Optional[int] = Field(default=None, gt=0, le=MAX_AMOUNT_MINOR, strict=True,
                                        description="None = 清除暂存，回到订单金额")


@admin_router.post("/recharges/{order_id}/approve")
async def admin_approve_recharge(
    order_id: int,
    payload: RechargeApprove,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    staff_id = staff_info["staff_id"]
    await _ensure_recharge_in_scope(db, order_id, staff_info)
    try:
        row = await wallet.approve_recharge(
            db, order_id=order_id, staff_id=staff_id,
            received_minor=payload.received_minor, remark=payload.remark,
        )
    except wallet.WalletError as exc:
        raise _http(exc)
    done, required = await wallet.approval_progress(db, order_id)
    db.add(AuditLog(
        actor_staff_id=staff_id, entity_type="wallet_recharge", entity_id=str(order_id),
        action="recharge_approved" if done >= required else "recharge_approval_recorded",
        details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                 "order_no": row["order_no"], "amount_minor": row["amount"],
                 "received_minor": row["received_amount"],
                 "bonus_minor": row["bonus_amount"], "customer_id": row["customer_id"]},
    ))
    await db.commit()
    if done >= required:
        await _notify_customer_recharge(db, row, approved=True)
    return {**_order_json(row), "approval_count": done, "required_approvals": required}


@admin_router.post("/recharges/{order_id}/reject")
async def admin_reject_recharge(
    order_id: int,
    payload: RechargeReject,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    staff_id = staff_info["staff_id"]
    await _ensure_recharge_in_scope(db, order_id, staff_info)
    try:
        row = await wallet.reject_recharge(
            db, order_id=order_id, staff_id=staff_id, reason=payload.reason.strip()
        )
    except wallet.WalletError as exc:
        raise _http(exc)
    db.add(AuditLog(
        actor_staff_id=staff_id, entity_type="wallet_recharge", entity_id=str(order_id),
        action="recharge_rejected",
        details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                 "order_no": row["order_no"], "reason": payload.reason.strip()},
    ))
    await db.commit()
    await _notify_customer_recharge(db, row, approved=False)
    return _order_json(row)


@admin_router.post("/recharges/{order_id}/received")
async def admin_set_received(
    order_id: int,
    payload: ReceivedAmount,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """审核前暂存「实收金额」。入库持久化，多实例/重启都不会丢。"""
    await _ensure_recharge_in_scope(db, order_id, staff_info)
    try:
        row = await wallet.set_received_amount(
            db, order_id=order_id, staff_id=staff_info["staff_id"],
            amount_minor=payload.amount_minor,
        )
    except wallet.WalletError as exc:
        raise _http(exc)
    await db.commit()
    return _order_json(row)


@admin_router.get("/wallets/{customer_id}")
async def admin_get_wallet(
    customer_id: int,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Customer).filter(Customer.id == customer_id))
    customer = result.scalars().first()
    if customer is None or not can_access_store(staff_info, customer.store_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    currency = await _currency(db)
    summary = await wallet.get_summary(db, customer_id, currency)
    entries = await wallet.list_ledger(db, customer_id, limit=50)
    return {
        "customer_id": customer_id,
        "telegram_user_id": customer.telegram_user_id,
        "display_name": customer.display_name,
        "username": customer.username,
        "currency": summary["currency"],
        "balance_minor": summary["balance_minor"],
        "available_minor": summary["available_minor"],
        "frozen_minor": summary["frozen_minor"],
        "principal_minor": summary["principal_minor"],
        "bonus_minor": summary["bonus_minor"],
        "total_minor": summary["total_minor"],
        "bonus_expire_at": summary["bonus_expire_at"],
        "ledger": [
            {"id": e["id"], "bucket": e["bucket"], "direction": e["direction"],
             "amount_minor": e["amount"], "balance_after_minor": e["balance_after"],
             "entry_type": e["entry_type"], "biz_type": e["biz_type"], "biz_id": e["biz_id"],
             "remark": e["remark"], "created_at": e["created_at"]}
            for e in entries
        ],
    }


class AdjustRequest(BaseModel):
    bucket: str = Field(pattern="^(principal|bonus)$")
    delta_minor: int = Field(ge=-MAX_AMOUNT_MINOR, le=MAX_AMOUNT_MINOR, strict=True,
                             description="正数加钱，负数扣钱")
    reason: str = Field(min_length=1, max_length=500)


@admin_router.post("/wallets/{customer_id}/adjust")
async def admin_adjust_wallet(
    customer_id: int,
    payload: AdjustRequest,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """手动调账。仅 MANAGER（数据库函数的 can_adjust 会再校验一次）。"""
    if payload.delta_minor == 0:
        raise HTTPException(status_code=422, detail="delta_minor must not be zero")
    # 门店隔离：不能给别家店的客户调账（真钱操作）
    customer = (await db.execute(select(Customer).filter(Customer.id == customer_id))).scalars().first()
    if customer is None or not can_access_store(staff_info, customer.store_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    staff_id = staff_info["staff_id"]
    currency = await _currency(db)
    import uuid as _uuid

    try:
        entry = await wallet.adjust_balance(
            db, customer_id=customer_id, bucket=payload.bucket,
            delta_minor=payload.delta_minor, staff_id=staff_id,
            reason=payload.reason.strip(), idem=f"adjust:admin:{_uuid.uuid4()}",
            currency=currency,
        )
    except wallet.WalletError as exc:
        raise _http(exc)
    db.add(AuditLog(
        actor_staff_id=staff_id, entity_type="wallet", entity_id=str(customer_id),
        action="wallet_adjusted",
        details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                 "bucket": payload.bucket, "delta_minor": payload.delta_minor,
                 "reason": payload.reason.strip(), "currency": currency},
    ))
    await db.commit()
    return {
        "customer_id": customer_id,
        "bucket": entry["bucket"],
        "delta_minor": payload.delta_minor,
        "balance_after_minor": entry["balance_after"],
    }


async def _notify_customer_recharge(db: AsyncSession, row, approved: bool) -> None:
    """审核结果通知客户（失败不影响审核本身）。"""
    from telegram_service import notify_recharge_review

    result = await db.execute(select(Customer).filter(Customer.id == row["customer_id"]))
    customer = result.scalars().first()
    if customer is None:
        return
    try:
        await notify_recharge_review(customer, row, "APPROVED" if approved else "REJECTED")
    except Exception:  # noqa: BLE001 - 通知失败不能影响资金结果
        pass


@admin_router.post("/wallet/maintenance")
async def admin_wallet_maintenance(
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """运维任务：过期充值单清理 + 账实对账。仅 MANAGER。

    * 过期清理**按调用者的门店**：门店经理只能作废本店的充值单（总部账号 = 全部）；
    * 账实对账是**全局**的（钱包余额跨店通用，按门店过滤账本会算出假差异），
      而且会暴露别家客户的余额，所以只对总部账号开放。
    """
    if staff_info.get("role") != "MANAGER":
        raise HTTPException(status_code=403, detail="Manager access required")
    scope = visible_store_id(staff_info)
    try:
        expired = await wallet.expire_stale_recharges(db, store_id=scope)
        drift = await wallet.reconcile(db) if scope is None else []
    except wallet.WalletError as exc:
        raise _http(exc)
    await db.commit()
    return {
        "expired_recharges": expired,
        # 门店账号看不到全局对账（返回 null 而不是空数组，避免误读成「对账通过」）
        "reconcile_drift": [dict(row) for row in drift] if scope is None else None,
        "healthy": (len(drift) == 0) if scope is None else None,
        "scope": "ALL" if scope is None else f"STORE:{scope}",
    }
