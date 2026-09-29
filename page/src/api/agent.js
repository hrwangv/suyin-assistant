// 企业业务智能 Agent —— 统一聊天入口
//
// 后端接口（app/api/agent.py）：
//   POST /api/agent/upload          上传会话附件（只支持 PNG / JPEG 图片）
//   POST /api/agent/chat            统一入口（同步 / 流式 / HITL 恢复）
//   GET  /api/agent/stream/{id}     SSE（与 /stream/{session_id} 共用事件协议）
//   GET  /api/agent/state/{id}      查看线程状态
//   GET  /api/agent/tools           MCP 配置自检
//
// 已有的 /query 接口保持不变，这个文件只是把 Agent 入口准备好，
// 页面可以先并行双跑，确认无误后再把 ChatView 切过来。
import request from './index'

// 上传附件：后端保存到 output/agent_files/{file_id}/，返回 file_id 供 chat 引用
export function uploadAgentAttachments(files) {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))
  return request.post('/api/agent/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// 同步问答 / HITL 恢复
// message 为空且传 resume 时表示「恢复被 interrupt 暂停的流程」
export function sendAgentMessage(message, options = {}) {
  const { threadId, userId, attachments, resume, newTurn } = options
  return request.post('/api/agent/chat', {
    message,
    thread_id: threadId || undefined,
    user_id: userId || undefined,
    attachments: attachments || [],
    stream: false,
    resume,
    new_turn: newTurn || false,
  })
}

// 流式问答：POST /api/agent/chat {stream:true} 拿 thread_id，再连 SSE
//
// 事件协议（app/utils/sse_utils.py + app/agent/events.py）：
//   progress  {status, done_list, running_list}      节点进度（status=completed 表示本轮结束）
//   delta     {delta}                                模型输出增量
//   final     {answer, status, image_urls}           最终结果（HITL 时带 type=interrupt）
//   error     {error}                                异常
//   ---- Agent 专属 ----
//   agent_started / routing / tool_call / tool_result / document_processing /
//   ocr_completed / company_candidates / human_confirmation_required /
//   template_selected / document_generating / document_ready / agent_message
export function streamAgentMessage(message, options = {}, handlers = {}) {
  const {
    threadId, userId, attachments, resume, newTurn,
  } = options
  const {
    onReady, onProgress, onDelta, onFinal, onError, onAgentEvent,
  } = handlers

  let source = null
  let settled = false
  let resolveDone
  const done = new Promise((resolve) => {
    resolveDone = resolve
  })

  const parse = (event) => {
    try {
      return JSON.parse(event.data)
    } catch (e) {
      return {}
    }
  }

  const finish = (reason, payload) => {
    if (settled) return
    settled = true
    if (source) {
      source.close()
      source = null
    }
    resolveDone({ reason, payload })
  }

  const fail = (msg) => {
    if (settled) return
    onError?.(msg)
    finish('error', { message: msg })
  }

  request
    .post('/api/agent/chat', {
      message,
      thread_id: threadId || undefined,
      user_id: userId || undefined,
      attachments: attachments || [],
      stream: true,
      resume,
      new_turn: newTurn || false,
    })
    .then((res) => {
      if (settled) return
      const tid = res?.thread_id
      if (!tid) {
        fail('后端未返回 thread_id')
        return
      }
      onReady?.(tid)

      const es = new EventSource(`/api/agent/stream/${encodeURIComponent(tid)}`)
      source = es

      es.addEventListener('progress', (event) => {
        if (settled) return
        const data = parse(event)
        onProgress?.(data)
        if (data?.status === 'completed') finish('completed', data)
        else if (data?.status === 'failed') fail('服务端处理失败，请稍后重试')
      })

      es.addEventListener('delta', (event) => {
        if (settled) return
        const delta = parse(event)?.delta
        if (delta) onDelta?.(delta)
      })

      es.addEventListener('final', (event) => {
        if (settled) return
        const data = parse(event)
        onFinal?.(data)
        finish('final', data)
      })

      // Agent 专属事件统一回调，页面按 event 名分派（正在查询企业信息 / 正在生成申请书 …）
      const agentEvents = [
        'agent_started', 'routing', 'tool_call', 'tool_result', 'document_processing',
        'ocr_completed', 'company_candidates', 'human_confirmation_required',
        'template_selected', 'document_generating', 'document_ready', 'agent_message',
      ]
      agentEvents.forEach((name) => {
        es.addEventListener(name, (event) => {
          if (settled) return
          onAgentEvent?.(name, parse(event))
        })
      })

      es.addEventListener('error', (event) => {
        if (settled) return
        if (event?.data === undefined) return
        fail(parse(event)?.error || '查询失败')
      })

      es.onerror = (event) => {
        if (settled) return
        if (event?.data !== undefined) return
        fail('与服务器的流式连接中断')
      }
    })
    .catch((error) => {
      fail(error?.response?.data?.detail || error?.message || '请求失败')
    })

  return {
    done,
    close: () => {
      if (settled) return
      settled = true
      if (source) {
        source.close()
        source = null
      }
      resolveDone({ reason: 'cancelled', payload: null })
    },
  }
}

// 线程状态（调试 HITL / 多轮上下文）
export function getAgentState(threadId) {
  return request.get(`/api/agent/state/${encodeURIComponent(threadId)}`)
}

// MCP 配置自检；probe=true 时会真的连一次 MCP 并列出工具名
export function getAgentTools(probe = false) {
  return request.get('/api/agent/tools', { params: { probe } })
}

// 生成文件下载地址（后端返回的 url 已经是这个前缀）
export function agentFileUrl(fileName) {
  return `/api/agent/files/${encodeURIComponent(fileName)}`
}
