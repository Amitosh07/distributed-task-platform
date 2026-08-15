"""initial users projects tasks schema

Revision ID: 52f2c23c1add
Revises: 
Create Date: 2026-08-15 16:21:17.181570

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '52f2c23c1add'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    task_status = postgresql.ENUM("CREATED", "QUEUED", "RUNNING", "SUCCESS", "RETRY_WAIT", "FAILED", "DEAD_LETTER", "CANCELLED", "TIMED_OUT", name="task_status", create_type=False)
    task_priority = postgresql.ENUM("HIGH", "NORMAL", "LOW", name="task_priority", create_type=False)
    task_status.create(op.get_bind(), checkfirst=True)
    task_priority.create(op.get_bind(), checkfirst=True)
    op.create_table("users", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("email", sa.String(320), nullable=False), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("email"))
    op.create_index(op.f("ix_users_email"), "users", ["email"])
    op.create_table("projects", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("owner_id", sa.Uuid(), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"))
    op.create_index(op.f("ix_projects_owner_id"), "projects", ["owner_id"])
    op.create_table("tasks", sa.Column("id", sa.Uuid(), nullable=False), sa.Column("project_id", sa.Uuid(), nullable=False), sa.Column("type", sa.String(100), nullable=False), sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False), sa.Column("status", task_status, nullable=False), sa.Column("priority", task_priority, nullable=False), sa.Column("idempotency_key", sa.String(255), nullable=True), sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True), sa.Column("timeout_seconds", sa.Integer(), nullable=False), sa.Column("max_retries", sa.Integer(), nullable=False), sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True), sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("project_id", "idempotency_key", name="uq_tasks_project_idempotency_key"))
    op.create_index("ix_tasks_project_status", "tasks", ["project_id", "status"])
    op.create_index("ix_tasks_scheduled_at", "tasks", ["scheduled_at"])
    op.create_index("ix_tasks_status_priority_created", "tasks", ["status", "priority", "created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_tasks_status_priority_created", table_name="tasks")
    op.drop_index("ix_tasks_scheduled_at", table_name="tasks")
    op.drop_index("ix_tasks_project_status", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_projects_owner_id"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    sa.Enum(name="task_priority").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="task_status").drop(op.get_bind(), checkfirst=True)
