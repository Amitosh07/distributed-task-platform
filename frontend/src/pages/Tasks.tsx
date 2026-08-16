import React, { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Panel, Badge, Button, EmptyState, TableSkeleton, formatAge } from '../components/ui'
import { useData } from '../hooks/useData'
import { getTasks, cancelTask } from '../services/platform'
import { SubmitTaskModal } from '../components/tasks/SubmitTaskModal'
import type { Task } from '../types'

export interface TasksPageProps {
  projectId: string
}

export function TasksPage({ projectId }: TasksPageProps) {
  const [params, setParams] = useSearchParams()
  const [isSubmitModalOpen, setIsSubmitModalOpen] = useState(false)
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')

  const page = Number(params.get('page') || 1)
  const status = params.get('status') || ''
  const taskType = params.get('type') || ''

  const queryString = useMemo(
    () =>
      new URLSearchParams({
        project_id: projectId,
        page: String(page),
        page_size: '20',
        ...(status ? { status } : {}),
        ...(taskType ? { type: taskType } : {}),
      }),
    [projectId, page, status, taskType]
  )

  const tasksState = useData(() => getTasks(queryString), [queryString.toString()], 5000)

  const updateFilters = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([k, v]) => {
      if (v) next.set(k, v)
      else next.delete(k)
    })
    if (!('page' in changes)) {
      next.set('page', '1')
    }
    setParams(next)
  }

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

  const tasks = tasksState.data?.items || []
  const total = tasksState.data?.total || 0
  const totalPages = Math.ceil(total / (tasksState.data?.page_size || 20))

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">
            Task Queue & Executions
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Real-time state tracking of atomic tasks running across worker nodes.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void tasksState.refresh()}
          >
            Refresh
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

      {/* Main Panel with Filters and Table */}
      <Panel noPadding>
        {/* Filter Bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-4 bg-slate-50/50">
          <div className="flex flex-wrap items-center gap-3">
            {/* Status Filter */}
            <select
              aria-label="Filter by status"
              value={status}
              onChange={(e) => updateFilters({ status: e.target.value })}
              className="h-8 rounded-lg border border-slate-300 bg-white px-2.5 text-xs text-slate-800 shadow-2xs focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-medium"
            >
              <option value="">All Statuses</option>
              <option value="QUEUED">QUEUED</option>
              <option value="RUNNING">RUNNING</option>
              <option value="SUCCESS">SUCCESS</option>
              <option value="FAILED">FAILED</option>
              <option value="RETRY_WAIT">RETRY_WAIT</option>
              <option value="DEAD_LETTER">DEAD_LETTER</option>
              <option value="CANCELLED">CANCELLED</option>
              <option value="TIMED_OUT">TIMED_OUT</option>
            </select>

            {/* Task Type Filter */}
            <select
              aria-label="Filter by task type"
              value={taskType}
              onChange={(e) => updateFilters({ type: e.target.value })}
              className="h-8 rounded-lg border border-slate-300 bg-white px-2.5 text-xs text-slate-800 shadow-2xs focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-mono"
            >
              <option value="">All Task Types</option>
              <option value="sleep">sleep</option>
              <option value="csv_stats">csv_stats</option>
              <option value="http_check">http_check</option>
              <option value="image_resize">image_resize</option>
            </select>

            {(status || taskType) && (
              <button
                onClick={() => updateFilters({ status: '', type: '' })}
                className="text-xs text-cyan-700 hover:underline font-medium"
              >
                Clear filters
              </button>
            )}
          </div>

          <div className="text-xs font-medium text-slate-500">
            {total} {total === 1 ? 'task' : 'tasks'} found
          </div>
        </div>

        {/* Table Content */}
        {tasksState.loading && !tasksState.data ? (
          <div className="p-6">
            <TableSkeleton rows={6} cols={8} />
          </div>
        ) : tasks.length === 0 ? (
          <div className="p-8">
            <EmptyState
              title="No tasks match the filter criteria"
              description="Submit a new task or adjust your status/type filters."
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
                  <th className="py-3 px-4">Worker ID</th>
                  <th className="py-3 px-4">Age</th>
                  <th className="py-3 px-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {tasks.map((task) => {
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

        {/* Pagination Footer */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3 text-xs text-slate-600 bg-slate-50/30">
            <span>
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => updateFilters({ page: String(page - 1) })}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => updateFilters({ page: String(page + 1) })}
              >
                Next
              </Button>
            </div>
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
