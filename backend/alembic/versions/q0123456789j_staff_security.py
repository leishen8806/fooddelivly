"""add staff login audit and forced password change flags"""
from alembic import op
import sqlalchemy as sa

revision = "q0123456789j"
down_revision = "p0123456789i"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("staff", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("staff", "must_change_password")
    op.drop_column("staff", "last_login_at")
