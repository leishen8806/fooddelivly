"""self-dealing guards: no self review / self adjust / self refund

Revision ID: b0123456789a
Revises: af0123456789
Create Date: 2026-10-01 18:20:00.000000

堵住三条「自己批自己」的资金路径（安全审计发现并已复现）：
  1. wallet.set_received_amount —— 员工能改**自己名下充值单**的实收金额。
     approve/reject 拦住了自己批自己，但实收金额是入账依据：自己把它从 $10
     改成 $1000，等别的员工点「确认到账」（不带金额）就会按 $1000 入账。
  2. wallet.adjust_balance —— MANAGER 能给自己账户调账。
  3. wallet.refund_payment —— 操作人就是订单所属客户本人时能给自己退款。

判定「是不是本人」必须用 telegram_user_id 对齐：staff.id 与 customers.id
是两套独立序列，直接比 id 会误判也会漏判（见 wallet.is_order_owner_staff）。
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b0123456789a'
down_revision = 'af0123456789'
branch_labels = None
depends_on = None


# ---- 加保护后的版本 ----
SET_RECEIVED_NEW = r"""
CREATE OR REPLACE FUNCTION wallet.set_received_amount(
  p_order_id BIGINT,
  p_staff_id BIGINT,
  p_amount   BIGINT          -- NULL = 清除暂存，回到订单金额
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE v_order wallet.recharge_orders;
BEGIN
  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ORDER_NOT_FOUND'; END IF;
  -- 不能给自己名下的充值单改「实收金额」：approve 拦住了自己批自己，
  -- 但实收金额是审核时的入账依据 —— 自己把它改大，等别的员工点「确认到账」
  -- 就会按被放大的金额入账。
  IF wallet.is_order_owner_staff(p_staff_id, v_order.customer_id) THEN
    RAISE EXCEPTION 'SELF_APPROVE_FORBIDDEN: 不能修改自己充值单的实收金额';
  END IF;
  IF v_order.status NOT IN ('awaiting_proof', 'under_review') THEN
    RAISE EXCEPTION 'ORDER_STATE: 当前状态 % 不能修改实收金额', v_order.status;
  END IF;
  IF NOT wallet.is_reviewer(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无审核权限';
  END IF;
  IF p_amount IS NOT NULL AND p_amount <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 实收金额必须大于 0';
  END IF;

  UPDATE wallet.recharge_orders
     SET pending_received_amount = p_amount
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
  VALUES ('staff', p_staff_id, 'set_received_amount', 'recharge_order', p_order_id::text,
          jsonb_build_object('pending_received_amount', p_amount));

  RETURN v_order;
END $$;
"""

ADJUST_NEW = r"""
CREATE OR REPLACE FUNCTION wallet.adjust_balance(
  p_customer_id  BIGINT,
  p_currency TEXT,
  p_bucket   TEXT,
  p_delta    BIGINT,
  p_staff_id BIGINT,
  p_reason   TEXT,
  p_idem     TEXT
) RETURNS wallet.ledger_entries
LANGUAGE plpgsql AS $$
DECLARE v_entry wallet.ledger_entries;
BEGIN
  IF p_reason IS NULL OR btrim(p_reason) = '' THEN
    RAISE EXCEPTION 'REASON_REQUIRED: 调账必须填写原因';
  END IF;
  IF NOT wallet.can_adjust(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无调账权限';
  END IF;
  -- MANAGER 不能给自己的账户凭空加钱/减钱（自己批自己），要改找另一个管理员
  IF wallet.is_order_owner_staff(p_staff_id, p_customer_id) THEN
    RAISE EXCEPTION 'SELF_APPROVE_FORBIDDEN: 不能给自己的账户调账，请让其他管理员处理';
  END IF;

  -- 幂等：同一 idem 重复提交不再写审计日志
  IF EXISTS (SELECT 1 FROM wallet.ledger_entries WHERE idempotency_key = p_idem) THEN
    SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
    RETURN v_entry;
  END IF;

  -- 赠送桶必须同时维护批次，否则批次与余额会脱钩
  IF p_bucket = 'bonus' AND p_delta > 0 THEN
    PERFORM wallet.add_bonus_lot(p_customer_id, p_currency, p_delta, NULL,   -- 调账赠送默认永久
      'adjust', p_staff_id::text, p_idem, 'admin_adjust', p_staff_id, p_reason);
    SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
  ELSIF p_bucket = 'bonus' AND p_delta < 0 THEN
    PERFORM wallet.consume_bonus_lots(p_customer_id, p_currency, -p_delta,
      'manual', p_staff_id::text, p_idem, 'admin_adjust', p_staff_id, p_reason);
    SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
  ELSE
    v_entry := wallet._apply(p_customer_id, p_currency, p_bucket, p_delta,
      'admin_adjust', 'manual', p_staff_id::text, p_idem, p_staff_id, p_reason);
  END IF;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
  VALUES ('staff', p_staff_id, 'adjust_balance', 'wallet', p_customer_id::text,
          jsonb_build_object('bucket', p_bucket, 'delta', p_delta,
                             'currency', p_currency, 'reason', p_reason));

  RETURN v_entry;
END $$;
"""

REFUND_NEW = r"""
CREATE OR REPLACE FUNCTION wallet.refund_payment(
  p_biz_id   TEXT,
  p_amount   BIGINT,
  p_idem     TEXT,
  p_operator BIGINT DEFAULT NULL,
  p_reason   TEXT DEFAULT NULL
) RETURNS wallet.order_payments
LANGUAGE plpgsql AS $$
DECLARE
  v_pay       wallet.order_payments;
  v_remaining BIGINT;
  v_bonus     BIGINT;
  v_principal BIGINT;
BEGIN
  SELECT * INTO v_pay FROM wallet.order_payments WHERE biz_id = p_biz_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'PAYMENT_NOT_FOUND'; END IF;
  -- 操作人如果就是这笔支付所属客户本人，禁止退款（出餐后又把钱退回自己钱包）
  IF p_operator IS NOT NULL
     AND wallet.is_order_owner_staff(p_operator, v_pay.customer_id) THEN
    RAISE EXCEPTION 'SELF_APPROVE_FORBIDDEN: 不能给自己的订单退款，请让其他管理员处理';
  END IF;

  -- 幂等：同一笔退款请求重复提交直接返回，不重复退款、不重复记账
  IF EXISTS (SELECT 1 FROM wallet.ledger_entries
              WHERE idempotency_key IN (p_idem || ':principal', p_idem || ':bonus')) THEN
    RETURN v_pay;
  END IF;

  v_remaining := v_pay.amount - v_pay.refunded_amount;
  IF p_amount <= 0 OR p_amount > v_remaining THEN
    RAISE EXCEPTION 'BAD_REFUND: 退款金额非法 (可退 %)', v_remaining;
  END IF;

  IF p_amount = v_remaining THEN
    v_bonus     := v_pay.bonus_used - v_pay.refunded_bonus;
    v_principal := v_pay.principal_used - v_pay.refunded_principal;
  ELSE
    v_bonus     := ((v_pay.bonus_used - v_pay.refunded_bonus) * p_amount) / v_remaining;
    v_principal := p_amount - v_bonus;
  END IF;

  IF v_bonus > 0 THEN
    -- 退回的赠送金建一个新批次：原批次可能已过期，给一个全新的有效期窗口
    PERFORM wallet.add_bonus_lot(v_pay.customer_id, v_pay.currency, v_bonus,
      now() + (wallet.cfg_int('refund_bonus_valid_days', 30) || ' days')::interval,
      'refund', p_biz_id, p_idem || ':bonus', 'refund', p_operator, p_reason);
  END IF;
  IF v_principal > 0 THEN
    PERFORM wallet._apply(v_pay.customer_id, v_pay.currency, 'principal', v_principal,
      'refund', 'order', p_biz_id, p_idem || ':principal', p_operator, p_reason);
  END IF;

  UPDATE wallet.order_payments
     SET refunded_amount    = refunded_amount + p_amount,
         refunded_bonus     = refunded_bonus + v_bonus,
         refunded_principal = refunded_principal + v_principal,
         status = CASE WHEN refunded_amount + p_amount = amount
                       THEN 'refunded' ELSE 'partially_refunded' END
   WHERE id = v_pay.id
  RETURNING * INTO v_pay;

  RETURN v_pay;
END $$;
"""


# ---- 原始版本（downgrade 用）----
SET_RECEIVED_OLD = r"""
CREATE OR REPLACE FUNCTION wallet.set_received_amount(
  p_order_id BIGINT,
  p_staff_id BIGINT,
  p_amount   BIGINT          -- NULL = 清除暂存，回到订单金额
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE v_order wallet.recharge_orders;
BEGIN
  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ORDER_NOT_FOUND'; END IF;
  IF v_order.status NOT IN ('awaiting_proof', 'under_review') THEN
    RAISE EXCEPTION 'ORDER_STATE: 当前状态 % 不能修改实收金额', v_order.status;
  END IF;
  IF NOT wallet.is_reviewer(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无审核权限';
  END IF;
  IF p_amount IS NOT NULL AND p_amount <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 实收金额必须大于 0';
  END IF;

  UPDATE wallet.recharge_orders
     SET pending_received_amount = p_amount
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
  VALUES ('staff', p_staff_id, 'set_received_amount', 'recharge_order', p_order_id::text,
          jsonb_build_object('pending_received_amount', p_amount));

  RETURN v_order;
END $$;
"""

ADJUST_OLD = r"""
CREATE OR REPLACE FUNCTION wallet.adjust_balance(
  p_customer_id  BIGINT,
  p_currency TEXT,
  p_bucket   TEXT,
  p_delta    BIGINT,
  p_staff_id BIGINT,
  p_reason   TEXT,
  p_idem     TEXT
) RETURNS wallet.ledger_entries
LANGUAGE plpgsql AS $$
DECLARE v_entry wallet.ledger_entries;
BEGIN
  IF p_reason IS NULL OR btrim(p_reason) = '' THEN
    RAISE EXCEPTION 'REASON_REQUIRED: 调账必须填写原因';
  END IF;
  IF NOT wallet.can_adjust(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无调账权限';
  END IF;

  -- 幂等：同一 idem 重复提交不再写审计日志
  IF EXISTS (SELECT 1 FROM wallet.ledger_entries WHERE idempotency_key = p_idem) THEN
    SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
    RETURN v_entry;
  END IF;

  -- 赠送桶必须同时维护批次，否则批次与余额会脱钩
  IF p_bucket = 'bonus' AND p_delta > 0 THEN
    PERFORM wallet.add_bonus_lot(p_customer_id, p_currency, p_delta, NULL,   -- 调账赠送默认永久
      'adjust', p_staff_id::text, p_idem, 'admin_adjust', p_staff_id, p_reason);
    SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
  ELSIF p_bucket = 'bonus' AND p_delta < 0 THEN
    PERFORM wallet.consume_bonus_lots(p_customer_id, p_currency, -p_delta,
      'manual', p_staff_id::text, p_idem, 'admin_adjust', p_staff_id, p_reason);
    SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
  ELSE
    v_entry := wallet._apply(p_customer_id, p_currency, p_bucket, p_delta,
      'admin_adjust', 'manual', p_staff_id::text, p_idem, p_staff_id, p_reason);
  END IF;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
  VALUES ('staff', p_staff_id, 'adjust_balance', 'wallet', p_customer_id::text,
          jsonb_build_object('bucket', p_bucket, 'delta', p_delta,
                             'currency', p_currency, 'reason', p_reason));

  RETURN v_entry;
END $$;
"""

REFUND_OLD = r"""
CREATE OR REPLACE FUNCTION wallet.refund_payment(
  p_biz_id   TEXT,
  p_amount   BIGINT,
  p_idem     TEXT,
  p_operator BIGINT DEFAULT NULL,
  p_reason   TEXT DEFAULT NULL
) RETURNS wallet.order_payments
LANGUAGE plpgsql AS $$
DECLARE
  v_pay       wallet.order_payments;
  v_remaining BIGINT;
  v_bonus     BIGINT;
  v_principal BIGINT;
BEGIN
  SELECT * INTO v_pay FROM wallet.order_payments WHERE biz_id = p_biz_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'PAYMENT_NOT_FOUND'; END IF;

  -- 幂等：同一笔退款请求重复提交直接返回，不重复退款、不重复记账
  IF EXISTS (SELECT 1 FROM wallet.ledger_entries
              WHERE idempotency_key IN (p_idem || ':principal', p_idem || ':bonus')) THEN
    RETURN v_pay;
  END IF;

  v_remaining := v_pay.amount - v_pay.refunded_amount;
  IF p_amount <= 0 OR p_amount > v_remaining THEN
    RAISE EXCEPTION 'BAD_REFUND: 退款金额非法 (可退 %)', v_remaining;
  END IF;

  IF p_amount = v_remaining THEN
    v_bonus     := v_pay.bonus_used - v_pay.refunded_bonus;
    v_principal := v_pay.principal_used - v_pay.refunded_principal;
  ELSE
    v_bonus     := ((v_pay.bonus_used - v_pay.refunded_bonus) * p_amount) / v_remaining;
    v_principal := p_amount - v_bonus;
  END IF;

  IF v_bonus > 0 THEN
    -- 退回的赠送金建一个新批次：原批次可能已过期，给一个全新的有效期窗口
    PERFORM wallet.add_bonus_lot(v_pay.customer_id, v_pay.currency, v_bonus,
      now() + (wallet.cfg_int('refund_bonus_valid_days', 30) || ' days')::interval,
      'refund', p_biz_id, p_idem || ':bonus', 'refund', p_operator, p_reason);
  END IF;
  IF v_principal > 0 THEN
    PERFORM wallet._apply(v_pay.customer_id, v_pay.currency, 'principal', v_principal,
      'refund', 'order', p_biz_id, p_idem || ':principal', p_operator, p_reason);
  END IF;

  UPDATE wallet.order_payments
     SET refunded_amount    = refunded_amount + p_amount,
         refunded_bonus     = refunded_bonus + v_bonus,
         refunded_principal = refunded_principal + v_principal,
         status = CASE WHEN refunded_amount + p_amount = amount
                       THEN 'refunded' ELSE 'partially_refunded' END
   WHERE id = v_pay.id
  RETURNING * INTO v_pay;

  RETURN v_pay;
END $$;
"""


_PAIRS = (
    (SET_RECEIVED_NEW, SET_RECEIVED_OLD),
    (ADJUST_NEW, ADJUST_OLD),
    (REFUND_NEW, REFUND_OLD),
)


# 注意：这里必须用 exec_driver_sql，不能用 op.execute(字符串)。
# op.execute 会把 SQL 文本包成 text()，于是 PL/pgSQL 函数体里的
# `%`（RAISE 的格式化占位）和 `:principal`（拼接幂等键）会被当成绑定参数，
# 直接报 "A value is required for bind parameter ..."。
def upgrade() -> None:
    bind = op.get_bind()
    for new, _old in _PAIRS:
        bind.exec_driver_sql(new)


def downgrade() -> None:
    bind = op.get_bind()
    for _new, old in _PAIRS:
        bind.exec_driver_sql(old)
