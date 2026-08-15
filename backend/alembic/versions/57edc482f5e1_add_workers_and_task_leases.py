"""add_workers_and_task_leases

Phase 4 migration:
- Creates workers table for worker registration, liveness, and heartbeat monitoring.
- Adds worker_id, lease_acquired_at, lease_expires_at, and last_heartbeat_at to tasks table.
- Adds index ix_tasks_status_lease_expires to support fast stale task recovery queries.

Revision ID: 57edc482f5e1
Revises: 5fdc19981948
Create Date: 2026-08-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57edc482f5e1'
down_revision: Union[str, Sequence[str], None] = '5fdc19981948'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for Phase 4."""
    op.create_table('workers',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'STALE', 'STOPPED', name='worker_status'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_workers_status_heartbeat', 'workers', ['status', 'last_heartbeat_at'], unique=False)
    op.add_column('tasks', sa.Column('worker_id', sa.String(length=100), nullable=True))
    op.add_column('tasks', sa.Column('lease_acquired_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tasks', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tasks', sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_tasks_status_lease_expires', 'tasks', ['status', 'lease_expires_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema for Phase 4."""
    op.drop_index('ix_tasks_status_lease_expires', table_name='tasks')
    op.drop_column('tasks', 'last_heartbeat_at')
    op.drop_column('tasks', 'lease_expires_at')
    op.drop_column('tasks', 'lease_acquired_at')
    op.drop_column('tasks', 'worker_id')
    op.drop_index('ix_workers_status_heartbeat', table_name='workers')
    op.drop_table('workers')
    op.execute("DROP TYPE IF EXISTS worker_status")

