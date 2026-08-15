"""add_queued_at_result_summary_error_message_to_tasks

Phase 2 migration: adds three new nullable columns to the tasks table to
support the worker execution lifecycle.

  queued_at      — timestamp when the task was accepted into the queue.
  result_summary — JSONB dict written by the handler on SUCCESS.
  error_message  — plain-text error written by the worker on FAILED.

Revision ID: 5fdc19981948
Revises: 52f2c23c1add
Create Date: 2026-08-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5fdc19981948'
down_revision: Union[str, Sequence[str], None] = '52f2c23c1add'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add Phase 2 columns to tasks."""
    op.add_column('tasks', sa.Column('queued_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tasks', sa.Column('result_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('tasks', sa.Column('error_message', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove Phase 2 columns from tasks."""
    op.drop_column('tasks', 'error_message')
    op.drop_column('tasks', 'result_summary')
    op.drop_column('tasks', 'queued_at')
