<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  loading: boolean
  statusText: string
  progress: number
  active: boolean
  showProgress: boolean
}>()

const emit = defineEmits<{
  submit: [url: string]
}>()

const url = ref('')

const normalizedProgress = computed(() => {
  return Math.max(0, Math.min(100, props.progress))
})

function handleSubmit(): void {
  const value = url.value.trim()
  if (!value || props.loading) {
    return
  }

  emit('submit', value)
  url.value = ''
}
</script>

<template>
  <section class="parse-panel" :class="{ 'parse-panel-active': active }">
    <div class="composer">
      <label class="composer-label sr-only" for="parse-url">视频链接</label>
      <div class="composer-field">
        <input
          id="parse-url"
          name="parse-url"
          v-model="url"
          class="url-input"
          type="url"
          placeholder="请输入视频地址"
          :disabled="loading"
          autocomplete="off"
          autocapitalize="off"
          autocorrect="off"
          spellcheck="false"
          @keyup.enter="handleSubmit"
        />
        <button class="primary-button" type="button" :disabled="loading" @click="handleSubmit">
          {{ loading ? '解析中' : '立即解析' }}
        </button>
      </div>
    </div>

    <div v-if="props.showProgress" class="progress-stack">
      <div class="progress-track" aria-hidden="true">
        <div class="progress-fill" :style="{ width: `${normalizedProgress}%` }"></div>
      </div>
      <p v-if="statusText" class="form-status">{{ statusText }}</p>
    </div>
  </section>
</template>
