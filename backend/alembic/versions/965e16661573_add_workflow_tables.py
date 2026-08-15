"""add_workflow_tables

Revision ID: 965e16661573
Revises: 57edc482f5e1
Create Date: 2026-08-15 23:35:12.582774

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '965e16661573'
down_revision: Union[str, Sequence[str], None] = '57edc482f5e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workflow tables and add task FK."""

    # 1. workflows (root table)
    op.create_table('workflows',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('failure_policy', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_workflows_project_id', 'workflows', ['project_id'], unique=False)

    # 2. workflow_nodes (depends on workflows)
    op.create_table('workflow_nodes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workflow_id', sa.Uuid(), nullable=False),
        sa.Column('node_key', sa.String(length=120), nullable=False),
        sa.Column('task_type', sa.String(length=100), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('timeout_seconds', sa.Integer(), nullable=False),
        sa.Column('max_retries', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workflow_id', 'node_key', name='uq_workflow_nodes_workflow_key'),
    )
    op.create_index('ix_workflow_nodes_workflow_id', 'workflow_nodes', ['workflow_id'], unique=False)

    # 3. workflow_edges (depends on workflows + workflow_nodes)
    op.create_table('workflow_edges',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workflow_id', sa.Uuid(), nullable=False),
        sa.Column('from_node_id', sa.Uuid(), nullable=False),
        sa.Column('to_node_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['from_node_id'], ['workflow_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['to_node_id'], ['workflow_nodes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workflow_id', 'from_node_id', 'to_node_id', name='uq_workflow_edges_unique'),
    )
    op.create_index('ix_workflow_edges_to_node', 'workflow_edges', ['workflow_id', 'to_node_id'], unique=False)

    # 4. Create enum types with raw SQL to avoid metadata auto-creation conflicts
    op.execute("CREATE TYPE workflow_run_status AS ENUM ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')")
    op.execute("CREATE TYPE workflow_run_node_status AS ENUM ('PENDING', 'READY', 'RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED')")

    # 5. workflow_runs (depends on workflows)
    op.create_table('workflow_runs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workflow_id', sa.Uuid(), nullable=False),
        sa.Column('status', postgresql.ENUM('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', name='workflow_run_status', create_type=False), nullable=False),
        sa.Column('failure_policy', sa.String(length=32), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_workflow_runs_workflow_started', 'workflow_runs', ['workflow_id', 'started_at'], unique=False)

    # 6. workflow_run_nodes (depends on workflow_runs + workflow_nodes + tasks)
    op.create_table('workflow_run_nodes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('workflow_run_id', sa.Uuid(), nullable=False),
        sa.Column('workflow_node_id', sa.Uuid(), nullable=False),
        sa.Column('status', postgresql.ENUM('PENDING', 'READY', 'RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED', name='workflow_run_node_status', create_type=False), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['workflow_node_id'], ['workflow_nodes.id']),
        sa.ForeignKeyConstraint(['workflow_run_id'], ['workflow_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workflow_run_id', 'workflow_node_id', name='uq_run_nodes_run_node'),
    )
    op.create_index('ix_run_nodes_run_status', 'workflow_run_nodes', ['workflow_run_id', 'status'], unique=False)

    # 6. Add FK from tasks -> workflow_run_nodes
    op.add_column('tasks', sa.Column('workflow_run_node_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_tasks_workflow_run_node_id', 'tasks', 'workflow_run_nodes',
        ['workflow_run_node_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Drop workflow tables and task FK."""

    # Remove task FK first
    op.drop_constraint('fk_tasks_workflow_run_node_id', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'workflow_run_node_id')

    # Drop in reverse dependency order
    op.drop_index('ix_run_nodes_run_status', table_name='workflow_run_nodes')
    op.drop_table('workflow_run_nodes')

    op.drop_index('ix_workflow_runs_workflow_started', table_name='workflow_runs')
    op.drop_table('workflow_runs')

    op.drop_index('ix_workflow_edges_to_node', table_name='workflow_edges')
    op.drop_table('workflow_edges')

    op.drop_index('ix_workflow_nodes_workflow_id', table_name='workflow_nodes')
    op.drop_table('workflow_nodes')

    op.drop_index('ix_workflows_project_id', table_name='workflows')
    op.drop_table('workflows')

    # Drop custom enum types
    sa.Enum(name='workflow_run_node_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='workflow_run_status').drop(op.get_bind(), checkfirst=True)
