// System Management mock APIs

// Users
export function getUserList() {
  return Promise.resolve({
    code: 200,
    data: [
      { id: 1, username: '张三', role: '员工', roleType: 'employee', status: '正常', email: 'zhangsan@company.com', createdAt: '2026-01-15' },
      { id: 2, username: '李四', role: '管理员', roleType: 'admin', status: '正常', email: 'lisi@company.com', createdAt: '2026-01-10' },
      { id: 3, username: '王五', role: '员工', roleType: 'employee', status: '正常', email: 'wangwu@company.com', createdAt: '2026-03-20' },
      { id: 4, username: '赵六', role: '员工', roleType: 'employee', status: '禁用', email: 'zhaoliu@company.com', createdAt: '2026-05-08' },
    ],
  })
}

export function createUser(data) {
  return Promise.resolve({ code: 200, data: { ...data, id: Date.now() } })
}

export function updateUser(id, data) {
  return Promise.resolve({ code: 200, data: { id, ...data } })
}

export function deleteUser(id) {
  return Promise.resolve({ code: 200 })
}

// Tools
export function getToolList() {
  return Promise.resolve({
    code: 200,
    data: [
      { id: 1, name: '晨报助手', key: 'morning-report', status: true, description: '企业知识库RAG问答' },
      { id: 2, name: '询证函处理', key: 'confirmation-letter', status: true, description: '询证函OCR识别与处理' },
      { id: 3, name: '申请书生成', key: 'application', status: true, description: '自动生成业务申请书' },
      { id: 4, name: '企业尽调', key: 'due-diligence', status: true, description: '企业调查分析报告' },
      { id: 5, name: '新闻中心', key: 'news', status: true, description: '企业新闻自动采集' },
      { id: 6, name: 'AI聊天', key: 'chat', status: true, description: '通用AI助手对话' },
    ],
  })
}

export function toggleToolStatus(id, status) {
  return Promise.resolve({ code: 200, data: { id, status } })
}

// Prompts
export function getPromptList() {
  return Promise.resolve({
    code: 200,
    data: [
      { id: 1, name: '晨报回答Prompt', updatedAt: '2026-07-25 14:30' },
      { id: 2, name: '申请书生成Prompt', updatedAt: '2026-07-24 10:15' },
      { id: 3, name: '尽调分析Prompt', updatedAt: '2026-07-20 09:00' },
      { id: 4, name: '询证函识别Prompt', updatedAt: '2026-07-18 16:45' },
    ],
  })
}

export function getPromptDetail(id) {
  const prompts = {
    1: '你是一个专业的经营分析助手。根据提供的晨报内容，回答用户关于企业经营情况的问题。请确保回答基于数据事实，并注明信息来源。',
    2: '你是一个专业的企业文档撰写助手。根据用户输入的企业信息和业务类型，生成规范的业务申请书。请确保格式规范、内容完整。',
    3: '你是一个专业的企业尽调分析助手。根据提供的企业信息，从工商、司法、新闻等多个维度进行全面分析，生成专业的尽调报告。',
    4: '你是一个专业的文档识别助手。请从询证函扫描文件中准确提取企业名称、回函地址、余额等关键信息。',
  }
  return Promise.resolve({
    code: 200,
    data: { id, name: 'Mock Prompt', content: prompts[id] || '暂无内容', updatedAt: '2026-07-25 14:30' },
  })
}

export function updatePrompt(id, content) {
  return Promise.resolve({ code: 200, data: { id, content } })
}

// Logs
export function getLogList() {
  return Promise.resolve({
    code: 200,
    data: [
      { id: 1, user: '张三', action: '登录系统', time: '2026-07-28 10:30:00', result: '成功', ip: '192.168.1.100' },
      { id: 2, user: '张三', action: '使用晨报助手', time: '2026-07-28 10:31:00', result: '成功', ip: '192.168.1.100' },
      { id: 3, user: '李四', action: '上传文件到知识库', time: '2026-07-28 09:15:00', result: '成功', ip: '192.168.1.101' },
      { id: 4, user: '王五', action: '查看新闻中心', time: '2026-07-27 16:45:00', result: '成功', ip: '192.168.1.102' },
      { id: 5, user: '张三', action: '导出尽调报告', time: '2026-07-27 14:20:00', result: '成功', ip: '192.168.1.100' },
      { id: 6, user: '赵六', action: '登录系统', time: '2026-07-27 11:00:00', result: '失败-密码错误', ip: '192.168.1.103' },
      { id: 7, user: '李四', action: '修改系统配置', time: '2026-07-26 15:30:00', result: '成功', ip: '192.168.1.101' },
      { id: 8, user: '李四', action: '新增用户', time: '2026-07-26 14:00:00', result: '成功', ip: '192.168.1.101' },
    ],
  })
}

// Settings
export function getSettings() {
  return Promise.resolve({
    code: 200,
    data: {
      systemName: '企业AI办公平台',
      logo: '',
      maxUploadSize: 50,
      sessionTimeout: 30,
      enableRegistration: false,
    },
  })
}

export function updateSettings(data) {
  return Promise.resolve({ code: 200, data })
}
