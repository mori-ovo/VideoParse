<script setup lang="ts">
import { computed, ref } from 'vue'

import type { TaskRecord, TaskResult } from '../types/task'

const props = defineProps<{
  task: TaskRecord | null
  result: TaskResult | null
}>()

const copyFeedback = ref('复制直链')

function formatDate(value: string): string {
  return new Date(value).toLocaleString('zh-CN')
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) {
    return '-'
  }

  const hour = Math.floor(seconds / 3600)
  const minute = Math.floor((seconds % 3600) / 60)
  const second = seconds % 60

  if (hour > 0) {
    return `${hour}:${String(minute).padStart(2, '0')}:${String(second).padStart(2, '0')}`
  }

  return `${minute}:${String(second).padStart(2, '0')}`
}

function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) {
    return '-'
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = bytes
  let unitIndex = 0

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }

  return `${size.toFixed(size >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

const copyUrl = computed(() => {
  const result = props.result
  if (!result) {
    return null
  }

  return (
    result.play_url ??
    result.proxy_url ??
    result.direct_url ??
    result.video_proxy_url ??
    result.download_url ??
    result.redirect_url ??
    null
  )
})

const downloadUrl = computed(() => {
  const result = props.result
  if (!result) {
    return null
  }

  return result.download_url ?? result.play_url ?? result.proxy_url ?? result.direct_url ?? null
})

const linkSummary = computed(() => {
  const result = props.result
  if (!result) {
    return ''
  }

  if (result.play_url && result.download_url) {
    return '复制直链会复制可播放的视频地址；下载视频会使用单独的下载地址。'
  }

  if (result.play_url) {
    return '当前结果已经生成可播放的视频直链，适合复制给播放器或外部应用。'
  }

  if (result.video_proxy_url || result.audio_proxy_url) {
    return '当前源站仍是分离流。只有在源站本身存在单文件流，或者后端合流完成后，才能得到单文件视频地址。'
  }

  return ''
})

const streamSummary = computed(() => {
  const task = props.task
  if (!task) {
    return '-'
  }

  if (task.direct_playable) {
    return '存在单文件直链'
  }

  if (task.requires_merge) {
    return '检测到音视频分离流'
  }

  return '标准媒体流'
})

async function handleCopy(): Promise<void> {
  if (!copyUrl.value) {
    return
  }

  try {
    await navigator.clipboard.writeText(copyUrl.value)
    copyFeedback.value = '已复制'
    window.setTimeout(() => {
      copyFeedback.value = '复制直链'
    }, 1500)
  } catch {
    copyFeedback.value = '复制失败'
    window.setTimeout(() => {
      copyFeedback.value = '复制直链'
    }, 1500)
  }
}
</script>

<template>
  <section class="panel task-panel">
    <div class="panel-heading">
      <p class="eyebrow">Task Status</p>
      <h2>当前任务</h2>
    </div>

    <div v-if="task" class="task-body">
      <div class="task-meta">
        <span class="status-badge" :data-status="task.status">{{ task.status }}</span>
        <span>{{ task.platform }}</span>
        <span>自动模式</span>
        <span>{{ streamSummary }}</span>
      </div>

      <p class="task-title">{{ task.title }}</p>
      <p class="task-message">{{ task.message }}</p>

      <div class="progress-track">
        <div class="progress-fill" :style="{ width: `${task.progress}%` }"></div>
      </div>

      <dl class="task-grid">
        <div>
          <dt>任务 ID</dt>
          <dd>{{ task.task_id }}</dd>
        </div>
        <div>
          <dt>创建时间</dt>
          <dd>{{ formatDate(task.created_at) }}</dd>
        </div>
        <div>
          <dt>原始链接</dt>
          <dd class="truncate">{{ task.source_url }}</dd>
        </div>
        <div>
          <dt>更新时间</dt>
          <dd>{{ formatDate(task.updated_at) }}</dd>
        </div>
        <div>
          <dt>发布者</dt>
          <dd>{{ task.uploader || '-' }}</dd>
        </div>
        <div>
          <dt>时长</dt>
          <dd>{{ formatDuration(task.duration) }}</dd>
        </div>
        <div>
          <dt>解析器</dt>
          <dd>{{ task.extractor || '-' }}</dd>
        </div>
        <div>
          <dt>文件大小</dt>
          <dd>{{ formatFileSize(result?.file_size) }}</dd>
        </div>
        <div>
          <dt>单文件直链</dt>
          <dd>{{ task.direct_playable ? '是' : '否' }}</dd>
        </div>
      </dl>

      <div v-if="result" class="result-links">
        <button class="download-link" type="button" :disabled="!copyUrl" @click="handleCopy">
          {{ copyFeedback }}
        </button>
        <a
          v-if="downloadUrl"
          class="download-link secondary-link"
          :href="downloadUrl"
          download
          target="_blank"
          rel="noreferrer"
        >
          下载视频
        </a>
      </div>

      <p v-if="copyUrl" class="url-preview">{{ copyUrl }}</p>
      <p v-if="linkSummary" class="task-message">{{ linkSummary }}</p>
      <p v-if="result?.expires_note" class="task-message">{{ result.expires_note }}</p>
      <p v-if="task.error_message" class="task-message task-error">{{ task.error_message }}</p>
    </div>

    <p v-else class="empty-copy">提交一个链接后，任务状态会在这里实时刷新。</p>
  </section>
</template>
