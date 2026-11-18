"""Curves schema: currencies, curve definitions, current state, history.

Revision ID: 0001
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def _snapshot_columns():
    return [
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("input_mode", sa.Text, nullable=False),
        sa.Column("quotes", JSONB, nullable=False),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("conventions", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
    ]


def upgrade() -> None:
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(3), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("numeric_code", sa.String(3)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="curves",
    )
    op.create_table(
        "curve_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("currency_code", sa.String(3), sa.ForeignKey("curves.currencies.code"), nullable=False),
        sa.Column("index_name", sa.Text, nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("day_count", sa.Text, nullable=False, server_default="ACT/365F"),
        sa.Column("fixed_frequency_months", sa.Integer, nullable=False, server_default="12"),
        sa.Column("single_payment_max_months", sa.Integer, nullable=False, server_default="12"),
        sa.Column("default_params", JSONB, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="curves",
    )
    op.create_table(
        "curve_states",
        sa.Column("curve_id", UUID(as_uuid=True), sa.ForeignKey("curves.curve_definitions.id"), primary_key=True),
        *_snapshot_columns(),
        sa.Column("saved_by", sa.Text, nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="curves",
    )
    op.create_table(
        "curve_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("curve_id", UUID(as_uuid=True), sa.ForeignKey("curves.curve_definitions.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        *_snapshot_columns(),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("curve_id", "as_of_date", "version", name="uq_curve_history_version"),
        schema="curves",
    )
    op.create_index(
        "ix_curve_history_lookup",
        "curve_history",
        ["curve_id", sa.text("as_of_date DESC"), sa.text("version DESC")],
        schema="curves",
    )

    # ---- reference data ----
    currencies = sa.table(
        "currencies", sa.column("code"), sa.column("name"), sa.column("numeric_code"), schema="curves"
    )
    op.bulk_insert(
        currencies,
        [
            {"code": "RUB", "name": "Российский рубль", "numeric_code": "643"},
            {"code": "USD", "name": "Доллар США", "numeric_code": "840"},
            {"code": "EUR", "name": "Евро", "numeric_code": "978"},
            {"code": "CNY", "name": "Китайский юань", "numeric_code": "156"},
        ],
    )
    definitions = sa.table(
        "curve_definitions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("code"),
        sa.column("name"),
        sa.column("currency_code"),
        sa.column("index_name"),
        sa.column("description"),
        sa.column("default_params", JSONB),
        schema="curves",
    )
    params = {"max_avg_deviation_bp": 5.0, "max_point_deviation_bp": 15.0, "smoothness": 10.0}
    op.bulk_insert(
        definitions,
        [
            {
                "id": uuid.uuid4(),
                "code": "RUB-KEYRATE",
                "name": "RUB: свопы на ключевую ставку",
                "currency_code": "RUB",
                "index_name": "Ключевая ставка ЦБ РФ",
                "description": "Кривая по котировкам процентных свопов на ключевую ставку",
                "default_params": params,
            },
            {
                "id": uuid.uuid4(),
                "code": "RUB-RUONIA-OIS",
                "name": "RUB: OIS RUONIA",
                "currency_code": "RUB",
                "index_name": "RUONIA",
                "description": "Кривая по котировкам OIS на RUONIA",
                "default_params": params,
            },
            {
                "id": uuid.uuid4(),
                "code": "RUB-RUSFAR-OIS",
                "name": "RUB: OIS RUSFAR",
                "currency_code": "RUB",
                "index_name": "RUSFAR",
                "description": "Кривая по котировкам OIS на RUSFAR",
                "default_params": params,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("curve_history", schema="curves")
    op.drop_table("curve_states", schema="curves")
    op.drop_table("curve_definitions", schema="curves")
    op.drop_table("currencies", schema="curves")
