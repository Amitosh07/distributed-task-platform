import React, { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Panel, Badge, Button, LoadingSkeleton, formatDate } from '../components/ui'
import { useData, isTerminalStatus } from '../hooks/useData'
import { getTask, cancelTask } from '../services/platform'

export function TaskDetailPage() {
  const { taskId = '' } = useParams()
  const [isCancelling, setIsCancelling] = useState(false)
  const [cancelError, setCancelError] = useState('')

  const taskState = useData(
    () => getTask(taskId),
    [taskId],
    2000,
    true,
    (task) => !isTerminalStatus(task?.status || 'RUNNING')
  )

  const task = taskState.data

  const handleCancel = async () => {
    if (!taskId) return
    setIsCancelling(true)
    setCancelError('')
    try {
      await cancelTask(taskId)
      void taskState.refresh()
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : 'Failed to cancel task')
    } finally {
      setIsCancelling(false)
    }
  }

  if (taskState.loading && !task) {
    return (
      <div className="space-y-6">
        <LoadingSkeleton className="h-8 w-48" />
        <div className="grid gap-6 lg:grid-cols-3">
          <LoadingSkeleton className="h-80" />
          <LoadingSkeleton className="h-80 lg:col-span-2" />
        </div>
      </div>
    )
  }

  if (taskState.error || !task) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-800">
        <h3 className="font-semibold">Error Loading Task</h3>
        <p className="mt-1 text-xs text-rose-600">{taskState.error || 'Task not found'}</p>
        <Link to="/tasks" className="mt-4 inline-block text-xs font-semibold text-cyan-700 hover:underline">
          ← Back to task list
        </Link>
      </div>
    )
  }

  const normalizedStatus = (task.status || '').toUpperCase()
  const isCancellable = normalizedStatus === 'CREATED' || normalizedStatus === 'QUEUED'

  const timelineSteps = [
    { label: 'Created', date: task.created_at, state: 'done' },
    { label: 'Queued', date: task.queued_at, state: task.queued_at ? 'done' : 'pending' },
    { label: 'Started (Claimed by Worker)', date: task.started_at, state: task.started_at ? 'done' : 'pending' },
    { label: task.status, date: task.finished_at, state: isTerminalStatus(task.status) ? 'done' : 'current' },
  ]

  return (
    <div className="space-y-6">
      {/* Navigation Breadcrumb */}
      <div className="flex items-center justify-between">
        <Link
          to="/tasks"
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-cyan-700 hover:text-cyan-900 hover:underline"
        >
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Back to Tasks
        </Link>

        <div className="flex items-center gap-2">
          {isCancellable && (
            <Button
              variant="danger"
              size="sm"
              isLoading={isCancelling}
              onClick={handleCancel}
            >
              Cancel Task
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void taskState.refresh()}
          >
            Refresh State
          </Button>
        </div>
      </div>

      {cancelError && (
        <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 border border-rose-200">
          {cancelError}
        </div>
      )}

      {/* Task Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200/80 pb-5">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="font-mono text-xl font-bold text-slate-900">{task.id}</h2>
            <Badge status={task.status} />
          </div>
          <p className="mt-1 font-mono text-xs text-slate-500">
            Type: <span className="font-semibold text-slate-700">{task.type}</span> | Project:{' '}
            <span className="font-semibold text-slate-700">{task.project_id}</span>
          </p>
        </div>
      </div>

      {/* Two Column Layout: Timeline & Details */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Left Column: State Timeline & Actions */}
        <div className="space-y-6">
          {/* Action Control Box */}
          <Panel title="Task Actions" subtitle="Queue control & lifecycle operations">
            {isCancellable ? (
              <div className="space-y-3">
                <p className="text-xs text-slate-600">
                  This task is currently <span className="font-bold text-amber-700">{task.status}</span> and waiting to be claimed by a worker. You can safely cancel it.
                </p>
                <Button
                  variant="danger"
                  size="md"
                  className="w-full"
                  isLoading={isCancelling}
                  onClick={handleCancel}
                >
                  ✕ Cancel Task Now
                </Button>
              </div>
            ) : task.status === 'RUNNING' ? (
              <div className="rounded-lg bg-sky-50 p-3 text-xs text-sky-800 border border-sky-200">
                <span className="font-semibold">Actively Executing:</span> Task has been claimed by worker{' '}
                <span className="font-mono font-bold">{task.worker_id}</span> and is running under an active lease.
              </div>
            ) : task.status === 'CANCELLED' ? (
              <div className="rounded-lg bg-slate-100 p-3 text-xs text-slate-700 font-medium">
                Task was cancelled by user and transitioned to terminal CANCELLED state.
              </div>
            ) : (
              <div className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600 font-medium border border-slate-200">
                Task is in terminal state <span className="font-mono font-bold text-slate-800">{task.status}</span>.
              </div>
            )}
          </Panel>

          {/* Timeline Panel */}
          <Panel title="Lifecycle Timeline" subtitle="Recorded state transitions">
            <ol className="relative border-l-2 border-slate-200 ml-2.5 mt-4 space-y-6">
              {timelineSteps.map((step, idx) => (
                <li key={idx} className="mb-4 ml-6">
                  <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-white bg-cyan-600" />
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-800">
                    {step.label}
                  </h4>
                  <p className="text-xs font-mono text-slate-500 mt-0.5">
                    {formatDate(step.date)}
                  </p>
                </li>
              ))}
            </ol>
          </Panel>
        </div>

        {/* Right Column: Execution Attributes & Payloads */}
        <div className="lg:col-span-2 space-y-6">
          {/* Metadata Grid */}
          <Panel title="Task Parameters" subtitle="Worker and retry configuration">
            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-4 text-xs">
              <div>
                <dt className="text-slate-500 font-medium">Worker ID</dt>
                <dd className="mt-1 font-mono font-semibold text-slate-800">
                  {task.worker_id || '—'}
                </dd>
              </div>

              <div>
                <dt className="text-slate-500 font-medium">Attempt Count</dt>
                <dd className="mt-1 font-mono font-semibold text-slate-800">
                  {task.attempt_count} / {task.max_retries} max
                </dd>
              </div>

              <div>
                <dt className="text-slate-500 font-medium">Priority</dt>
                <dd className="mt-1 font-semibold text-slate-800">{task.priority}</dd>
              </div>

              <div>
                <dt className="text-slate-500 font-medium">Timeout Limit</dt>
                <dd className="mt-1 font-mono font-semibold text-slate-800">
                  {task.timeout_seconds}s
                </dd>
              </div>

              <div>
                <dt className="text-slate-500 font-medium">Idempotency Key</dt>
                <dd className="mt-1 font-mono text-slate-700 truncate">
                  {task.idempotency_key || '—'}
                </dd>
              </div>

              <div>
                <dt className="text-slate-500 font-medium">Workflow Node</dt>
                <dd className="mt-1 font-mono text-slate-700">
                  {task.workflow_run_node_id ? task.workflow_run_node_id.slice(0, 8) : 'Standalone'}
                </dd>
              </div>
            </dl>
          </Panel>

          {/* Payload View */}
          <Panel title="Task Payload" subtitle="JSON inputs dispatched to worker handler">
            <pre className="overflow-x-auto rounded-lg bg-slate-950 p-4 font-mono text-xs text-slate-100 border border-slate-800">
              {JSON.stringify(task.payload, null, 2)}
            </pre>
          </Panel>

          {/* Result or Error View */}
          <Panel
            title={task.error_message ? 'Execution Failure' : 'Execution Result'}
            subtitle={
              task.error_message
                ? 'Error reported by worker handler'
                : 'Output summary returned by worker'
            }
          >
            {task.error_message ? (
              <div className="rounded-lg bg-rose-50 p-4 border border-rose-200">
                <p className="font-mono text-xs text-rose-800 font-medium">
                  {task.error_message}
                </p>
              </div>
            ) : task.result_summary ? (
              <pre className="overflow-x-auto rounded-lg bg-slate-950 p-4 font-mono text-xs text-emerald-400 border border-slate-800">
                {JSON.stringify(task.result_summary, null, 2)}
              </pre>
            ) : (
              <div className="py-6 text-center text-xs text-slate-400 font-mono">
                No execution output recorded yet. Task is currently {task.status.toLowerCase()}.
              </div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
