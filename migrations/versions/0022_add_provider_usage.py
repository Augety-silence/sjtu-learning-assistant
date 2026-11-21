"""增加无学习正文与凭据的全入口模型用量计数。"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("provider_usage"):
        return
    op.create_table("provider_usage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger()),
        sa.Column("completion_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("cached_tokens", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_provider_usage_created", "provider_usage", ["created_at"])


def downgrade():
    op.drop_table("provider_usage")
