"""multi-store foundation: merchants, stores, store_id columns, product overrides

Revision ID: g0123456789f
Revises: f0123456789e
Create Date: 2026-10-02 10:10:00.000000

多商家的地基（P0）。已确认的方案：

  * 1 个 bot，客户由二维码/深链带 store_id 绑定
  * 菜单「模板 + 覆盖」：products 属总部，各店用 store_product_overrides
    覆盖价格/上下架/排序/售卖时间
  * 钱包余额**全局通用**（所以 wallets 不加 store_id），跨店消费在 P4 用
    store_interstore_entries 记内部往来
  * 每店配置抽成（万分比 + 固定费）与结算周期，结算单由加盟商在后台确认

本迁移只做三件事，**不改任何现有行为**：

  1. 建 merchants / stores，并把现有 store_settings 的值复制进「主店」（id=1）；
  2. 给业务表加**可空** store_id 并全部回填成主店（P2 再收紧为 NOT NULL）；
  3. 建 store_product_overrides（覆盖表为空 = 与总部菜单完全一致）。

先不做全量读路径隔离——那是 P1/P2。半隔离状态本身是危险的，所以这里
刻意保持「可空 + 默认主店」，等隔离写完再收紧约束。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g0123456789f'
down_revision = 'f0123456789e'
branch_labels = None
depends_on = None


#: 需要加 store_id 的业务表（public 与 wallet 两个 schema）
_PUBLIC_TABLES = (
    'customers', 'staff', 'categories', 'products', 'orders',
    'payment_reviews', 'payment_proofs', 'audit_logs', 'report_deliveries',
)
_WALLET_TABLES = ('recharge_orders', 'ledger_entries')


def upgrade() -> None:
    op.create_table(
        'merchants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('contact', sa.String(), nullable=True),
        sa.Column('settlement_note', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='merchants_code_uniq'),
    )

    op.create_table(
        'stores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('merchant_id', sa.Integer(), sa.ForeignKey('merchants.id'), nullable=True),
        sa.Column('name', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='ACTIVE'),
        # 经营参数（从全局 store_settings 下移到店）
        sa.Column('currency', sa.String(), nullable=False, server_default='USD'),
        sa.Column('timezone', sa.String(), nullable=False, server_default='Asia/Phnom_Penh'),
        sa.Column('payment_link', sa.String(), nullable=True),
        sa.Column('aba_qr_asset_key', sa.String(), nullable=True),
        sa.Column('telegram_staff_group_id', sa.String(), nullable=True),
        sa.Column('staff_group_language', sa.String(), nullable=False, server_default='en'),
        sa.Column('is_accepting_orders', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('business_hours', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('min_order_minor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('delivery_fee_minor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('service_fee_minor', sa.Integer(), nullable=False, server_default='0'),
        # 加盟商结算
        sa.Column('commission_bps', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('commission_fixed_minor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('settlement_cycle', sa.String(), nullable=False, server_default='DAILY'),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='stores_code_uniq'),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'CLOSED')", name='stores_status_ck'),
        sa.CheckConstraint("settlement_cycle IN ('DAILY', 'WEEKLY')", name='stores_cycle_ck'),
    )

    # 主店：把现有全局设置整体复制过来，保证升级前后行为一致
    op.execute(
        """
        INSERT INTO stores (code, name, currency, timezone, payment_link, aba_qr_asset_key,
                            telegram_staff_group_id, staff_group_language, is_accepting_orders,
                            business_hours, min_order_minor, delivery_fee_minor, service_fee_minor)
        SELECT 'MAIN', '{"en": "Main store", "zh-CN": "总店"}'::json,
               COALESCE(s.currency, 'USD'), COALESCE(s.timezone, 'Asia/Phnom_Penh'),
               s.payment_link, s.aba_qr_asset_key, s.telegram_staff_group_id,
               COALESCE(s.staff_group_language, 'en'),
               COALESCE(s.is_accepting_orders, true),
               COALESCE(s.business_hours, '[]'::json),
               COALESCE(s.min_order_minor, 0), COALESCE(s.delivery_fee_minor, 0),
               COALESCE(s.service_fee_minor, 0)
          FROM store_settings s
         ORDER BY s.id
         LIMIT 1
        """
    )
    # 没有 store_settings 行（全新库）时也要有主店
    op.execute(
        """
        INSERT INTO stores (code, name)
        SELECT 'MAIN', '{"en": "Main store", "zh-CN": "总店"}'::json
         WHERE NOT EXISTS (SELECT 1 FROM stores)
        """
    )

    for table in _PUBLIC_TABLES:
        op.add_column(table, sa.Column('store_id', sa.Integer(),
                                       sa.ForeignKey('stores.id'), nullable=True))
        op.create_index(f'{table}_store_idx', table, ['store_id'])
        op.execute(f"UPDATE {table} SET store_id = (SELECT id FROM stores ORDER BY id LIMIT 1) WHERE store_id IS NULL")

    for table in _WALLET_TABLES:
        op.add_column(table, sa.Column('store_id', sa.Integer(), nullable=True), schema='wallet')
        op.execute(
            f"UPDATE wallet.{table} SET store_id = (SELECT id FROM stores ORDER BY id LIMIT 1) WHERE store_id IS NULL"
        )

    op.create_table(
        'store_product_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('store_id', sa.Integer(), sa.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('price_minor', sa.Integer(), nullable=True),
        sa.Column('available', sa.Boolean(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('sale_windows', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('store_id', 'product_id', name='store_product_overrides_uniq'),
        sa.CheckConstraint('price_minor IS NULL OR price_minor > 0', name='store_override_price_ck'),
    )
    op.create_index('store_product_overrides_store_idx', 'store_product_overrides', ['store_id'])


def downgrade() -> None:
    op.drop_index('store_product_overrides_store_idx', table_name='store_product_overrides')
    op.drop_table('store_product_overrides')
    for table in _WALLET_TABLES:
        op.drop_column(table, 'store_id', schema='wallet')
    for table in _PUBLIC_TABLES:
        op.drop_index(f'{table}_store_idx', table_name=table)
        op.drop_column(table, 'store_id')
    op.drop_table('stores')
    op.drop_table('merchants')
