import React from 'react'
import { Panel, Badge, Button, EmptyState, LoadingSkeleton, formatDate, formatAge } from '../components/ui'
import { useData } from '../hooks/useData'
import { getWorkers } from '../services/platform'

export function WorkersPage() {
  const workersState = useData(getWorkers, [], 5000)

  const workers = workersState.data?.items || []
  const activeCount = workers.filter((w) => w.status === 'ACTIVE').length
  const staleCount = workers.filter((w) => w.status === 'STALE').length
  const stoppedCount = workers.filter((w) => w.status === 'STOPPED').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">
            Worker Fleet & Heartbeats
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Distributed worker processes consuming tasks from Redis and heartbeating to PostgreSQL.
          </p>
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={() => void workersState.refresh()}
        >
          Refresh Fleet
        </Button>
      </div>

      {/* Fleet Summary Stats */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-emerald-800">
            Active Nodes
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-emerald-700">
            {activeCount}
          </div>
          <p className="mt-1 text-[11px] text-emerald-600">
            Heartbeating within heartbeat timeout threshold
          </p>
        </div>

        <div className="rounded-xl border border-rose-500/20 bg-rose-500/5 p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-rose-800">
            Stale Nodes
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-rose-700">
            {staleCount}
          </div>
          <p className="mt-1 text-[11px] text-rose-600">
            Missed heartbeats; tasks subject to lease recovery
          </p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-600">
            Stopped Nodes
          </div>
          <div className="mt-2 font-mono text-2xl font-bold text-slate-700">
            {stoppedCount}
          </div>
          <p className="mt-1 text-[11px] text-slate-500">
            Gracefully terminated worker processes
          </p>
        </div>
      </div>

      {/* Worker List Table */}
      <Panel
        title="Registered Worker Nodes"
        subtitle="Live registry of all current and previous worker instances"
        noPadding
      >
        {workersState.loading && !workersState.data ? (
          <div className="p-6">
            <LoadingSkeleton className="h-64" />
          </div>
        ) : workers.length === 0 ? (
          <div className="p-8">
            <EmptyState
              title="No worker nodes registered"
              description="Start worker processes using 'python -m app.workers.runtime' or Docker Compose to begin processing tasks."
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-100 bg-slate-50/50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-3 px-5">Worker ID</th>
                  <th className="py-3 px-4">Hostname</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Last Heartbeat</th>
                  <th className="py-3 px-4">Started</th>
                  <th className="py-3 px-5 text-right">Stopped</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {workers.map((w) => (
                  <tr key={w.id} className="hover:bg-slate-50/80 transition-colors">
                    <td className="py-3 px-5 font-mono text-xs font-bold text-slate-800">
                      {w.id}
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-600">
                      {w.hostname}
                    </td>
                    <td className="py-3 px-4">
                      <Badge status={w.status} size="sm" />
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-700">
                      <span className="font-semibold text-slate-900">
                        {formatAge(w.last_heartbeat_at)}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-500">
                      {formatDate(w.started_at)}
                    </td>
                    <td className="py-3 px-5 text-right font-mono text-xs text-slate-500">
                      {w.stopped_at ? formatDate(w.stopped_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
