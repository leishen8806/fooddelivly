"""dual approval for large recharges + wallet.config threshold

Revision ID: c0123456789b
Revises: b0123456789a
Create Date: 2026-10-02 08:50:00.000000

大额充值需要**两位不同的员工**各确认一次才入账。

  * 阈值：wallet.config.dual_approval_threshold_minor（默认 20000 = $200，0 = 关闭）
  * 同一个人重复点击不会凑数：(order_id, staff_id) 唯一约束
  * 两个人确认的实收金额必须一致，否则 APPROVAL_MISMATCH 直接拒绝
  * 一旦有人确认过（approvals > 0），即使阈值之后被调大，也必须凑满两人
  * 未凑满时订单状态仍是 under_review，不会入账

阈值可随时改：
    UPDATE wallet.config SET value = '50000' WHERE key = 'dual_approval_threshold_minor';
    UPDATE wallet.config SET value = '0'     WHERE key = 'dual_approval_threshold_minor';  -- 关闭
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'c0123456789b'
down_revision = 'b0123456789a'
branch_labels = None
depends_on = None


_NEW_TABLE = """
CREATE TABLE IF NOT EXISTS wallet.recharge_approvals (
  id         BIGSERIAL PRIMARY KEY,
  order_id   BIGINT      NOT NULL REFERENCES wallet.recharge_orders(id),
  staff_id   BIGINT      NOT NULL REFERENCES public.staff(id),
  credit     BIGINT      NOT NULL,          -- 该员工确认的入账金额
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT recharge_approvals_uniq UNIQUE (order_id, staff_id),
  CONSTRAINT recharge_approvals_credit_ck CHECK (credit > 0)
);
CREATE INDEX IF NOT EXISTS recharge_approvals_order_idx
  ON wallet.recharge_approvals (order_id);
"""

_COUNT_FN = """
CREATE OR REPLACE FUNCTION wallet.recharge_approval_count(p_order_id BIGINT)
RETURNS INT LANGUAGE sql STABLE AS $$
  SELECT count(*)::int FROM wallet.recharge_approvals WHERE order_id = p_order_id
$$;
"""

_REQUIRED_FN = """
CREATE OR REPLACE FUNCTION wallet.required_approvals(p_order_id BIGINT)
RETURNS INT LANGUAGE sql STABLE AS $$
  SELECT CASE
           WHEN EXISTS (SELECT 1 FROM wallet.recharge_approvals a WHERE a.order_id = p_order_id)
             THEN 2
           WHEN COALESCE(wallet.cfg_int('dual_approval_threshold_minor', 0), 0) > 0
            AND COALESCE(o.pending_received_amount, o.amount)
                >= wallet.cfg_int('dual_approval_threshold_minor', 0)
             THEN 2
           ELSE 1
         END
    FROM wallet.recharge_orders o WHERE o.id = p_order_id
