"""钱包 / 充值的唯一数据访问层。

铁律：**本文件只调用 wallet schema 里的数据库函数**，没有任何一条
`UPDATE wallet.wallets` / `balance = balance + x`。余额只能由数据库函数改，
原因见 alembic 迁移 7e8f90123456 的说明与 docs/WALLET.md。

对外暴露两类东西：
  * `WalletError` —— 数据库业务错误（'CODE: 中文'）翻译后的异常，
    路由层按 `code` 映射成 HTTP 状态码；机器人层按 `code` 取 i18n 文案。
  * `wallet_*` 系列 async 函数 —— 每个都是一次数据库调用。
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_CURRENCY = "USD"

#: 数据库里 `RAISE EXCEPTION 'CODE: ...'` 的语义 -> HTTP 状态码
_HTTP_STATUS = {
    "AMOUNT_TOO_SMALL": 400,
    "AMOUNT_TOO_LARGE": 400,
    "BAD_AMOUNT": 400,
    "BAD_BUCKET": 400,
    "ZERO_DELTA": 400,
    "BAD_REFUND": 400,
    "REASON_REQUIRED": 400,
    "INSUFFICIENT_FUNDS": 409,
    "TOO_MANY_OPEN_ORDERS": 409,
    "DAILY_LIMIT_EXCEEDED": 409,
    "ORDER_EXPIRED": 409,
    "ORDER_STATE": 409,
    "PROOF_DUPLICATE": 409,
    # 自己批自己属于「无权」，语义上是 403 而不是状态冲突
    "SELF_APPROVE_FORBIDDEN": 403,
    "LEDGER_IMMUTABLE": 500,
    "BONUS_LOT_MISMATCH": 500,
    "NOT_REVIEWER": 403,
    "NOT_ORDER_OWNER": 403,
    "ORDER_NOT_FOUND": 404,
    "PAYMENT_NOT_FOUND": 404,
    "WALLET_NOT_FOUND": 404,
}

#: 每个业务错误的中文提示（机器人直接发给用户；API 用英文 detail）
_MESSAGES = {
    "AMOUNT_TOO_SMALL": "充值金额低于最小限额。",
    "AMOUNT_TOO_LARGE": "单笔充值超过上限，请分次充值或联系客服。",
    "TOO_MANY_OPEN_ORDERS": "你还有未完成的充值单，请先完成或取消其中一笔。",
    "DAILY_LIMIT_EXCEEDED": "今日累计充值已达上限，请明天再试。",
    "PROOF_DUPLICATE": "这张截图已经被用于其它充值单，请上传本次转账的原始截图。",
    "ORDER_EXPIRED": "这张充值单已过期，请重新发起充值。",
    "ORDER_STATE": "该充值单当前状态不支持这个操作。",
    "ORDER_NOT_FOUND": "找不到这张充值单。",
    "NOT_ORDER_OWNER": "只能操作自己的充值单。",
    "NOT_REVIEWER": "你没有审核或调账权限。",
    "SELF_APPROVE_FORBIDDEN": "不能审核自己的充值单，请让其他同事处理。",
    "REASON_REQUIRED": "必须填写原因。",
    "INSUFFICIENT_FUNDS": "钱包余额不足。",
    "BAD_AMOUNT": "金额不合法。",
    "BAD_BUCKET": "账户类型不合法。",
    "ZERO_DELTA": "变动金额不能为 0。",
    "BAD_REFUND": "退款金额不合法。",
    "PAYMENT_NOT_FOUND": "找不到对应的支付记录。",
    "LEDGER_IMMUTABLE": "内部错误：账本不允许修改。",
    "BONUS_LOT_MISMATCH": "系统数据异常，请联系客服核对。",
}

DEFAULT_MESSAGE = "系统繁忙，请稍后再试或联系客服。"


class WalletError(Exception):
    """数据库抛出的业务错误，已翻译成可直接展示的文案。"""

    def __init__(self, code: str, message: str, db_message: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.db_message = db_message

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 400)


def _raw_message(exc: BaseException) -> str:
    """从 SQLAlchemy / asyncpg 的异常里挖出数据库原始消息。

    SQLAlchemy 的 asyncpg 方言把 asyncpg 异常包成
    `sqlalchemy.dialects.postgresql.asyncpg.Error`：它的 `str()` 是干净的
    （'AMOUNT_TOO_SMALL: 最小充值 100'），而 `str(DBAPIError)` 前面会多一段
    "(sqlalchemy.dialects.postgresql.asyncpg.Error) " 前缀。所以必须先取 `orig`，
    并兜底剥掉这个前缀——否则 CODE 解析不出来，所有业务错误都会退化成「系统繁忙」。
    """
    orig = getattr(exc, "orig", None)
    for candidate in (orig, exc):
        if candidate is None:
            continue
        message = getattr(candidate, "message", None)
        if isinstance(message, str) and message.strip():
            return message.strip()

    text_value = str(orig) if orig is not None else str(exc)
    text_value = text_value.strip()
    if text_value.startswith("("):
        close = text_value.find(")")
        if close != -1 and "sqlalchemy" in text_value[:close]:
            text_value = text_value[close + 1:].strip()
    return text_value.split("\n", 1)[0].strip()


def _pgcode(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    return getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)


def translate_db_error(exc: BaseException) -> WalletError:
    """把数据库异常翻译成 WalletError。

    schema 里所有 `RAISE EXCEPTION 'CODE: 中文'` 都是 SQLSTATE P0001，
    消息形如 ``AMOUNT_TOO_SMALL: 最小充值金额 100``，取冒号前的 CODE 查表即可。
    """
    raw = _raw_message(exc)
    code = raw.split(":", 1)[0].strip() or "DB_ERROR"
    # 数据库的 'CODE: 具体说明' 里那句说明通常比这里的通用文案更精确
    # （例如 ORDER_STATE 在不同状态下会给出不同原因），优先用它。
    detail = raw.split(":", 1)[1].strip() if ":" in raw else ""
    if code == "INSUFFICIENT_FUNDS" and "数据异常" in raw:
        # consume_bonus_lots 检测到「赠送批次合计 != 钱包赠送余额」的数据异常，
        # 不能提示用户去充值。
        return WalletError("BONUS_LOT_MISMATCH", _MESSAGES["BONUS_LOT_MISMATCH"], raw)
    if code not in _MESSAGES:
        # 唯一索引冲突（SQLSTATE 23505）里最常见的是「同一张截图用在了两张单上」
        # ——wallet.recharge_proofs 的 proof_unique_file_idx。
        if _pgcode(exc) == "23505" and "proof_unique_file_idx" in raw:
            return WalletError("PROOF_DUPLICATE", _MESSAGES["PROOF_DUPLICATE"], raw)
        return WalletError("DB_ERROR", DEFAULT_MESSAGE, raw)
    return WalletError(code, detail or _MESSAGES[code], raw)


async def _fetchrow(db: AsyncSession, sql: str, params: dict[str, Any]):
    try:
        result = await db.execute(text(sql), params)
    except Exception as exc:  # noqa: BLE001 - 统一在这里翻译
        raise translate_db_error(exc) from exc
    return result.mappings().first()


async def _fetchall(db: AsyncSession, sql: str, params: dict[str, Any]):
    try:
        result = await db.execute(text(sql), params)
    except Exception as exc:  # noqa: BLE001
        raise translate_db_error(exc) from exc
    return result.mappings().all()


async def _call(db: AsyncSession, sql: str, params: dict[str, Any]):
    """调用返回钱包表行的数据库函数。"""
    row = await _fetchrow(db, sql, params)
    if row is None:
        raise WalletError("DB_ERROR", DEFAULT_MESSAGE, "数据库函数没有返回结果")
    return row


# ---------------------------------------------------------------------------
# 钱包
# ---------------------------------------------------------------------------

async def get_summary(
    db: AsyncSession, customer_id: int, currency: str = DEFAULT_CURRENCY
) -> dict[str, Any]:
    """余额概览。没有钱包时返回全 0（不建空钱包，等第一次入账再建）。

    两个数要分清（前端「我的」页就是展示这两个）：
      * `balance_minor`   余额 = 本金 + 赠送 + 冻结（账户总额）
      * `available_minor` 可用 = 本金 + 赠送（现在能花的）
      * `total_minor`     与 available_minor 同值，保留给既有调用方

    列名归一化：数据库函数返回 `principal / bonus / frozen / total / balance`，
    API 对外统一 `*_minor` 后缀；两条分支（有钱包 / 没钱包）必须返回同样的键。
    """
    row = await _fetchrow(
        db,
        "SELECT * FROM wallet.get_summary(:customer_id, :currency)",
        {"customer_id": customer_id, "currency": currency},
    )
    data = dict(row) if row is not None else {}
    principal = data.get("principal", 0) or 0
    bonus = data.get("bonus", 0) or 0
    frozen = data.get("frozen", 0) or 0
    available = data.get("total", principal + bonus) or 0
    return {
        "customer_id": data.get("customer_id", customer_id),
        "currency": data.get("currency", currency),
        "principal_minor": principal,
        "bonus_minor": bonus,
        "frozen_minor": frozen,
        "available_minor": available,
        "balance_minor": data.get("balance", available + frozen) or 0,
        "total_minor": available,
        "bonus_expire_at": data.get("bonus_expire_at"),
    }


async def list_ledger(
    db: AsyncSession, customer_id: int, limit: int = 30, offset: int = 0
) -> Sequence[Any]:
    """交易明细（只读账本）。分页用 offset，够用且简单。"""
    return await _fetchall(
        db,
        """
        SELECT id, currency, bucket, direction, amount, balance_after,
               entry_type, biz_type, biz_id, remark, created_at
          FROM wallet.ledger_entries
         WHERE customer_id = :customer_id
         ORDER BY id DESC
         LIMIT :limit OFFSET :offset
        """,
        {"customer_id": customer_id, "limit": limit, "offset": offset},
    )


# ---------------------------------------------------------------------------
# 充值单（客户侧）
# ---------------------------------------------------------------------------

async def start_recharge(
    db: AsyncSession,
    *,
    customer_id: int,
    amount_minor: int,
    idem: str,
    currency: str = DEFAULT_CURRENCY,
    store_id: int | None = None,
):
    """创建充值单。同一个 idem 永远返回同一张单（并发下由唯一约束兜底）。

    `store_id` 是**收款门店**，结算与门店隔离都要用；不传则落到主店。
    """
    return await _call(
        db,
        """SELECT * FROM wallet.start_recharge(
               :customer_id, :currency, :amount_minor, :idem,
               CAST(:store_id AS BIGINT))""",
        {
            "customer_id": customer_id,
            "currency": currency,
            "amount_minor": amount_minor,
            "idem": idem,
            "store_id": store_id,
        },
    )


async def get_recharge(db: AsyncSession, order_id: int):
    return await _fetchrow(
        db, "SELECT * FROM wallet.recharge_orders WHERE id = :order_id",
        {"order_id": order_id},
    )


async def get_recharge_by_order_no(db: AsyncSession, order_no: str):
    """按转账备注里的订单号回查（deep link `?start=rc_<订单号>` 用）。"""
    return await _fetchrow(
        db, "SELECT * FROM wallet.recharge_orders WHERE order_no = :order_no",
        {"order_no": (order_no or "").strip().upper()},
    )


async def get_recharge_for_customer(db: AsyncSession, order_id: int, customer_id: int):
    """回库读单并校验归属——**金额与状态一律以数据库为准**，不信回调数据。"""
    row = await get_recharge(db, order_id)
    if row is None or row["customer_id"] != customer_id:
        return None
    return row


async def latest_open_recharge(
    db: AsyncSession, customer_id: int, currency: str = DEFAULT_CURRENCY
):
    """用户最近一张待付款/待审核的充值单（用于「没点按钮直接发截图」的兜底）。"""
    return await _fetchrow(
        db,
        """
        SELECT * FROM wallet.recharge_orders
         WHERE customer_id = :customer_id AND currency = :currency
           AND status IN ('awaiting_proof', 'under_review')
         ORDER BY created_at DESC LIMIT 1
        """,
        {"customer_id": customer_id, "currency": currency},
    )


async def list_recharges(
    db: AsyncSession, customer_id: int, limit: int = 20
) -> Sequence[Any]:
    return await _fetchall(
        db,
        """
        SELECT id, order_no, currency, amount, bonus_amount, status,
               received_amount, pay_reference, proof_count,
               expires_at, submitted_at, reviewed_at, reject_reason, created_at
          FROM wallet.recharge_orders
         WHERE customer_id = :customer_id
         ORDER BY id DESC LIMIT :limit
        """,
        {"customer_id": customer_id, "limit": limit},
    )


async def list_proofs(db: AsyncSession, order_id: int) -> Sequence[Any]:
    return await _fetchall(
        db,
        """
        SELECT id, order_id, tg_file_id, tg_file_unique_id, created_at
          FROM wallet.recharge_proofs WHERE order_id = :order_id ORDER BY id
        """,
        {"order_id": order_id},
    )


async def submit_proof(
    db: AsyncSession,
    *,
    order_id: int,
    customer_id: int,
    file_id: str,
    file_unique_id: str,
    pay_reference: str | None = None,
):
    """上传转账凭证（截图）。同一张图不能用于两张单：唯一索引直接拒绝。"""
    return await _call(
        db,
        """SELECT * FROM wallet.submit_proof(
               :order_id, :customer_id, :file_id, :file_unique_id,
               :mime, :size, :storage, :sha256, :reference)""",
        {
            "order_id": order_id,
            "customer_id": customer_id,
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "mime": None,
            "size": None,
            "storage": None,
            "sha256": None,
            "reference": pay_reference,
        },
    )


async def cancel_recharge(db: AsyncSession, *, order_id: int, customer_id: int):
    """用户自行取消未付款的充值单（幂等，不动余额）。"""
    return await _call(
        db,
        "SELECT * FROM wallet.cancel_recharge(:order_id, :customer_id)",
        {"order_id": order_id, "customer_id": customer_id},
    )


# ---------------------------------------------------------------------------
# 充值单（员工侧）
# ---------------------------------------------------------------------------

async def list_recharges_admin(db: AsyncSession, status: str | None, limit: int = 50,
                               store_id: int | None = None):
    """管理端充值单列表。

    除了单据本身，还带出客户信息与**当前钱包余额**——审核时要能一眼看出
    这个客户是不是老用户、账户里已经有多少钱。余额用 LEFT JOIN 取，
    没有钱包的客户按 0 处理（不建空钱包）。
    """
    base = """
        SELECT o.*,
               c.telegram_user_id, c.display_name, c.username,
               COALESCE(w.principal, 0) + COALESCE(w.bonus, 0) AS available_minor,
               COALESCE(w.principal, 0) + COALESCE(w.bonus, 0)
                 + COALESCE(w.frozen, 0)                          AS balance_minor,
               COALESCE(w.frozen, 0)     AS frozen_minor,
               wallet.recharge_approval_count(o.id) AS approval_count,
               wallet.required_approvals(o.id)      AS required_approvals,
               COALESCE(w.principal, 0)  AS principal_minor,
               COALESCE(w.bonus, 0)      AS bonus_minor
          FROM wallet.recharge_orders o
          JOIN public.customers c ON c.id = o.customer_id
          LEFT JOIN wallet.wallets w
                 ON w.customer_id = o.customer_id AND w.currency = o.currency
         WHERE (CAST(:store_id AS INTEGER) IS NULL
                OR o.store_id = CAST(:store_id AS INTEGER)
                OR o.store_id IS NULL)
    """
    if status:
        return await _fetchall(
            db,
            base + " AND o.status = :status ORDER BY o.created_at DESC LIMIT :limit",
            {"status": status, "limit": limit, "store_id": store_id},
        )
    return await _fetchall(
        db, base + " ORDER BY o.created_at DESC LIMIT :limit",
        {"limit": limit, "store_id": store_id},
    )


async def list_pending_recharges(db: AsyncSession, limit: int = 50):
    return await _fetchall(
        db,
        "SELECT * FROM wallet.v_pending_recharges LIMIT :limit",
        {"limit": limit},
    )


async def approval_progress(db: AsyncSession, order_id: int) -> tuple[int, int]:
    """返回 (已确认人数, 需要人数)。需要人数 > 1 表示这笔单要走大额双人复核。"""
    row = await _fetchrow(
        db,
        """SELECT wallet.recharge_approval_count(:order_id) AS done,
                  wallet.required_approvals(:order_id)      AS required""",
        {"order_id": order_id},
    )
    if row is None:
        return (0, 1)
    return (int(row["done"] or 0), int(row["required"] or 1))


async def approve_recharge(
    db: AsyncSession,
    *,
    order_id: int,
    staff_id: int,
    received_minor: int | None = None,
    remark: str | None = None,
):
    """审核通过并把钱入账（幂等：已 credited 直接返回）。

    实收金额取值顺序由数据库决定：显式传入 > pending_received_amount > 订单金额。
    """
    return await _call(
        db,
        """SELECT * FROM wallet.approve_recharge(
               :order_id, :staff_id, :received, :remark)""",
        {
            "order_id": order_id,
            "staff_id": staff_id,
            "received": received_minor,
            "remark": remark,
        },
    )


async def reject_recharge(
    db: AsyncSession, *, order_id: int, staff_id: int, reason: str
):
    return await _call(
        db,
        "SELECT * FROM wallet.reject_recharge(:order_id, :staff_id, :reason)",
        {"order_id": order_id, "staff_id": staff_id, "reason": reason},
    )


async def set_received_amount(
    db: AsyncSession, *, order_id: int, staff_id: int, amount_minor: int | None
):
    """审核前暂存「实收金额」（置 NULL 表示清除，回到订单金额）。"""
    return await _call(
        db,
        "SELECT * FROM wallet.set_received_amount(:order_id, :staff_id, :amount)",
        {"order_id": order_id, "staff_id": staff_id, "amount": amount_minor},
    )


async def adjust_balance(
    db: AsyncSession,
    *,
    customer_id: int,
    bucket: str,
    delta_minor: int,
    staff_id: int,
    reason: str,
    idem: str,
    currency: str = DEFAULT_CURRENCY,
):
    """手动调账（仅 MANAGER，权限在数据库函数里再校验一次）。"""
    row = await _fetchrow(
        db,
        """SELECT * FROM wallet.adjust_balance(
               :customer_id, :currency, :bucket, :delta, :staff_id, :reason, :idem)""",
        {
            "customer_id": customer_id,
            "currency": currency,
            "bucket": bucket,
            "delta": delta_minor,
            "staff_id": staff_id,
            "reason": reason,
            "idem": idem,
        },
    )
    return row


# ---------------------------------------------------------------------------
# 余额支付（订单侧；本次只提供接口，是否接入结算由业务决定）
# ---------------------------------------------------------------------------

async def spend_balance(
    db: AsyncSession,
    *,
    customer_id: int,
    amount_minor: int,
    biz_id: str,
    idem: str,
    currency: str = DEFAULT_CURRENCY,
    remark: str | None = None,
):
    """用余额抵扣订单（赠送金优先扣）。同一 biz_id 重复调用只扣一次。"""
    return await _call(
        db,
        """SELECT * FROM wallet.spend_balance(
               :customer_id, :currency, :amount_minor, :biz_id, :idem, :remark)""",
        {
            "customer_id": customer_id,
            "currency": currency,
            "amount_minor": amount_minor,
            "biz_id": biz_id,
            "idem": idem,
            "remark": remark,
        },
    )


async def refund_payment(
    db: AsyncSession,
    *,
    biz_id: str,
    amount_minor: int,
    idem: str,
    operator_staff_id: int | None = None,
    reason: str | None = None,
):
    """退款回钱包，按原支付的「赠送 / 本金」构成比例退回。"""
    return await _call(
        db,
        """SELECT * FROM wallet.refund_payment(
               :biz_id, :amount_minor, :idem, :operator, :reason)""",
        {
            "biz_id": biz_id,
            "amount_minor": amount_minor,
            "idem": idem,
            "operator": operator_staff_id,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# 运维
# ---------------------------------------------------------------------------

async def expire_stale_recharges(db: AsyncSession, store_id: int | None = None) -> int:
    """把过期的**充值单**置为 expired（建议定时任务每 10 分钟跑一次）。

    注意：数据库里的函数名是 `wallet.expire_stale_orders()`（沿用了订单域的叫法），
    它只处理 wallet.recharge_orders，和订单表无关。
    """
    row = await _fetchrow(db, "SELECT wallet.expire_stale_orders(CAST(:store_id AS BIGINT)) AS n",
                          {"store_id": store_id})
    return int(row["n"]) if row else 0


async def expire_bonus(db: AsyncSession, customer_id: int, currency: str = DEFAULT_CURRENCY) -> int:
    row = await _fetchrow(
        db,
        "SELECT wallet.expire_bonus(:customer_id, :currency) AS n",
        {"customer_id": customer_id, "currency": currency},
    )
    return int(row["n"]) if row else 0


async def reconcile(db: AsyncSession) -> Sequence[Any]:
    """对账：必须返回空。非空即代表账本与钱包余额不一致（要告警）。"""
    return await _fetchall(db, "SELECT * FROM wallet.reconcile()", {})


# ---------------------------------------------------------------------------
# 充值规则 / 限额（运营可调）
# ---------------------------------------------------------------------------

async def list_recharge_rules(db: AsyncSession) -> Sequence[Any]:
    return await _fetchall(
        db,
        """
        SELECT id, name, currency, min_amount, max_amount, bonus_type, bonus_value,
               max_bonus, bonus_valid_days, active
          FROM wallet.recharge_rules
         WHERE active IS TRUE
         ORDER BY priority DESC, min_amount
        """,
        {},
    )


async def cfg_int(db: AsyncSession, key: str, default: int) -> int:
    row = await _fetchrow(db, "SELECT wallet.cfg_int(:key, :default) AS v",
                          {"key": key, "default": default})
    return int(row["v"]) if row and row["v"] is not None else default


def new_idempotency_key(prefix: str) -> str:
    """Bot/前端兜底用的幂等键；正常情况下用「消息身份」构造，见 routers/bot.py。"""
    return f"{prefix}:{secrets.token_hex(12)}"
