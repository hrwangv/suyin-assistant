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
    // 先转义，避免内容里的标签被当成 HTML 执行
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Headers
    .replace(/^#### (.*$)/gim, '<h5>$1</h5>')
    .replace(/^### (.*$)/gim, '<h4>$1</h4>')
    .replace(/^## (.*$)/gim, '<h3>$1</h3>')
    .replace(/^# (.*$)/gim, '<h2>$1</h2>')
    // Bold / Italic
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Line breaks
    .replace(/\n/g, '<br>')
    // Links
    .replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    )
  return html
})
</script>

<style scoped>
.markdown-viewer {
  font-size: 14px;
  line-height: 1.8;
  color: var(--text-primary);
  word-break: break-word;
}

.markdown-viewer :deep(h2) {
  margin: 18px 0 10px;
  font-size: 18px;
  font-weight: 600;
}

.markdown-viewer :deep(h3) {
  margin: 14px 0 8px;
  font-size: 16px;
  font-weight: 600;
}

.markdown-viewer :deep(h4) {
  margin: 12px 0 6px;
  font-size: 14px;
  font-weight: 600;
}

.markdown-viewer :deep(h5) {
  margin: 10px 0 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular);
}

.markdown-viewer :deep(code) {
  padding: 2px 6px;
  font-family: 'IBM Plex Mono', Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  color: var(--brand-700);
  background: var(--brand-50);
  border-radius: var(--radius-xs);
}

.markdown-viewer :deep(a) {
  color: var(--color-primary);
  text-underline-offset: 3px;
  text-decoration: underline;
  text-decoration-color: var(--brand-200);
}

.markdown-viewer :deep(a:hover) {
  text-decoration-color: var(--color-primary);
}

.markdown-viewer :deep(strong) {
  font-weight: 600;
}

.markdown-viewer :deep(em) {
  font-style: italic;
  color: var(--text-regular);
}
</style>
