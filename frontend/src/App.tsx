import React, { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { getToken } from './services/api'
import { getMe, getProjects } from './services/platform'
import type { Project, User } from './types'
import { AppShell } from './components/layout/AppShell'
import {
  LoginPage,
  RegisterPage,
  OverviewPage,
  ProjectsPage,
  TasksPage,
  TaskDetailPage,
  WorkflowsPage,
  WorkflowRunPage,
  WorkersPage,
} from './pages/index'

function App() {
  const [token, setAuth] = useState(getToken())
  const [projects, setProjects] = useState<Project[]>([])
  const [user, setUser] = useState<User>()
  const [projectId, setProjectId] = useState(sessionStorage.getItem('dtp.project') || '')
  const [isLoading, setIsLoading] = useState(true)

  const loadUserData = async () => {
    if (!getToken()) {
      setIsLoading(false)
      return
    }
    try {
      const [{ items }, currentUser] = await Promise.all([getProjects(), getMe()])
      setProjects(items)
      setUser(currentUser)
      if (items.length > 0) {
        setProjectId((prev) => {
          const exists = items.some((p) => p.id === prev)
          return exists ? prev : items[0].id
        })
      }
    } catch {
      setAuth(null)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    if (token) {
      setIsLoading(true)
      void loadUserData()
    } else {
      setIsLoading(false)
    }
  }, [token])

  useEffect(() => {
    if (projectId) {
      sessionStorage.setItem('dtp.project', projectId)
    }
  }, [projectId])

  useEffect(() => {
    const expired = () => setAuth(null)
    window.addEventListener('auth-expired', expired)
    return () => window.removeEventListener('auth-expired', expired)
  }, [])

  const handleAuthSuccess = () => {
    setAuth(getToken())
  }

  const handleProjectCreated = (newProject: Project) => {
    setProjects((prev) => [newProject, ...prev])
    setProjectId(newProject.id)
  }

  // Unauthenticated routes
  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage onLogin={handleAuthSuccess} />} />
        <Route path="/register" element={<RegisterPage onRegisterSuccess={handleAuthSuccess} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  // Loading initial user profile & projects
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-300">
        <div className="flex flex-col items-center gap-3">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
          <p className="font-mono text-xs text-slate-400">Loading execution cluster context…</p>
        </div>
      </div>
    )
  }

  const activeUser: User = user || {
    id: '',
    email: 'developer@example.com',
    role: 'developer',
    created_at: new Date().toISOString(),
  }

  return (
    <Routes>
      <Route
        element={
          <AppShell
            user={activeUser}
            projects={projects}
            selectedProjectId={projectId}
            onSelectProject={setProjectId}
            onProjectCreated={handleProjectCreated}
          />
        }
      >
        <Route path="/" element={<OverviewPage projectId={projectId} />} />
        <Route
          path="/projects"
          element={
            <ProjectsPage
              projects={projects}
              selectedProjectId={projectId}
              onSelectProject={setProjectId}
              onProjectCreated={handleProjectCreated}
            />
          }
        />
        <Route path="/tasks" element={<TasksPage projectId={projectId} />} />
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
        <Route path="/workflows" element={<WorkflowsPage projectId={projectId} />} />
        <Route path="/workflows/:workflowId/runs/:runId" element={<WorkflowRunPage />} />
        <Route path="/workers" element={<WorkersPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}

export default function Root() {
  return (
    <BrowserRouter>
      <App />
    </BrowserRouter>
  )
}
