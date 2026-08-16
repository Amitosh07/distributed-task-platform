import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { StatCard, Panel, Badge, EmptyState, LoadingSkeleton, formatAge } from '../components/ui'
import { useData } from '../hooks/useData'
import { getTasks, getWorkers, getWorkflows, cancelTask } from '../services/platform'
import { SubmitTaskModal } from '../components/tasks/SubmitTaskModal'
import { Button } from '../components/ui/Button'
import type { Task } from '../types'

export interface OverviewPageProps {
  projectId: string
}

export function OverviewPage({ projectId }: OverviewPageProps) {
  const [isSubmitModalOpen, setIsSubmitModalOpen] = useState(false)
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')

  const tasksState = useData(
    () => getTasks(new URLSearchParams({ project_id: projectId, page_size: '100' })),
    [projectId],
    5000
  )

  const workersState = useData(getWorkers, [], 5000)
  const workflowsState = useData(() => getWorkflows(projectId), [projectId], 8000)

  const handleCancelTask = async (taskId: string, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setCancellingTaskId(taskId)
    setActionError('')
    try {
      await cancelTask(taskId)
      void tasksState.refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to cancel task')
    } finally {
      setCancellingTaskId(null)
    }
  }

  if (tasksState.loading && workersState.loading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <LoadingSkeleton key={i} className="h-28" />
          ))}
        </div>
        <LoadingSkeleton className="h-72" />
      </div>
    )
  }

  const allTasks = tasksState.data?.items || []
  const countStatus = (s: string) =>
    allTasks.filter((t) => (t.status || '').toUpperCase() === s.toUpperCase()).length

  const queuedCount = countStatus('QUEUED') + countStatus('RETRY_WAIT')
  const runningCount = countStatus('RUNNING')
  const successCount = countStatus('SUCCESS')
  const failedCount = countStatus('FAILED') + countStatus('DEAD_LETTER') + countStatus('TIMED_OUT')

  const activeWorkers = workersState.data?.items.filter((w) => w.status === 'ACTIVE').length || 0
  const staleWorkers = workersState.data?.items.filter((w) => w.status === 'STALE').length || 0
  const totalWorkers = workersState.data?.total || 0

  const workflows = workflowsState.data?.items || []

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">
            Cluster Overview
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Real-time execution telemetry for active project, polling every 5s.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void tasksState.refresh()
              void workersState.refresh()
              void workflowsState.refresh()
            }}
          >
            Refresh Telemetry
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setIsSubmitModalOpen(true)}
            leftIcon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
              </svg>
            }
          >
            Submit Task
          </Button>
        </div>
      </div>

      {actionError && (
        <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 border border-rose-200">
          {actionError}
        </div>
      )}

      {/* Primary KPI Metrics */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="Total Tasks"
          value={tasksState.data?.total || 0}
          tone="default"
        />
        <StatCard
          label="Queued / Waiting"
          value={queuedCount}
          tone="warning"
        />
        <StatCard
          label="Running"
          value={runningCount}
          tone="info"
        />
        <StatCard
          label="Succeeded"
          value={successCount}
          tone="success"
        />
        <StatCard
          label="Failed / Timed Out"
          value={failedCount}
          tone={failedCount > 0 ? 'danger' : 'default'}
        />
      </div>

      {/* Workers & Workflows Summary Cards */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Worker Fleet Health */}
        <Panel
          title="Worker Fleet"
          subtitle="Distributed execution nodes"
          headerAction={
            <Link to="/workers" className="text-xs font-medium text-cyan-700 hover:underline">
              View all →
            </Link>
          }
        >
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-3xl font-bold text-slate-900">
              {activeWorkers}
            </span>
            <span className="text-xs text-slate-500 font-medium">
              / {totalWorkers} nodes active
            </span>
          </div>

          <div className="mt-4 flex gap-4 text-xs font-medium">
            <span className="flex items-center gap-1 text-emerald-700">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              {activeWorkers} Active
            </span>
            <span className="flex items-center gap-1 text-rose-700">
              <span className="h-2 w-2 rounded-full bg-rose-500" />
              {staleWorkers} Stale
            </span>
          </div>
        </Panel>

        {/* Workflow Pipelines */}
        <Panel
          title="Workflow Definitions"
          subtitle="DAG pipelines in this project"
          headerAction={
            <Link to="/workflows" className="text-xs font-medium text-cyan-700 hover:underline">
              View all →
            </Link>
          }
        >
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-3xl font-bold text-slate-900">
              {workflows.length}
            </span>
            <span className="text-xs text-slate-500 font-medium">
              DAG definitions
            </span>
          </div>

          <p className="mt-4 text-xs text-slate-500">
            Parallel branch execution and failure policies managed by workflow engine.
          </p>
        </Panel>

        {/* Engine Status */}
        <Panel
          title="Persistence & Queue"
          subtitle="Infrastructure coordination"
        >
          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between">
              <span className="text-slate-500">Authoritative State</span>
              <span className="font-mono font-medium text-slate-800">PostgreSQL (Durable)</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Dispatch Queue</span>
              <span className="font-mono font-medium text-slate-800">Redis (Atomic BLPOP)</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Execution Semantics</span>
              <span className="font-mono font-medium text-emerald-700">At-least-once with leases</span>
            </div>
          </div>
        </Panel>
      </div>

      {/* Recent Task Activity */}
      <Panel
        title="Recent Task Executions"
        subtitle="Latest tasks submitted to this project"
        headerAction={
          <Link to="/tasks" className="text-xs font-medium text-cyan-700 hover:underline">
            View full task table →
          </Link>
        }
        noPadding
      >
        {allTasks.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title="No task executions yet"
              description="Submit your first task to see execution telemetry and logs."
              actionLabel="Submit Task"
              onAction={() => setIsSubmitModalOpen(true)}
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-100 bg-slate-50/50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-3 px-5">Task ID</th>
                  <th className="py-3 px-4">Type</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Priority</th>
                  <th className="py-3 px-4">Attempts</th>
                  <th className="py-3 px-4">Worker</th>
                  <th className="py-3 px-4">Age</th>
                  <th className="py-3 px-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {allTasks.slice(0, 8).map((task) => {
                  const normalizedStatus = (task.status || '').toUpperCase()
                  const isCancellable = normalizedStatus === 'QUEUED' || normalizedStatus === 'CREATED'

                  return (
                    <tr key={task.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="py-3 px-5 font-mono text-xs font-medium">
                        <Link
                          to={`/tasks/${task.id}`}
                          className="text-cyan-700 hover:text-cyan-900 hover:underline"
                        >
                          {task.id.slice(0, 8)}…{task.id.slice(-4)}
                        </Link>
                      </td>
                      <td className="py-3 px-4 font-mono text-xs text-slate-700">
                        {task.type}
                      </td>
                      <td className="py-3 px-4">
                        <Badge status={task.status} size="sm" />
                      </td>
                      <td className="py-3 px-4 text-xs font-medium text-slate-600">
                        {task.priority}
                      </td>
                      <td className="py-3 px-4 font-mono text-xs text-slate-600">
                        {task.attempt_count} / {task.max_retries}
                      </td>
                      <td className="py-3 px-4 font-mono text-xs text-slate-500">
                        {task.worker_id || '—'}
                      </td>
                      <td className="py-3 px-4 font-mono text-xs text-slate-500">
                        {formatAge(task.created_at)}
                      </td>
                      <td className="py-3 px-5 text-right">
                        {isCancellable ? (
                          <Button
                            variant="danger"
                            size="sm"
                            isLoading={cancellingTaskId === task.id}
                            onClick={(e) => handleCancelTask(task.id, e)}
                            className="text-[11px] py-0.5 px-2 font-medium"
                          >
                            Cancel
                          </Button>
                        ) : (
                          <Link
                            to={`/tasks/${task.id}`}
                            className="text-xs font-medium text-slate-400 hover:text-slate-700"
                          >
                            Details →
                          </Link>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* Submit Task Modal */}
      <SubmitTaskModal
        isOpen={isSubmitModalOpen}
        projectId={projectId}
        onClose={() => setIsSubmitModalOpen(false)}
        onTaskSubmitted={(task: Task) => {
          setIsSubmitModalOpen(false)
          void tasksState.refresh()
        }}
      />
    </div>
  )
}
