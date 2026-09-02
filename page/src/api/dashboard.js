// Dashboard mock data — TODO tasks + quick tools

export function getTodoTasks() {
  return Promise.resolve({
    code: 200,
    data: {
      today: [
        { id: 1, title: '审核XX公司的询证函回函', done: false, priority: 'high', time: '10:00', note: '' },
        { id: 2, title: '准备项目立项申请材料', done: false, priority: 'high', time: '14:00', note: '需法务审核' },
        { id: 3, title: '查看今日经营晨报', done: true, priority: 'medium', time: '09:00', note: '' },
        { id: 4, title: '回复投资部门尽调需求', done: false, priority: 'medium', time: '16:00', note: '' },
        { id: 5, title: '整理上周会议纪要上传知识库', done: false, priority: 'low', time: '', note: '' },
      ],
      week: [
        { id: 11, title: '完成月度经营分析报告', done: false, priority: 'high', date: '07-29', note: '周三前提交' },
        { id: 12, title: '更新知识库制度文档', done: false, priority: 'medium', date: '07-30', note: '' },
        { id: 13, title: '新员工AI工具使用培训', done: false, priority: 'medium', date: '07-31', note: '10人参加' },
        { id: 14, title: '评审供应商尽调报告', done: true, priority: 'high', date: '07-28', note: '' },
        { id: 15, title: '整理Prompt模板库', done: false, priority: 'low', date: '08-01', note: '' },
        { id: 16, title: '系统权限梳理与优化', done: false, priority: 'low', date: '08-02', note: '' },
      ],
    },
  })
}

export function getQuickTools() {
  return Promise.resolve({
    code: 200,
    data: [
      { id: 1, name: '晨报助手', icon: 'Sunrise', route: '/ai/morning-report' },
      { id: 2, name: '询证函处理', icon: 'DocumentChecked', route: '/workflow/confirmation-letter' },
      { id: 3, name: '申请书生成', icon: 'DocumentAdd', route: '/ai/application' },
      { id: 4, name: '企业尽调', icon: 'Search', route: '/ai/due-diligence' },
    ],
  })
}
