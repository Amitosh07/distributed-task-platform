import React, { FormEvent, useEffect, useState } from 'react'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { createTask } from '../../services/platform'
import type { Task, TaskPriority } from '../../types'

export interface SubmitTaskModalProps {
  isOpen: boolean
  projectId: string
  onClose: () => void
  onTaskSubmitted: (task: Task) => void
}

const DEFAULT_PAYLOADS: Record<string, string> = {
  sleep: JSON.stringify({ seconds: 2.0 }, null, 2),
  csv_stats: JSON.stringify({ csv_data: 'metric,value\nalpha,100\nbeta,250\ngamma,180' }, null, 2),
  http_check: JSON.stringify({ url: 'https://example.com', expected_status: 200 }, null, 2),
  image_resize: JSON.stringify(
    {
      image_base64: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
      width: 64,
      height: 64,
    },
    null,
    2
  ),
}

export function SubmitTaskModal({
  isOpen,
  projectId,
  onClose,
  onTaskSubmitted,
}: SubmitTaskModalProps) {
  const [taskType, setTaskType] = useState('sleep')
  const [payloadText, setPayloadText] = useState(DEFAULT_PAYLOADS.sleep)
  const [priority, setPriority] = useState<TaskPriority>('NORMAL')
  const [timeoutSeconds, setTimeoutSeconds] = useState(30)
  const [maxRetries, setMaxRetries] = useState(3)
  const [idempotencyKey, setIdempotencyKey] = useState('')
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  useEffect(() => {
    if (DEFAULT_PAYLOADS[taskType]) {
      setPayloadText(DEFAULT_PAYLOADS[taskType])
    }
  }, [taskType])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')

    let parsedPayload: Record<string, unknown>
    try {
      parsedPayload = JSON.parse(payloadText)
      if (typeof parsedPayload !== 'object' || parsedPayload === null || Array.isArray(parsedPayload)) {
        throw new Error('Payload must be a JSON object')
      }
    } catch (err) {
      setError(err instanceof Error ? `Invalid JSON: ${err.message}` : 'Payload must be valid JSON')
      return
    }

    setIsSubmitting(true)
    try {
      const task = await createTask({
        project_id: projectId,
        type: taskType,
        payload: parsedPayload,
        priority,
        timeout_seconds: timeoutSeconds,
        max_retries: maxRetries,
        ...(idempotencyKey.trim() ? { idempotency_key: idempotencyKey.trim() } : {}),
      })
      onTaskSubmitted(task)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit task')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Submit New Task"
      subtitle="Dispatch an atomic execution task to the distributed worker queue."
      maxWidth="lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 border border-rose-200">
            {error}
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
              Task Type
            </label>
            <select
              value={taskType}
              onChange={(e) => setTaskType(e.target.value)}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-mono"
            >
              <option value="sleep">sleep</option>
              <option value="csv_stats">csv_stats</option>
              <option value="http_check">http_check</option>
              <option value="image_resize">image_resize</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
              Priority
            </label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as TaskPriority)}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600"
            >
              <option value="HIGH">HIGH (Priority queue)</option>
              <option value="NORMAL">NORMAL</option>
              <option value="LOW">LOW</option>
            </select>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600">
              Payload (JSON)
            </label>
            <button
              type="button"
              onClick={() => setPayloadText(DEFAULT_PAYLOADS[taskType] || '{}')}
              className="text-[11px] font-medium text-cyan-700 hover:underline"
            >
              Reset template
            </button>
          </div>
          <textarea
            required
            rows={5}
            value={payloadText}
            onChange={(e) => setPayloadText(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-slate-950 p-3 font-mono text-xs text-slate-100 focus:border-cyan-500 focus:outline-none focus:ring-1 focus:ring-cyan-500"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
              Timeout (Seconds)
            </label>
            <input
              type="number"
              min={1}
              max={86400}
              value={timeoutSeconds}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-mono"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
              Max Retries
            </label>
            <input
              type="number"
              min={0}
              max={20}
              value={maxRetries}
              onChange={(e) => setMaxRetries(Number(e.target.value))}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-mono"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
            Idempotency Key (Optional)
          </label>
          <input
            type="text"
            placeholder="e.g. order-process-10492"
            value={idempotencyKey}
            onChange={(e) => setIdempotencyKey(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-mono text-xs"
          />
        </div>

        <div className="flex justify-end gap-2.5 pt-4 border-t border-slate-100">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" size="sm" isLoading={isSubmitting}>
            Submit Task
          </Button>
        </div>
      </form>
    </Modal>
  )
}
