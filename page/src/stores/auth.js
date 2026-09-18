import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { loginApi, logoutApi } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const storedToken = localStorage.getItem('token') || ''
  const isLegacyMockToken = storedToken.startsWith('mock-') || storedToken === 'mock-token'

  if (isLegacyMockToken) {
    localStorage.removeItem('token')
    localStorage.removeItem('userInfo')
  }

  const token = ref(isLegacyMockToken ? '' : storedToken)
  const userInfo = ref(
    isLegacyMockToken
      ? null
      : JSON.parse(localStorage.getItem('userInfo') || 'null')
  )

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => userInfo.value?.role === 'admin')

  async function login(credentials) {
    const res = await loginApi(credentials)
    const data = res?.data || res
    token.value = data.token
    userInfo.value = data.user
    localStorage.setItem('token', token.value)
    localStorage.setItem('userInfo', JSON.stringify(userInfo.value))
    return data
  }

  async function logout() {
    try {
      await logoutApi()
    } catch (error) {
      // 后端未接入或网络异常时，也清理本地登录状态。
      console.warn('退出登录接口调用失败，已清理本地登录状态', error)
    } finally {
      token.value = ''
      userInfo.value = null
      localStorage.removeItem('token')
      localStorage.removeItem('userInfo')
    }
  }

  return { token, userInfo, isLoggedIn, isAdmin, login, logout }
})
