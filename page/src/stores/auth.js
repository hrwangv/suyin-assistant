import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const userInfo = ref(JSON.parse(localStorage.getItem('userInfo') || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => userInfo.value?.role === 'admin')

  function login(credentials) {
    // Mock login — replace with real API call
    return new Promise((resolve) => {
      setTimeout(() => {
        token.value = 'mock-jwt-token-' + Date.now()
        userInfo.value = {
          name: '张三',
          role: credentials.username === 'admin' ? 'admin' : 'employee',
          avatar: '',
        }
        localStorage.setItem('token', token.value)
        localStorage.setItem('userInfo', JSON.stringify(userInfo.value))
        resolve({ success: true })
      }, 800)
    })
  }

  function logout() {
    token.value = ''
    userInfo.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('userInfo')
  }

  return { token, userInfo, isLoggedIn, isAdmin, login, logout }
})
