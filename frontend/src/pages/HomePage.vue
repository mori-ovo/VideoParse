<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { storeToRefs } from 'pinia'

import ParseForm from '../components/ParseForm.vue'
import TaskStatusCard from '../components/TaskStatusCard.vue'
import { useTaskStore } from '../stores/task'

const taskStore = useTaskStore()
const { currentTask, currentResult, errorMessage, polling, submitting } = storeToRefs(taskStore)

const formLoading = computed(() => submitting.value || polling.value)
const formActive = computed(() => Boolean(currentTask.value || currentResult.value || formLoading.value))
const progressValue = computed(() => currentTask.value?.progress ?? 0)
const showProgress = computed(() => Boolean(currentTask.value && !currentResult.value))

const statusText = computed(() => {
  if (currentTask.value?.status === 'failed') {
    return currentTask.value.error_message || '解析失败，请稍后重试。'
  }

  if (currentResult.value) {
    return currentTask.value?.message || '链接已生成，可以复制或下载。'
  }

  if (currentTask.value && formLoading.value) {
    return currentTask.value.message || '正在解析并生成本站链接...'
  }

  return ''
})

onMounted(() => {
  void taskStore.bootstrap()
})

onBeforeUnmount(() => {
  taskStore.stopPolling()
})
</script>

<template>
  <main class="page-shell">
    <div class="page-glow glow-top"></div>
    <div class="page-glow glow-side"></div>
    <div class="page-glain"></div>

    <section class="stage stage-single" :class="{ 'stage-single-active': formActive }">
      <ParseForm
        :loading="formLoading"
        :status-text="statusText"
        :progress="progressValue"
        :active="formActive"
        :show-progress="showProgress"
        @submit="taskStore.submitUrl"
      />
      <TaskStatusCard v-if="currentResult" :task="currentTask" :result="currentResult" />
    </section>

    <p v-if="errorMessage" class="notice notice-error">{{ errorMessage }}</p>
    <p v-else-if="currentTask?.error_message" class="notice notice-error">{{ currentTask.error_message }}</p>
  </main>
</template>
