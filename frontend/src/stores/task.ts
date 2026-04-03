import axios from 'axios'
import { defineStore } from 'pinia'
import { ref } from 'vue'

import { createParseTask, fetchTask, fetchTaskResult } from '../api/parse'
import type { ParseAcceptedResponse, TaskRecord, TaskResult, TaskStatus } from '../types/task'

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
  const errorMessage = ref('')
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

  async function loadTaskState(taskId: string): Promise<void> {
    const task = await fetchTask(taskId)
    if (!isTaskRecord(task)) {
      throw new Error('任务接口返回格式不正确。')
    }

    currentTask.value = task

    if (task.status === 'success') {
      currentResult.value = await fetchTaskResult(taskId)
      stopPolling()
      return
    }

    if (task.status === 'failed') {
      currentResult.value = null
      stopPolling()
      return
    }

    currentResult.value = null
    startPolling(taskId)
  }

  async function bootstrap(): Promise<void> {
    currentTask.value = null
    currentResult.value = null
    errorMessage.value = ''
  }

  async function submitUrl(url: string): Promise<void> {
    stopPolling()
    errorMessage.value = ''
    currentResult.value = null
    submitting.value = true

    try {
      const response = await createParseTask({ url, delivery_mode: 'auto' })
      if (!isParseAcceptedResponse(response)) {
        throw new Error('解析接口返回格式不正确，请检查 /api/v1/parse 是否已正确指向后端。')
      }

      currentTask.value = response.task

      if (TERMINAL_STATUSES.includes(response.task.status)) {
        await loadTaskState(response.task.task_id)
      } else {
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
        await loadTaskState(taskId)
      } catch (error) {
        errorMessage.value = extractErrorMessage(error)
        stopPolling()
      }
    }, 2000)
  }

  return {
    currentTask,
    currentResult,
    errorMessage,
    submitting,
    polling,
    bootstrap,
    submitUrl,
    stopPolling,
  }
})

function isParseAcceptedResponse(value: unknown): value is ParseAcceptedResponse {
  return isObject(value) && isTaskRecord(value.task)
}
