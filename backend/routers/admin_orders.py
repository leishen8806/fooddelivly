from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from datetime import datetime
from zoneinfo import ZoneInfo
from pydantic import BaseModel
from database import get_db
from models import AuditLog, Order, PaymentProof, PaymentReview, OrderEvent, Customer, OrderIssue, OrderRefund, Product, ProductOptionGroup
from dependencies import get_current_manager, get_current_staff
from store_context import can_access_store, store_for_order, store_scope_clause, load_overrides, effective_product
from sale_window import is_on_sale, windows_from_json
from order_lifecycle import customer_can_cancel, cancel_order_record, notify_cancelled
from routers.uploads import IMAGE_TYPES, MAX_UPLOAD_BYTES, UPLOAD_DIR
import os
import httpx
from pathlib import Path
from uuid import uuid4

router = APIRouter(prefix="/api/v1/admin/orders", tags=["Admin Orders"])
refund_router = APIRouter(prefix="/api/v1/admin/order-refunds", tags=["Admin Order Refunds"])
PAYMENT_PROOF_DIR = UPLOAD_DIR / "payment-proofs"


def _image_content_type(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None

@router.get("")
async def list_orders(
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    # 门店隔离：门店员工只能看到本店订单（总部账号不过滤）
    query = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
    clause = store_scope_clause(Order, staff_info)
    if clause is not None:
        query = query.filter(clause)
    result = await db.execute(query)
    orders = result.scalars().all()
    order_ids = [order.id for order in orders]
    issues_by_order: dict[int, list] = {order_id: [] for order_id in order_ids}
    refunds_by_order: dict[int, list] = {order_id: [] for order_id in order_ids}
    if order_ids:
        issue_rows = (await db.execute(select(OrderIssue).filter(OrderIssue.order_id.in_(order_ids)))).scalars().all()
        refund_rows = (await db.execute(select(OrderRefund).filter(OrderRefund.order_id.in_(order_ids)))).scalars().all()
        for issue in issue_rows:
            issues_by_order.setdefault(issue.order_id, []).append({"id": issue.id, "status": issue.status,
                "resolution": issue.resolution, "order_item_id": issue.order_item_id, "note": issue.note})
        for refund in refund_rows:
            refunds_by_order.setdefault(refund.order_id, []).append({"id": refund.id, "amount_minor": refund.amount_minor,
                "method": refund.method, "status": refund.status, "requires_approval": bool(refund.requires_approval), "reference": refund.reference})
    return [{
        "id": order.id, "public_code": order.public_code, "room_number": order.room_number,
        "order_status": order.order_status, "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "currency": order.currency, "total_minor": order.total_minor,
        "subtotal_minor": order.subtotal_minor if order.subtotal_minor is not None else order.total_minor,
        "coupon_name_snapshot": order.coupon_name_snapshot,
        "coupon_discount_minor": order.coupon_discount_minor or 0,
        "delivery_fee_minor": order.delivery_fee_minor or 0,
        "service_fee_minor": order.service_fee_minor or 0,
        "wallet_paid_minor": order.wallet_paid_minor or 0,
        "external_due_minor": order.external_due_minor if order.external_due_minor is not None else order.total_minor,
        "created_at": order.created_at,
        "issues": issues_by_order.get(order.id, []),
        "refunds": refunds_by_order.get(order.id, []),
        "items": [{"id": item.id, "name": item.product_name_snapshot, "quantity": item.quantity,
                   "options": item.options_json or {},
                   "line_total_minor": item.line_total_minor} for item in order.items],
    } for order in orders]

@router.get("/{order_id}/payment-proof")
async def get_payment_proof(order_id: int, staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    # 门店隔离：先确认这张订单属于调用者的门店，再取截图。
    # 只查 PaymentProof 是不够的——转账截图里有金额、账号等敏感信息。
    order = (await db.execute(select(Order).filter(Order.id == order_id))).scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Payment proof not found")
    result = await db.execute(select(PaymentProof).filter(PaymentProof.order_id == order_id).order_by(PaymentProof.submitted_at.desc()))
    proof = result.scalars().first()
    if not proof:
        raise HTTPException(status_code=404, detail="Payment proof not found")
    if proof.telegram_file_id.startswith("local:"):
        filename = Path(proof.telegram_file_id.removeprefix("local:")).name
        path = PAYMENT_PROOF_DIR / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Payment image is unavailable")
        body = path.read_bytes()
        content_type = _image_content_type(body)
        if not content_type:
            raise HTTPException(status_code=415, detail="Uploaded file is not an image")
        return Response(content=body, media_type=content_type, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Payment image service is not configured")
    async with httpx.AsyncClient(timeout=20) as client:
        file_result = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": proof.telegram_file_id})
        if file_result.status_code != 200 or not file_result.json().get("ok"):
            raise HTTPException(status_code=502, detail="Telegram could not provide this payment image")
        path = file_result.json().get("result", {}).get("file_path")
        if not path:
            raise HTTPException(status_code=404, detail="Payment image is unavailable")
        image = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        if image.status_code != 200 or len(image.content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=502, detail="Payment image could not be loaded")
        body = image.content
        # Telegram's file endpoint can label valid uploaded photos as
        # application/octet-stream. Determine the safe raster MIME from bytes.
        content_type = _image_content_type(body)
        if not content_type:
            raise HTTPException(status_code=415, detail="Uploaded file is not an image")
        return Response(content=body, media_type=content_type, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })


@router.post("/{order_id}/payment-proof", status_code=201)
async def upload_payment_proof(
    order_id: int,
    file: UploadFile = File(...),
    manager_info: dict = Depends(get_current_manager),
    db: AsyncSession = Depends(get_db),
):
    """Allow a manager to attach a locally uploaded payment screenshot to an order."""
    image_type = IMAGE_TYPES.get(file.content_type or "")
    if not image_type:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG, or WebP image")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 5 MB or smaller")
    if not image_type[1](content):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid image")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(manager_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status not in {"UNPAID", "REJECTED"}:
        raise HTTPException(status_code=409, detail="This order does not need a payment proof")
    pending_result = await db.execute(select(PaymentProof).filter(
        PaymentProof.order_id == order.id,
        PaymentProof.review_status == "PENDING",
    ).limit(1))
    if pending_result.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="This order already has a pending payment proof")

    PAYMENT_PROOF_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{image_type[0]}"
    path = PAYMENT_PROOF_DIR / filename
    path.write_bytes(content)
    old_payment_status = order.payment_status
    proof = PaymentProof(
        store_id=order.store_id,
        order_id=order.id,
        telegram_file_id=f"local:{filename}",
        submitted_by=None,
        review_status="PENDING",
    )
    db.add(proof)
    order.payment_status = "PROOF_SUBMITTED"
    staff_id = manager_info["staff_id"]
    db.add(OrderEvent(
        order_id=order.id, actor_type="STAFF", actor_id=staff_id,
        event="PAYMENT_PROOF_UPLOADED",
        from_state=f"{order.order_status}/{old_payment_status}",
        to_state=f"{order.order_status}/{order.payment_status}",
    ))
    db.add(AuditLog(store_id=order.store_id, 
        actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id),
        action="payment_proof_uploaded",
        details={
            "source": "admin_console", "operator_name": manager_info.get("login_name"),
            "operator_telegram_id": manager_info.get("telegram_user_id"),
            "public_code": order.public_code, "from_state": f"{order.order_status}/{old_payment_status}",
            "to_state": f"{order.order_status}/{order.payment_status}",
        },
    ))
    customer_result = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
    customer = customer_result.scalars().first()
    settings = await store_for_order(db, order)
    try:
        await db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        await db.rollback()
        raise
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        from telegram_service import update_order_message
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items, customer)
        except Exception:
            pass
    return {"payment_status": order.payment_status, "proof_source": "admin_upload"}

