"""wallet summary: frozen / balance / available

Revision ID: af0123456789
Revises: 9f9012345678
Create Date: 2026-10-01 18:00:00.000000

钱包要区分两个数：
    余额 = 本金 + 赠送 + 冻结     （账户总额）
    可用 = 本金 + 赠送            （现在能花的）
`wallets.frozen` 早就预留了，这里只是把它暴露到 get_summary 里。
注意：`total` 保持原语义（= 本金 + 赠送 = 可用），避免影响已有调用方。

RETURNS TABLE 的列变了，CREATE OR REPLACE 改不了返回类型，必须先 DROP。
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'af0123456789'
down_revision = '9f9012345678'
branch_labels = None
depends_on = None


_NEW_BODY = """
CREATE FUNCTION wallet.get_summary(p_customer_id BIGINT, p_currency TEXT)
RETURNS TABLE (customer_id BIGINT, currency TEXT, principal BIGINT, bonus BIGINT,
               frozen BIGINT, total BIGINT, balance BIGINT, bonus_expire_at TIMESTAMPTZ)
LANGUAGE sql STABLE AS $$
  SELECT w.customer_id,
         w.currency,
         w.principal,
         w.bonus,
         w.frozen,
         w.principal + w.bonus,                 -- total   = 可用（能消费的部分）
         w.principal + w.bonus + w.frozen,      -- balance = 余额（含被冻结的部分）
         w.bonus_expire_at
    FROM wallet.wallets w
   WHERE w.customer_id = p_customer_id AND w.currency = p_currency
$$"""

_OLD_BODY = """
CREATE FUNCTION wallet.get_summary(p_customer_id BIGINT, p_currency TEXT)
RETURNS TABLE (customer_id BIGINT, currency TEXT, principal BIGINT, bonus BIGINT,
               total BIGINT, bonus_expire_at TIMESTAMPTZ)
LANGUAGE sql STABLE AS $$
  SELECT w.customer_id, w.currency, w.principal, w.bonus,
         w.principal + w.bonus, w.bonus_expire_at
    FROM wallet.wallets w
   WHERE w.customer_id = p_customer_id AND w.currency = p_currency
$$"""


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS wallet.get_summary(BIGINT, TEXT)")
    op.execute(_NEW_BODY)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS wallet.get_summary(BIGINT, TEXT)")
    op.execute(_OLD_BODY)
