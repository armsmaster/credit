"""CDS trades: trades, versions, tags, valuations.

Revision ID: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

S = "credit"
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "trade_tags",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_index("uq_trade_tags_name", "trade_tags", [sa.text("lower(name)")], unique=True, schema=S)
    op.create_table(
        "cds_trades",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.Text, nullable=False, unique=True),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_entities.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_index("ix_cds_trades_entity", "cds_trades", ["entity_id"], schema=S)
    op.create_table(
        "cds_trade_tags",
        sa.Column("trade_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_trades.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.BigInteger, sa.ForeignKey(f"{S}.trade_tags.id", ondelete="CASCADE"), primary_key=True),
        schema=S,
    )
    op.create_index("ix_cds_trade_tags_tag", "cds_trade_tags", ["tag_id"], schema=S)
    op.create_table(
        "cds_trade_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_trades.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_entities.id"), nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("notional", sa.Float, nullable=False),
        sa.Column("coupon_bp", sa.Float, nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("effective_date", sa.Date, nullable=False),
        sa.Column("maturity", sa.Date, nullable=False),
        sa.Column("rebate", sa.Boolean, nullable=False),
        sa.Column("schedule_params", JSONB, nullable=False),
        sa.Column("schedule", JSONB, nullable=False),
        sa.Column("schedule_edited", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("tags", JSONB, nullable=False, server_default="[]"),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("trade_id", "version", name="uq_cds_trade_version"),
        schema=S,
    )
    op.create_table(
        "cds_trade_valuations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("trade_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_trades.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_version", sa.Integer, nullable=False),
        sa.Column("valuation_date", sa.Date, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("calculated_by", sa.Text, nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("curve_date", sa.Date, nullable=False),
        sa.Column("npv", sa.Float, nullable=False),
        sa.Column("clean", sa.Float, nullable=False),
        sa.Column("accrued", sa.Float, nullable=False),
        sa.Column("par_spread_bp", sa.Float, nullable=False),
        sa.Column("rpv01", sa.Float, nullable=False),
        sa.Column("cs01", sa.Float, nullable=False),
        sa.Column("details", JSONB, nullable=False),
        schema=S,
    )
    op.create_index("ix_cds_trade_valuations_trade_date", "cds_trade_valuations", ["trade_id", "valuation_date", "calculated_at"], schema=S)
    op.create_index("ix_cds_trade_valuations_date", "cds_trade_valuations", ["valuation_date"], schema=S)


def downgrade() -> None:
    for table in ("cds_trade_valuations", "cds_trade_versions", "cds_trade_tags", "cds_trades", "trade_tags"):
        op.drop_table(table, schema=S)