class PaymentReviewRequest(BaseModel):
    decision: str # APPROVED or REJECTED
    reason: str | None = None

class OrderStatusRequest(BaseModel):
    status: str
    reason: str | None = None


class ShortageRequest(BaseModel):
    order_item_id: int
    note: str = ""
    replacement_product_id: int | None = None


def _product_name(product: Product) -> str:
    if isinstance(product.name, dict):
        return str(product.name.get("en") or product.name.get("zh-CN") or product.name.get("km") or next(iter(product.name.values()), product.id))
    return str(product.name)

@router.post("/{order_id}/status")
async def change_order_status(order_id: int, req: OrderStatusRequest,
                             staff_info: dict = Depends(get_current_staff), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")
    staff_id = staff_info["staff_id"]
    wallet_paid = order.payment_method in {"WALLET", "MIXED"} and (order.wallet_paid_minor or 0) > 0
    wallet_only = order.payment_method == "WALLET"
    transitions = {
        "ACCEPTED": order.order_status == "NEW",
        "READY": order.order_status == "PREPARING" and order.payment_status == "PAID_CONFIRMED",
        "DELIVERED": order.order_status == "READY",
        "COMPLETED": order.order_status == "DELIVERED",
        # 含钱包抵扣的订单：只在「餐还没做出去」的阶段允许取消并退款
        # （NEW/ACCEPTED/PREPARING）。到了 READY/DELIVERED 说明已经出餐/送出，
        # 这时候把钱退回去应该走单独的、仅 MANAGER 的退款接口，而不是点一下「取消」。
        # 人工转账的已确认付款订单仍然禁止取消（退款走线下）。
        "CANCELLED": order.order_status not in {"CANCELLED", "COMPLETED"} and (
            order.payment_status != "PAID_CONFIRMED"
            or (wallet_only and order.order_status in {"NEW", "ACCEPTED", "PREPARING"})),
    }
    if not transitions.get(req.status, False):
        raise HTTPException(status_code=409, detail="Order status transition is not allowed")
    if req.status == "CANCELLED" and not (req.reason and req.reason.strip()):
        raise HTTPException(status_code=422, detail="A cancellation reason is required")
    if req.status != "CANCELLED":
        open_issue = (await db.execute(select(OrderIssue.id).filter(
            OrderIssue.order_id == order.id, OrderIssue.status == "OPEN"
        ).limit(1))).first()
        if open_issue:
            raise HTTPException(status_code=409, detail="This order has an unresolved shortage; wait for the customer")
    old_state = f"{order.order_status}/{order.payment_status}"
    reason = req.reason.strip()[:500] if req.reason else None
    if req.status == "CANCELLED":
        try:
            result = await cancel_order_record(db, order, actor_type="STAFF", actor_id=staff_id,
                                               reason=reason or "Order cancelled")
            customer = (await db.execute(select(Customer).filter(Customer.id == order.customer_id))).scalars().first()
            await db.commit()
        except Exception as exc:
            await db.rollback()
            from wallet_service import WalletError
            if isinstance(exc, WalletError):
                raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
            raise
        await notify_cancelled(db, order, "order.cancelledByStaff")
        return {"order_status": order.order_status, "payment_status": order.payment_status, **result}

    order.order_status = req.status

    if req.status == "ACCEPTED" and order.payment_status == "PAID_CONFIRMED":
        # 下单即已付清（钱包余额抵扣）：接单后直接进入制作。
        # 这与「员工确认收款后 PAID_CONFIRMED + PREPARING」的既有行为保持一致。
        order.order_status = "PREPARING"

    event_name = f"ORDER_{req.status}" + (f": {reason}" if reason else "")
    db.add(OrderEvent(order_id=order.id, actor_type="STAFF", actor_id=staff_id, event=event_name,
                      from_state=old_state, to_state=f"{order.order_status}/{order.payment_status}"))
    db.add(AuditLog(store_id=order.store_id, actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id), action="status_changed",
                    details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                             "operator_telegram_id": staff_info.get("telegram_user_id"), "public_code": order.public_code,
                             "room_number": order.room_number, "from_state": old_state,
                             "to_state": f"{order.order_status}/{order.payment_status}", "reason": reason}))
    customer_result = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
    customer = customer_result.scalars().first()
    settings = await store_for_order(db, order)
    await db.commit()
    if customer:
        from telegram_service import send_bot_message, tr
        try:
            await send_bot_message(customer.telegram_user_id,
                                   tr("bot.statusUpdated", order.customer_language,
                                      status=tr(f"order.status.{req.status.lower()}", order.customer_language)))
        except Exception:
            pass
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        from telegram_service import update_order_message
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items, customer)
        except Exception:
            pass
    return {"order_status": order.order_status, "payment_status": order.payment_status}


