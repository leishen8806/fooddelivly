"""product sale windows + option groups/options

Revision ID: e0123456789d
Revises: d0123456789c
Create Date: 2026-10-02 09:30:00.000000

两件事：

1. **菜品售卖时间**：`product_sale_windows`，一个菜品可以配多个时间段（每天生效，
   按店铺时区）。**没有记录 = 全天可售**（对存量菜品零影响）。
   `start < end` 是当天区间；`start > end` 表示跨午夜（如 20:00–02:00）。

2. **规格 / 附加选择**（参考美团外卖）：`product_option_groups` +
   `product_options`。
     * kind = SPEC  -> 单选（如 中杯/大杯），required=true 表示必选
     * kind = ADDON -> 多选（如 加珍珠/加椰果），max_select 限制最多选几个
     * 每个选项带 `price_delta_minor`，下单时由**服务端**按 id 回库取价并合计，
     前端只传 id，不传价格。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e0123456789d'
down_revision = 'd0123456789c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'product_sale_windows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('start_time <> end_time', name='sale_window_range_ck'),
    )
    op.create_index('product_sale_windows_product_idx', 'product_sale_windows', ['product_id'])

    op.create_table(
        'product_option_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.JSON(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),                  # SPEC | ADDON
        sa.Column('required', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('multi_select', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('max_select', sa.Integer(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('SPEC', 'ADDON')", name='option_group_kind_ck'),
        sa.CheckConstraint('max_select IS NULL OR max_select >= 1', name='option_group_max_ck'),
    )
    op.create_index('product_option_groups_product_idx', 'product_option_groups', ['product_id'])

    op.create_table(
        'product_options',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('product_option_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.JSON(), nullable=False),
        sa.Column('price_delta_minor', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('product_options_group_idx', 'product_options', ['group_id'])


def downgrade() -> None:
    op.drop_index('product_options_group_idx', table_name='product_options')
    op.drop_table('product_options')
    op.drop_index('product_option_groups_product_idx', table_name='product_option_groups')
    op.drop_table('product_option_groups')
    op.drop_index('product_sale_windows_product_idx', table_name='product_sale_windows')
    op.drop_table('product_sale_windows')
