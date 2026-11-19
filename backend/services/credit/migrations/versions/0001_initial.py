"""Credit schema: issuers, bonds and their market data, credit curves (state + history).

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

S = "credit"
NOW = sa.text("now()")


def _snapshot_columns():
    return [
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("bonds", JSONB, nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("inputs", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
    ]


def upgrade() -> None:
    op.create_table(
        "issuers",
        sa.Column("emitter_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("short_title", sa.Text, nullable=False, server_default=""),
        sa.Column("inn", sa.Text),
        sa.Column("ogrn", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("added_by", sa.Text, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("scan_status", sa.Text),
        sa.Column("scan_error", sa.Text),
        sa.Column("scanned_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_table(
        "bonds",
        sa.Column("secid", sa.Text, primary_key=True),
        sa.Column("issuer_id", sa.BigInteger, sa.ForeignKey(f"{S}.issuers.emitter_id"), nullable=False, index=True),
        sa.Column("isin", sa.Text),
        sa.Column("shortname", sa.Text, nullable=False, server_default=""),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("board", sa.Text),
        sa.Column("is_traded", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("face_unit", sa.Text),
        sa.Column("face_value", sa.Float),
        sa.Column("mat_date", sa.Date),
        sa.Column("desc", JSONB, nullable=False, server_default="{}"),
        sa.Column("schedule", JSONB, nullable=False, server_default="{}"),
        sa.Column("manual", JSONB, nullable=False, server_default="{}"),
        sa.Column("manual_updated_by", sa.Text),
        sa.Column("manual_updated_at", sa.DateTime(timezone=True)),
        sa.Column("liquidity", JSONB, nullable=False, server_default="{}"),
        sa.Column("liquidity_at", sa.DateTime(timezone=True)),
        sa.Column("history_until", sa.Date),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        schema=S,
    )
    op.create_table(
        "bond_daily",
        sa.Column("secid", sa.Text, sa.ForeignKey(f"{S}.bonds.secid", ondelete="CASCADE"), primary_key=True),
        sa.Column("trade_date", sa.Date, primary_key=True),
        *[sa.Column(c, sa.Float) for c in ("face_value", "accrued")],
        sa.Column("num_trades", sa.Integer),
        *[sa.Column(c, sa.Float) for c in ("turnover", "close", "waprice", "bid", "ask", "last", "vwap", "dema",
                                            "dema_half_life", "eod_face_value", "eod_accrued")],
        sa.Column("eod_settle_date", sa.Date),
        sa.Column("eod_at", sa.DateTime(timezone=True)),
        sa.Column("spread_pct", sa.Float),
        sa.Column("snapshots", sa.Integer),
        schema=S,
    )
    op.create_table(
        "bond_snapshots",
        sa.Column("secid", sa.Text, sa.ForeignKey(f"{S}.bonds.secid", ondelete="CASCADE"), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("trade_date", sa.Date, nullable=False, index=True),
        sa.Column("market_time", sa.DateTime(timezone=True)),
        *[sa.Column(c, sa.Float) for c in ("bid", "ask", "last", "vwap")],
        sa.Column("num_trades", sa.Integer),
        *[sa.Column(c, sa.Float) for c in ("turnover", "face_value", "accrued")],
        sa.Column("settle_date", sa.Date),
        *[sa.Column(c, sa.Float) for c in ("dema", "dema_half_life")],
        schema=S,
    )
    op.create_table(
        "bond_candles",
        sa.Column("secid", sa.Text, sa.ForeignKey(f"{S}.bonds.secid", ondelete="CASCADE"), primary_key=True),
        sa.Column("begin", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open", sa.Float),
        sa.Column("close", sa.Float, nullable=False),
        *[sa.Column(c, sa.Float) for c in ("high", "low", "turnover", "volume")],
        schema=S,
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", JSONB),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "curve_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("issuer_id", sa.BigInteger, sa.ForeignKey(f"{S}.issuers.emitter_id"), nullable=False),
        sa.Column("discount_curve_code", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("default_params", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "curve_bonds",
        sa.Column("curve_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.curve_definitions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("secid", sa.Text, sa.ForeignKey(f"{S}.bonds.secid"), primary_key=True, index=True),
        sa.Column("weight", sa.Float, nullable=False, server_default="1"),
        sa.Column("added_by", sa.Text, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "curve_states",
        sa.Column("curve_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.curve_definitions.id"), primary_key=True),
        *_snapshot_columns(),
        sa.Column("saved_by", sa.Text, nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "curve_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("curve_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.curve_definitions.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        *_snapshot_columns(),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("curve_id", "as_of_date", "version", name="uq_credit_curve_history_version"),
        schema=S,
    )
    op.create_index("ix_credit_curve_history_curve_date", "curve_history", ["curve_id", "as_of_date"], schema=S)


def downgrade() -> None:
    for table in ("curve_history", "curve_states", "curve_bonds", "curve_definitions", "settings",
                  "bond_candles", "bond_snapshots", "bond_daily", "bonds", "issuers"):
        op.drop_table(table, schema=S)
