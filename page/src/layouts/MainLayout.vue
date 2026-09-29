<template>
  <div class="app-shell">
    <!-- ================= 侧边导航 ================= -->
    <aside class="app-sidebar" :class="{ 'is-collapsed': sidebarCollapsed }">
      <div class="sidebar-brand" @click="go('/dashboard')">
        <BrandMark :size="38" />
        <div class="brand-copy">
          <span class="brand-name">苏银助手</span>
          <span class="brand-sub">SuYin Assistant</span>
        </div>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <div v-for="group in navGroups" :key="group.label" class="nav-group">
          <div class="nav-group__label">{{ group.label }}</div>
          <el-tooltip
            v-for="item in group.items"
            :key="item.path"
            :content="item.title"
            placement="right"
            :offset="14"
            :show-after="180"
            :disabled="!sidebarCollapsed"
          >
            <button
              type="button"
              class="nav-item"
              :class="{ 'is-active': isActive(item.path) }"
              :aria-current="isActive(item.path) ? 'page' : undefined"
              @click="go(item.path)"
            >
              <span class="nav-item__icon">
                <el-icon :size="18"><component :is="item.icon" /></el-icon>
              </span>
              <span class="nav-item__label">{{ item.title }}</span>
            </button>
          </el-tooltip>
        </div>
      </nav>

      <div class="sidebar-footer">
        <div class="sidebar-footer__mark">
          <el-icon :size="14"><Lock /></el-icon>
          <span>内部使用 · v1.0.0</span>
        </div>
      </div>
    </aside>

    <!-- ================= 主区域 ================= -->
    <div class="app-main">
      <header class="app-topbar">
        <div class="topbar-left">
          <button
            type="button"
            class="icon-btn"
            :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="appStore.toggleSidebar()"
          >
            <el-icon :size="18">
              <Fold v-if="!sidebarCollapsed" />
              <Expand v-else />
            </el-icon>
          </button>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="topbar-right">
          <el-tag v-if="authStore.isAdmin" class="role-tag" size="small">
            <el-icon :size="12"><Medal /></el-icon>
            管理员
          </el-tag>

          <el-popover placement="bottom-end" :width="300" trigger="click">
            <template #reference>
              <button type="button" class="icon-btn" aria-label="通知">
                <el-icon :size="18"><Bell /></el-icon>
              </button>
            </template>
            <div class="notice-panel">
              <div class="notice-panel__head">通知</div>
              <div class="notice-panel__empty">
                <el-icon :size="22"><BellFilled /></el-icon>
                <p>暂无新通知</p>
              </div>
            </div>
          </el-popover>

          <span class="topbar-divider" />

          <el-dropdown trigger="click">
            <button type="button" class="user-chip">
              <span class="user-avatar">{{ avatarText }}</span>
              <span class="user-meta">
                <span class="user-name">{{ authStore.userInfo?.name || '用户' }}</span>
                <span class="user-role">{{ authStore.isAdmin ? '管理员' : '普通用户' }}</span>
              </span>
              <el-icon :size="14" class="user-caret"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  <el-icon><User /></el-icon>
                  账号：{{ authStore.userInfo?.username || '-' }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="app-content" :class="{ 'is-flush': isFlushPage }">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import BrandMark from '@/components/BrandMark.vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const appStore = useAppStore()

const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)
const currentTitle = computed(() => route.meta?.title || '')
const isFlushPage = computed(() => route.meta?.flush === true)

const avatarText = computed(() => {
  const name = authStore.userInfo?.name || '用户'
  return name.slice(-2)
})

const navGroups = computed(() => {
  const groups = [
    {
      label: '业务中心',
      items: [
        { path: '/dashboard', title: '首页工作台', icon: 'HomeFilled' },
        { path: '/ai/chat', title: '苏银AI问答助手', icon: 'ChatDotRound' },
        { path: '/ai/application', title: '申请书生成', icon: 'DocumentAdd' },
        { path: '/workflow/confirmation-letter', title: '询证函处理', icon: 'DocumentChecked' },
        { path: '/analysis/news', title: '经营晨报新闻中心', icon: 'Notebook' },
      ],
    },
  ]

  if (authStore.isAdmin) {
    groups.push({
      label: '系统管理',
      items: [
        { path: '/system/users', title: '用户管理', icon: 'User' },
        { path: '/system/tools', title: '工具管理', icon: 'Switch' },
        { path: '/system/prompts', title: 'Prompt管理', icon: 'Edit' },
        { path: '/knowledge/knowledge-base', title: '知识库管理', icon: 'FolderOpened' },
        { path: '/system/logs', title: '系统日志', icon: 'Tickets' },
        { path: '/system/settings', title: '系统配置', icon: 'Tools' },
      ],
    })
  }

  return groups
})

function isActive(path) {
  if (path === '/dashboard') return route.path === '/dashboard'
  return route.path === path || route.path.startsWith(`${path}/`)
}

function go(path) {
  if (route.path !== path) router.push(path)
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--color-bg-app);
}

