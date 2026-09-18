import { defineStore } from 'pinia'
import { ref } from 'vue'

// 会话列表的本地持久化。
//
// 只存「会话外壳」：id / sessionId / 标题 / 创建时间。
// 不存消息正文，原因有二：
// 1. localStorage 只有 5MB，长对话很容易撑爆；
// 2. 后端 memory_recent_messages 才是消息的权威来源（Redis 缓存 + MySQL 兜底），
//    刷新后用 GET /history/{session_id} 拉回来即可（见 ChatView.ensureHistory）。
//
// 注意：这里的 key 带版本号，将来结构变化时改版本即可，不会读到旧格式。
const STORAGE_KEY = 'suyin:chat:v1'

// 自增 id 也要持久化：否则刷新后新建会话会和已恢复的会话 id 撞车，
// 导致侧边栏 v-for 的 key 重复、点错会话。
let nextId = 1

// 本地记录按登录用户隔离。
//
// 为什么必须隔离：session_id 是 GET /history/{session_id} 的唯一凭证（该接口没有鉴权），
// 一旦把它留在共享浏览器里，换个账号登录就能读到上一个账号的整段对话。
// 这里直接从 localStorage 的 userInfo 取用户名，避免为了一个 key 去 import auth store。
function storageKeyFor(username) {
  return username ? `${STORAGE_KEY}:${username}` : STORAGE_KEY
}

function loadPersisted(key) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const data = JSON.parse(raw)
    if (!data || !Array.isArray(data.conversations)) return null
    return data
  } catch (e) {
    // 解析失败（被手动改坏、旧格式）就当没有本地记录，不影响使用
    console.warn('[chat] 本地会话记录解析失败，已忽略', e)
    return null
  }
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref([])
  const currentConversationId = ref(null)
  // 当前本地记录归属的用户（也是 storage key）。为空表示还没绑定，此时不落盘。
  const boundUser = ref(null)

  // 本次页面生命周期内「已经拉过历史」的会话 id。
  // 故意不持久化：刷新后必须重新拉一次，否则会把空消息列表误当成"已加载"。
  const loadedHistory = new Set()

  function hydrate(persisted) {
    conversations.value = (persisted?.conversations || []).map((conv) => ({
      id: conv.id,
      sessionId: conv.sessionId || null, // 后端 UUID，首次提问后回填，多轮对话必须带回
      title: conv.title || '新对话',
      createdAt: conv.createdAt || new Date().toISOString(),
      messages: [],                      // 不持久化，恢复时按需向后端拉
    }))
    nextId = persisted?.nextId ?? 1
    // 恢复出来的 currentConversationId 可能指向已被删掉的会话，做一次校验
    const persistedCurrent = persisted?.currentConversationId
    currentConversationId.value = conversations.value.some((c) => c.id === persistedCurrent)
      ? persistedCurrent
      : conversations.value[0]?.id ?? null
    loadedHistory.clear()
  }

  /**
   * 绑定当前登录用户（在 ChatView 挂载时调用）。
   *
   * 用户名变化时（同一浏览器换账号登录）要丢掉上一个账号的内存态并重新加载，
   * 否则新账号会看到旧账号的会话列表和 session_id。
   * 同一个用户重复调用是 no-op，避免组件重新挂载时把内存里的消息清掉。
   */
  function bindUser(username) {
    const key = storageKeyFor(username)
    if (boundUser.value === key) return
    boundUser.value = key
    hydrate(loadPersisted(key))
  }

  function persist() {
    if (!boundUser.value) return   // 还没绑定用户就不落盘，避免写到错误的 key
    try {
      localStorage.setItem(
        boundUser.value,
        JSON.stringify({
          conversations: conversations.value.map((conv) => ({
            id: conv.id,
            sessionId: conv.sessionId,
            title: conv.title,
            createdAt: conv.createdAt,
          })),
          currentConversationId: currentConversationId.value,
          nextId,
        })
      )
    } catch (e) {
      // 隐私模式 / 配额写满：只丢持久化，不影响当前会话使用
      console.warn('[chat] 本地会话记录写入失败', e)
    }
  }

  // 这里刻意**不用** deep watch 兜底持久化，而是在各 action 里同步写：
  // 1. 流式回答是逐 token 往消息里追加的，deep watch 会在每个 token 上触发一次
  //    JSON.stringify + localStorage 写入，纯属浪费；
  // 2. watch 的回调是异步（nextTick）刷新的，"改完立刻关页面" 有丢写的窗口。
  // 消息正文本来就不持久化，所以只有会话外壳需要落盘的地方才调用 persist()。

  function findConversation(id) {
    return conversations.value.find((c) => c.id === id) || null
  }

  function createConversation(title = '新对话') {
    const conv = {
      id: nextId++,
      sessionId: null,
      title,
      messages: [],
      createdAt: new Date().toISOString(),
    }
    conversations.value.unshift(conv)
    currentConversationId.value = conv.id
    persist()
    return conv
  }

  function deleteConversation(id) {
    const index = conversations.value.findIndex((c) => c.id === id)
    if (index > -1) {
      conversations.value.splice(index, 1)
      loadedHistory.delete(id)
      if (currentConversationId.value === id) {
        currentConversationId.value = conversations.value[0]?.id || null
      }
      persist()
    }
  }

  function selectConversation(id) {
    currentConversationId.value = id
    // 记住当前选中的会话，刷新后能直接回到这一段
    persist()
  }

  function setSessionId(id, sessionId) {
    const conv = findConversation(id)
    if (conv && !conv.sessionId) {
      conv.sessionId = sessionId
      persist()
    }
  }

  // 用后端历史覆盖/补齐消息。
  // 拉取期间用户可能已经发了新消息，这种情况下历史要放在前面，保持时间顺序。
  function mergeHistory(id, messages) {
    const conv = findConversation(id)
    if (!conv) return
    conv.messages = conv.messages.length
      ? [...messages, ...conv.messages]
      : messages
  }

  function clearMessages(id) {
    const conv = findConversation(id)
    if (conv) conv.messages = []
  }

  function hasLoadedHistory(id) {
    return loadedHistory.has(id)
  }

  function markHistoryLoaded(id) {
    loadedHistory.add(id)
  }

  function unmarkHistoryLoaded(id) {
    loadedHistory.delete(id)
  }

  const currentConversation = () =>
    conversations.value.find((c) => c.id === currentConversationId.value) || null

  function sendMessage(content, role = 'user') {
    const conv = conversations.value.find((c) => c.id === currentConversationId.value)
    if (conv) {
      conv.messages.push({
        id: Date.now(),
        role,
        content,
        timestamp: new Date().toISOString(),
      })
      // Update title from first user message
      if (role === 'user' && conv.title === '新对话') {
        conv.title = content.slice(0, 20) + (content.length > 20 ? '...' : '')
      }
      persist()
    }
  }

  return {
    conversations,
    currentConversationId,
    boundUser,
    bindUser,
    createConversation,
    deleteConversation,
    selectConversation,
    setSessionId,
    mergeHistory,
    clearMessages,
    hasLoadedHistory,
    markHistoryLoaded,
    unmarkHistoryLoaded,
    currentConversation,
    sendMessage,
  }
})