$$;
"""

_CONFIG = """
INSERT INTO wallet.config (key, value) VALUES ('dual_approval_threshold_minor', '20000')
ON CONFLICT (key) DO NOTHING;
"""

APPROVE_NEW = r"""CREATE OR REPLACE FUNCTION wallet.approve_recharge(
  p_order_id  BIGINT,
  p_staff_id  BIGINT,
  p_received  BIGINT DEFAULT NULL,   -- 实收金额；NULL = 按订单金额
  p_remark    TEXT   DEFAULT NULL
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_order     wallet.recharge_orders;
  v_credit    BIGINT;
  v_bonus     BIGINT;
  v_days      INT;
  v_rule      BIGINT;
  v_expire_at TIMESTAMPTZ;
  v_threshold BIGINT;      -- 双人复核阈值（wallet.config 可调，0 = 关闭）
  v_approvals INT;         -- 本单已确认人数
  v_prev_credit BIGINT;    -- 已确认过的金额（用于比对两人是否一致）
BEGIN
  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ORDER_NOT_FOUND';
  END IF;

  -- 幂等：已入账直接返回成功，不重复加钱
  IF v_order.status = 'credited' THEN
    RETURN v_order;
  END IF;
  IF v_order.status NOT IN ('awaiting_proof', 'under_review') THEN
    RAISE EXCEPTION 'ORDER_STATE: 当前状态 % 不能入账', v_order.status;
  END IF;

  IF NOT wallet.is_reviewer(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无审核权限';
  END IF;
  IF wallet.cfg_int('allow_self_approve', 0) <> 1
     AND wallet.is_order_owner_staff(p_staff_id, v_order.customer_id) THEN
    RAISE EXCEPTION 'SELF_APPROVE_FORBIDDEN: 不能审核自己的充值单';
  END IF;

  -- 实收金额优先级：本次显式传入 > 审核前暂存值 > 订单金额
  v_credit := COALESCE(p_received, v_order.pending_received_amount, v_order.amount);
  IF v_credit <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 实收金额必须大于 0';
  END IF;

  -- 大额双人复核：达到阈值（或已经有人确认过）时，必须**两位不同的员工**各确认一次。
  -- 同一个人重复点击只会记一条（approvals 表上有 (order_id, staff_id) 唯一约束），
  -- 所以凑不出第二个人，也就骗不过复核。
  v_threshold := COALESCE(wallet.cfg_int('dual_approval_threshold_minor', 0), 0);
  v_approvals := wallet.recharge_approval_count(p_order_id);
  IF v_approvals > 0 OR (v_threshold > 0 AND v_credit >= v_threshold) THEN
    -- 两人确认的金额必须一致：不一致说明有人改过实收金额或看错了，直接停下
    SELECT a.credit INTO v_prev_credit FROM wallet.recharge_approvals a
     WHERE a.order_id = p_order_id AND a.staff_id <> p_staff_id LIMIT 1;
    IF v_prev_credit IS NOT NULL AND v_prev_credit <> v_credit THEN
      RAISE EXCEPTION 'APPROVAL_MISMATCH: 两位审核员确认的实收金额不一致（% vs %），请核对后再确认',
        v_prev_credit, v_credit;
    END IF;

    INSERT INTO wallet.recharge_approvals (order_id, staff_id, credit)
    VALUES (p_order_id, p_staff_id, v_credit)
    ON CONFLICT (order_id, staff_id) DO NOTHING;

    v_approvals := wallet.recharge_approval_count(p_order_id);
    IF v_approvals < 2 THEN
      INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
      VALUES ('staff', p_staff_id, 'approve_recharge_awaiting_second', 'recharge_order',
              p_order_id::text,
              jsonb_build_object('credit', v_credit, 'approvals', v_approvals, 'required', 2));
      RETURN v_order;      -- 状态仍为 under_review，等第二位同事确认
    END IF;
  END IF;

  -- 实收与订单金额不一致时，按实收重新算赠送，避免「充 $1 拿 $10 赠送」
  IF v_credit = v_order.amount THEN
    v_bonus     := v_order.bonus_amount;
    v_rule      := v_order.bonus_rule_id;
    v_expire_at := v_order.bonus_expire_at;          -- 下单时的快照
  ELSE
    SELECT c.rule_id, c.bonus, c.valid_days INTO v_rule, v_bonus, v_days
      FROM wallet.calc_bonus(v_order.customer_id, v_order.currency, v_credit) c;
    v_expire_at := CASE WHEN v_days IS NOT NULL
                        THEN now() + (v_days || ' days')::interval END;
  END IF;

  -- 本金入账
  PERFORM wallet._apply(
    v_order.customer_id, v_order.currency, 'principal', v_credit,
    'recharge', 'recharge_order', v_order.order_no,
    'recharge:' || v_order.order_no || ':principal', p_staff_id,
    COALESCE(p_remark, '充值审核通过 ' || v_order.order_no));

  -- 赠送入账：建一个批次（永久赠送与限时赠送互不干扰）
  IF v_bonus > 0 THEN
    PERFORM wallet.add_bonus_lot(
      v_order.customer_id, v_order.currency, v_bonus, v_expire_at,
      'recharge', v_order.order_no,
      'recharge:' || v_order.order_no || ':bonus',
      'recharge_bonus', p_staff_id, '充值赠送 ' || v_order.order_no);
  END IF;

  UPDATE wallet.recharge_orders
     SET status = 'credited',
         received_amount = v_credit,
         bonus_amount = v_bonus,
         bonus_rule_id = v_rule,
         reviewed_by = p_staff_id,
         reviewed_at = now(),
         reject_reason = NULL
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, before_data, after_data)
  VALUES ('staff', p_staff_id, 'approve_recharge', 'recharge_order', p_order_id::text,
          jsonb_build_object('status', 'under_review'),
          jsonb_build_object('status', 'credited', 'principal', v_credit, 'bonus', v_bonus,
                             'approvals', GREATEST(v_approvals, 1)));

  RETURN v_order;
END $$;"""

APPROVE_OLD = r"""CREATE OR REPLACE FUNCTION wallet.approve_recharge(
  p_order_id  BIGINT,
  p_staff_id  BIGINT,
  p_received  BIGINT DEFAULT NULL,   -- 实收金额；NULL = 按订单金额
  p_remark    TEXT   DEFAULT NULL
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_order     wallet.recharge_orders;
  v_credit    BIGINT;
  v_bonus     BIGINT;
  v_days      INT;
  v_rule      BIGINT;
  v_expire_at TIMESTAMPTZ;
BEGIN
  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ORDER_NOT_FOUND';
  END IF;

  -- 幂等：已入账直接返回成功，不重复加钱
  IF v_order.status = 'credited' THEN
    RETURN v_order;
  END IF;
  IF v_order.status NOT IN ('awaiting_proof', 'under_review') THEN
    RAISE EXCEPTION 'ORDER_STATE: 当前状态 % 不能入账', v_order.status;
  END IF;

  IF NOT wallet.is_reviewer(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无审核权限';
  END IF;
  IF wallet.cfg_int('allow_self_approve', 0) <> 1
     AND wallet.is_order_owner_staff(p_staff_id, v_order.customer_id) THEN
    RAISE EXCEPTION 'SELF_APPROVE_FORBIDDEN: 不能审核自己的充值单';
  END IF;

  -- 实收金额优先级：本次显式传入 > 审核前暂存值 > 订单金额
  v_credit := COALESCE(p_received, v_order.pending_received_amount, v_order.amount);
  IF v_credit <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 实收金额必须大于 0';
  END IF;

  -- 实收与订单金额不一致时，按实收重新算赠送，避免「充 $1 拿 $10 赠送」
  IF v_credit = v_order.amount THEN
    v_bonus     := v_order.bonus_amount;
    v_rule      := v_order.bonus_rule_id;
    v_expire_at := v_order.bonus_expire_at;          -- 下单时的快照
  ELSE
    SELECT c.rule_id, c.bonus, c.valid_days INTO v_rule, v_bonus, v_days
      FROM wallet.calc_bonus(v_order.customer_id, v_order.currency, v_credit) c;
    v_expire_at := CASE WHEN v_days IS NOT NULL
                        THEN now() + (v_days || ' days')::interval END;
  END IF;

  -- 本金入账
  PERFORM wallet._apply(
    v_order.customer_id, v_order.currency, 'principal', v_credit,
    'recharge', 'recharge_order', v_order.order_no,
    'recharge:' || v_order.order_no || ':principal', p_staff_id,
    COALESCE(p_remark, '充值审核通过 ' || v_order.order_no));

  -- 赠送入账：建一个批次（永久赠送与限时赠送互不干扰）
  IF v_bonus > 0 THEN
    PERFORM wallet.add_bonus_lot(
      v_order.customer_id, v_order.currency, v_bonus, v_expire_at,
      'recharge', v_order.order_no,
      'recharge:' || v_order.order_no || ':bonus',
      'recharge_bonus', p_staff_id, '充值赠送 ' || v_order.order_no);
  END IF;

  UPDATE wallet.recharge_orders
     SET status = 'credited',
         received_amount = v_credit,
         bonus_amount = v_bonus,
         bonus_rule_id = v_rule,
         reviewed_by = p_staff_id,
         reviewed_at = now(),
         reject_reason = NULL
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, before_data, after_data)
  VALUES ('staff', p_staff_id, 'approve_recharge', 'recharge_order', p_order_id::text,
          jsonb_build_object('status', 'under_review'),
          jsonb_build_object('status', 'credited', 'principal', v_credit, 'bonus', v_bonus));

  RETURN v_order;
END $$;"""


def upgrade() -> None:
    bind = op.get_bind()
    for statement in (_NEW_TABLE, _COUNT_FN, _REQUIRED_FN, _CONFIG):
        for chunk in [c.strip() for c in statement.split(";") if c.strip()]:
            bind.exec_driver_sql(chunk)
    bind.exec_driver_sql(APPROVE_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(APPROVE_OLD)
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS wallet.required_approvals(BIGINT)")
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS wallet.recharge_approval_count(BIGINT)")
    bind.exec_driver_sql("DROP TABLE IF EXISTS wallet.recharge_approvals")
    bind.exec_driver_sql(
        "DELETE FROM wallet.config WHERE key = 'dual_approval_threshold_minor'")
