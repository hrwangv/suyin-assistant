<template>
  <div class="page-header" :class="{ 'flex-between': showExtra }">
    <div class="header-left">
      <h2 class="page-title">{{ title }}</h2>
      <p v-if="description" class="page-description">{{ description }}</p>
    </div>
    <div v-if="showExtra" class="header-extra">
      <slot name="extra" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
})

const showExtra = defineSlots().extra
</script>

<style scoped>
.page-header {
  margin-bottom: 20px;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.page-description {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 4px;
}
</style>
