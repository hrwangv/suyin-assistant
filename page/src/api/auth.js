// 内测阶段的临时前端登录（后端鉴权接入完成前的过渡方案）。
//
// ⚠️ 安全提醒
//   鉴权逻辑跑在浏览器里，口令就一定会出现在打包产物中，
//   「登录页不显示口令」不等于安全。正式上线请改为后端校验：
//     loginApi → request.post('/auth/login', data)
//   由后端校验口令、签发 token，前端只保存后端下发的凭证。
//
// 内测口令来源（优先级由高到低）：
//   1. 环境变量，写在 page/.env.local（已被 .gitignore 忽略，不会进仓库）
//        VITE_DEV_ADMIN_PASSWORD=xxxx
//        VITE_DEV_USER_PASSWORD=xxxx
//   2. 开发模式（npm run dev）下的默认内测口令。生产构建不会包含它们，
//      因此生产构建若未配置环境变量，mock 登录会直接失败并提示原因。

const DEV_FALLBACK_PASSWORDS = import.meta.env.DEV
  ? { admin: 'admin123', user: 'user123' }
  : {}

// 标记为 userFacing 的错误，登录页会直接把文案展示给用户
function userFacingError(message) {
  const error = new Error(message)
  error.userFacing = true
  return error
}

const DEV_ACCOUNTS = {
  admin: {
    password: import.meta.env.VITE_DEV_ADMIN_PASSWORD || DEV_FALLBACK_PASSWORDS.admin || '',
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
    password: import.meta.env.VITE_DEV_USER_PASSWORD || DEV_FALLBACK_PASSWORDS.user || '',
    user: {
      name: '普通用户',
      role: 'employee',
      username: 'user',
      avatar: '',
    },
  },
}

const MOCK_AUTH_READY = Object.values(DEV_ACCOUNTS).some((item) => !!item.password)

export function loginApi(data) {
  if (!MOCK_AUTH_READY) {
    return Promise.reject(
      userFacingError('当前未接入后端鉴权，且未配置内测口令，请联系系统管理员')
    )
  }

  const account = DEV_ACCOUNTS[data?.username]

  if (!account || !account.password || account.password !== data.password) {
    return Promise.reject(userFacingError('用户名或密码不正确，请重新输入'))
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
