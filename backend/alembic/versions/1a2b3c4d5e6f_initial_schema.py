"""initial schema

Revision ID: 1a2b3c4d5e6f
Revises: 
Create Date: 2026-09-29 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a2b3c4d5e6f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is a manual snapshot of the models because DB connection is unavailable for autogenerate.
    
    op.create_table('categories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.JSON(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_categories_id'), 'categories', ['id'], unique=False)
    
    op.create_table('customers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_user_id', sa.String(), nullable=False),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('username', sa.String(), nullable=True),
    sa.Column('language_code', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customers_id'), 'customers', ['id'], unique=False)
    op.create_index(op.f('ix_customers_telegram_user_id'), 'customers', ['telegram_user_id'], unique=True)
    
    op.create_table('staff',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_user_id', sa.String(), nullable=True),
    sa.Column('login_name', sa.String(), nullable=False),
    sa.Column('password_hash', sa.String(), nullable=False),
    sa.Column('role', sa.String(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_staff_id'), 'staff', ['id'], unique=False)
    op.create_index(op.f('ix_staff_login_name'), 'staff', ['login_name'], unique=True)
    op.create_index(op.f('ix_staff_telegram_user_id'), 'staff', ['telegram_user_id'], unique=True)
    
    op.create_table('store_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(), nullable=True),
    sa.Column('timezone', sa.String(), nullable=True),
    sa.Column('aba_qr_asset_key', sa.String(), nullable=True),
    sa.Column('payment_link', sa.String(), nullable=True),
    sa.Column('telegram_staff_group_id', sa.String(), nullable=True),
    sa.Column('open_hours', sa.String(), nullable=True),
    sa.Column('delivery_mode', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_store_settings_id'), 'store_settings', ['id'], unique=False)
    
    op.create_table('products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.JSON(), nullable=False),
    sa.Column('description', sa.JSON(), nullable=True),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(), nullable=True),
    sa.Column('image_key', sa.String(), nullable=True),
    sa.Column('available', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_products_id'), 'products', ['id'], unique=False)
    
    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_code', sa.String(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('room_number', sa.String(), nullable=False),
    sa.Column('order_status', sa.String(), nullable=True),
    sa.Column('payment_status', sa.String(), nullable=True),
    sa.Column('currency', sa.String(), nullable=True),
    sa.Column('total_minor', sa.Integer(), nullable=False),
    sa.Column('telegram_group_message_id', sa.String(), nullable=True),
    sa.Column('idempotency_key', sa.String(), nullable=True),
    sa.Column('request_digest', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('customer_id', 'idempotency_key', name='uq_order_idempotency')
    )
    op.create_index(op.f('ix_orders_id'), 'orders', ['id'], unique=False)
    op.create_index(op.f('ix_orders_public_code'), 'orders', ['public_code'], unique=True)
    op.create_index(op.f('ix_orders_idempotency_key'), 'orders', ['idempotency_key'], unique=False)
    
    op.create_table('order_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('actor_type', sa.String(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('event', sa.String(), nullable=False),
    sa.Column('from_state', sa.String(), nullable=True),
    sa.Column('to_state', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_events_id'), 'order_events', ['id'], unique=False)
    
    op.create_table('order_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('product_name_snapshot', sa.JSON(), nullable=False),
    sa.Column('unit_price_minor', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('options_json', sa.JSON(), nullable=True),
    sa.Column('line_total_minor', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_items_id'), 'order_items', ['id'], unique=False)
    
    op.create_table('payment_proofs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('telegram_file_id', sa.String(), nullable=False),
    sa.Column('submitted_by', sa.Integer(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('review_status', sa.String(), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['submitted_by'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payment_proofs_id'), 'payment_proofs', ['id'], unique=False)
    
    op.create_table('payment_reviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('staff_id', sa.Integer(), nullable=True),
    sa.Column('decision', sa.String(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payment_reviews_id'), 'payment_reviews', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_payment_reviews_id'), table_name='payment_reviews')
    op.drop_table('payment_reviews')
    op.drop_index(op.f('ix_payment_proofs_id'), table_name='payment_proofs')
    op.drop_table('payment_proofs')
    op.drop_index(op.f('ix_order_items_id'), table_name='order_items')
    op.drop_table('order_items')
    op.drop_index(op.f('ix_order_events_id'), table_name='order_events')
    op.drop_table('order_events')
    op.drop_index(op.f('ix_orders_idempotency_key'), table_name='orders')
    op.drop_index(op.f('ix_orders_public_code'), table_name='orders')
    op.drop_index(op.f('ix_orders_id'), table_name='orders')
    op.drop_table('orders')
    op.drop_index(op.f('ix_products_id'), table_name='products')
    op.drop_table('products')
    op.drop_index(op.f('ix_store_settings_id'), table_name='store_settings')
    op.drop_table('store_settings')
    op.drop_index(op.f('ix_staff_telegram_user_id'), table_name='staff')
    op.drop_index(op.f('ix_staff_login_name'), table_name='staff')
    op.drop_index(op.f('ix_staff_id'), table_name='staff')
    op.drop_table('staff')
    op.drop_index(op.f('ix_customers_telegram_user_id'), table_name='customers')
    op.drop_index(op.f('ix_customers_id'), table_name='customers')
    op.drop_table('customers')
    op.drop_index(op.f('ix_categories_id'), table_name='categories')
    op.drop_table('categories')
