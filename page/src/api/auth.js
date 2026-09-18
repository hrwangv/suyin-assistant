// 内测阶段临时前端登录。
// 后续后端提供 /auth/login 后，把这里替换成 request.post('/auth/login', data) 即可。
const DEV_ACCOUNTS = {
  admin: {
    password: 'admin123',
    user: {
      name: '管理员',
      role: 'admin',
      // username 是长期记忆的归属标识（scope = user:admin），不要用 role：
      // role 只是权限维度，将来新增普通用户时会重复。
      username: 'admin',
      avatar: '',
    },
  },
  user: {
    password: 'user123',
    user: {
      name: '普通用户',
      role: 'employee',
      username: 'user',
      avatar: '',
    },
  },
}

export function loginApi(data) {
  const account = DEV_ACCOUNTS[data.username]

  if (!account || account.password !== data.password) {
    return Promise.reject(new Error('账号或密码错误'))
  }

  return Promise.resolve({
    code: 200,
    data: {
      token: `dev-token-${data.username}`,
      user: account.user,
    },
  })
}

export function logoutApi() {
  return Promise.resolve({ code: 200 })
}
