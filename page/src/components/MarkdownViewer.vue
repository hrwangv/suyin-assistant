<template>
  <div class="markdown-viewer" v-html="renderedContent" />
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  content: { type: String, default: '' },
})

const renderedContent = computed(() => {
  if (!props.content) return ''
  let html = props.content
    // Headers
    .replace(/^### (.*$)/gim, '<h4>$1</h4>')
    .replace(/^## (.*$)/gim, '<h3>$1</h3>')
    .replace(/^# (.*$)/gim, '<h2>$1</h2>')
    // Bold
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Line breaks
    .replace(/\n/g, '<br>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
  return html
})
</script>

<style scoped>
.markdown-viewer {
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-primary);
}

.markdown-viewer :deep(h2) {
  font-size: 18px;
  font-weight: 600;
  margin: 16px 0 8px;
}

.markdown-viewer :deep(h3) {
  font-size: 16px;
  font-weight: 600;
  margin: 12px 0 6px;
}

.markdown-viewer :deep(h4) {
  font-size: 14px;
  font-weight: 600;
  margin: 10px 0 4px;
}

.markdown-viewer :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: 'Menlo', 'Monaco', monospace;
  font-size: 13px;
}

.markdown-viewer :deep(a) {
  color: var(--color-primary);
}

.markdown-viewer :deep(strong) {
  font-weight: 600;
}

.markdown-viewer :deep(em) {
  font-style: italic;
}
</style>
