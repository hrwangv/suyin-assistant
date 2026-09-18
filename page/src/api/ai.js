// AI module APIs
import request from './index'

// AI Chat — 对接 POST /query（同步模式）
// userId 用于区分长期记忆归属（后端 scope = user:{userId}），
// 不传时后端会回退到配置的默认用户。
export function sendChatMessage(query, sessionId = null, userId = '') {
  return request.post('/query', {
    query,
    // 空值用 undefined：axios 序列化时会丢掉这些键，
    // 后端 QueryRequest 的 session_id/user_id 注解是 str，显式传 null 会 422。
    session_id: sessionId || undefined,
    is_stream: false,
    user_id: userId || undefined,
  })
}

// AI Chat — 流式问答（SSE）
//
// 两步走：POST /query {is_stream:true} 拿到 session_id，
//         再用 EventSource 连 GET /stream/{session_id} 收事件。
// 后端在返回 session_id 之前就已经建好了队列，
// 所以即使图跑得比连接快，事件也会缓存在队列里，连上后补发，不会丢。
//
// 后端事件协议（app/utils/sse_utils.py）：
//   ready    {}                                   连接建立
//   progress {status, done_list, running_list}    节点进度；status 为 completed/failed 时是终态
//   delta    {delta}                              模型输出增量
//   final    {answer, status, image_urls}         带图片时的最终结果（没图片时后端不发这个事件）
//   error    {error}                              流程异常
//
// 返回 { done, close }：done 在流结束时 resolve（含出错、被取消），
// close() 供组件卸载或切换会话时主动断开。
export function streamChatMessage(query, sessionId = null, userId = '', handlers = {}) {
  const { onReady, onProgress, onDelta, onFinal, onError } = handlers

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

  const fail = (message) => {
    if (settled) return
    onError?.(message)
    finish('error', { message })
  }

  request
    .post('/query', {
      query,
      // 同上：新会话首轮 session_id 为 null，必须省略该键而不是传 null
      session_id: sessionId || undefined,
      is_stream: true,
      user_id: userId || undefined,
    })
    .then((res) => {
      if (settled) return // 调用方已经取消
      const sid = res?.session_id
      if (!sid) {
        fail('后端未返回 session_id')
        return
      }
      onReady?.(sid)

      const es = new EventSource(`/stream/${encodeURIComponent(sid)}`)
      source = es

      es.addEventListener('progress', (event) => {
        if (settled) return
        const data = parse(event)
        onProgress?.(data)
        if (data?.status === 'completed') {
          finish('completed', data)
        } else if (data?.status === 'failed') {
          fail('服务端处理失败，请稍后重试')
        }
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

      // 后端把业务异常也定义成名为 error 的事件，和 EventSource 连接层的 error 重名。
      // 业务异常是 MessageEvent（带 data），连接异常是普通 Event（没有 data），据此区分。
      es.addEventListener('error', (event) => {
        if (settled) return
        if (event?.data === undefined) return // 连接层异常交给下面的 onerror
        fail(parse(event)?.error || '查询失败')
      })

      es.onerror = (event) => {
        if (settled) return
        if (event?.data !== undefined) return // 业务异常已在上面处理
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

// AI Chat — 获取历史对话 GET /history/{session_id}
export function getChatHistory(sessionId, limit = 20) {
  return request.get(`/history/${sessionId}`, { params: { limit } })
}

export function deleteChatHistory(sessionId) {
  return request.delete(`/history/${sessionId}`)
}

// 会话收尾：用户点「新建对话」时调用一次，
// 把上一个对话的残余消息沉淀成长期记忆。
// 后端按新增条数触发的抽取有个盲区：短对话攒不到阈值就永远不抽，
// 这个接口用 force 模式补上（残余 ≥ 2 条就会抽一次）。
export function closeChatSession(sessionId, userId = '') {
  if (!sessionId) return Promise.resolve({ code: 200, triggered: false })
  return request.post('/session/close', null, {
    params: { session_id: sessionId, user_id: userId || undefined },
  })
}

// 申请书生成：后端接口尚未提供。
export function generateApplication() {
  return Promise.reject(new Error('申请书生成后端接口未接入'))
}
