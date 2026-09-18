<template>
  <el-container class="main-layout">
    <!-- Sidebar -->
    <el-aside :width="sidebarCollapsed ? '64px' : '220px'" class="main-sidebar">
      <div class="sidebar-logo" @click="$router.push('/dashboard')">
        <el-icon :size="24"><OfficeBuilding /></el-icon>
        <span v-show="!sidebarCollapsed" class="logo-text">苏银助手</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="sidebarCollapsed"
        :collapse-transition="false"
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
        router
        class="sidebar-menu"
      >
        <el-menu-item index="/dashboard">
          <el-icon><HomeFilled /></el-icon>
          <span>首页工作台</span>
        </el-menu-item>

        <el-menu-item index="/ai/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>苏银AI问答助手</span>
        </el-menu-item>
        <el-menu-item index="/ai/application">
          <el-icon><DocumentAdd /></el-icon>
          <span>申请书生成</span>
        </el-menu-item>

        <el-menu-item index="/workflow/confirmation-letter">
          <el-icon><DocumentChecked /></el-icon>
          <span>询证函处理</span>
        </el-menu-item>

        <el-menu-item index="/analysis/news">
          <el-icon><Notebook /></el-icon>
          <span>经营晨报新闻中心</span>
        </el-menu-item>

        <el-sub-menu index="system" v-if="authStore.isAdmin">
          <template #title>
            <el-icon><Setting /></el-icon>
            <span>系统管理</span>
          </template>
          <el-menu-item index="/system/users">
            <el-icon><User /></el-icon>
            <span>用户管理</span>
          </el-menu-item>
          <el-menu-item index="/system/tools">
            <el-icon><Switch /></el-icon>
            <span>工具管理</span>
          </el-menu-item>
          <el-menu-item index="/system/prompts">
            <el-icon><Edit /></el-icon>
            <span>Prompt管理</span>
          </el-menu-item>
          <el-menu-item index="/knowledge/knowledge-base">
            <el-icon><FolderOpened /></el-icon>
            <span>知识库管理</span>
          </el-menu-item>
          <el-menu-item index="/system/logs">
            <el-icon><Tickets /></el-icon>
            <span>系统日志</span>
          </el-menu-item>
          <el-menu-item index="/system/settings">
            <el-icon><Tools /></el-icon>
            <span>系统配置</span>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>

    <!-- Main Content -->
    <el-container class="main-right">
      <!-- Topbar -->
      <el-header class="main-topbar">
        <div class="topbar-left">
          <el-icon
            class="collapse-btn"
            :size="20"
            @click="appStore.toggleSidebar()"
          >
            <Fold v-if="!sidebarCollapsed" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="topbar-right">
          <el-tag v-if="authStore.isAdmin" type="danger" size="small" effect="dark" class="admin-badge">管理员</el-tag>
          <el-dropdown trigger="click">
            <span class="user-info">
              <el-avatar :size="32" icon="UserFilled" />
              <span class="user-name">{{ authStore.userInfo?.name || '用户' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item>
                  <el-icon><User /></el-icon>个人中心
                </el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- Page Content -->
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const appStore = useAppStore()

const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)
const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta?.title || '')

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.main-layout {
  height: 100vh;
}

.main-sidebar {
  background-color: #304156;
  overflow-y: auto;
  overflow-x: hidden;
  transition: width 0.3s;
}

.sidebar-logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  cursor: pointer;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.logo-text {
  margin-left: 8px;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}

.sidebar-menu {
  border-right: none;
}

.sidebar-menu:not(.el-menu--collapse) {
  width: 220px;
}

.main-right {
  flex-direction: column;
}

.main-topbar {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e6e6e6;
  padding: 0 20px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.collapse-btn {
  cursor: pointer;
  color: #606266;
}

.collapse-btn:hover {
  color: var(--color-primary);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-badge {
  letter-spacing: 1px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
}

.user-info:hover {
  background: #f5f7fa;
}

.user-name {
  font-size: 14px;
  color: var(--text-primary);
}

.main-content {
  background: var(--content-bg);
  padding: 20px;
  overflow-y: auto;
}
</style>
