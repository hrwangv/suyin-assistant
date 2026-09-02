// AI module APIs
import request from './index'

// AI Chat — 对接 POST /query（同步模式）
export function sendChatMessage(query, sessionId = null) {
  return request.post('/query', {
    query,
    session_id: sessionId,
    is_stream: false,
  })
}

// AI Chat — 获取历史对话 GET /history/{session_id}
export function getChatHistory(sessionId, limit = 20) {
  return request.get(`/history/${sessionId}`, { params: { limit } })
}

// Morning Report
export function queryMorningReport(question, knowledgeBase) {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        code: 200,
        data: {
          answer: `根据${knowledgeBase || '晨报知识库'}的分析，回答如下：\n\n关于"${question}"，根据2026年经营晨报数据，最近三个月经营情况如下：\n\n1. **营收方面**：整体保持稳定增长态势，环比增长约5%\n2. **成本控制**：运营成本较上季度下降3%\n3. **重点项目**：多个项目按计划推进中`,
          sources: [
            { file: '2026-03-01晨报.pdf', page: 3, snippet: '本月营业收入达到预期目标，同比增长12%...' },
            { file: '2026-02-15晨报.pdf', page: 5, snippet: '成本控制措施初见成效，运营效率提升...' },
          ],
        },
      })
    }, 1500)
  })
}

export function getKnowledgeBases() {
  return Promise.resolve({
    code: 200,
    data: [
      { id: 1, name: '晨报知识库' },
      { id: 2, name: '制度文档库' },
      { id: 3, name: '业务资料库' },
    ],
  })
}

// Application Generator
export function generateApplication(form) {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        code: 200,
        data: {
          filename: `${form.companyName}_业务申请书.docx`,
          downloadUrl: '#',
        },
      })
    }, 2000)
  })
}

// Due Diligence
export function startDueDiligence(companyName) {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        code: 200,
        data: {
          taskId: 'dd-' + Date.now(),
          progress: [
            { step: '工商信息', status: 'completed', icon: 'Check' },
            { step: '司法信息', status: 'completed', icon: 'Check' },
            { step: '新闻信息', status: 'completed', icon: 'Check' },
          ],
          reportUrl: '#',
        },
      })
    }, 3000)
  })
}
