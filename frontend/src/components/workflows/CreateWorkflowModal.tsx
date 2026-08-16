import React, { FormEvent, useState } from 'react'
import { Modal } from '../ui/Modal'
import { Button } from '../ui/Button'
import { createWorkflow } from '../../services/platform'
import type { Workflow, WorkflowCreateInput } from '../../types'

export interface CreateWorkflowModalProps {
  isOpen: boolean
  projectId: string
  onClose: () => void
  onCreated: (workflow: Workflow) => void
}

type PresetKey = 'diamond' | 'data_pipeline' | 'linear'

const PRESETS: Record<
  PresetKey,
  {
    name: string
    description: string
    template: (projectId: string, name: string, policy: 'FAIL_FAST' | 'CONTINUE') => WorkflowCreateInput
  }
> = {
  diamond: {
    name: 'Diamond DAG Pipeline',
    description: '4 nodes: Root extract → parallel transform_a & transform_b → join load node.',
    template: (projectId, name, failure_policy) => ({
      project_id: projectId,
      name: name || 'Diamond DAG Pipeline',
      failure_policy,
      nodes: [
        { node_key: 'extract', task_type: 'sleep', payload: { seconds: 1 } },
        { node_key: 'transform_a', task_type: 'sleep', payload: { seconds: 1.5 } },
        { node_key: 'transform_b', task_type: 'sleep', payload: { seconds: 1.5 } },
        { node_key: 'load', task_type: 'sleep', payload: { seconds: 1 } },
      ],
      edges: [
        { from: 'extract', to: 'transform_a' },
        { from: 'extract', to: 'transform_b' },
        { from: 'transform_a', to: 'load' },
        { from: 'transform_b', to: 'load' },
      ],
    }),
  },
  data_pipeline: {
    name: 'ETL & Analytics Pipeline',
    description: '3 nodes: fetch_data (http_check) → compute_stats (csv_stats) → archive (sleep).',
    template: (projectId, name, failure_policy) => ({
      project_id: projectId,
      name: name || 'ETL & Analytics Pipeline',
      failure_policy,
      nodes: [
        { node_key: 'fetch_data', task_type: 'http_check', payload: { url: 'https://example.com' } },
        {
          node_key: 'compute_stats',
          task_type: 'csv_stats',
          payload: { csv_data: 'metric,val\na,10\nb,20\nc,30' },
        },
        { node_key: 'archive', task_type: 'sleep', payload: { seconds: 1 } },
      ],
      edges: [
        { from: 'fetch_data', to: 'compute_stats' },
        { from: 'compute_stats', to: 'archive' },
      ],
    }),
  },
  linear: {
    name: 'Sequential 3-Stage Pipeline',
    description: '3 nodes executed strictly sequentially: stage_1 → stage_2 → stage_3.',
    template: (projectId, name, failure_policy) => ({
      project_id: projectId,
      name: name || 'Sequential 3-Stage Pipeline',
      failure_policy,
      nodes: [
        { node_key: 'stage_1', task_type: 'sleep', payload: { seconds: 1 } },
        { node_key: 'stage_2', task_type: 'sleep', payload: { seconds: 1 } },
        { node_key: 'stage_3', task_type: 'sleep', payload: { seconds: 1 } },
      ],
      edges: [
        { from: 'stage_1', to: 'stage_2' },
        { from: 'stage_2', to: 'stage_3' },
      ],
    }),
  },
}

export function CreateWorkflowModal({
  isOpen,
  projectId,
  onClose,
  onCreated,
}: CreateWorkflowModalProps) {
  const [selectedPreset, setSelectedPreset] = useState<PresetKey>('diamond')
  const [name, setName] = useState('Diamond DAG Pipeline')
  const [policy, setPolicy] = useState<'FAIL_FAST' | 'CONTINUE'>('FAIL_FAST')
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handlePresetSelect = (preset: PresetKey) => {
    setSelectedPreset(preset)
    setName(PRESETS[preset].name)
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return

    setIsSubmitting(true)
    setError('')
    try {
      const payload = PRESETS[selectedPreset].template(projectId, name.trim(), policy)
      const wf = await createWorkflow(payload)
      onCreated(wf)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create workflow')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create Workflow Pipeline (DAG)"
      subtitle="Define a multi-node dependency graph with topological parallel branch coordination."
      maxWidth="lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700 border border-rose-200">
            {error}
          </div>
        )}

        {/* Preset Selector */}
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-2">
            Choose DAG Architecture Template
          </label>
          <div className="space-y-2">
            {(Object.keys(PRESETS) as PresetKey[]).map((key) => {
              const preset = PRESETS[key]
              const isSelected = selectedPreset === key

              return (
                <div
                  key={key}
                  onClick={() => handlePresetSelect(key)}
                  className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                    isSelected
                      ? 'border-cyan-600 bg-cyan-50/50 ring-2 ring-cyan-600/20'
                      : 'border-slate-200 bg-white hover:border-slate-300'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-sm text-slate-900">{preset.name}</span>
                    <span
                      className={`h-4 w-4 rounded-full border flex items-center justify-center ${
                        isSelected
                          ? 'border-cyan-600 bg-cyan-600 text-white'
                          : 'border-slate-300 bg-white'
                      }`}
                    >
                      {isSelected && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">{preset.description}</p>
                </div>
              )
            })}
          </div>
        </div>

        {/* Pipeline Name */}
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
            Pipeline Name
          </label>
          <input
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3.5 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-medium"
          />
        </div>

        {/* Failure Policy */}
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wider text-slate-600 mb-1.5">
            Failure Policy
          </label>
          <select
            value={policy}
            onChange={(e) => setPolicy(e.target.value as 'FAIL_FAST' | 'CONTINUE')}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600 font-medium"
          >
            <option value="FAIL_FAST">FAIL_FAST — Stop and fail workflow immediately on node failure</option>
            <option value="CONTINUE">CONTINUE — Continue running independent branches on node failure</option>
          </select>
        </div>

        <div className="flex justify-end gap-2.5 pt-4 border-t border-slate-100">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" size="sm" isLoading={isSubmitting}>
            Create & Save Pipeline
          </Button>
        </div>
      </form>
    </Modal>
  )
}
