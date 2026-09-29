<template>
  <svg
    class="brand-mark"
    :width="size"
    :height="size"
    viewBox="0 0 40 40"
    fill="none"
    role="img"
    aria-label="苏银助手"
  >
    <defs>
      <linearGradient :id="gradientId" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="#4E7BFF" />
        <stop offset="55%" stop-color="#2447D8" />
        <stop offset="100%" stop-color="#152A73" />
      </linearGradient>
    </defs>
    <rect width="40" height="40" rx="12" :fill="`url(#${gradientId})`" />
    <rect x="0.5" y="0.5" width="39" height="39" rx="11.5" stroke="rgba(255,255,255,0.22)" />
    <!-- 对话气泡：AI 问答助手 -->
    <path
      d="M20 10.5c-5.2 0-9.4 3.3-9.4 7.4 0 2.3 1.3 4.3 3.4 5.7l-.8 4.1c-.1.5.4.9.9.7l4.3-2.1c.5.1 1 .1 1.6.1 5.2 0 9.4-3.3 9.4-7.4s-4.2-8.5-9.4-8.5Z"
      fill="#fff"
      fill-opacity="0.96"
    />
    <circle cx="15.6" cy="17.9" r="1.35" fill="#2447D8" />
    <circle cx="20" cy="17.9" r="1.35" fill="#2447D8" fill-opacity="0.72" />
    <circle cx="24.4" cy="17.9" r="1.35" fill="#2447D8" fill-opacity="0.46" />
  </svg>
</template>

<script setup>
defineProps({
  size: { type: [Number, String], default: 36 },
})

const gradientId = `brand-mark-${Math.random().toString(36).slice(2, 9)}`
</script>

<style scoped>
.brand-mark {
  display: block;
  flex-shrink: 0;
}
</style>
