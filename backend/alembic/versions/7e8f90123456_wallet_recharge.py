"""wallet & recharge  (钱包 / 充值)

Revision ID: 7e8f90123456
Revises: 6d7e8f901234
Create Date: 2026-10-01 17:05:00.000000

在独立的 wallet schema 里建钱包模块：账本 + 钱包 + 充值单 + 赠送批次 + 资金函数。
与 public.customers / public.staff 通过外键关联。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '7e8f90123456'
down_revision = '6d7e8f901234'
branch_labels = None
depends_on = None


# 完整 DDL（表 + 函数 + 触发器 + 视图），以 `-- @@` 分隔单条语句。
#
# 为什么把整块 SQL 放进迁移而不是拆成 op.create_table：
#   1. 资金安全的核心是数据库函数（行锁 + 幂等键 + append-only 账本 + 权限校验），
#      用 ORM 表达 plpgsql 只会更难审计；
#   2. 迁移必须自包含、不可变——不依赖仓库里的外部 .sql 文件，
#      避免“改了文件但已升级的库没变”这种幻觉。
#   3. 数据库侧再也不允许任何人绕过函数改余额（见迁移里的账本触发器）。
WALLET_DDL = r"""-- ============================================================================
--  Tea+Cafe · 钱包 / 充值  (Wallet & Recharge)   PostgreSQL
--
--  这是 Tea+Cafe 订单系统的钱包模块，独立放在 wallet schema 里，
--  与应用的 public 表（customers / staff / orders ...）通过外键关联。
--
--  设计约定（不要破坏）:
--   1. 所有金额一律用「整数最小货币单位」存储（USD -> cents），
--      与 orders.total_minor / products.price_minor 同单位，绝不使用 float。
--   2. wallet.ledger_entries 是唯一事实来源，只增不改不删（有触发器强制）。
--      wallet.wallets 的余额列只是账本的物化结果。
--   3. 任何余额变动都必须经过 wallet._apply()，禁止业务代码直接 UPDATE wallet.wallets。
--   4. 所有涉及资金的操作都有 idempotency_key 唯一约束，重放不会重复入账。
--   5. 状态流转用「条件 UPDATE + 行锁」，并发下只有一个请求能成功。
--   6. 权限在函数内部再校验：审核到账 = 任意在职员工；调账/退款 = 仅 MANAGER。
--
--  该 DDL 由 alembic 迁移 7e8f90123456 原样执行；如需变更请新增迁移。
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS wallet;
-- @@
-- ---------------------------------------------------------------------------
-- 0. 通用工具
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION wallet.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;
-- @@
-- 账本不可篡改
CREATE OR REPLACE FUNCTION wallet.forbid_ledger_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'LEDGER_IMMUTABLE: 账本只允许 INSERT，禁止 % 操作', TG_OP
    USING ERRCODE = 'P0001';
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- 1. 钱包（每个用户每种货币一条）
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet.wallets (
  id                BIGSERIAL PRIMARY KEY,
  customer_id           BIGINT      NOT NULL REFERENCES public.customers(id),              -- public.customers.id
  currency          TEXT        NOT NULL DEFAULT 'USD',
  principal         BIGINT      NOT NULL DEFAULT 0,    -- 本金余额
  bonus             BIGINT      NOT NULL DEFAULT 0,    -- 赠送余额
  bonus_expire_at   TIMESTAMPTZ,                       -- 赠送余额过期时间 (NULL = 永不过期)
  frozen            BIGINT      NOT NULL DEFAULT 0,    -- 冻结（预留：提现/风控冻结）
  version           BIGINT      NOT NULL DEFAULT 0,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT wallets_customer_currency_uniq UNIQUE (customer_id, currency),
  CONSTRAINT wallets_currency_len CHECK (length(currency) = 3),
  CONSTRAINT wallets_non_negative
    CHECK (principal >= 0 AND bonus >= 0 AND frozen >= 0)
);
-- @@
CREATE INDEX IF NOT EXISTS wallets_customer_idx ON wallet.wallets (customer_id);
-- @@
DROP TRIGGER IF EXISTS trg_wallets_touch ON wallet.wallets;
-- @@
CREATE TRIGGER trg_wallets_touch BEFORE UPDATE ON wallet.wallets
  FOR EACH ROW EXECUTE FUNCTION wallet.touch_updated_at();
