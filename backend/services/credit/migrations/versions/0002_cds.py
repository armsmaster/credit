"""CDS pricing: calendars, reference entities, CDS curves (state + history), pushed rate curves.

Revision ID: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

S = "credit"
NOW = sa.text("now()")


def _snapshot_columns():
    return [
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("inputs", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "calendars",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("base_code", sa.Text, sa.ForeignKey(f"{S}.calendars.code")),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        schema=S,
    )
    op.create_table(
        "calendar_days",
        sa.Column("calendar_code", sa.Text, sa.ForeignKey(f"{S}.calendars.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("is_business", sa.Boolean, nullable=False),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    calendars = sa.table("calendars", sa.column("code"), sa.column("name"), sa.column("base_code"), sa.column("description"), schema=S)
    op.bulk_insert(calendars, [
        {"code": "MOSCOW", "name": "Москва", "base_code": None,
         "description": "Рабочие дни в Москве (расчёты в рублях): будни, кроме праздников, плюс рабочие выходные. "
                        "Внебиржевые CDS по Подтверждению Сбера."},
        {"code": "SPFI", "name": "СПФИ", "base_code": "MOSCOW",
         "description": "Расчётный день RUB и день клиринговой сессии СПФИ одновременно: календарь «Москва» плюс дни "
                        "без клиринговых сессий (по данным НКЦ)."},
    ])
    op.create_table(
        "cds_entities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("inn", sa.Text),
        sa.Column("ogrn", sa.Text),
        sa.Column("lei", sa.Text),
        sa.Column("reference_obligation", sa.Text),
        sa.Column("credit_curve_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.curve_definitions.id"), nullable=False),
        sa.Column("standard_coupon_bp", sa.Integer, nullable=False, server_default="100"),
        sa.Column("max_tenor", sa.Text, nullable=False, server_default="5Y"),
        sa.Column("recovery_override", sa.Float),
        sa.Column("doc_basis_bp", sa.Float, nullable=False, server_default="0"),
        sa.Column("basis_bp", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "cds_states",
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_entities.id", ondelete="CASCADE"), primary_key=True),
        *_snapshot_columns(),
        sa.Column("calculated_by", sa.Text, nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "cds_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.cds_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        *_snapshot_columns(),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("entity_id", "as_of_date", "version", name="uq_cds_history_version"),
        schema=S,
    )
    op.create_index("ix_cds_history_entity_date", "cds_history", ["entity_id", "as_of_date"], schema=S)
    op.create_table(
        "rate_snapshots",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("as_of_date", sa.Date, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("version", sa.Integer),
        sa.Column("day_count", sa.Text, nullable=False),
        sa.Column("nodes", JSONB, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )


def downgrade() -> None:
    for table in ("rate_snapshots", "cds_history", "cds_states", "cds_entities", "calendar_days", "calendars"):
        op.drop_table(table, schema=S)
