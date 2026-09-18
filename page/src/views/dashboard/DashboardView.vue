<template>
  <div class="dashboard-page">
    <div class="welcome-section">
      <h2 class="welcome-text">
        欢迎回来，{{ authStore.userInfo?.name || '用户' }}
        <span class="welcome-date">{{ currentDate }}</span>
      </h2>
      <p class="welcome-subtitle">这里是苏银助手工作台，请从左侧菜单进入业务功能。</p>
    </div>

  </div>
</template>

<script setup>
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const currentDate = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
})

</script>

<style scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 96px);
  margin: -20px;
  padding: 20px;
  gap: 20px;
}

.welcome-section {
  flex-shrink: 0;
  margin-top: 24px;
}

.welcome-text {
  font-size: 34px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 16px;
}

.welcome-date {
  font-size: 16px;
  font-weight: 400;
  color: var(--text-secondary);
}

.welcome-subtitle {
  margin-top: 12px;
  color: var(--text-secondary);
  font-size: 15px;
}

</style>
