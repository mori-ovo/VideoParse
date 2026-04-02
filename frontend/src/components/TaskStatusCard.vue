<script setup lang="ts">
import type { TaskRecord, TaskResult } from '../types/task'

defineProps<{
  task: TaskRecord | null
  result: TaskResult | null
}>()

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
        <span>{{ task.delivery_mode === 'direct' ? '直链优先' : '下载合流' }}</span>
        <span>
          {{
            task.direct_playable
              ? '存在单文件直链'
              : task.requires_merge
                ? '单 URL 需合流'
                : '单文件流程'
          }}
        </span>
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
          <dt>最近更新时间</dt>
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
          <dt>输出大小</dt>
          <dd>{{ formatFileSize(result?.file_size) }}</dd>
        </div>
        <div>
          <dt>直链可用</dt>
          <dd>{{ task.direct_playable ? '是' : '否' }}</dd>
        </div>
      </dl>

      <div v-if="result" class="result-links">
        <a
          v-if="result.proxy_url"
          class="download-link"
          :href="result.proxy_url"
          target="_blank"
          rel="noreferrer"
        >
          项目代理直链
        </a>
        <a
          v-if="result.redirect_url"
          class="download-link secondary-link"
          :href="result.redirect_url"
          target="_blank"
          rel="noreferrer"
        >
          重定向直链
        </a>
        <a
          v-if="result.download_url"
          class="download-link secondary-link"
          :href="result.download_url"
          target="_blank"
          rel="noreferrer"
        >
          下载文件
        </a>
        <a
          v-if="result.direct_url"
          class="download-link secondary-link"
          :href="result.direct_url"
          target="_blank"
          rel="noreferrer"
        >
          原始直链
        </a>
        <a
          v-if="result.video_proxy_url"
          class="download-link secondary-link"
          :href="result.video_proxy_url"
          target="_blank"
          rel="noreferrer"
        >
          视频代理直链
        </a>
        <a
          v-if="result.video_redirect_url"
          class="download-link secondary-link"
          :href="result.video_redirect_url"
          target="_blank"
          rel="noreferrer"
        >
          视频流重定向
        </a>
        <a
          v-if="result.audio_proxy_url"
          class="download-link secondary-link"
          :href="result.audio_proxy_url"
          target="_blank"
          rel="noreferrer"
        >
          音频代理直链
        </a>
        <a
          v-if="result.audio_redirect_url"
          class="download-link secondary-link"
          :href="result.audio_redirect_url"
          target="_blank"
          rel="noreferrer"
        >
          音频流重定向
        </a>
      </div>
      <p v-if="result?.expires_note" class="task-message">{{ result.expires_note }}</p>
      <p v-if="task.error_message" class="task-message" style="color: #a12626">{{ task.error_message }}</p>
    </div>

    <p v-else class="empty-copy">提交一个链接后，任务状态会在这里实时刷新。</p>
  </section>
</template>
