import { http } from './http'
import type {
  HealthResponse,
  ParseAcceptedResponse,
  ParseRequest,
  TaskRecord,
  TaskResult,
} from '../types/task'

export async function createParseTask(payload: ParseRequest): Promise<ParseAcceptedResponse> {
  const { data } = await http.post<ParseAcceptedResponse>('/parse', payload)
  return data
}

export async function fetchTask(taskId: string): Promise<TaskRecord> {
  const { data } = await http.get<TaskRecord>(`/tasks/${taskId}`)
  return data
}

export async function fetchTaskResult(taskId: string): Promise<TaskResult> {
  const { data } = await http.get<TaskResult>(`/tasks/${taskId}/result`)
  return data
}

export async function fetchHistory(): Promise<TaskRecord[]> {
  const { data } = await http.get<TaskRecord[]>('/history')
  return data
}

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>('/health')
  return data
}

