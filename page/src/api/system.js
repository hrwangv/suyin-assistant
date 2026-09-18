// 系统管理相关后端接口尚未提供，先返回空数据。
// 后续接入真实接口时，将对应函数改为 request.get/post/put/delete 即可。

export function getUserList() {
  return Promise.resolve({ code: 200, data: [] })
}

export function createUser() {
  return Promise.reject(new Error('用户管理后端接口未接入'))
}

export function updateUser() {
  return Promise.reject(new Error('用户管理后端接口未接入'))
}

export function deleteUser() {
  return Promise.reject(new Error('用户管理后端接口未接入'))
}

export function getToolList() {
  return Promise.resolve({ code: 200, data: [] })
}

export function toggleToolStatus() {
  return Promise.reject(new Error('工具管理后端接口未接入'))
}

export function getPromptList() {
  return Promise.resolve({ code: 200, data: [] })
}

export function getPromptDetail(id) {
  return Promise.resolve({
    code: 200,
    data: { id, name: '', content: '', updatedAt: '' },
  })
}

export function updatePrompt() {
  return Promise.reject(new Error('Prompt 管理后端接口未接入'))
}

export function getLogList() {
  return Promise.resolve({ code: 200, data: [] })
}

export function getSettings() {
  return Promise.resolve({
    code: 200,
    data: {
      systemName: '苏银助手',
      logo: '',
      maxUploadSize: 50,
      sessionTimeout: 30,
      enableRegistration: false,
    },
  })
}

export function updateSettings() {
  return Promise.reject(new Error('系统配置后端接口未接入'))
}
