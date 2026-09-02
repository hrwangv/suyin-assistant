// import request from './index'

// Mock login
export function loginApi(data) {
  // return request.post('/auth/login', data)
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({ code: 200, data: { token: 'mock-token', user: { name: '张三', role: data.username === 'admin' ? 'admin' : 'employee' } } })
    }, 500)
  })
}

export function logoutApi() {
  // return request.post('/auth/logout')
  return Promise.resolve({ code: 200 })
}
