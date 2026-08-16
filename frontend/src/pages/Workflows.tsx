import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Panel, Badge, Button, EmptyState, LoadingSkeleton, formatDate } from '../components/ui'
import { useData } from '../hooks/useData'
import { getWorkflows, runWorkflow, getWorkflowRuns } from '../services/platform'
import { CreateWorkflowModal } from '../components/workflows/CreateWorkflowModal'
import type { Workflow } from '../types'

export interface WorkflowsPageProps {
  projectId: string
}

function WorkflowCard({
  workflow,
  onRun,
  isRunning,
}: {
  workflow: Workflow
  onRun: (id: string) => void
  isRunning: boolean
}) {
  const runsState = useData(
    () => getWorkflowRuns(workflow.id),
    [workflow.id],
    6000
  )

  const runs = runsState.data?.items || []

  return (
    <Panel
      title={workflow.name}
      subtitle={`Policy: ${workflow.failure_policy}`}
      headerAction={
        <Button
          variant="primary"
          size="sm"
          isLoading={isRunning}
          onClick={() => onRun(workflow.id)}
          leftIcon={
            <svg className="h-3.5 w-3.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          }
        >
          Run Workflow
        </Button>
      }
    >
      <div className="space-y-4">
        {/* DAG Structure Summary */}
        <div className="flex flex-wrap items-center gap-3 text-xs font-mono text-slate-600 bg-slate-50 p-3 rounded-lg border border-slate-200/60">
          <div>
            <span className="font-bold text-slate-900">{workflow.nodes.length}</span> Nodes:{' '}
            <span className="text-cyan-700">[{workflow.nodes.map((n) => n.node_key).join(', ')}]</span>
          </div>
          <span className="text-slate-300">|</span>
          <div>
            <span className="font-bold text-slate-900">{workflow.edges.length}</span> Directed Edges
          </div>
        </div>

        {/* Recent Runs List */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Execution History
            </h4>
            {runs.length > 0 && (
              <span className="text-[11px] font-mono text-slate-400">
                {runs.length} {runs.length === 1 ? 'run' : 'runs'} total
              </span>
            )}
          </div>

          {runs.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-200 p-4 text-center">
              <p className="text-xs text-slate-500 font-medium">
                No runs executed yet.
              </p>
              <div className="mt-2">
                <Button
                  variant="outline"
                  size="sm"
                  isLoading={isRunning}
                  onClick={() => onRun(workflow.id)}
                >
                  ▶ Start First Run
                </Button>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-slate-100 border border-slate-200/80 rounded-lg overflow-hidden">
              {runs.slice(0, 5).map((run) => (
                <div
                  key={run.id}
                  className="flex items-center justify-between p-3 text-xs hover:bg-slate-50 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <Badge status={run.status} size="sm" />
                    <Link
                      to={`/workflows/${workflow.id}/runs/${run.id}`}
                      className="font-mono font-bold text-cyan-700 hover:underline hover:text-cyan-900"
                    >
                      {run.id.slice(0, 8)}…{run.id.slice(-4)}
                    </Link>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-slate-400 font-mono text-[11px]">
                      {formatDate(run.started_at)}
                    </span>
                    <Link
                      to={`/workflows/${workflow.id}/runs/${run.id}`}
                      className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600 hover:bg-slate-200"
                    >
                      Inspect DAG →
                    </Link>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Panel>
  )
}

export function WorkflowsPage({ projectId }: WorkflowsPageProps) {
  const navigate = useNavigate()
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [runningWorkflowId, setRunningWorkflowId] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')

  const workflowsState = useData(
    () => getWorkflows(projectId),
    [projectId],
    8000
  )

  const handleRunWorkflow = async (workflowId: string) => {
    setRunningWorkflowId(workflowId)
    setActionError('')
    try {
      const run = await runWorkflow(workflowId)
      navigate(`/workflows/${workflowId}/runs/${run.id}`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to trigger workflow run')
    } finally {
      setRunningWorkflowId(null)
    }
  }

  const workflows = workflowsState.data?.items || []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">
            Workflow Pipelines (DAGs)
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Multi-stage dependency graphs executed with topological parallel branch coordination.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void workflowsState.refresh()}
          >
            Refresh
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setIsCreateModalOpen(true)}
            leftIcon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
              </svg>
            }
          >
            Create Pipeline
          </Button>
        </div>
      </div>

      {actionError && (
        <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 border border-rose-200">
          {actionError}
        </div>
      )}

      {workflowsState.loading && !workflowsState.data ? (
        <div className="grid gap-6 md:grid-cols-2">
          <LoadingSkeleton className="h-64" />
          <LoadingSkeleton className="h-64" />
        </div>
      ) : workflows.length === 0 ? (
        <EmptyState
          title="No workflow DAG definitions in this project"
          description="Create a multi-node DAG pipeline template (e.g. Diamond DAG) to begin executing workflows."
          actionLabel="Create Pipeline"
          onAction={() => setIsCreateModalOpen(true)}
        />
      ) : (
        <div className="grid gap-6 md:grid-cols-2">
          {workflows.map((wf) => (
            <WorkflowCard
              key={wf.id}
              workflow={wf}
              isRunning={runningWorkflowId === wf.id}
              onRun={handleRunWorkflow}
            />
          ))}
        </div>
      )}

      {/* Create Workflow Modal */}
      <CreateWorkflowModal
        isOpen={isCreateModalOpen}
        projectId={projectId}
        onClose={() => setIsCreateModalOpen(false)}
        onCreated={(wf) => {
          setIsCreateModalOpen(false)
          void workflowsState.refresh()
        }}
      />
    </div>
  )
}
