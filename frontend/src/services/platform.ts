import { request } from './api'
import type { AuthResponse, Project, Task, TaskList, User, WorkerList, Workflow, WorkflowRun, WorkflowRunSummary } from '../types'
export const login = (email: string, password: string) => request<AuthResponse>('/v1/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
export const getProjects = () => request<{ items: Project[] }>('/v1/projects')
export const getMe = () => request<User>('/v1/auth/me')
export const getTasks = (params: URLSearchParams) => request<TaskList>(`/v1/tasks?${params}`)
export const getTask = (id: string) => request<Task>(`/v1/tasks/${id}`)
export const getWorkers = () => request<WorkerList>('/v1/workers')
export const getWorkflows = (projectId: string) => request<{ items: Workflow[] }>(`/v1/workflows?project_id=${encodeURIComponent(projectId)}`)
export const getWorkflow = (workflowId: string) => request<Workflow>(`/v1/workflows/${workflowId}`)
export const getWorkflowRuns = (workflowId: string) => request<{ items: WorkflowRunSummary[] }>(`/v1/workflows/${workflowId}/runs`)
export const getWorkflowRun = (workflowId: string, runId: string) => request<WorkflowRun>(`/v1/workflows/${workflowId}/runs/${runId}`)