/* ---------------- 侧边栏 ---------------- */
.app-sidebar {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: var(--sidebar-width);
  background: var(--sidebar-bg);
  transition: width var(--duration-slow) var(--ease-out);
}

.app-sidebar.is-collapsed {
  width: var(--sidebar-collapsed-width);
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  height: var(--topbar-height);
  padding: 0 20px;
  cursor: pointer;
  border-bottom: 1px solid var(--sidebar-border);
  transition: padding var(--duration-slow) var(--ease-out);
}

.is-collapsed .sidebar-brand {
  padding: 0 19px;
}

.brand-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.brand-name {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--sidebar-text-strong);
  white-space: nowrap;
}

.brand-sub {
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--sidebar-group-label);
  white-space: nowrap;
}

.sidebar-nav {
  flex: 1;
  padding: 16px 12px;
  overflow-x: hidden;
  overflow-y: auto;
}

.nav-group + .nav-group {
  margin-top: 18px;
}

.nav-group__label {
  padding: 0 10px 8px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--sidebar-group-label);
  white-space: nowrap;
  transition: opacity var(--duration-fast) var(--ease-standard);
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 42px;
  padding: 0 12px;
  margin-bottom: 2px;
  font-family: inherit;
  font-size: 14px;
  color: var(--sidebar-text);
  text-align: left;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.nav-item:hover {
  background: var(--sidebar-hover-bg);
  color: var(--sidebar-text-strong);
}

.nav-item.is-active {
  background: var(--sidebar-active-bg);
  color: var(--sidebar-text-strong);
  font-weight: 500;
}

.nav-item.is-active::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  width: 3px;
  height: 20px;
  border-radius: 0 3px 3px 0;
  background: var(--sidebar-active-bar);
  transform: translateY(-50%);
}

.nav-item__icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
}

.nav-item__label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.is-collapsed .nav-item {
  justify-content: center;
  padding: 0;
}

.is-collapsed .nav-item__label,
.is-collapsed .nav-group__label,
.is-collapsed .brand-copy,
.is-collapsed .sidebar-footer__mark span {
  display: none;
}

.sidebar-footer {
  padding: 14px 20px 18px;
  border-top: 1px solid var(--sidebar-border);
}

.sidebar-footer__mark {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--sidebar-group-label);
  white-space: nowrap;
}

.is-collapsed .sidebar-footer__mark {
  justify-content: center;
  padding: 0;
}

/* ---------------- 主区域 ---------------- */
.app-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.app-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  height: var(--topbar-height);
  padding: 0 var(--space-6);
  background: var(--topbar-bg);
  border-bottom: 1px solid var(--topbar-border);
  backdrop-filter: saturate(180%) blur(12px);
}

.topbar-left,
.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.topbar-left {
  min-width: 0;
  gap: var(--space-4);
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  color: var(--text-regular);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard),
    border-color var(--duration-fast) var(--ease-standard);
}

.icon-btn:hover {
  color: var(--color-primary);
  background: var(--brand-50);
  border-color: var(--brand-100);
}

.role-tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 24px;
  color: var(--color-accent);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}

.topbar-divider {
  width: 1px;
  height: 22px;
  background: var(--color-border);
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 4px 4px;
  font-family: inherit;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-standard),
    border-color var(--duration-fast) var(--ease-standard);
}

.user-chip:hover {
  background: var(--brand-50);
  border-color: var(--brand-100);
}

.user-avatar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 0.02em;
  border-radius: 50%;
  background: linear-gradient(135deg, #4e7bff, #1c3190);
}

.user-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.25;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.user-role {
  font-size: 11px;
  color: var(--text-secondary);
}

.user-caret {
  color: var(--text-placeholder);
}

.notice-panel__head {
  padding: 0 4px 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  border-bottom: 1px solid var(--color-border-light);
}

.notice-panel__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 26px 0 18px;
  color: var(--text-placeholder);
}

.notice-panel__empty p {
  font-size: 13px;
  color: var(--text-secondary);
}

.app-content {
  flex: 1;
  padding: var(--space-6);
  overflow-y: auto;
}

.app-content.is-flush {
  padding: 0;
  overflow: hidden;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 1080px) {
  .app-sidebar {
    width: var(--sidebar-collapsed-width);
  }

  .app-sidebar .sidebar-brand {
    padding: 0 19px;
  }

  .app-sidebar .nav-item {
    justify-content: center;
    padding: 0;
  }

  .app-sidebar .nav-item__label,
  .app-sidebar .nav-group__label,
  .app-sidebar .brand-copy,
  .app-sidebar .sidebar-footer__mark span {
    display: none;
  }

  .user-meta {
    display: none;
  }
}

@media (max-width: 720px) {
  .app-content {
    padding: var(--space-4);
  }

  .app-topbar {
    padding: 0 var(--space-4);
  }

  .app-topbar :deep(.el-breadcrumb) {
    display: none;
  }
}
</style>
