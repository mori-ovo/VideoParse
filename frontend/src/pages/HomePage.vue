<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { storeToRefs } from 'pinia'

import ParseForm from '../components/ParseForm.vue'
import TaskStatusCard from '../components/TaskStatusCard.vue'
import { useTaskStore } from '../stores/task'

const taskStore = useTaskStore()
const { currentTask, currentResult, errorMessage, health, history, polling, submitting, systemNote } =
  storeToRefs(taskStore)

onMounted(() => {
  void taskStore.bootstrap()
})

onBeforeUnmount(() => {
  taskStore.stopPolling()
})
</script>

<template>
  <main class="page-shell">
    <div class="background-orb orb-left"></div>
    <div class="background-orb orb-right"></div>

    <section class="hero-grid">
      <ParseForm :loading="submitting" @submit="taskStore.submitUrl" />
      <TaskStatusCard :task="currentTask" :result="currentResult" />
    </section>

    <p v-if="errorMessage" class="notice notice-error">{{ errorMessage }}</p>
    <p v-else-if="systemNote" class="notice notice-info">{{ systemNote }}</p>

    <section class="info-grid">
      <article class="panel info-panel">
        <p class="eyebrow">System</p>
        <h2>基础运行信息</h2>
        <ul class="info-list">
          <li>后端状态：{{ health?.status ?? 'loading...' }}</li>
          <li>缓存清理间隔：{{ health?.cleanup_interval_hours ?? '-' }} 小时</li>
          <li>缓存保留时长：{{ health?.cleanup_retention_hours ?? '-' }} 小时</li>
          <li>yt-dlp 可用：{{ health?.yt_dlp_available ? '是' : '否' }}</li>
          <li>ffmpeg 可用：{{ health?.ffmpeg_available ? '是' : '否' }}</li>
          <li>默认模式：{{ health?.default_delivery_mode === 'direct' ? '直链优先' : '下载合流' }}</li>
          <li>支持站点：{{ health?.supported_platforms?.join(' / ') ?? '-' }}</li>
          <li>任务轮询状态：{{ polling ? '进行中' : '空闲' }}</li>
        </ul>
      </article>

      <article class="panel info-panel">
        <p class="eyebrow">History</p>
        <h2>最近任务</h2>
        <div v-if="history.length" class="history-list">
          <div v-for="item in history" :key="item.task_id" class="history-item">
            <div>
              <p class="history-title">{{ item.title }}</p>
              <p class="history-subtitle">{{ item.platform }} / {{ item.status }}</p>
            </div>
            <strong>{{ item.progress }}%</strong>
          </div>
        </div>
        <p v-else class="empty-copy">暂无任务记录。</p>
      </article>
    </section>
  </main>
</template>
