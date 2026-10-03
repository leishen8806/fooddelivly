"""wallet-first order payment with ABA remainder

Revision ID: i0123456789b
Revises: h0123456789a
Create Date: 2026-10-03 16:30:00

Orders can now use the available wallet balance first and leave only the
remainder for manual ABA payment. The database function locks the wallet row
and records the actual wallet portion in the existing order_payments ledger.
"""
from alembic import op
import sqlalchemy as sa


revision = "i0123456789b"
down_revision = "h0123456789a"
branch_labels = None
depends_on = None


SPEND_UP_TO = r"""
CREATE OR REPLACE FUNCTION wallet.spend_up_to(
  p_customer_id BIGINT,
  p_currency TEXT,
  p_amount BIGINT,
  p_biz_id TEXT,
  p_idem TEXT,
  p_remark TEXT DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
  v_wallet wallet.wallets;
  v_pay wallet.order_payments;
  v_spend BIGINT;
  v_bonus BIGINT;
  v_principal BIGINT;
BEGIN
  IF p_amount <= 0 THEN RETURN 0; END IF;

  -- A retry for the same order must return the original wallet portion.
  SELECT * INTO v_pay FROM wallet.order_payments WHERE biz_id = p_biz_id;
  IF FOUND THEN RETURN v_pay.amount; END IF;

  PERFORM wallet.expire_bonus(p_customer_id, p_currency);
  SELECT * INTO v_wallet FROM wallet.wallets
   WHERE customer_id = p_customer_id AND currency = p_currency FOR UPDATE;
  IF NOT FOUND THEN RETURN 0; END IF;

  v_spend := LEAST(p_amount, GREATEST(v_wallet.principal + v_wallet.bonus, 0));
  IF v_spend <= 0 THEN RETURN 0; END IF;

  -- Check again after acquiring the lock for concurrent idempotent requests.
  SELECT * INTO v_pay FROM wallet.order_payments WHERE biz_id = p_biz_id;
  IF FOUND THEN RETURN v_pay.amount; END IF;

  v_bonus := LEAST(v_wallet.bonus, v_spend);
  v_principal := v_spend - v_bonus;

  IF v_bonus > 0 THEN
    PERFORM wallet.consume_bonus_lots(
      p_customer_id, p_currency, v_bonus,
      'order', p_biz_id, p_idem || '-bonus', 'payment', NULL, p_remark
    );
  END IF;
  IF v_principal > 0 THEN
    PERFORM wallet._apply(
      p_customer_id, p_currency, 'principal', -v_principal,
      'payment', 'order', p_biz_id, p_idem || '-principal', NULL, p_remark
    );
  END IF;

  INSERT INTO wallet.order_payments
    (biz_id, customer_id, currency, amount, bonus_used, principal_used)
  VALUES (p_biz_id, p_customer_id, p_currency, v_spend, v_bonus, v_principal);

  RETURN v_spend;
END $$;
"""


def upgrade() -> None:
    op.add_column("orders", sa.Column("wallet_paid_minor", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("external_due_minor", sa.BigInteger(), nullable=False, server_default="0"))
    # Preserve the payment split for orders created before this migration.
    op.execute("UPDATE orders SET wallet_paid_minor = total_minor, external_due_minor = 0 WHERE payment_method = 'WALLET'")
    op.execute("UPDATE orders SET external_due_minor = total_minor WHERE payment_method = 'MANUAL'")
    op.execute(SPEND_UP_TO)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS wallet.spend_up_to(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)")
    op.drop_column("orders", "external_due_minor")
    op.drop_column("orders", "wallet_paid_minor")
