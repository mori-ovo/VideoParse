<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import type { TaskRecord, TaskResult } from '../types/task'

const props = defineProps<{
  task: TaskRecord | null
  result: TaskResult | null
}>()

const defaultCopyLabel = '复制链接'
const copyFeedback = ref(defaultCopyLabel)

let copyResetTimer: number | null = null

function decodeHtmlEntities(value: string): string {
  const textarea = document.createElement('textarea')
  textarea.innerHTML = value
  return textarea.value
}

function buildPublicFileName(fileName: string): string {
  const normalized = decodeHtmlEntities(fileName).trim()
  const lastDotIndex = normalized.lastIndexOf('.')
  const hasExtension = lastDotIndex > 0 && lastDotIndex < normalized.length - 1
  const stem = hasExtension ? normalized.slice(0, lastDotIndex) : normalized
  const extension = hasExtension ? normalized.slice(lastDotIndex).toLowerCase() : '.mp4'

  const publicStem = stem
    .replace(/[^0-9A-Za-z\u4e00-\u9fff]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 120 - extension.length)

  return `${publicStem || 'video'}${extension}`
}

function buildShortPublicFileName(result: TaskResult): string | null {
  if (!result.file_id) {
    return null
  }

  const sourceName = result.file_name?.trim() || ''
  const lastDotIndex = sourceName.lastIndexOf('.')
  const hasExtension = lastDotIndex > 0 && lastDotIndex < sourceName.length - 1
  const extension = hasExtension ? sourceName.slice(lastDotIndex).toLowerCase() : '.mp4'
  return `${result.file_id}${extension}`
}

function buildFilePlayUrl(result: TaskResult): string | null {
  if (!result.file_id) {
    return null
  }

  const seedUrl = result.play_url ?? result.download_url
  if (!seedUrl) {
    return null
  }

  try {
    const publicFileName = buildShortPublicFileName(result)
    if (!publicFileName) {
      return null
    }

    const resolved = new URL(seedUrl, window.location.origin)
    const shortPath = `/files/${encodeURIComponent(publicFileName)}`

    if (resolved.pathname === shortPath) {
      return resolved.toString()
    }

    if (resolved.pathname.endsWith('/download')) {
      resolved.pathname = shortPath
      return resolved.toString()
    }

    if (resolved.pathname.startsWith(`/files/${result.file_id}`)) {
      resolved.pathname = shortPath
      return resolved.toString()
    }
  } catch {
    return null
  }

  return null
}

function buildDownloadName(): string | undefined {
  if (props.result?.file_name) {
    return buildPublicFileName(props.result.file_name)
  }

  const rawTitle = props.task?.title?.trim()
  if (!rawTitle) {
    return undefined
  }

  return buildPublicFileName(rawTitle)
}

function fallbackCopyText(value: string): boolean {
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()

  let copied = false
  try {
    copied = document.execCommand('copy')
  } catch {
    copied = false
  } finally {
    document.body.removeChild(textarea)
  }

  return copied
}

function setCopyFeedback(value: string): void {
  copyFeedback.value = value

  if (copyResetTimer !== null) {
    window.clearTimeout(copyResetTimer)
  }

  copyResetTimer = window.setTimeout(() => {
    copyFeedback.value = defaultCopyLabel
    copyResetTimer = null
  }, 1500)
}

const copyUrl = computed(() => {
  const result = props.result
  if (!result) {
    return null
  }

  if (result.file_id) {
    return buildFilePlayUrl(result) ?? result.play_url ?? result.download_url ?? null
  }

  return result.direct_url ?? result.play_url ?? result.proxy_url ?? result.download_url ?? null
})

const downloadUrl = computed(() => {
  const result = props.result
  if (!result) {
    return null
  }

  if (!result.file_id) {
    return result.proxy_url ?? result.download_url ?? result.play_url ?? null
  }

  return result.download_url ?? result.play_url ?? result.proxy_url ?? null
})

const downloadName = computed(() => {
  return buildDownloadName()
})

const resultTitle = computed(() => {
  return decodeHtmlEntities(props.task?.title?.trim() || '链接已生成')
})

async function handleCopy(): Promise<void> {
  if (!copyUrl.value) {
    return
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(copyUrl.value)
      setCopyFeedback('已复制')
      return
    }
  } catch {
    // Ignore and try fallback copy below.
  }

  if (fallbackCopyText(copyUrl.value)) {
    setCopyFeedback('已复制')
    return
  }

  setCopyFeedback('复制失败')
}

onBeforeUnmount(() => {
  if (copyResetTimer !== null) {
    window.clearTimeout(copyResetTimer)
  }
})
</script>

<template>
  <section class="glass-card result-panel">
    <div class="result-copy">
      <h2 class="status-title status-title-compact">{{ resultTitle }}</h2>
    </div>

    <div class="action-row">
      <button class="action-button action-primary" type="button" :disabled="!copyUrl" @click="handleCopy">
        {{ copyFeedback }}
      </button>
      <a
        v-if="downloadUrl"
        class="action-button action-secondary"
        :href="downloadUrl"
        :download="downloadName"
        :target="result?.file_id ? undefined : '_blank'"
        :rel="result?.file_id ? undefined : 'noreferrer'"
      >
        下载视频
      </a>
      <button v-else class="action-button action-secondary" type="button" disabled>
        下载视频
      </button>
    </div>
  </section>
</template>
