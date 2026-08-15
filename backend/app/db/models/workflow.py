"""Workflow DAG models — definitions, edges, runs, and per-run node state.

Tables:
    workflows          – reusable workflow definitions
    workflow_nodes     – node definitions within a workflow
    workflow_edges     – directed dependency edges between nodes
    workflow_runs      – execution instances of a workflow
    workflow_run_nodes – per-run node execution state (isolated per run)
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FailurePolicy(str, Enum):
    """Workflow-level failure policy."""
    FAIL_FAST = "FAIL_FAST"
    CONTINUE = "CONTINUE"


class WorkflowRunStatus(str, Enum):
    """Lifecycle status of a workflow run."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class WorkflowRunNodeStatus(str, Enum):
    """Lifecycle status of a node within a workflow run."""
    PENDING = "PENDING"      # waiting for dependencies
    READY = "READY"          # all deps satisfied (transient)
    RUNNING = "RUNNING"      # task dispatched / executing
    SUCCESS = "SUCCESS"      # task completed successfully
    FAILED = "FAILED"        # task failed permanently
    SKIPPED = "SKIPPED"      # skipped due to dependency failure or fail-fast


# ---------------------------------------------------------------------------
# Workflow Definition
# ---------------------------------------------------------------------------

class Workflow(Base):
    """Reusable workflow DAG definition."""
    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflows_project_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    failure_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FailurePolicy.FAIL_FAST.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="workflows")  # type: ignore[name-defined]
    nodes: Mapped[list["WorkflowNode"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan",
    )
    edges: Mapped[list["WorkflowEdge"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan",
    )
    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan",
    )


class WorkflowNode(Base):
    """A single node in a workflow definition (maps to one task type)."""
    __tablename__ = "workflow_nodes"
    __table_args__ = (
        UniqueConstraint("workflow_id", "node_key", name="uq_workflow_nodes_workflow_key"),
        Index("ix_workflow_nodes_workflow_id", "workflow_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False,
    )
    node_key: Mapped[str] = mapped_column(String(120), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="nodes")


class WorkflowEdge(Base):
    """Directed dependency edge: from_node must succeed before to_node can run."""
    __tablename__ = "workflow_edges"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "from_node_id", "to_node_id",
            name="uq_workflow_edges_unique",
        ),
        Index("ix_workflow_edges_to_node", "workflow_id", "to_node_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False,
    )
    from_node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False,
    )
    to_node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False,
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="edges")
    from_node: Mapped["WorkflowNode"] = relationship(foreign_keys=[from_node_id])
    to_node: Mapped["WorkflowNode"] = relationship(foreign_keys=[to_node_id])


# ---------------------------------------------------------------------------
# Workflow Runs (execution instances)
# ---------------------------------------------------------------------------

class WorkflowRun(Base):
    """One execution instance of a workflow definition."""
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_workflow_started", "workflow_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[WorkflowRunStatus] = mapped_column(
        SqlEnum(WorkflowRunStatus, name="workflow_run_status"),
        nullable=False,
        default=WorkflowRunStatus.PENDING,
    )
    failure_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FailurePolicy.FAIL_FAST.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="runs")
    run_nodes: Mapped[list["WorkflowRunNode"]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan",
    )


class WorkflowRunNode(Base):
    """Per-run execution state for a workflow node.

    Isolated per workflow_run_id — two runs of the same workflow have
    completely independent WorkflowRunNode rows.
    """
    __tablename__ = "workflow_run_nodes"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "workflow_node_id",
            name="uq_run_nodes_run_node",
        ),
        Index("ix_run_nodes_run_status", "workflow_run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False,
    )
    workflow_node_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_nodes.id"), nullable=False,
    )
    status: Mapped[WorkflowRunNodeStatus] = mapped_column(
        SqlEnum(WorkflowRunNodeStatus, name="workflow_run_node_status"),
        nullable=False,
        default=WorkflowRunNodeStatus.PENDING,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    workflow_run: Mapped["WorkflowRun"] = relationship(back_populates="run_nodes")
    workflow_node: Mapped["WorkflowNode"] = relationship()
    task: Mapped["Task"] = relationship(foreign_keys=[task_id])  # type: ignore[name-defined]
