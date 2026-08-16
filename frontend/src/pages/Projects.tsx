import React, { useState } from 'react'
import { Panel, Button, EmptyState, Badge, formatDate } from '../components/ui'
import { CreateProjectModal } from '../components/projects/CreateProjectModal'
import type { Project } from '../types'

export interface ProjectsPageProps {
  projects: Project[]
  selectedProjectId: string
  onSelectProject: (id: string) => void
  onProjectCreated: (project: Project) => void
}

export function ProjectsPage({
  projects,
  selectedProjectId,
  onSelectProject,
  onProjectCreated,
}: ProjectsPageProps) {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">
            Projects & Workspaces
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Isolated tenant namespaces for tasks, workflows, and execution telemetry.
          </p>
        </div>

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
          Create Project
        </Button>
      </div>

      {projects.length === 0 ? (
        <EmptyState
          title="No projects found"
          description="Create a project workspace to start running tasks and orchestrating workflows."
          actionLabel="Create First Project"
          onAction={() => setIsCreateModalOpen(true)}
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((p) => {
            const isSelected = p.id === selectedProjectId

            return (
              <Panel
                key={p.id}
                className={`transition-all ${
                  isSelected
                    ? 'border-cyan-600 ring-2 ring-cyan-600/20 shadow-md'
                    : 'hover:border-slate-300'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="font-semibold text-base text-slate-900">{p.name}</h3>
                    <p className="mt-1 font-mono text-[11px] text-slate-400 truncate max-w-[200px]">
                      {p.id}
                    </p>
                  </div>
                  {isSelected ? (
                    <span className="inline-flex items-center rounded-full bg-cyan-50 px-2.5 py-0.5 text-xs font-semibold text-cyan-800 border border-cyan-200">
                      Active
                    </span>
                  ) : (
                    <Badge status={p.status} size="sm" />
                  )}
                </div>

                <div className="mt-6 pt-4 border-t border-slate-100 flex items-center justify-between text-xs">
                  <span className="text-slate-500">Created: {formatDate(p.created_at)}</span>
                  {!isSelected && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onSelectProject(p.id)}
                    >
                      Switch to Project
                    </Button>
                  )}
                </div>
              </Panel>
            )
          })}
        </div>
      )}

      {/* Create Project Modal */}
      <CreateProjectModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreated={(project) => {
          onProjectCreated(project)
          setIsCreateModalOpen(false)
        }}
      />
    </div>
  )
}
