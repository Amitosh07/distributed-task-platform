import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { Panel, Badge, Button, LoadingSkeleton, formatDate } from '../components/ui'
import { useData, isTerminalStatus } from '../hooks/useData'
import { getWorkflow, getWorkflowRun } from '../services/platform'
import { DagViewer } from '../components/workflows/DagViewer'

export function WorkflowRunPage() {
  const { workflowId = '', runId = '' } = useParams()

  const workflowState = useData(() => getWorkflow(workflowId), [workflowId])
  const runState = useData(
    () => getWorkflowRun(workflowId, runId),
    [workflowId, runId],
    2000,
    true,
    (run) => !isTerminalStatus(run?.status || 'RUNNING')
  )

  const workflow = workflowState.data
  const run = runState.data

  if ((workflowState.loading && !workflow) || (runState.loading && !run)) {
    return (
      <div className="space-y-6">
        <LoadingSkeleton className="h-8 w-48" />
        <LoadingSkeleton className="h-96" />
        <LoadingSkeleton className="h-64" />
      </div>
    )
  }

  if (workflowState.error || runState.error || !workflow || !run) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-800">
        <h3 className="font-semibold">Error Loading Workflow Run</h3>
        <p className="mt-1 text-xs text-rose-600">
          {workflowState.error || runState.error || 'Run not found'}
        </p>
        <Link to="/workflows" className="mt-4 inline-block text-xs font-semibold text-cyan-700 hover:underline">
          ← Back to Workflows
        </Link>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Navigation Breadcrumb */}
      <div className="flex items-center justify-between">
        <Link
          to="/workflows"
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-cyan-700 hover:text-cyan-900 hover:underline"
        >
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Back to Workflows
        </Link>

        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void workflowState.refresh()
            void runState.refresh()
          }}
        >
          Refresh State
        </Button>
      </div>

      {/* Header Info */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200/80 pb-5">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="font-bold text-xl text-slate-900">{workflow.name}</h2>
            <Badge status={run.status} />
          </div>
          <p className="mt-1 font-mono text-xs text-slate-500">
            Run ID: <span className="font-semibold text-slate-700">{run.id}</span> | Policy:{' '}
            <span className="font-semibold text-slate-700">{run.failure_policy}</span>
          </p>
        </div>

        <div className="flex items-center gap-6 text-xs text-slate-500 font-mono">
          <div>Started: {formatDate(run.started_at)}</div>
          <div>Finished: {formatDate(run.finished_at)}</div>
        </div>
      </div>

      {/* Error banner if failed */}
      {run.error_message && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs font-mono text-rose-800">
          <span className="font-bold">Workflow Failure:</span> {run.error_message}
        </div>
      )}

      {/* Visual Topological DAG Viewer */}
      <Panel
        title="Visual Execution DAG"
        subtitle="Live state transitions across dependent nodes and parallel branches"
      >
        <DagViewer workflow={workflow} run={run} />
      </Panel>

      {/* Structured Execution Table */}
      <Panel
        title="Node Execution Details"
        subtitle="Individual task states dispatched for each DAG node"
        noPadding
      >
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              <tr>
                <th className="py-3 px-5">Node Key</th>
                <th className="py-3 px-4">Task Type</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Task ID</th>
                <th className="py-3 px-4">Started</th>
                <th className="py-3 px-4">Finished</th>
                <th className="py-3 px-5 text-right">Error</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {run.nodes.map((node) => {
                const nodeDef = workflow.nodes.find((n) => n.node_key === node.node_key)

                return (
                  <tr key={node.node_key} className="hover:bg-slate-50/80 transition-colors">
                    <td className="py-3 px-5 font-mono text-xs font-bold text-slate-800">
                      {node.node_key}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-600">
                      {nodeDef?.task_type || 'task'}
                    </td>
                    <td className="py-3 px-4">
                      <Badge status={node.status} size="sm" />
                    </td>
                    <td className="py-3 px-4 font-mono text-xs">
                      {node.task_id ? (
                        <Link
                          to={`/tasks/${node.task_id}`}
                          className="text-cyan-700 hover:text-cyan-900 hover:underline"
                        >
                          {node.task_id.slice(0, 8)}…
                        </Link>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-500">
                      {formatDate(node.started_at)}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-500">
                      {formatDate(node.finished_at)}
                    </td>
                    <td className="py-3 px-5 text-right font-mono text-xs text-rose-600 truncate max-w-[200px]">
                      {node.error_message || '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}
