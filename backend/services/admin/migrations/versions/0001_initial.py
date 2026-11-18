"""Platform schemas: admin (invitations, preferences), audit (journal), tasks (queue + schedules).

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    # ---------------- admin ----------------
    op.create_table(
        "invitations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("username", sa.Text, nullable=False),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        schema="admin",
    )
    op.create_index("ix_invitations_user", "invitations", ["user_id"], schema="admin")

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Text, primary_key=True),
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="admin",
    )

    # ---------------- audit ----------------
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("service", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("actor_id", sa.Text, nullable=False),
        sa.Column("actor_username", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("changes", JSONB),
        sa.Column("tags", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("request_id", sa.Text),
        schema="audit",
    )
    op.create_index("ix_events_occurred", "events", [sa.text("occurred_at DESC")], schema="audit")
    op.create_index("ix_events_entity", "events", ["entity_type", "entity_id"], schema="audit")
    op.create_index("ix_events_actor", "events", ["actor_username"], schema="audit")
    op.create_index("ix_events_tags", "events", ["tags"], schema="audit", postgresql_using="gin")
    # Journal is append-only even for its owner's application code paths.
    op.execute(
        """
        CREATE FUNCTION audit.forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit.events is append-only'; END $$;
        CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
        FOR EACH ROW EXECUTE FUNCTION audit.forbid_change();
        """
    )

    # ---------------- tasks ----------------
    op.create_table(
        "task_types",
        sa.Column("task_type", sa.Text, primary_key=True),
        sa.Column("queue", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("default_params", JSONB, nullable=False, server_default="{}"),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="tasks",
    )
    op.create_table(
        "task_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task_type", sa.Text, nullable=False),
        sa.Column("queue", sa.Text, nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("schedule_id", UUID(as_uuid=True)),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("worker", sa.Text),
        sa.Column("result", JSONB),
        sa.Column("error", sa.Text),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="ck_task_runs_status"
        ),
        schema="tasks",
    )
    op.create_index("ix_task_runs_poll", "task_runs", ["queue", "status", "created_at"], schema="tasks")
    op.create_index("ix_task_runs_created", "task_runs", [sa.text("created_at DESC")], schema="tasks")
    op.create_index("ix_task_runs_type", "task_runs", ["task_type", sa.text("created_at DESC")], schema="tasks")

    op.create_table(
        "schedules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task_type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("cron", sa.Text, nullable=False),
        sa.Column("timezone", sa.Text, nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_id", UUID(as_uuid=True)),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema="tasks",
    )


def downgrade() -> None:
    op.drop_table("schedules", schema="tasks")
    op.drop_table("task_runs", schema="tasks")
    op.drop_table("task_types", schema="tasks")
    op.execute("DROP TABLE audit.events; DROP FUNCTION audit.forbid_change()")
    op.drop_table("user_preferences", schema="admin")
    op.drop_table("invitations", schema="admin")
