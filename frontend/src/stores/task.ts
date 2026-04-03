import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

import { createParseTask, fetchHealth, fetchHistory, fetchTask, fetchTaskResult } from '../api/parse'
import type { DeliveryMode, HealthResponse, TaskRecord, TaskResult, TaskStatus } from '../types/task'

const TERMINAL_STATUSES: TaskStatus[] = ['success', 'failed']

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}

function isTaskRecord(value: unknown): value is TaskRecord {
  return (
    isObject(value) &&
    typeof value.task_id === 'string' &&
    typeof value.status === 'string' &&
    typeof value.platform === 'string'
  )
}

function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail ?? error.message
  }

  if (error instanceof Error) {
    return error.message
  }

  return '发生未知错误，请稍后重试。'
}

export const useTaskStore = defineStore('task', () => {
  const currentTask = ref<TaskRecord | null>(null)
  const currentResult = ref<TaskResult | null>(null)
  const history = ref<TaskRecord[]>([])
  const health = ref<HealthResponse | null>(null)
  const errorMessage = ref('')
  const systemNote = ref('')
  const submitting = ref(false)
  const polling = ref(false)

  let pollTimer: number | null = null

  function stopPolling(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
    polling.value = false
  }

  async function loadHealth(): Promise<void> {
    health.value = await fetchHealth()
  }

  async function loadHistory(): Promise<void> {
    history.value = await fetchHistory()
  }

  async function bootstrap(): Promise<void> {
    try {
      await Promise.all([loadHealth(), loadHistory()])
    } catch (error) {
      errorMessage.value = extractErrorMessage(error)
    }
  }

  async function submitUrl(url: string, deliveryMode: DeliveryMode): Promise<void> {
    stopPolling()
    errorMessage.value = ''
    currentResult.value = null
    submitting.value = true

    try {
      const response = await createParseTask({ url, delivery_mode: deliveryMode })
      if (!isObject(response) || !isTaskRecord(response.task)) {
        throw new Error('解析接口返回格式不正确，请检查 /api/v1/parse 是否已正确反向代理到后端。')
      }

      currentTask.value = response.task
      systemNote.value = typeof response.note === 'string' ? response.note : ''
      await loadHistory()

      if (!TERMINAL_STATUSES.includes(response.task.status)) {
        startPolling(response.task.task_id)
      }
    } catch (error) {
      errorMessage.value = extractErrorMessage(error)
    } finally {
      submitting.value = false
    }
  }

  function startPolling(taskId: string): void {
    stopPolling()
    polling.value = true

    pollTimer = window.setInterval(async () => {
      try {
        const task = await fetchTask(taskId)
        if (!isTaskRecord(task)) {
          throw new Error('任务接口返回格式不正确，请检查 /api/v1/tasks 是否已正确反向代理到后端。')
        }

        currentTask.value = task

        if (task.status === 'success') {
          currentResult.value = await fetchTaskResult(taskId)
          await loadHistory()
          stopPolling()
        } else if (task.status === 'failed') {
          await loadHistory()
          stopPolling()
        }
      } catch (error) {
        errorMessage.value = extractErrorMessage(error)
        stopPolling()
      }
    }, 2000)
  }

  return {
    currentTask,
    currentResult,
    history,
    health,
    errorMessage,
    systemNote,
    submitting,
    polling,
    bootstrap,
    submitUrl,
    stopPolling,
  }
})
