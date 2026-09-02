import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/LoginView.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/DashboardView.vue'),
        meta: { title: '首页工作台', icon: 'HomeFilled' },
      },
      {
        path: 'ai/chat',
        name: 'AIChat',
        component: () => import('@/views/ai/ChatView.vue'),
        meta: { title: 'AI聊天', icon: 'ChatDotRound' },
      },
      {
        path: 'ai/morning-report',
        name: 'MorningReport',
        component: () => import('@/views/ai/MorningReportView.vue'),
        meta: { title: '晨报助手', icon: 'Sunrise' },
      },
      {
        path: 'ai/application',
        name: 'Application',
        component: () => import('@/views/ai/ApplicationView.vue'),
        meta: { title: '申请书生成', icon: 'DocumentAdd' },
      },
      {
        path: 'ai/due-diligence',
        name: 'DueDiligence',
        component: () => import('@/views/ai/DueDiligenceView.vue'),
        meta: { title: '尽调助手', icon: 'Search' },
      },
      {
        path: 'knowledge/knowledge-base',
        name: 'KnowledgeBase',
        component: () => import('@/views/knowledge/KnowledgeBaseView.vue'),
        meta: { title: '企业知识库', icon: 'FolderOpened', requiresAdmin: true },
      },
      {
        path: 'workflow/confirmation-letter',
        name: 'ConfirmationLetter',
        component: () => import('@/views/workflow/ConfirmationLetterView.vue'),
        meta: { title: '询证函处理', icon: 'DocumentChecked' },
      },
      {
        path: 'analysis/news',
        name: 'News',
        component: () => import('@/views/analysis/NewsView.vue'),
        meta: { title: '新闻中心', icon: 'Notebook' },
      },
      {
        path: 'system/users',
        name: 'Users',
        component: () => import('@/views/system/UsersView.vue'),
        meta: { title: '用户管理', icon: 'User', requiresAdmin: true },
      },
      {
        path: 'system/tools',
        name: 'Tools',
        component: () => import('@/views/system/ToolsView.vue'),
        meta: { title: '工具管理', icon: 'Switch', requiresAdmin: true },
      },
      {
        path: 'system/prompts',
        name: 'Prompts',
        component: () => import('@/views/system/PromptsView.vue'),
        meta: { title: 'Prompt管理', icon: 'Edit', requiresAdmin: true },
      },
      {
        path: 'system/logs',
        name: 'Logs',
        component: () => import('@/views/system/LogsView.vue'),
        meta: { title: '系统日志', icon: 'Tickets', requiresAdmin: true },
      },
      {
        path: 'system/settings',
        name: 'Settings',
        component: () => import('@/views/system/SettingsView.vue'),
        meta: { title: '系统配置', icon: 'Setting', requiresAdmin: true },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  const authStore = useAuthStore()

  if (to.path === '/login') {
    if (authStore.token) {
      return next('/dashboard')
    }
    return next()
  }

  if (!authStore.token) {
    return next('/login')
  }

  if (to.meta.requiresAdmin && authStore.userInfo?.role !== 'admin') {
    return next('/dashboard')
  }

  next()
})

export default router