-- @@
-- ---------------------------------------------------------------------------
-- 2. 账本（append-only）
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet.ledger_entries (
  id              BIGSERIAL PRIMARY KEY,
  wallet_id       BIGINT      NOT NULL REFERENCES wallet.wallets(id),
  customer_id         BIGINT      NOT NULL REFERENCES public.customers(id),
  currency        TEXT        NOT NULL,
  bucket          TEXT        NOT NULL,                -- principal | bonus
  direction       SMALLINT    NOT NULL,                -- +1 入账 / -1 出账
  amount          BIGINT      NOT NULL,                -- 正数，符号看 direction
  balance_after   BIGINT      NOT NULL,                -- 该 bucket 变动后余额
  entry_type      TEXT        NOT NULL,                -- recharge | recharge_bonus | payment | refund
                                                       -- | admin_adjust | bonus_expire | withdraw
  biz_type        TEXT,                                -- recharge_order | order | manual
  biz_id          TEXT,
  idempotency_key TEXT        NOT NULL,
  operator_id     BIGINT REFERENCES public.staff(id),                              -- 员工操作时记录 public.staff.id
  remark          TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ledger_direction_ck CHECK (direction IN (1, -1)),
  CONSTRAINT ledger_amount_ck    CHECK (amount > 0),
  CONSTRAINT ledger_bucket_ck    CHECK (bucket IN ('principal', 'bonus')),
  CONSTRAINT ledger_idem_uniq    UNIQUE (idempotency_key)
);
-- @@
CREATE INDEX IF NOT EXISTS ledger_wallet_idx  ON wallet.ledger_entries (wallet_id, id DESC);
-- @@
CREATE INDEX IF NOT EXISTS ledger_biz_idx     ON wallet.ledger_entries (biz_type, biz_id);
-- @@
CREATE INDEX IF NOT EXISTS ledger_created_idx ON wallet.ledger_entries (created_at);
-- @@
DROP TRIGGER IF EXISTS trg_ledger_immutable ON wallet.ledger_entries;
-- @@
CREATE TRIGGER trg_ledger_immutable
  BEFORE UPDATE OR DELETE ON wallet.ledger_entries
  FOR EACH ROW EXECUTE FUNCTION wallet.forbid_ledger_mutation();
-- @@
-- ---------------------------------------------------------------------------
-- 3. 赠送金批次（lot）
--    为什么需要它：如果赠送余额只有一个「到期时间」字段，
--      · 永久赠送 + 30天赠送 混在一起时，新赠送会把「永久」改成会过期（用户资产被偷走）
--      · 也无法做到「先扣快到期的」，用户会白白损失
--    所以赠送余额按「批次」管理：每笔赠送入一个 lot，扣款时按到期日由近到远扣。
--    wallets.bonus 仍然是各 lot 剩余额的物化合计；wallets.bonus_expire_at = 最近的到期日。
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet.bonus_lots (
  id              BIGSERIAL PRIMARY KEY,
  wallet_id       BIGINT      NOT NULL REFERENCES wallet.wallets(id),
  customer_id         BIGINT      NOT NULL REFERENCES public.customers(id),
  currency        TEXT        NOT NULL,
  remaining       BIGINT      NOT NULL,          -- 该批次剩余
  original_amount BIGINT      NOT NULL,
  expire_at       TIMESTAMPTZ,                   -- NULL = 永久有效
  source_type     TEXT        NOT NULL,          -- recharge | refund | adjust
  source_id       TEXT,
  idempotency_key TEXT        NOT NULL UNIQUE,   -- 与账本流水同 key，双重防重复
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT lots_remaining_ck CHECK (remaining >= 0),
  CONSTRAINT lots_original_ck  CHECK (original_amount > 0),
  CONSTRAINT lots_fits_ck      CHECK (remaining <= original_amount),
  CONSTRAINT lots_source_ck    CHECK (source_type IN ('recharge', 'refund', 'adjust'))
);
-- @@
CREATE INDEX IF NOT EXISTS lots_consume_idx
  ON wallet.bonus_lots (wallet_id, expire_at NULLS LAST, id);
-- @@
CREATE INDEX IF NOT EXISTS lots_customer_idx ON wallet.bonus_lots (customer_id);
-- @@
-- ---------------------------------------------------------------------------
-- 4. 充值赠送规则
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet.recharge_rules (
  id                    BIGSERIAL PRIMARY KEY,
  name                  TEXT        NOT NULL,
  currency              TEXT        NOT NULL DEFAULT 'USD',
  min_amount            BIGINT      NOT NULL,          -- 最小充值额（含）
  max_amount            BIGINT,                        -- 最大充值额（含），NULL = 不限
  bonus_type            TEXT        NOT NULL,          -- fixed(固定金额) | percent(万分比)
  bonus_value           BIGINT      NOT NULL,          -- fixed: 金额; percent: 万分比 (1000 = 10%)
  max_bonus             BIGINT,                        -- 封顶
  bonus_valid_days      INT,                           -- 赠送金有效期，NULL = 永久
  per_user_limit_per_day INT,                          -- 每人每日可用次数，NULL = 不限
  priority              INT         NOT NULL DEFAULT 0,-- 越大越优先，取第一条命中的
  active                BOOLEAN     NOT NULL DEFAULT true,
  valid_from            TIMESTAMPTZ,
  valid_to              TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT rules_type_ck CHECK (bonus_type IN ('fixed', 'percent')),
  CONSTRAINT rules_value_ck CHECK (bonus_value >= 0),
  CONSTRAINT rules_min_ck CHECK (min_amount > 0),
  CONSTRAINT rules_range_ck CHECK (max_amount IS NULL OR max_amount >= min_amount)
);
-- @@
-- ---------------------------------------------------------------------------
-- 5. 配置（限制值，运营可调）
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet.config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- @@
INSERT INTO wallet.config (key, value) VALUES
  ('min_recharge_amount',   '100'),      -- 最小充值 1.00 USD
  ('max_recharge_amount',   '50000'),    -- 单笔上限 500.00 USD
  ('max_open_orders',       '3'),        -- 同时存在的未完成充值单上限
  ('daily_recharge_limit',  '100000'),   -- 单日累计充值上限 1000.00 USD
  ('recharge_ttl_minutes',  '1440'),     -- 充值单有效期 24h
  ('allow_self_approve',    '0'),        -- 0 = 禁止审核自己的单
  ('refund_bonus_valid_days','30')       -- 退款退回的赠送金重新给 30 天有效期
