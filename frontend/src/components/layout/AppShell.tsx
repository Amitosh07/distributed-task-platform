import React, { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import type { Project, User } from '../../types'
import { clearToken } from '../../services/api'
import { CreateProjectModal } from '../projects/CreateProjectModal'

export interface AppShellProps {
  user: User
  projects: Project[]
  selectedProjectId: string
  onSelectProject: (id: string) => void
  onProjectCreated: (project: Project) => void
}

export function AppShell({
  user,
  projects,
  selectedProjectId,
  onSelectProject,
  onProjectCreated,
}: AppShellProps) {
  const navigate = useNavigate()
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)

  const navItems = [
    {
      to: '/',
      label: 'Overview',
      icon: (
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"
          />
        </svg>
      ),
    },
    {
      to: '/projects',
      label: 'Projects',
      icon: (
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
          />
        </svg>
      ),
    },
    {
      to: '/tasks',
      label: 'Tasks',
      icon: (
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
          />
        </svg>
      ),
    },
    {
      to: '/workflows',
      label: 'Workflows',
      icon: (
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M13 10V3L4 14h7v7l9-11h-7z"
          />
        </svg>
      ),
    },
    {
      to: '/workers',
      label: 'Workers',
      icon: (
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01"
          />
        </svg>
      ),
    },
  ]

  const handleLogout = () => {
    clearToken()
    navigate('/login')
  }

  const selectedProject = projects.find((p) => p.id === selectedProjectId)

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex">
      {/* Dark Sidebar */}
      <aside className="fixed inset-y-0 left-0 w-60 bg-slate-950 text-slate-300 flex flex-col z-30 border-r border-slate-800">
        {/* Brand */}
        <div className="flex h-16 items-center gap-2.5 px-6 border-b border-slate-800/80">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 font-bold text-white text-xs shadow-md">
            DTP
          </div>
          <div>
            <h1 className="text-sm font-bold text-white tracking-tight">
              Task<span className="text-cyan-400">Platform</span>
            </h1>
            <p className="text-[10px] font-mono text-slate-400 uppercase tracking-wider">
              Control Plane
            </p>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1.5 px-3 py-4 overflow-y-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all ${
                  isActive
                    ? 'bg-cyan-950/70 text-cyan-400 font-semibold shadow-xs border border-cyan-800/30'
                    : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                }`
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* System telemetry banner */}
        <div className="p-4 border-t border-slate-900 bg-slate-950/50">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="flex items-center gap-1.5 font-mono text-[11px]">
              <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
              Engine Online
            </span>
            <span className="font-mono text-[10px] text-slate-500">v0.1.0</span>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="ml-60 flex-1 flex flex-col min-w-0">
        {/* Top Header */}
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200/80 bg-white/95 px-8 backdrop-blur-xs">
          {/* Project Selector & Actions */}
          <div className="flex items-center gap-3">
            <label htmlFor="project-selector" className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Project:
            </label>
            {projects.length > 0 ? (
              <div className="flex items-center gap-2">
                <select
                  id="project-selector"
                  aria-label="Active Project"
                  value={selectedProjectId}
                  onChange={(e) => onSelectProject(e.target.value)}
                  className="h-9 rounded-lg border border-slate-300 bg-white px-3 font-medium text-sm text-slate-800 shadow-2xs focus:border-cyan-600 focus:outline-none focus:ring-1 focus:ring-cyan-600"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => setIsCreateModalOpen(true)}
                  title="Create new project"
                  className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-100 hover:text-slate-800 transition-colors"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                  </svg>
                </button>
              </div>
            ) : (
              <button
                onClick={() => setIsCreateModalOpen(true)}
                className="text-xs font-medium text-cyan-700 hover:underline"
              >
                + Create first project
              </button>
            )}
          </div>

          {/* User Profile & Logout */}
          <div className="flex items-center gap-5">
            <div className="flex items-center gap-2 text-sm">
              <div className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 font-bold text-xs text-slate-700 border border-slate-200">
                {user.email.charAt(0).toUpperCase()}
              </div>
              <span className="hidden font-medium text-slate-700 md:inline">
                {user.email}
              </span>
            </div>
            <button
              onClick={handleLogout}
              className="rounded-lg px-2.5 py-1 text-xs font-medium text-slate-500 hover:bg-rose-50 hover:text-rose-700 transition-colors"
            >
              Sign out
            </button>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1 p-8 max-w-7xl w-full mx-auto">
          <Outlet />
        </main>
      </div>

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
