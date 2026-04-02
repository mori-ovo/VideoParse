<script setup lang="ts">
import { ref } from 'vue'

import type { DeliveryMode } from '../types/task'

const props = defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [url: string, deliveryMode: DeliveryMode]
}>()

const url = ref('')
const deliveryMode = ref<DeliveryMode>('direct')

function handleSubmit(): void {
  const value = url.value.trim()
  if (!value || props.loading) {
    return
  }
  emit('submit', value, deliveryMode.value)
}
</script>

<template>
  <section class="panel parse-panel">
    <div class="panel-heading">
      <p class="eyebrow">Universal Video Parse</p>
      <h1>前后端分离基础骨架已经就位</h1>
      <p class="panel-copy">
        默认走直链优先模式，减少 1C1G 服务器的 CPU、内存和磁盘压力。只有需要单文件合流时才建议切到下载模式。
      </p>
    </div>

    <div class="mode-row">
      <button
        class="mode-button"
        :class="{ active: deliveryMode === 'direct' }"
        type="button"
        :disabled="loading"
        @click="deliveryMode = 'direct'"
      >
        直链优先
      </button>
      <button
        class="mode-button"
        :class="{ active: deliveryMode === 'download' }"
        type="button"
        :disabled="loading"
        @click="deliveryMode = 'download'"
      >
        下载合流
      </button>
    </div>

    <div class="input-row">
      <input
        v-model="url"
        class="url-input"
        type="url"
        placeholder="粘贴哔哩哔哩 / 抖音 / Twitter / YouTube / Reddit 链接"
        :disabled="loading"
        @keyup.enter="handleSubmit"
      />
      <button class="primary-button" type="button" :disabled="loading" @click="handleSubmit">
        {{ loading ? '提交中...' : '开始解析' }}
      </button>
    </div>
  </section>
</template>