ON CONFLICT (key) DO NOTHING;
-- @@
CREATE OR REPLACE FUNCTION wallet.cfg_int(p_key TEXT, p_default BIGINT DEFAULT NULL)
RETURNS BIGINT LANGUAGE sql STABLE AS $$
  SELECT COALESCE((SELECT value::BIGINT FROM wallet.config WHERE key = p_key), p_default)
$$;
-- @@
-- ---------------------------------------------------------------------------
-- 6. 权限 / 审计
--    本模块不自己维护员工表，直接读应用已有的 public.staff：
--      · 审核充值到账 = 任意在职员工（与订单收款审核同口径：staff.active）
--      · 调账 / 退款   = 仅 MANAGER（wallet.can_adjust）
--    两者都在数据库函数内部再校验一次，应用层绕不过去。
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION wallet.is_reviewer(p_staff_id BIGINT) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.staff WHERE id = p_staff_id AND active IS TRUE
  )
$$;
-- @@
CREATE OR REPLACE FUNCTION wallet.is_order_owner_staff(
  p_staff_id BIGINT, p_customer_id BIGINT
) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1
      FROM public.staff s
      JOIN public.customers c ON c.id = p_customer_id
     WHERE s.id = p_staff_id
       AND s.telegram_user_id IS NOT NULL
       AND s.telegram_user_id = c.telegram_user_id
  )
$$;
-- @@
CREATE OR REPLACE FUNCTION wallet.can_adjust(p_staff_id BIGINT) RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.staff
     WHERE id = p_staff_id AND active IS TRUE AND role = 'MANAGER'
  )
