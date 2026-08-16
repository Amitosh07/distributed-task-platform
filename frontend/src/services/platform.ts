import { request } from './api'
import type {
  AuthResponse,
  Project,
  Task,
  TaskCreateInput,
  TaskList,
  User,
  WorkerList,
  Workflow,
  WorkflowCreateInput,
  WorkflowRun,
  WorkflowRunSummary,
} from '../types'

export const login = (email: string, password: string) =>
  request<AuthResponse>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })

export const register = (email: string, password: string, role = 'developer') =>
  request<User>('/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, role }),
  })

export const getMe = () => request<User>('/v1/auth/me')

export const getProjects = () => request<{ items: Project[] }>('/v1/projects')

export const createProject = (name: string) =>
  request<Project>('/v1/projects', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })

export const getTasks = (params: URLSearchParams) =>
  request<TaskList>(`/v1/tasks?${params.toString()}`)

export const getTask = (id: string) => request<Task>(`/v1/tasks/${id}`)

export const createTask = (input: TaskCreateInput) =>
  request<Task>('/v1/tasks', {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const cancelTask = (id: string) =>
  request<Task>(`/v1/tasks/${id}/cancel`, {
    method: 'POST',
  })

export const getWorkers = () => request<WorkerList>('/v1/workers')

export const getWorkflows = (projectId: string) =>
  request<{ items: Workflow[] }>(`/v1/workflows?project_id=${encodeURIComponent(projectId)}`)

export const getWorkflow = (workflowId: string) =>
  request<Workflow>(`/v1/workflows/${workflowId}`)

export const createWorkflow = (input: WorkflowCreateInput) =>
  request<Workflow>('/v1/workflows', {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const runWorkflow = (workflowId: string) =>
  request<WorkflowRun>(`/v1/workflows/${workflowId}/run`, {
    method: 'POST',
  })

export const getWorkflowRuns = (workflowId: string) =>
  request<{ items: WorkflowRunSummary[] }>(`/v1/workflows/${workflowId}/runs`)

export const getWorkflowRun = (workflowId: string, runId: string) =>
  request<WorkflowRun>(`/v1/workflows/${workflowId}/runs/${runId}`)
