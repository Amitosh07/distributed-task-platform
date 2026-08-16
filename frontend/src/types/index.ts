export type TaskStatus =
  | 'CREATED'
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCESS'
  | 'RETRY_WAIT'
  | 'FAILED'
  | 'DEAD_LETTER'
  | 'CANCELLED'
  | 'TIMED_OUT'

export type TaskPriority = 'HIGH' | 'NORMAL' | 'LOW'

export interface User {
  id: string
  email: string
  role: string
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface Project {
  id: string
  owner_id: string
  name: string
  status: string
  created_at: string
}

export interface Task {
  id: string
  project_id: string
  type: string
  payload: Record<string, unknown>
  status: TaskStatus
  priority: TaskPriority
  idempotency_key: string | null
  scheduled_at: string | null
  timeout_seconds: number
  max_retries: number
  attempt_count: number
  worker_id: string | null
  lease_expires_at: string | null
  created_at: string
  queued_at: string | null
  started_at: string | null
  finished_at: string | null
  result_summary: Record<string, unknown> | null
  error_message: string | null
  workflow_run_node_id?: string | null
}

export interface TaskList {
  items: Task[]
  page: number
  page_size: number
  total: number
}

export interface TaskCreateInput {
  project_id: string
  type: string
  payload: Record<string, unknown>
  priority?: TaskPriority
  idempotency_key?: string
  timeout_seconds?: number
  max_retries?: number
}

export interface Worker {
  id: string
  hostname: string
  status: 'ACTIVE' | 'STALE' | 'STOPPED'
  started_at: string
  last_heartbeat_at: string
  stopped_at: string | null
}

export interface WorkerList {
  items: Worker[]
  total: number
}

export interface WorkflowNode {
  id: string
  node_key: string
  task_type: string
  payload: Record<string, unknown>
  timeout_seconds: number
  max_retries: number
}

export interface WorkflowEdge {
  from_node_key: string
  to_node_key: string
}

export interface Workflow {
  id: string
  project_id: string
  name: string
  failure_policy: 'FAIL_FAST' | 'CONTINUE'
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  created_at: string
}

export interface WorkflowNodeCreateInput {
  node_key: string
  task_type: string
  payload?: Record<string, unknown>
  timeout_seconds?: number
  max_retries?: number
}

export interface WorkflowEdgeCreateInput {
  from: string
  to: string
}

export interface WorkflowCreateInput {
  project_id: string
  name: string
  failure_policy?: 'FAIL_FAST' | 'CONTINUE'
  nodes: WorkflowNodeCreateInput[]
  edges?: WorkflowEdgeCreateInput[]
}

export interface WorkflowRunNode {
  node_key: string
  status: 'PENDING' | 'READY' | 'RUNNING' | 'SUCCESS' | 'FAILED' | 'SKIPPED'
  task_id: string | null
  started_at: string | null
  finished_at: string | null
  error_message: string | null
}

export interface WorkflowRun {
  id: string
  workflow_id: string
  status: 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED'
  failure_policy: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  nodes: WorkflowRunNode[]
}

export type WorkflowRunSummary = Omit<WorkflowRun, 'nodes'>
