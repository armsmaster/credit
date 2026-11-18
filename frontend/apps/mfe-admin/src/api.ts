import { getPlatform, Role } from '@cds/platform'

const BASE = '/api/admin'
const http = () => getPlatform().http

export interface User {
  id: string
  username: string
  email: string | null
  first_name: string | null
  last_name: string | null
  enabled: boolean
  role: Role | null
  created_at: string | null
  invite_pending: boolean
  invite_expires_at: string | null
}
export interface Invite {
  link: string
  expires_at: string
}
export interface AuditEvent {
  id: number
  occurred_at: string
  service: string
  entity_type: string
  entity_id: string
  action: string
  actor_username: string
  summary: string
  changes: Record<string, { old: unknown; new: unknown }> | null
  tags: string[]
  request_id: string | null
}
export interface TaskType {
  task_type: string
  queue: string
  title: string
  description: string
  default_params: Record<string, unknown>
  last_seen_at: string
}
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export interface TaskRun {
  id: string
  task_type: string
  queue: string
  params: Record<string, unknown>
  status: TaskStatus
  trigger: 'user' | 'schedule' | 'system'
  schedule_id: string | null
  created_by: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  heartbeat_at: string | null
  worker: string | null
  duration_s: number | null
  result: unknown
  error: string | null
}
export interface TaskSummary {
  last_24h: Record<TaskStatus, number>
  active: number
  last_by_type: Array<{ task_type: string; status: TaskStatus; created_at: string; finished_at: string | null }>
}
export interface Schedule {
  id: string
  task_type: string
  name: string
  cron: string
  timezone: string
  params: Record<string, unknown>
  enabled: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_run_id: string | null
  last_run_status: TaskStatus | null
  updated_by: string
  updated_at: string
  version: number
}
export type ScheduleIn = Pick<Schedule, 'task_type' | 'name' | 'cron' | 'timezone' | 'params' | 'enabled'>
export interface LogEntry {
  id: string
  timestamp: string
  service: string | null
  level: string | null
  logger: string | null
  message: string | null
  request_id: string | null
  user: string | null
  task_id: string | null
  exc_info: string | null
  extra: Record<string, unknown>
}
export interface Page<T> {
  items: T[]
  total: number
}

type Query = Record<string, string | number | boolean | null | undefined | Array<string | number>>

export const api = {
  users: {
    list: (search?: string) => http().get<User[]>(`${BASE}/users`, { search }),
    create: (data: { username: string; email: string; first_name: string; last_name: string; role: Role; invite_ttl_days?: number }) =>
      http().post<{ user: User; invite: Invite }>(`${BASE}/users`, data),
    setRole: (id: string, role: Role) => http().put<User>(`${BASE}/users/${id}/role`, { role }),
    setEnabled: (id: string, enabled: boolean) => http().put<User>(`${BASE}/users/${id}/enabled`, { enabled }),
    invite: (id: string, ttl_days?: number) => http().post<Invite>(`${BASE}/users/${id}/invite`, { ttl_days }),
  },
  audit: {
    list: (q: Query) => http().get<Page<AuditEvent>>(`${BASE}/audit`, q),
    facets: () => http().get<{ services: string[]; entity_types: string[]; actions: string[]; tags: string[] }>(`${BASE}/audit/facets`),
  },
  tasks: {
    types: () => http().get<TaskType[]>(`${BASE}/task-types`),
    list: (q: Query) => http().get<Page<TaskRun>>(`${BASE}/tasks`, q),
    summary: () => http().get<TaskSummary>(`${BASE}/tasks/summary`),
    get: (id: string) => http().get<TaskRun>(`${BASE}/tasks/${id}`),
    run: (task_type: string, params: Record<string, unknown>) => http().post<TaskRun>(`${BASE}/tasks`, { task_type, params }),
    cancel: (id: string) => http().post<TaskRun>(`${BASE}/tasks/${id}/cancel`),
  },
  schedules: {
    list: () => http().get<Schedule[]>(`${BASE}/schedules`),
    create: (data: ScheduleIn) => http().post<Schedule>(`${BASE}/schedules`, data),
    update: (id: string, data: ScheduleIn) => http().put<Schedule>(`${BASE}/schedules/${id}`, data),
    remove: (id: string) => http().del<void>(`${BASE}/schedules/${id}`),
    run: (id: string) => http().post<TaskRun>(`${BASE}/schedules/${id}/run`),
    preview: (cron: string, timezone: string) => http().get<{ next_runs: string[] }>(`${BASE}/schedules/preview`, { cron, timezone }),
  },
  logs: {
    search: (q: Query) => http().get<Page<LogEntry>>(`${BASE}/logs`, q),
    services: () => http().get<{ services: string[]; levels: string[] }>(`${BASE}/logs/services`),
  },
}

export const STATUS_META: Record<TaskStatus, { label: string; type: 'info' | 'warning' | 'success' | 'danger' | 'primary' }> = {
  queued: { label: 'В очереди', type: 'info' },
  running: { label: 'Выполняется', type: 'primary' },
  succeeded: { label: 'Успех', type: 'success' },
  failed: { label: 'Ошибка', type: 'danger' },
  cancelled: { label: 'Отменена', type: 'warning' },
}

export const TRIGGER_LABELS: Record<string, string> = { user: 'Пользователь', schedule: 'Расписание', system: 'Система' }
