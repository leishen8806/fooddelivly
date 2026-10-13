"""订单退款（缺货删品 / 取消）的财务指标，统计接口与日报共用，避免口径分叉。

口径（已与业务确认）：
  * 钱包退款已完成：order_refunds.method=WALLET 且 DONE，按 **done_at** 归属期间。
    钱包扣款本身已是「扣除退款后的净额」，所以这一项**只展示、不再从现金流里减**。
  * ABA 退款已转账：method=ABA_MANUAL 且 DONE，按 **done_at**（人工转账完成时间）归属期间，
    是真实的现金流出，会从「现金净流入」里扣除。
  * 待处理退款：当前仍是 PENDING 的 ABA 人工退款 + 等经理审批的钱包退款。这是**快照**，
    不随查询期间变化——它是经理的待办，也是尚未发生的现金流出，因此不计入现金净流入。
  * 缺货删品减少额：未取消订单的 removed_minor，按订单创建时间归属（与订单销售额同口径，
    销售额本身已是调整后的金额，这里只是拆解）。
  * 对账差额：对「已有退款记录」且在期间内创建的订单，
        Σ(已确认收款 − 订单留存额) − Σ退款记录(不含 FAILED)
    其中留存额 = 取消订单为 0，其余为当前订单总额。理论上恒为 0；不为 0 说明有退款没记录或重复记录。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ZERO_REFUND_METRICS = {
    "shortage_removed_minor": 0,
    "refund_wallet_done_minor": 0,
    "refund_aba_done_minor": 0,
    "refund_pending_minor": 0,
    "refund_pending_count": 0,
    "refund_reconcile_diff_minor": 0,
}


async def refund_metrics(
    db: AsyncSession,
    *,
    start_utc: datetime,
    end_utc: datetime,
    store_id: int | None,
    include_legacy_null_store: bool,
) -> dict[str, dict[str, int]]:
    """返回 {币种: 指标}。`include_legacy_null_store`：门店账号沿用「历史空 store_id 可见」的规则。"""
    params = {"start_utc": start_utc, "end_utc": end_utc, "store_id": store_id}
    store_clause = (
        "(CAST(:store_id AS INTEGER) IS NULL OR o.store_id = CAST(:store_id AS INTEGER)"
        + (" OR o.store_id IS NULL)" if include_legacy_null_store else ")")
    )
    out: dict[str, dict[str, int]] = {}

    def bucket(currency: str) -> dict[str, int]:
        return out.setdefault(currency, dict(ZERO_REFUND_METRICS))

    done = await db.execute(text(f"""
        SELECT o.currency, r.method, COALESCE(sum(r.amount_minor), 0)
          FROM order_refunds r JOIN orders o ON o.id = r.order_id
         WHERE r.status = 'DONE' AND r.done_at >= :start_utc AND r.done_at < :end_utc
           AND {store_clause}
         GROUP BY o.currency, r.method"""), params)
    for currency, method, amount in done.all():
        key = "refund_wallet_done_minor" if method == "WALLET" else "refund_aba_done_minor"
        bucket(currency)[key] += int(amount or 0)

    pending = await db.execute(text(f"""
        SELECT o.currency, count(*), COALESCE(sum(r.amount_minor), 0)
          FROM order_refunds r JOIN orders o ON o.id = r.order_id
         WHERE r.status = 'PENDING' AND {store_clause}
         GROUP BY o.currency"""), params)
    for currency, count, amount in pending.all():
        row = bucket(currency)
        row["refund_pending_count"] += int(count or 0)
        row["refund_pending_minor"] += int(amount or 0)

    removed = await db.execute(text(f"""
        SELECT o.currency, COALESCE(sum(o.removed_minor), 0)
          FROM orders o
         WHERE o.order_status <> 'CANCELLED' AND o.removed_minor > 0
           AND o.created_at >= :start_utc AND o.created_at < :end_utc AND {store_clause}
         GROUP BY o.currency"""), params)
    for currency, amount in removed.all():
        bucket(currency)["shortage_removed_minor"] += int(amount or 0)

    diff = await db.execute(text(f"""
        SELECT o.currency,
               COALESCE(sum((o.paid_confirmed_minor
                             - CASE WHEN o.order_status = 'CANCELLED' THEN 0 ELSE o.total_minor END)
                            - r.refunded), 0)
          FROM orders o
          JOIN (SELECT order_id, sum(amount_minor) AS refunded
                  FROM order_refunds WHERE status <> 'FAILED' GROUP BY order_id) r
            ON r.order_id = o.id
         WHERE o.created_at >= :start_utc AND o.created_at < :end_utc AND {store_clause}
         GROUP BY o.currency"""), params)
    for currency, amount in diff.all():
        bucket(currency)["refund_reconcile_diff_minor"] += int(amount or 0)
    return out
