"""P2 收尾：钱包运维按店 + 关键表 store_id 收紧为 NOT NULL

Revision ID: h0123456789a
Revises: g0123456789f
Create Date: 2026-10-02 10:30:00.000000

两件事：

1. **钱包运维按店**
   `wallet.expire_stale_orders()` 原来会过期**所有门店**的充值单——
   门店经理点一下运维就把别家店的单作废了。改成接受 p_store_id：
   NULL = 全部门店（总部），否则只处理该店。

   `wallet.reconcile()` 保持全局：钱包余额是**全局**的（跨店通用），
   按门店过滤账本反而会算出假差异。它只读不写，所以在接口层限制为总部可用。

2. **NOT NULL 收紧**
   只对「必然属于某家店」的表加约束：
     orders / payment_reviews / payment_proofs / wallet.recharge_orders
   刻意**不**加的表（NULL 有明确语义）：
     products.store_id   —— NULL = 总部模板（菜单「模板+覆盖」模型的基础）
     categories.store_id —— 同上
     staff.store_id      —— NULL = 总部账号（可跨店）
     customers.store_id  —— 尚未绑定门店的客户
     audit_logs / ledger_entries / report_deliveries —— 系统动作可能没有门店

   加约束前先校验存量数据，避免在有 NULL 的库上直接失败。
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'h0123456789a'
down_revision = 'g0123456789f'
branch_labels = None
depends_on = None


_EXPIRE_OLD = """
CREATE OR REPLACE FUNCTION wallet.expire_stale_orders()
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE v_n INT;
BEGIN
  WITH upd AS (
    UPDATE wallet.recharge_orders
       SET status = 'expired'
     WHERE status IN ('awaiting_proof', 'under_review')
       AND expires_at <= now()
    RETURNING 1
  )
  SELECT count(*) INTO v_n FROM upd;
  RETURN v_n;
END $$;
"""

_EXPIRE_NEW = """
CREATE OR REPLACE FUNCTION wallet.expire_stale_orders(p_store_id BIGINT DEFAULT NULL)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE v_n INT;
BEGIN
  WITH upd AS (
    UPDATE wallet.recharge_orders
       SET status = 'expired'
     WHERE status IN ('awaiting_proof', 'under_review')
       AND expires_at <= now()
       -- NULL = 总部：处理所有门店；否则只处理这一家
       AND (p_store_id IS NULL OR store_id = p_store_id)
    RETURNING 1
  )
  SELECT count(*) INTO v_n FROM upd;
  RETURN v_n;
