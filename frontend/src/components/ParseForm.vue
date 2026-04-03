<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [url: string]
}>()

const url = ref('')

function handleSubmit(): void {
  const value = url.value.trim()
  if (!value || props.loading) {
    return
  }

  emit('submit', value)
}
</script>

<template>
  <section class="panel parse-panel">
    <div class="panel-heading">
      <p class="eyebrow">Universal Video Parse</p>
      <h1>直链优先，分离流自动合成</h1>
      <p class="panel-copy">
        现在默认使用自动模式：如果源站本身有单文件直链，就直接返回可复制地址；如果只有音视频分离流，就自动下载并通过
        ffmpeg 合成为单文件。
      </p>
    </div>

    <div class="input-row">
      <input
        v-model="url"
        class="url-input"
        type="url"
        placeholder="粘贴 Bilibili / 抖音 / Twitter / YouTube / Reddit 链接"
        :disabled="loading"
        @keyup.enter="handleSubmit"
      />
      <button class="primary-button" type="button" :disabled="loading" @click="handleSubmit">
        {{ loading ? '解析中...' : '开始解析' }}
      </button>
    </div>
  </section>
</template>
