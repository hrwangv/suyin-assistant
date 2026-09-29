<template>
  <div class="page-header" :class="{ 'has-icon': !!icon }">
    <div class="page-header__main">
      <span v-if="icon" class="page-header__icon">
        <el-icon :size="20"><component :is="icon" /></el-icon>
      </span>
      <div class="page-header__text">
        <h1 class="page-title">{{ title }}</h1>
        <p v-if="description" class="page-description">{{ description }}</p>
      </div>
    </div>
    <div v-if="hasExtra" class="page-header__extra">
      <slot name="extra" />
    </div>
  </div>
</template>

<script setup>
import { useSlots } from 'vue'

defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  icon: { type: String, default: '' },
})

const hasExtra = !!useSlots().extra
</script>

<style scoped>
.page-header__main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.page-header__icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  color: var(--color-primary);
  background: var(--brand-50);
  border: 1px solid var(--brand-100);
  border-radius: var(--radius-md);
}

.page-header__text {
  min-width: 0;
}

/* 有图标时不再显示标题左侧的品牌色竖条 */
.has-icon :deep(.page-title::before) {
  display: none;
}

.page-header__extra {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}
</style>