@router.post("/{order_id}/shortage", status_code=201)
async def report_shortage(
    order_id: int,
    req: ShortageRequest,
    staff_info: dict = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a customer-facing shortage choice before the order is ready.

    Accepted/paid orders use the selected policy A: the customer can only
    remove the unavailable item, then the difference is refunded/queued.
    """
    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(
        Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")
    paid_shortage = (
        order.order_status in {"ACCEPTED", "PREPARING"}
        and (order.payment_status in {"PAID_CONFIRMED", "PARTIALLY_REFUNDED"}
             or int(order.wallet_paid_minor or 0) >= int(order.total_minor or 0))
    )
    if not customer_can_cancel(order.order_status, order.payment_status) and not paid_shortage:
        raise HTTPException(status_code=409, detail="Shortage choices are not available in this order state")
    if paid_shortage and req.replacement_product_id is not None:
        raise HTTPException(status_code=409, detail="Paid shortage orders only support removing the unavailable item")
    staff_customer = (await db.execute(select(Customer).filter(
        Customer.id == order.customer_id,
        Customer.telegram_user_id == str(staff_info.get("telegram_user_id") or "")
    ))).scalars().first()
    if staff_customer:
        raise HTTPException(status_code=403, detail="Staff cannot process a shortage refund for their own customer account")
    item = next((row for row in order.items if row.id == req.order_item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Order item not found")
    issue_result = await db.execute(select(OrderIssue).filter(
        OrderIssue.order_id == order.id, OrderIssue.order_item_id == item.id,
        OrderIssue.status == "OPEN"))
    open_issue = issue_result.scalars().first()
    if open_issue:
        raise HTTPException(status_code=409, detail="This item already has an open shortage issue")

    replacement_name = None
    replacement_price = None
    if req.replacement_product_id is not None:
        replacement = (await db.execute(select(Product).options(
            selectinload(Product.sale_windows),
            selectinload(Product.option_groups).selectinload(ProductOptionGroup.options),
        ).filter(Product.id == req.replacement_product_id, Product.available.is_(True)))).scalars().first()
        settings = await store_for_order(db, order)
        overrides = await load_overrides(db, order.store_id, [replacement.id]) if replacement else {}
        effective = effective_product(replacement, overrides.get(replacement.id), settings) if replacement else None
        if (replacement is None or effective is None or not effective.available
                or replacement.currency != order.currency):
            raise HTTPException(status_code=409, detail="Replacement product is unavailable")
        now = datetime.now(ZoneInfo(settings.timezone if settings else "Asia/Phnom_Penh")).time()
        if not is_on_sale(windows_from_json(effective.sale_windows), now):
            raise HTTPException(status_code=409, detail="Replacement product is outside its sale window")
        if replacement.sweetness_enabled or any(g.active and g.required for g in replacement.option_groups):
            raise HTTPException(status_code=422, detail="This product requires options and cannot be a direct replacement")
        replacement_name = _product_name(replacement)
        replacement_price = int(effective.price_minor)
    issue = OrderIssue(
        store_id=order.store_id,
        order_id=order.id,
        order_item_id=item.id,
        issue_type="OUT_OF_STOCK",
        note=req.note.strip()[:500] or None,
        replacement_product_id=req.replacement_product_id,
        replacement_name_snapshot=replacement_name,
        replacement_unit_price_minor=replacement_price,
    )
    db.add(issue)
    db.add(OrderEvent(order_id=order.id, actor_type="STAFF", actor_id=staff_info["staff_id"],
                      event="OUT_OF_STOCK_REPORTED", from_state=f"{order.order_status}/{order.payment_status}",
                      to_state=f"{order.order_status}/{order.payment_status}"))
    db.add(AuditLog(store_id=order.store_id, actor_staff_id=staff_info["staff_id"],
                    entity_type="order", entity_id=str(order.id), action="shortage_reported",
                    details={"source": "admin_console", "public_code": order.public_code,
                             "order_item_id": item.id, "note": issue.note,
                             "replacement_product_id": req.replacement_product_id}))
    customer = (await db.execute(select(Customer).filter(Customer.id == order.customer_id))).scalars().first()
    await db.commit()
    if customer:
        from telegram_service import send_bot_message, tr
        try:
            await send_bot_message(customer.telegram_user_id, tr(
                "order.shortagePaidNotice" if paid_shortage else "order.shortageNotice",
                order.customer_language, order=order.public_code))
        except Exception:
            pass
    return {"id": issue.id, "status": issue.status, "order_id": order.id,
            "order_item_id": item.id, "replacement_name": replacement_name}


class RefundCompletionRequest(BaseModel):
    reference: str
    note: str | None = None


class RefundApprovalRequest(BaseModel):
    note: str | None = None


@router.get("/refunds")
async def list_order_refunds(
    status: str | None = None,
    manager_info: dict = Depends(get_current_manager),
    db: AsyncSession = Depends(get_db),
):
    query = select(OrderRefund).join(Order, OrderRefund.order_id == Order.id).order_by(OrderRefund.created_at.desc())
    if status:
        query = query.filter(OrderRefund.status == status.upper())
    result = await db.execute(query)
    rows = [row for row in result.scalars().all() if can_access_store(manager_info, row.store_id)]
    return [{"id": row.id, "order_id": row.order_id, "amount_minor": row.amount_minor,
             "method": row.method, "status": row.status, "requires_approval": bool(row.requires_approval), "reference": row.reference,
             "note": row.note, "created_at": row.created_at, "done_at": row.done_at} for row in rows]


@router.post("/refunds/{refund_id}/complete")
async def complete_order_refund(
    refund_id: int,
    req: RefundCompletionRequest,
    manager_info: dict = Depends(get_current_manager),
    db: AsyncSession = Depends(get_db),
):
    reference = (req.reference or "").strip()[:200]
    if not reference:
        raise HTTPException(status_code=422, detail="A refund reference is required")
    result = await db.execute(select(OrderRefund).filter(OrderRefund.id == refund_id).with_for_update())
    refund = result.scalars().first()
    if not refund or not can_access_store(manager_info, refund.store_id):
        raise HTTPException(status_code=404, detail="Refund not found")
    if refund.method != "ABA_MANUAL" or refund.status != "PENDING":
        raise HTTPException(status_code=409, detail="This refund is not awaiting manual completion")
    refund.status = "DONE"
    refund.reference = reference
    refund.note = (req.note or refund.note or "").strip()[:500] or None
    refund.done_at = datetime.now(ZoneInfo("UTC"))
    refund.done_by = manager_info["staff_id"]
    order = (await db.execute(select(Order).filter(Order.id == refund.order_id).with_for_update())).scalars().first()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    aba_done = int((await db.execute(select(func.coalesce(func.sum(OrderRefund.amount_minor), 0)).filter(
        OrderRefund.order_id == order.id, OrderRefund.method == "ABA_MANUAL", OrderRefund.status == "DONE"
    ))).scalar_one() or 0)
    external_paid = max(int(order.paid_confirmed_minor or 0) - int(order.wallet_paid_minor or 0), 0)
    wallet_refunded = 0
    if int(order.wallet_paid_minor or 0) > 0:
        import wallet_service as wallet_service_module
        payment = await wallet_service_module.get_payment(db, order.public_code)
        wallet_refunded = int(payment["refunded_amount"] or 0) if payment else 0
    if order.order_status == "CANCELLED" and wallet_refunded >= int(order.wallet_paid_minor or 0) and aba_done >= external_paid:
        order.payment_status = "REFUNDED"
    elif aba_done or wallet_refunded:
        order.payment_status = "PARTIALLY_REFUNDED"
    db.add(AuditLog(store_id=refund.store_id, actor_staff_id=manager_info["staff_id"],
                    entity_type="order_refund", entity_id=str(refund.id), action="completed",
                    details={"order_id": refund.order_id, "amount_minor": refund.amount_minor,
                             "reference": reference, "note": refund.note,
                             "payment_status": order.payment_status}))
    await db.commit()
    return {"id": refund.id, "status": refund.status, "reference": refund.reference,
            "payment_status": order.payment_status}


@router.post("/refunds/{refund_id}/approve")
async def approve_wallet_refund(
    refund_id: int,
    req: RefundApprovalRequest,
    manager_info: dict = Depends(get_current_manager),
    db: AsyncSession = Depends(get_db),
):
    """Approve and execute a shortage wallet refund above the configured threshold."""
    result = await db.execute(select(OrderRefund).filter(OrderRefund.id == refund_id).with_for_update())
    refund = result.scalars().first()
    if not refund or not can_access_store(manager_info, refund.store_id):
        raise HTTPException(status_code=404, detail="Refund not found")
    if refund.method != "WALLET" or refund.status != "PENDING" or not refund.requires_approval:
        raise HTTPException(status_code=409, detail="This refund is not awaiting manager approval")
    order = (await db.execute(select(Order).filter(Order.id == refund.order_id).with_for_update())).scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    import wallet_service as wallet_service_module
    existing = await wallet_service_module.get_payment(db, order.public_code)
    refunded_before = int(existing["refunded_amount"] or 0) if existing else 0
    paid_total = int(existing["amount"] or order.wallet_paid_minor or 0) if existing else int(order.wallet_paid_minor or 0)
    if int(refund.amount_minor) > max(paid_total - refunded_before, 0):
        raise HTTPException(status_code=409, detail="Wallet refund exceeds the remaining wallet payment")
    try:
        payment = await wallet_service_module.refund_payment(
            db, biz_id=order.public_code, amount_minor=int(refund.amount_minor),
            idem=f"refund:{order.public_code}:approval:{refund.id}:{refunded_before}:{refund.amount_minor}",
            operator_staff_id=manager_info["staff_id"], reason="Approved shortage refund",
        )
    except wallet_service_module.WalletError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.message) from exc
    refund.status = "DONE"
    refund.requires_approval = False
    refund.approved_at = datetime.now(ZoneInfo("UTC"))
    refund.approved_by = manager_info["staff_id"]
    refund.done_at = datetime.now(ZoneInfo("UTC"))
    refund.done_by = manager_info["staff_id"]
    refund.note = (req.note or refund.note or "").strip()[:500] or None
    refunded_now = int(payment["refunded_amount"] or (refunded_before + refund.amount_minor)) if payment else refunded_before + refund.amount_minor
    external_paid = max(int(order.paid_confirmed_minor or 0) - int(order.wallet_paid_minor or 0), 0)
    if order.order_status == "CANCELLED" and refunded_now >= paid_total and external_paid == 0:
        order.payment_status = "REFUNDED"
    else:
        order.payment_status = "PARTIALLY_REFUNDED"
    db.add(AuditLog(store_id=refund.store_id, actor_staff_id=manager_info["staff_id"],
                    entity_type="order_refund", entity_id=str(refund.id), action="approved",
                    details={"order_id": refund.order_id, "amount_minor": refund.amount_minor,
                             "note": refund.note, "payment_status": order.payment_status}))
    await db.commit()
    return {"id": refund.id, "status": refund.status, "payment_status": order.payment_status}


# Canonical finance paths from the lifecycle contract.  Keep the nested
# `/admin/orders/refunds` aliases above for existing console clients.
refund_router.add_api_route("", list_order_refunds, methods=["GET"])
refund_router.add_api_route("/{refund_id}/complete", complete_order_refund, methods=["POST"])
refund_router.add_api_route("/{refund_id}/approve", approve_wallet_refund, methods=["POST"])


class RefundRequest(BaseModel):
    reason: str
    amount_minor: int | None = None


@router.post("/{order_id}/refund")
async def refund_wallet_order(order_id: int, req: RefundRequest,
                              staff_info: dict = Depends(get_current_staff),
                              db: AsyncSession = Depends(get_db)):
    """把已用钱包余额支付的订单退款回钱包。

    为什么单独开接口而不是复用「取消」：
      * 取消 = 单子没做成；退款 = 钱要退回去。出餐后（READY/DELIVERED）不该还能
        一点「取消」就把钱退回去，那是典型的内部勾结路径；
      * 所以这条路径**仅 MANAGER**，且必须填原因，全程进 audit_logs；
      * 幂等：同一订单重复调用由 `refund:{订单号}` 的幂等键兜底，只退一次。
    """
    if staff_info.get("role") != "MANAGER":
        raise HTTPException(status_code=403, detail="Manager access required")
    reason = (req.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A refund reason is required")

    result = await db.execute(
        select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")
    # 部分退款后状态是 PARTIALLY_REFUNDED，必须允许继续退剩余金额
    if order.payment_method not in {"WALLET", "MIXED"} or order.payment_status not in {"PAID_CONFIRMED", "PARTIALLY_REFUNDED"}:
        raise HTTPException(status_code=409, detail="Order has no confirmed wallet payment")

    staff_id = staff_info["staff_id"]
    import wallet_service as wallet_service_module
    existing = await wallet_service_module.get_payment(db, order.public_code)
    refunded_before = int(existing["refunded_amount"] or 0) if existing else 0
    paid_total = int(existing["amount"]) if existing else int(order.wallet_paid_minor or 0)
    remaining = paid_total - refunded_before
    amount = req.amount_minor or remaining
    # 以**支付金额**为上限（含赠送抵扣），不是订单面额
    if amount <= 0 or amount > remaining:
        raise HTTPException(status_code=422,
                            detail=f"Refund amount is invalid (remaining {remaining} minor units)")

    old_state = f"{order.order_status}/{order.payment_status}"
    try:
        payment = await wallet_service_module.refund_payment(
            db, biz_id=order.public_code, amount_minor=amount,
            # 幂等键带上「退款前的已退金额」：
            #  * 同一个请求重试 -> 键相同 -> 仍然是幂等 no-op；
            #  * 第二次部分退款（已退金额变大）-> 键不同 -> 能继续退。
            # 固定键会让第二次部分退款永远读回第一笔，退不了剩余金额。
            idem=f"refund:{order.public_code}:{refunded_before}:{amount}",
            operator_staff_id=staff_id, reason=reason,
        )
    except wallet_service_module.WalletError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.message)

    refunded_now = int(payment["refunded_amount"] or 0) if payment is not None else refunded_before + amount
    # 只有**退完**才是 REFUNDED，退一部分是 PARTIALLY_REFUNDED——
    # 之前无论退多少都标 REFUNDED，会让「还剩多少可退」在界面上彻底看不出来。
    order.payment_status = "REFUNDED" if refunded_now >= paid_total else "PARTIALLY_REFUNDED"
    db.add(OrderEvent(order_id=order.id, actor_type="STAFF", actor_id=staff_id,
                      event=f"ORDER_REFUNDED: {reason}", from_state=old_state,
                      to_state=f"{order.order_status}/{order.payment_status}"))
    db.add(AuditLog(store_id=order.store_id, actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id),
                    action="wallet_refund",
                    details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                             "operator_telegram_id": staff_info.get("telegram_user_id"),
                             "public_code": order.public_code, "amount_minor": amount,
                             "reason": reason, "from_state": old_state,
                             "to_state": f"{order.order_status}/{order.payment_status}"}))
    await db.commit()
    return {
        "order_status": order.order_status,
        "payment_status": order.payment_status,
        "refunded_amount_minor": payment["refunded_amount"],
    }

@router.post("/{order_id}/payment-review")
async def review_payment(
    order_id: int, 
    req: PaymentReviewRequest, 
    staff_info: dict = Depends(get_current_staff), 
    db: AsyncSession = Depends(get_db)
):
    staff_id = staff_info["staff_id"]

    # Fetch order
    result = await db.execute(select(Order).options(selectinload(Order.items)).filter(Order.id == order_id).with_for_update())
    order = result.scalars().first()
    if not order or not can_access_store(staff_info, order.store_id):
        raise HTTPException(status_code=404, detail="Order not found")

    if req.decision not in ["APPROVED", "REJECTED"]:
        raise HTTPException(status_code=400, detail="Invalid decision")

    if req.decision == "REJECTED" and not (req.reason and req.reason.strip()):
        raise HTTPException(status_code=400, detail="Reason required for rejection")

    if order.payment_status != "PROOF_SUBMITTED":
        raise HTTPException(status_code=409, detail="Order is not awaiting payment review")

    if order.order_status != "ACCEPTED":
        raise HTTPException(status_code=409, detail="Order must be accepted before payment review")
    open_issue = (await db.execute(select(OrderIssue.id).filter(
        OrderIssue.order_id == order.id, OrderIssue.status == "OPEN"
    ).limit(1))).first()
    if open_issue:
        raise HTTPException(status_code=409, detail="This order has an unresolved shortage; wait for the customer")

    proof_result = await db.execute(
        select(PaymentProof).filter(
            PaymentProof.order_id == order.id,
            PaymentProof.review_status == "PENDING",
        ).order_by(PaymentProof.submitted_at.desc()).with_for_update()
    )
    proof = proof_result.scalars().first()
    if proof is None:
        raise HTTPException(status_code=409, detail="No pending payment proof for this order")

    # Create review
    review = PaymentReview(
        order_id=order.id,
        staff_id=staff_id,
        decision=req.decision,
        reason=req.reason.strip() if req.reason else None,
        store_id=order.store_id,      # 结算与门店隔离都要用
    )
    db.add(review)

    # Update order payment status
    old_payment_status = order.payment_status
    old_order_status = order.order_status
    if req.decision == "APPROVED":
        order.payment_status = "PAID_CONFIRMED"
        order.paid_confirmed_minor = int(order.wallet_paid_minor or 0) + int(order.external_due_minor or 0)
        proof.review_status = "APPROVED"
        if order.order_status == "ACCEPTED":
            order.order_status = "PREPARING"
    else:
        order.payment_status = "REJECTED"
        proof.review_status = "REJECTED"

    db.add(OrderEvent(
        order_id=order.id,
        actor_type="STAFF",
        actor_id=staff_id,
        event=f"PAYMENT_{req.decision}",
        from_state=f"{old_order_status}/{old_payment_status}",
        to_state=f"{order.order_status}/{order.payment_status}",
    ))
    db.add(AuditLog(store_id=order.store_id, actor_staff_id=staff_id, entity_type="order", entity_id=str(order.id),
                    action=f"payment_{req.decision.lower()}",
                    details={"source": "admin_console", "operator_name": staff_info.get("login_name"),
                             "operator_telegram_id": staff_info.get("telegram_user_id"), "public_code": order.public_code,
                             "room_number": order.room_number,
                             "from_state": f"{old_order_status}/{old_payment_status}",
                             "to_state": f"{order.order_status}/{order.payment_status}",
                             "reason": req.reason.strip() if req.reason else None}))
    customer_result = await db.execute(select(Customer).filter(Customer.id == order.customer_id))
    customer = customer_result.scalars().first()
    settings = await store_for_order(db, order)
    await db.commit()
    if customer:
        from telegram_service import notify_payment_review
        try:
            await notify_payment_review(customer, order, req.decision, req.reason)
        except Exception:
            pass
    if settings and settings.telegram_staff_group_id and order.telegram_group_message_id:
        from telegram_service import update_order_message
        try:
            await update_order_message(settings.telegram_staff_group_id, order.telegram_group_message_id,
                                       order, settings.staff_group_language, order.items, customer)
        except Exception:
            pass

    return {"message": "Review submitted successfully", "payment_status": order.payment_status}