END $$;
"""

START_RECHARGE_NEW = r"""
CREATE OR REPLACE FUNCTION wallet.start_recharge(
  p_customer_id  BIGINT,
  p_currency TEXT,
  p_amount   BIGINT,
  p_idem     TEXT,
  p_store_id BIGINT DEFAULT NULL   -- 收款门店（结算与门店隔离用）
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_order    wallet.recharge_orders;
  v_existing wallet.recharge_orders;
  v_bonus    BIGINT;
  v_rule     BIGINT;
  v_days     INT;
  v_open     INT;
  v_today    BIGINT;
BEGIN
  -- 幂等：同一请求重复提交返回同一张单
  SELECT * INTO v_existing FROM wallet.recharge_orders WHERE idempotency_key = p_idem;
  IF FOUND THEN
    RETURN v_existing;
  END IF;

  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 充值金额必须大于 0';
  END IF;
  IF p_amount < wallet.cfg_int('min_recharge_amount', 100) THEN
    RAISE EXCEPTION 'AMOUNT_TOO_SMALL: 最小充值 %', wallet.cfg_int('min_recharge_amount', 100);
  END IF;
  IF p_amount > wallet.cfg_int('max_recharge_amount', 50000) THEN
    RAISE EXCEPTION 'AMOUNT_TOO_LARGE: 单笔上限 %', wallet.cfg_int('max_recharge_amount', 50000);
  END IF;

  -- 未完成单数量限制，防止刷单
  SELECT count(*) INTO v_open
    FROM wallet.recharge_orders
   WHERE customer_id = p_customer_id
     AND status IN ('awaiting_proof', 'under_review');
  IF v_open >= wallet.cfg_int('max_open_orders', 3) THEN
    RAISE EXCEPTION 'TOO_MANY_OPEN_ORDERS: 未完成充值单过多，请先完成或等待过期';
  END IF;

  -- 当日累计充值上限（含待审核）
  SELECT COALESCE(sum(amount), 0) INTO v_today
    FROM wallet.recharge_orders
   WHERE customer_id = p_customer_id
     AND currency = p_currency
     AND status NOT IN ('rejected', 'expired', 'cancelled')
     AND created_at >= date_trunc('day', now());
  IF v_today + p_amount > wallet.cfg_int('daily_recharge_limit', 100000) THEN
    RAISE EXCEPTION 'DAILY_LIMIT_EXCEEDED: 超出单日充值上限';
  END IF;

  SELECT c.rule_id, c.bonus, c.valid_days INTO v_rule, v_bonus, v_days
    FROM wallet.calc_bonus(p_customer_id, p_currency, p_amount) c;

  -- 并发同 key 下单：唯一约束兜底，冲突方返回已存在的那张单（而不是抛 500）
  BEGIN
    INSERT INTO wallet.recharge_orders
      (order_no, customer_id, currency, amount, bonus_amount, bonus_rule_id,
       bonus_expire_at, expires_at, idempotency_key, store_id)
    VALUES
      ('RC' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD')
            || lpad(nextval('wallet.recharge_order_no_seq')::text, 8, '0'),
       p_customer_id, p_currency, p_amount, v_bonus, v_rule,
       CASE WHEN v_bonus > 0 AND v_days IS NOT NULL
            THEN now() + (v_days || ' days')::interval END,
       now() + (wallet.cfg_int('recharge_ttl_minutes', 1440) || ' minutes')::interval,
       p_idem,
       -- 未指定就落到主店，保证 NOT NULL 成立
       COALESCE(p_store_id, (SELECT id FROM public.stores ORDER BY id LIMIT 1)))
    RETURNING * INTO v_order;
  EXCEPTION WHEN unique_violation THEN
    SELECT * INTO v_order FROM wallet.recharge_orders WHERE idempotency_key = p_idem;
    IF NOT FOUND THEN RAISE; END IF;
  END;

  RETURN v_order;
END $$;
"""

START_RECHARGE_OLD = r"""
CREATE OR REPLACE FUNCTION wallet.start_recharge(
  p_customer_id  BIGINT,
  p_currency TEXT,
  p_amount   BIGINT,
  p_idem     TEXT
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_order    wallet.recharge_orders;
  v_existing wallet.recharge_orders;
  v_bonus    BIGINT;
  v_rule     BIGINT;
  v_days     INT;
  v_open     INT;
  v_today    BIGINT;
BEGIN
  -- 幂等：同一请求重复提交返回同一张单
  SELECT * INTO v_existing FROM wallet.recharge_orders WHERE idempotency_key = p_idem;
  IF FOUND THEN
    RETURN v_existing;
  END IF;

  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 充值金额必须大于 0';
  END IF;
  IF p_amount < wallet.cfg_int('min_recharge_amount', 100) THEN
    RAISE EXCEPTION 'AMOUNT_TOO_SMALL: 最小充值 %', wallet.cfg_int('min_recharge_amount', 100);
  END IF;
  IF p_amount > wallet.cfg_int('max_recharge_amount', 50000) THEN
    RAISE EXCEPTION 'AMOUNT_TOO_LARGE: 单笔上限 %', wallet.cfg_int('max_recharge_amount', 50000);
  END IF;

  -- 未完成单数量限制，防止刷单
  SELECT count(*) INTO v_open
    FROM wallet.recharge_orders
   WHERE customer_id = p_customer_id
     AND status IN ('awaiting_proof', 'under_review');
  IF v_open >= wallet.cfg_int('max_open_orders', 3) THEN
    RAISE EXCEPTION 'TOO_MANY_OPEN_ORDERS: 未完成充值单过多，请先完成或等待过期';
  END IF;

  -- 当日累计充值上限（含待审核）
  SELECT COALESCE(sum(amount), 0) INTO v_today
    FROM wallet.recharge_orders
   WHERE customer_id = p_customer_id
     AND currency = p_currency
     AND status NOT IN ('rejected', 'expired', 'cancelled')
     AND created_at >= date_trunc('day', now());
  IF v_today + p_amount > wallet.cfg_int('daily_recharge_limit', 100000) THEN
    RAISE EXCEPTION 'DAILY_LIMIT_EXCEEDED: 超出单日充值上限';
  END IF;

  SELECT c.rule_id, c.bonus, c.valid_days INTO v_rule, v_bonus, v_days
    FROM wallet.calc_bonus(p_customer_id, p_currency, p_amount) c;

  -- 并发同 key 下单：唯一约束兜底，冲突方返回已存在的那张单（而不是抛 500）
  BEGIN
    INSERT INTO wallet.recharge_orders
      (order_no, customer_id, currency, amount, bonus_amount, bonus_rule_id,
       bonus_expire_at, expires_at, idempotency_key)
    VALUES
      ('RC' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD')
            || lpad(nextval('wallet.recharge_order_no_seq')::text, 8, '0'),
       p_customer_id, p_currency, p_amount, v_bonus, v_rule,
       CASE WHEN v_bonus > 0 AND v_days IS NOT NULL
            THEN now() + (v_days || ' days')::interval END,
       now() + (wallet.cfg_int('recharge_ttl_minutes', 1440) || ' minutes')::interval,
       p_idem)
    RETURNING * INTO v_order;
  EXCEPTION WHEN unique_violation THEN
    SELECT * INTO v_order FROM wallet.recharge_orders WHERE idempotency_key = p_idem;
    IF NOT FOUND THEN RAISE; END IF;
  END;

  RETURN v_order;
END $$;
"""

#: 必然属于某家店的表（schema, table）
_NOT_NULL_TABLES = (
    ('public', 'orders'),
    ('public', 'payment_reviews'),
    ('public', 'payment_proofs'),
    ('wallet', 'recharge_orders'),
)


def upgrade() -> None:
    bind = op.get_bind()
    # 签名变了：先删掉零参版本，避免留下重载造成调用歧义
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS wallet.expire_stale_orders()")
    bind.exec_driver_sql(_EXPIRE_NEW)

    # recharge_orders.store_id 要加 NOT NULL，建单函数就必须能写进去；
    # 不加这一步，NOT NULL 一加，充值功能当场全挂。
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS wallet.start_recharge(BIGINT, TEXT, BIGINT, TEXT)")
    bind.exec_driver_sql(START_RECHARGE_NEW)

    # 加约束前先确认没有漏网的 NULL（有就报错并指出表名，而不是让 DDL 直接炸）
    for schema, table in _NOT_NULL_TABLES:
        missing = bind.exec_driver_sql(
            f"SELECT count(*) FROM {schema}.{table} WHERE store_id IS NULL"
        ).scalar()
        if missing:
            raise RuntimeError(
                f"{schema}.{table} 还有 {missing} 行 store_id 为空，先回填再加 NOT NULL")
        bind.exec_driver_sql(
            f"ALTER TABLE {schema}.{table} ALTER COLUMN store_id SET NOT NULL")


def downgrade() -> None:
    bind = op.get_bind()
    for schema, table in _NOT_NULL_TABLES:
        bind.exec_driver_sql(
            f"ALTER TABLE {schema}.{table} ALTER COLUMN store_id DROP NOT NULL")
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS wallet.expire_stale_orders(BIGINT)")
    bind.exec_driver_sql(_EXPIRE_OLD)
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS wallet.start_recharge(BIGINT, TEXT, BIGINT, TEXT, BIGINT)")
    bind.exec_driver_sql(START_RECHARGE_OLD)