$$;
-- @@
CREATE TABLE IF NOT EXISTS wallet.audit_log (
  id          BIGSERIAL PRIMARY KEY,
  actor_type  TEXT NOT NULL DEFAULT 'customer',   -- customer | staff | system
  actor_id    BIGINT,
  action      TEXT NOT NULL,                    -- approve_recharge | reject_recharge | adjust_balance | ...
  target_type TEXT,
  target_id   TEXT,
  before_data JSONB,
  after_data  JSONB,
  ip          TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- @@
CREATE INDEX IF NOT EXISTS audit_target_idx ON wallet.audit_log (target_type, target_id);
-- @@
CREATE INDEX IF NOT EXISTS audit_actor_idx  ON wallet.audit_log (actor_id, created_at DESC);
-- @@
-- ---------------------------------------------------------------------------
-- 7. 充值单
-- ---------------------------------------------------------------------------

DO $$ BEGIN
  CREATE TYPE wallet.recharge_status AS ENUM
    ('awaiting_proof', 'under_review', 'credited', 'rejected', 'expired', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
-- @@
CREATE SEQUENCE IF NOT EXISTS wallet.recharge_order_no_seq;
-- @@
CREATE TABLE IF NOT EXISTS wallet.recharge_orders (
  id              BIGSERIAL PRIMARY KEY,
  order_no        TEXT NOT NULL UNIQUE,                 -- 同时作为转账备注，便于人工对账
  customer_id         BIGINT NOT NULL REFERENCES public.customers(id),
  currency        TEXT NOT NULL DEFAULT 'USD',
  amount          BIGINT NOT NULL,                      -- 用户应付金额
  bonus_amount    BIGINT NOT NULL DEFAULT 0,            -- 下单时快照的赠送额
  bonus_rule_id   BIGINT REFERENCES wallet.recharge_rules(id),
  bonus_expire_at TIMESTAMPTZ,
  status          wallet.recharge_status NOT NULL DEFAULT 'awaiting_proof',
  pay_method      TEXT NOT NULL DEFAULT 'manual_transfer',
  pay_reference   TEXT,                                 -- 用户填写的银行流水号
  received_amount BIGINT,                               -- 审核通过时确认的实收金额（终值）
  pending_received_amount BIGINT,                       -- 审核前管理员暂存的实收金额
  proof_count     INT NOT NULL DEFAULT 0,
  submitted_at    TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ NOT NULL,
  reviewed_by     BIGINT REFERENCES public.staff(id),
  reviewed_at     TIMESTAMPTZ,
  reject_reason   TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  risk_flags      TEXT[] NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ro_amount_ck  CHECK (amount > 0),
  CONSTRAINT ro_bonus_ck   CHECK (bonus_amount >= 0)
);
-- @@
CREATE INDEX IF NOT EXISTS ro_status_idx ON wallet.recharge_orders (status, created_at);
-- @@
CREATE INDEX IF NOT EXISTS ro_customer_idx   ON wallet.recharge_orders (customer_id, created_at DESC);
-- @@
DROP TRIGGER IF EXISTS trg_ro_touch ON wallet.recharge_orders;
-- @@
CREATE TRIGGER trg_ro_touch BEFORE UPDATE ON wallet.recharge_orders
  FOR EACH ROW EXECUTE FUNCTION wallet.touch_updated_at();
-- @@
-- 老库升级用（新库执行是空操作）
ALTER TABLE wallet.recharge_orders
  ADD COLUMN IF NOT EXISTS pending_received_amount BIGINT;
-- @@
-- 支付凭证
CREATE TABLE IF NOT EXISTS wallet.recharge_proofs (
  id                BIGSERIAL PRIMARY KEY,
  order_id          BIGINT NOT NULL REFERENCES wallet.recharge_orders(id),
  tg_file_id        TEXT NOT NULL,
  tg_file_unique_id TEXT NOT NULL,
  storage_key       TEXT,                    -- 已转存到对象存储的 key
  sha256            TEXT,
  mime_type         TEXT,
  file_size         INT,
  uploaded_by       BIGINT NOT NULL REFERENCES public.customers(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- @@
-- 关键反作弊：同一张图（Telegram file_unique_id 相同）不能用于两个订单
CREATE UNIQUE INDEX IF NOT EXISTS proof_unique_file_idx
  ON wallet.recharge_proofs (tg_file_unique_id);
-- @@
CREATE INDEX IF NOT EXISTS proof_order_idx ON wallet.recharge_proofs (order_id);
-- @@
-- ---------------------------------------------------------------------------
-- 8. 余额支付记录（退款按原始拆分退回）
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS wallet.order_payments (
  id                 BIGSERIAL PRIMARY KEY,
  biz_id             TEXT NOT NULL UNIQUE,     -- 业务订单号
  customer_id            BIGINT NOT NULL REFERENCES public.customers(id),
  currency           TEXT NOT NULL DEFAULT 'USD',
  amount             BIGINT NOT NULL,
  bonus_used         BIGINT NOT NULL DEFAULT 0,
  principal_used     BIGINT NOT NULL DEFAULT 0,
  status             TEXT NOT NULL DEFAULT 'captured',  -- captured | refunded | partially_refunded
  refunded_amount    BIGINT NOT NULL DEFAULT 0,
  refunded_bonus     BIGINT NOT NULL DEFAULT 0,
  refunded_principal BIGINT NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT op_split_ck CHECK (bonus_used + principal_used = amount),
  CONSTRAINT op_refund_ck CHECK (refunded_amount <= amount),
  CONSTRAINT op_status_ck CHECK (status IN ('captured', 'partially_refunded', 'refunded'))
);
-- @@
DROP TRIGGER IF EXISTS trg_op_touch ON wallet.order_payments;
-- @@
CREATE TRIGGER trg_op_touch BEFORE UPDATE ON wallet.order_payments
  FOR EACH ROW EXECUTE FUNCTION wallet.touch_updated_at();
-- @@
-- ===========================================================================
--  核心函数
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- wallet._apply : 唯一允许改动余额的内部函数
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet._apply(
  p_customer_id  BIGINT,
  p_currency TEXT,
  p_bucket   TEXT,
  p_delta    BIGINT,
  p_entry_type TEXT,
  p_biz_type TEXT,
  p_biz_id   TEXT,
  p_idem     TEXT,
  p_operator BIGINT DEFAULT NULL,
  p_remark   TEXT DEFAULT NULL
) RETURNS wallet.ledger_entries
LANGUAGE plpgsql AS $$
DECLARE
  v_wallet wallet.wallets;
  v_new    BIGINT;
  v_entry  wallet.ledger_entries;
BEGIN
  IF p_delta = 0 THEN
    RAISE EXCEPTION 'ZERO_DELTA: 金额不能为 0';
  END IF;
  IF p_bucket NOT IN ('principal', 'bonus') THEN
    RAISE EXCEPTION 'BAD_BUCKET: %', p_bucket;
  END IF;

  -- 幂等：同一 idempotency_key 直接返回既有流水，绝不重复入账
  SELECT * INTO v_entry FROM wallet.ledger_entries WHERE idempotency_key = p_idem;
  IF FOUND THEN
    RETURN v_entry;
  END IF;

  -- UPSERT 同时完成「建钱包」与「行锁」
  INSERT INTO wallet.wallets AS w (customer_id, currency)
  VALUES (p_customer_id, p_currency)
  ON CONFLICT (customer_id, currency) DO UPDATE SET updated_at = w.updated_at
  RETURNING * INTO v_wallet;

  IF p_bucket = 'principal' THEN
    v_new := v_wallet.principal + p_delta;
    IF v_new < 0 THEN
      RAISE EXCEPTION 'INSUFFICIENT_FUNDS: 本金余额不足' USING ERRCODE = 'P0001';
    END IF;
    UPDATE wallet.wallets SET principal = v_new, version = version + 1
     WHERE id = v_wallet.id;
  ELSE
    v_new := v_wallet.bonus + p_delta;
    IF v_new < 0 THEN
      RAISE EXCEPTION 'INSUFFICIENT_FUNDS: 赠送余额不足' USING ERRCODE = 'P0001';
    END IF;
    UPDATE wallet.wallets SET bonus = v_new, version = version + 1
     WHERE id = v_wallet.id;
  END IF;

  INSERT INTO wallet.ledger_entries
    (wallet_id, customer_id, currency, bucket, direction, amount, balance_after,
     entry_type, biz_type, biz_id, idempotency_key, operator_id, remark)
  VALUES
    (v_wallet.id, p_customer_id, p_currency, p_bucket,
     CASE WHEN p_delta > 0 THEN 1 ELSE -1 END, abs(p_delta), v_new,
     p_entry_type, p_biz_type, p_biz_id, p_idem, p_operator, p_remark)
  RETURNING * INTO v_entry;

  RETURN v_entry;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.refresh_bonus_expiry : 把 wallets.bonus_expire_at 同步为「最近的批次到期日」
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.refresh_bonus_expiry(p_customer_id BIGINT, p_currency TEXT)
RETURNS VOID LANGUAGE sql AS $$
  UPDATE wallet.wallets w
     SET bonus_expire_at = (
           SELECT min(l.expire_at) FROM wallet.bonus_lots l
            WHERE l.wallet_id = w.id AND l.remaining > 0 AND l.expire_at IS NOT NULL)
   WHERE w.customer_id = p_customer_id AND w.currency = p_currency
$$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.add_bonus_lot : 赠送入账（同时写账本 + 建批次，同一 idempotency_key 双保险）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.add_bonus_lot(
  p_customer_id     BIGINT,
  p_currency    TEXT,
  p_amount      BIGINT,
  p_expire_at   TIMESTAMPTZ,          -- NULL = 永久
  p_source_type TEXT,                 -- recharge | refund | adjust
  p_source_id   TEXT,
  p_idem        TEXT,
  p_entry_type  TEXT DEFAULT 'recharge_bonus',
  p_operator    BIGINT DEFAULT NULL,
  p_remark      TEXT DEFAULT NULL
) RETURNS wallet.bonus_lots
LANGUAGE plpgsql AS $$
DECLARE v_lot wallet.bonus_lots; v_wallet_id BIGINT;
BEGIN
  IF p_amount <= 0 THEN
    RAISE EXCEPTION 'BAD_AMOUNT: 赠送金额必须大于 0';
  END IF;

  SELECT * INTO v_lot FROM wallet.bonus_lots WHERE idempotency_key = p_idem;
  IF FOUND THEN RETURN v_lot; END IF;          -- 幂等

  PERFORM wallet._apply(p_customer_id, p_currency, 'bonus', p_amount,
    p_entry_type, p_source_type, p_source_id, p_idem, p_operator, p_remark);

  SELECT id INTO v_wallet_id FROM wallet.wallets
   WHERE customer_id = p_customer_id AND currency = p_currency;

  INSERT INTO wallet.bonus_lots
    (wallet_id, customer_id, currency, remaining, original_amount, expire_at,
     source_type, source_id, idempotency_key)
  VALUES (v_wallet_id, p_customer_id, p_currency, p_amount, p_amount, p_expire_at,
          p_source_type, p_source_id, p_idem)
  RETURNING * INTO v_lot;

  PERFORM wallet.refresh_bonus_expiry(p_customer_id, p_currency);
  RETURN v_lot;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.consume_bonus_lots : 扣赠送余额，按到期日由近到远（快过期的先扣）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.consume_bonus_lots(
  p_customer_id    BIGINT,
  p_currency   TEXT,
  p_amount     BIGINT,
  p_biz_type   TEXT,
  p_biz_id     TEXT,
  p_idem       TEXT,
  p_entry_type TEXT DEFAULT 'payment',
  p_operator   BIGINT DEFAULT NULL,
  p_remark     TEXT DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
  r RECORD;
  v_left      BIGINT := p_amount;
  v_take      BIGINT;
  v_wallet_id BIGINT;
BEGIN
  IF p_amount < 0 THEN RAISE EXCEPTION 'BAD_AMOUNT'; END IF;
  IF p_amount = 0 THEN RETURN 0; END IF;

  SELECT id INTO v_wallet_id FROM wallet.wallets
   WHERE customer_id = p_customer_id AND currency = p_currency FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'INSUFFICIENT_FUNDS: 赠送余额不足' USING ERRCODE = 'P0001';
  END IF;

  FOR r IN
    SELECT * FROM wallet.bonus_lots
     WHERE wallet_id = v_wallet_id AND remaining > 0
     ORDER BY expire_at NULLS LAST, id          -- 快过期的先扣，永久的最后扣
     FOR UPDATE
  LOOP
    v_take := LEAST(r.remaining, v_left);
    UPDATE wallet.bonus_lots SET remaining = remaining - v_take WHERE id = r.id;
    v_left := v_left - v_take;
    EXIT WHEN v_left = 0;
  END LOOP;

  IF v_left > 0 THEN
    -- 独立错误码：这是「批次与余额脱钩」的数据异常，不是普通的余额不足，
    -- 不能提示用户「去充值」，而应该触发对账告警。
    RAISE EXCEPTION 'BONUS_LOT_MISMATCH: 赠送批次与钱包余额不一致（数据异常，请对账）'
      USING ERRCODE = 'P0001';
  END IF;

  PERFORM wallet._apply(p_customer_id, p_currency, 'bonus', -p_amount,
    p_entry_type, p_biz_type, p_biz_id, p_idem, p_operator, p_remark);

  PERFORM wallet.refresh_bonus_expiry(p_customer_id, p_currency);
  RETURN p_amount;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.calc_bonus : 按规则计算赠送额
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.calc_bonus(
  p_customer_id BIGINT, p_currency TEXT, p_amount BIGINT
) RETURNS TABLE (rule_id BIGINT, bonus BIGINT, valid_days INT)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r            RECORD;
  v_bonus      BIGINT;
  v_used_today INT;
BEGIN
  FOR r IN
    SELECT * FROM wallet.recharge_rules ru
     WHERE ru.active
       AND ru.currency = p_currency
       AND p_amount >= ru.min_amount
       AND (ru.max_amount IS NULL OR p_amount <= ru.max_amount)
       AND (ru.valid_from IS NULL OR ru.valid_from <= now())
       AND (ru.valid_to   IS NULL OR ru.valid_to   >  now())
     ORDER BY ru.priority DESC, ru.id DESC
  LOOP
    IF r.per_user_limit_per_day IS NOT NULL THEN
      SELECT count(*) INTO v_used_today
        FROM wallet.recharge_orders o
       WHERE o.customer_id = p_customer_id
         AND o.bonus_rule_id = r.id
         AND o.status = 'credited'
         AND o.reviewed_at >= date_trunc('day', now());
      IF v_used_today >= r.per_user_limit_per_day THEN
        CONTINUE;
      END IF;
    END IF;

    v_bonus := CASE
                 WHEN r.bonus_type = 'fixed' THEN r.bonus_value
                 ELSE (p_amount * r.bonus_value) / 10000
               END;
    IF r.max_bonus IS NOT NULL THEN
      v_bonus := LEAST(v_bonus, r.max_bonus);
    END IF;

    rule_id := r.id; bonus := v_bonus; valid_days := r.bonus_valid_days;
    RETURN NEXT; RETURN;
  END LOOP;

  rule_id := NULL; bonus := 0; valid_days := NULL;
  RETURN NEXT;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.start_recharge : 创建充值单
-- ---------------------------------------------------------------------------
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
-- @@
-- ---------------------------------------------------------------------------
-- wallet.submit_proof : 用户上传转账凭证
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.submit_proof(
  p_order_id BIGINT,
  p_customer_id  BIGINT,
  p_file_id  TEXT,
  p_file_uid TEXT,
  p_mime     TEXT DEFAULT NULL,
  p_size     INT  DEFAULT NULL,
  p_storage  TEXT DEFAULT NULL,
  p_sha256   TEXT DEFAULT NULL,
  p_reference TEXT DEFAULT NULL
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE
  v_order wallet.recharge_orders;
BEGIN
  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ORDER_NOT_FOUND';
  END IF;
  IF v_order.customer_id <> p_customer_id THEN
    RAISE EXCEPTION 'NOT_ORDER_OWNER: 只能为自己的充值单上传凭证';
  END IF;
  IF v_order.status NOT IN ('awaiting_proof', 'under_review') THEN
    RAISE EXCEPTION 'ORDER_STATE: 当前状态 % 不能上传凭证', v_order.status;
  END IF;
  IF v_order.expires_at <= now() THEN
    RAISE EXCEPTION 'ORDER_EXPIRED: 充值单已过期，请重新发起';
  END IF;

  BEGIN
    INSERT INTO wallet.recharge_proofs
      (order_id, tg_file_id, tg_file_unique_id, storage_key, sha256,
       mime_type, file_size, uploaded_by)
    VALUES
      (p_order_id, p_file_id, p_file_uid, p_storage, p_sha256,
       p_mime, p_size, p_customer_id);
  EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'PROOF_DUPLICATE: 该凭证图片已被使用过';
  END;

  UPDATE wallet.recharge_orders
     SET status = 'under_review',
         submitted_at = COALESCE(submitted_at, now()),
         proof_count = proof_count + 1,
         pay_reference = COALESCE(p_reference, pay_reference)
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  RETURN v_order;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.approve_recharge : 管理员审核通过并入账（幂等 + 并发安全）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.approve_recharge(
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
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.set_received_amount : 审核前暂存「实收金额」（改实收金额按钮）
-- ---------------------------------------------------------------------------
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
-- @@
-- ---------------------------------------------------------------------------
-- wallet.cancel_recharge : 用户自行取消尚未提交凭证的充值单
--   没有这个函数会有一个实际运营问题：max_open_orders 默认 3，
--   用户误建 3 张单又不转账，就会被卡住 24 小时无法再充值。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.cancel_recharge(
  p_order_id BIGINT,
  p_customer_id  BIGINT
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE v_order wallet.recharge_orders;
BEGIN
  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'ORDER_NOT_FOUND';
  END IF;
  IF v_order.customer_id <> p_customer_id THEN
    RAISE EXCEPTION 'NOT_ORDER_OWNER: 只能取消自己的充值单';
  END IF;
  IF v_order.status = 'cancelled' THEN
    RETURN v_order;                                   -- 幂等
  END IF;
  IF v_order.status <> 'awaiting_proof' THEN
    RAISE EXCEPTION 'ORDER_STATE: 已提交凭证的充值单不能自行取消，请联系客服';
  END IF;

  UPDATE wallet.recharge_orders
     SET status = 'cancelled'
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
  VALUES ('customer', p_customer_id, 'cancel_recharge', 'recharge_order', p_order_id::text,
          jsonb_build_object('status', 'cancelled'));

  RETURN v_order;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.reject_recharge
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.reject_recharge(
  p_order_id BIGINT, p_staff_id BIGINT, p_reason TEXT
) RETURNS wallet.recharge_orders
LANGUAGE plpgsql AS $$
DECLARE v_order wallet.recharge_orders;
BEGIN
  IF p_reason IS NULL OR btrim(p_reason) = '' THEN
    RAISE EXCEPTION 'REASON_REQUIRED: 驳回必须填写原因';
  END IF;

  SELECT * INTO v_order FROM wallet.recharge_orders WHERE id = p_order_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ORDER_NOT_FOUND'; END IF;
  IF v_order.status = 'rejected' THEN RETURN v_order; END IF;
  IF v_order.status NOT IN ('awaiting_proof', 'under_review') THEN
    RAISE EXCEPTION 'ORDER_STATE: 当前状态 % 不能驳回', v_order.status;
  END IF;
  IF NOT wallet.is_reviewer(p_staff_id) THEN
    RAISE EXCEPTION 'NOT_REVIEWER: 无审核权限';
  END IF;
  IF wallet.cfg_int('allow_self_approve', 0) <> 1
     AND wallet.is_order_owner_staff(p_staff_id, v_order.customer_id) THEN
    RAISE EXCEPTION 'SELF_APPROVE_FORBIDDEN: 不能审核（通过或驳回）自己的充值单';
  END IF;

  UPDATE wallet.recharge_orders
     SET status = 'rejected', reject_reason = p_reason,
         reviewed_by = p_staff_id, reviewed_at = now()
   WHERE id = p_order_id
  RETURNING * INTO v_order;

  INSERT INTO wallet.audit_log (actor_type, actor_id, action, target_type, target_id, after_data)
  VALUES ('staff', p_staff_id, 'reject_recharge', 'recharge_order', p_order_id::text,
          jsonb_build_object('reason', p_reason));

  RETURN v_order;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.expire_bonus / expire_stale_orders : 定时任务调用
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.expire_bonus(p_customer_id BIGINT, p_currency TEXT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE r RECORD; v_total BIGINT := 0;
BEGIN
  -- 逐批次处理：只有真正到期的批次被清零，永久批次与其他未到期批次不受影响
  FOR r IN
    SELECT * FROM wallet.bonus_lots l
     WHERE l.customer_id = p_customer_id AND l.currency = p_currency
       AND l.remaining > 0
       AND l.expire_at IS NOT NULL AND l.expire_at <= now()
     ORDER BY l.id
     FOR UPDATE
  LOOP
    PERFORM wallet._apply(
      p_customer_id, p_currency, 'bonus', -r.remaining,
      'bonus_expire', 'expiry', r.id::text,
      'bonus-expire:lot:' || r.id,
      NULL, '赠送金批次到期清零');
    UPDATE wallet.bonus_lots SET remaining = 0 WHERE id = r.id;
    v_total := v_total + r.remaining;
  END LOOP;

  IF v_total > 0 THEN
    PERFORM wallet.refresh_bonus_expiry(p_customer_id, p_currency);
  END IF;
  RETURN v_total;
END $$;
-- @@
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
-- @@
-- ---------------------------------------------------------------------------
-- wallet.spend_balance : 用余额支付订单（赠送优先扣）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.spend_balance(
  p_customer_id  BIGINT,
  p_currency TEXT,
  p_amount   BIGINT,
  p_biz_id   TEXT,
  p_idem     TEXT,
  p_remark   TEXT DEFAULT NULL
) RETURNS wallet.order_payments
LANGUAGE plpgsql AS $$
DECLARE
  v_wallet    wallet.wallets;
  v_pay       wallet.order_payments;
  v_bonus     BIGINT;
  v_principal BIGINT;
BEGIN
  -- 快路径幂等（无锁）
  SELECT * INTO v_pay FROM wallet.order_payments WHERE biz_id = p_biz_id;
  IF FOUND THEN RETURN v_pay; END IF;

  IF p_amount <= 0 THEN RAISE EXCEPTION 'BAD_AMOUNT'; END IF;

  -- 先拿钱包行锁：同一用户的所有资金操作在此串行化
  PERFORM wallet.expire_bonus(p_customer_id, p_currency);

  SELECT * INTO v_wallet FROM wallet.wallets
   WHERE customer_id = p_customer_id AND currency = p_currency FOR UPDATE;
  IF NOT FOUND OR (v_wallet.principal + v_wallet.bonus) < p_amount THEN
    RAISE EXCEPTION 'INSUFFICIENT_FUNDS: 余额不足' USING ERRCODE = 'P0001';
  END IF;

  -- 拿到锁后再次检查：并发重复请求在此返回既有支付记录，不会二次扣款
  SELECT * INTO v_pay FROM wallet.order_payments WHERE biz_id = p_biz_id;
  IF FOUND THEN RETURN v_pay; END IF;

  v_bonus     := LEAST(v_wallet.bonus, p_amount);
  v_principal := p_amount - v_bonus;

  IF v_bonus > 0 THEN
    PERFORM wallet.consume_bonus_lots(p_customer_id, p_currency, v_bonus,
      'order', p_biz_id, p_idem || ':bonus', 'payment', NULL, p_remark);
  END IF;
  IF v_principal > 0 THEN
    PERFORM wallet._apply(p_customer_id, p_currency, 'principal', -v_principal,
      'payment', 'order', p_biz_id, p_idem || ':principal', NULL, p_remark);
  END IF;

  INSERT INTO wallet.order_payments
    (biz_id, customer_id, currency, amount, bonus_used, principal_used)
  VALUES (p_biz_id, p_customer_id, p_currency, p_amount, v_bonus, v_principal)
  RETURNING * INTO v_pay;

  RETURN v_pay;
END $$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.refund_payment : 退款回钱包（按原支付拆分退回）
-- ---------------------------------------------------------------------------
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
-- @@
-- ---------------------------------------------------------------------------
-- wallet.adjust_balance : 管理员手动调账（必须填原因）
-- ---------------------------------------------------------------------------
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
-- @@
-- ---------------------------------------------------------------------------
-- wallet.get_summary / list_entries : 查询
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.get_summary(p_customer_id BIGINT, p_currency TEXT)
RETURNS TABLE (customer_id BIGINT, currency TEXT, principal BIGINT, bonus BIGINT,
               total BIGINT, bonus_expire_at TIMESTAMPTZ)
LANGUAGE sql STABLE AS $$
  SELECT w.customer_id, w.currency, w.principal, w.bonus,
         w.principal + w.bonus, w.bonus_expire_at
    FROM wallet.wallets w
   WHERE w.customer_id = p_customer_id AND w.currency = p_currency
$$;
-- @@
-- ---------------------------------------------------------------------------
-- wallet.reconcile : 对账 —— 余额必须等于账本累加
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION wallet.reconcile()
RETURNS TABLE (wallet_id BIGINT, customer_id BIGINT, currency TEXT,
               bucket TEXT, column_value BIGINT, ledger_value BIGINT)
LANGUAGE sql STABLE AS $$
  WITH agg AS (
    SELECT wallet_id, bucket,
           sum(CASE WHEN direction = 1 THEN amount ELSE -amount END) AS v
      FROM wallet.ledger_entries
     GROUP BY wallet_id, bucket
  )
  SELECT w.id, w.customer_id, w.currency, 'principal', w.principal,
         COALESCE(a.v, 0)
    FROM wallet.wallets w
    LEFT JOIN agg a ON a.wallet_id = w.id AND a.bucket = 'principal'
   WHERE w.principal <> COALESCE(a.v, 0)
  UNION ALL
  SELECT w.id, w.customer_id, w.currency, 'bonus', w.bonus, COALESCE(a.v, 0)
    FROM wallet.wallets w
    LEFT JOIN agg a ON a.wallet_id = w.id AND a.bucket = 'bonus'
   WHERE w.bonus <> COALESCE(a.v, 0)
  UNION ALL
  -- 第三重校验：赠送余额必须等于各批次剩余额之和
  SELECT w.id, w.customer_id, w.currency, 'bonus_lots', w.bonus,
         COALESCE((SELECT sum(l.remaining) FROM wallet.bonus_lots l
                    WHERE l.wallet_id = w.id), 0)
    FROM wallet.wallets w
   WHERE w.bonus <> COALESCE((SELECT sum(l.remaining) FROM wallet.bonus_lots l
                               WHERE l.wallet_id = w.id), 0)
$$;
-- @@
-- ---------------------------------------------------------------------------
-- 常用视图
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW wallet.v_pending_recharges AS
  SELECT o.id, o.order_no, o.customer_id, o.currency, o.amount, o.bonus_amount,
         o.status, o.proof_count, o.submitted_at, o.expires_at,
         o.pay_reference, o.risk_flags, o.created_at
    FROM wallet.recharge_orders o
   WHERE o.status IN ('awaiting_proof', 'under_review')
   ORDER BY o.submitted_at NULLS LAST, o.created_at;
-- @@
CREATE OR REPLACE VIEW wallet.v_daily_recharge_report AS
  SELECT date_trunc('day', o.reviewed_at) AS day,
         o.currency,
         count(*)                         AS orders,
         sum(o.received_amount)           AS principal_total,
         sum(o.bonus_amount)              AS bonus_total,
         o.reviewed_by
    FROM wallet.recharge_orders o
   WHERE o.status = 'credited' AND o.reviewed_at IS NOT NULL
   GROUP BY 1, 2, 6
   ORDER BY 1 DESC;
"""


def _statements():
    """把 DDL 拆成单条语句。

    不能按 `;` 分割：plpgsql 函数体里满是 `;`。
    所以在生成过程中已经用“识别 dollar-quote / 字符串 / 注释”的扫描器
    把每条语句用 `-- @@` 隔开，这里只做一次 split。
    """
    return [s.strip() for s in WALLET_DDL.split("-- @@") if s.strip()]


def upgrade() -> None:
    bind = op.get_bind()
    for statement in _statements():
        bind.exec_driver_sql(statement)


def downgrade() -> None:
    # 整个模块都在 wallet schema 里，且没有其它表引用它，可以整体删。
    op.execute("DROP SCHEMA IF EXISTS wallet CASCADE")
