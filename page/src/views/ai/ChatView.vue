<template>
  <div class="chat-page">
    <div class="chat-container">
      <!-- Conversation Sidebar -->
      <div class="chat-sidebar">
        <div class="sidebar-header">
          <el-button type="primary" @click="handleNewChat" :icon="Plus" class="new-chat-btn">
            新建对话
          </el-button>
        </div>
        <div class="conversation-list">
          <div
            v-for="conv in chatStore.conversations"
            :key="conv.id"
            :class="['conv-item', { active: conv.id === chatStore.currentConversationId }]"
            @click="handleSelectConv(conv.id)"
          >
            <el-icon class="conv-icon"><ChatDotRound /></el-icon>
            <div class="conv-info">
              <span class="conv-title">{{ conv.title }}</span>
              <span class="conv-time">{{ formatTime(conv.createdAt) }}</span>
            </div>
            <el-icon class="conv-delete" @click.stop="handleDeleteConv(conv.id)"><Delete /></el-icon>
          </div>
          <el-empty v-if="chatStore.conversations.length === 0" description="暂无对话记录" :image-size="60" />
        </div>
      </div>

      <!-- Chat Area -->
      <div class="chat-main">
        <!-- Messages -->
        <div class="chat-messages" ref="messagesRef">
          <template v-if="currentConv && currentConv.messages.length > 0">
            <div
              v-for="msg in currentConv.messages"
              :key="msg.id"
              :class="['message-item', msg.role]"
            >
              <div class="message-avatar">
                <el-avatar v-if="msg.role === 'user'" :size="36" icon="UserFilled" />
                <el-avatar v-else :size="36" style="background: #409eff">
                  <el-icon :size="20"><Cpu /></el-icon>
                </el-avatar>
              </div>
              <div class="message-content">
                <div class="message-text" v-html="renderContent(msg.content)"></div>
              </div>
            </div>
          </template>
          <!-- 刷新/切换会话时，消息体不持久化，正在按需从后端恢复 -->
          <div v-else-if="restoring" class="chat-empty">
            <el-empty description="正在恢复历史对话…" :image-size="100" />
          </div>
          <div v-else class="chat-empty">
            <el-empty description="开始一段新对话吧" :image-size="120" />
          </div>
          <!-- 首个 delta 到达前显示"打字中"动画，并带一行后端节点进度 -->
          <div v-if="aiLoading && !hasStreamedText" class="message-item assistant">
            <div class="message-avatar">
              <el-avatar :size="36" style="background: #409eff">
                <el-icon :size="20"><Cpu /></el-icon>
              </el-avatar>
            </div>
            <div class="message-content">
              <div class="typing-indicator">
                <span></span><span></span><span></span>
              </div>
              <div v-if="streamStatus" class="stream-status">{{ streamStatus }}</div>
            </div>
          </div>
        </div>

        <!-- Input Area -->
        <div class="chat-input-area">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="3"
            placeholder="请输入问题，按 Enter 发送，Shift+Enter 换行"
            resize="none"
            @keydown.enter.exact="handleSend"
          />
          <div class="input-actions">
            <el-button @click="handleClearSession" :icon="Delete">清空会话</el-button>
            <el-button
              type="primary"
              @click="handleSend"
              :loading="aiLoading"
              :disabled="restoring"
              :icon="Promotion"
            >
              发送
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { streamChatMessage, getChatHistory, deleteChatHistory, closeChatSession } from '@/api/ai'

const chatStore = useChatStore()
const authStore = useAuthStore()
const inputMessage = ref('')
const aiLoading = ref(false)
// 后端节点进度文案（首个 delta 之前展示）
const streamStatus = ref('')
// 是否已经收到过模型增量输出
const hasStreamedText = ref(false)
// 是否正在从后端恢复历史消息（刷新页面 / 切换会话时）
const restoring = ref(false)
// 可能同时有多个会话在恢复（快速切换），用计数避免先回来的把状态提前关掉
let restoringCount = 0

// 每轮最多恢复多少条。后端 MySQL 每个会话只保留 50 条（CONTEXT_MYSQL_KEEP），
// 请求再大也只会返回 50 条，这里直接对齐上限。
const HISTORY_LIMIT = 50
const messagesRef = ref(null)
// 当前进行中的流式请求，用于切换会话 / 卸载组件时断开
let streamController = null

const currentConv = computed(() =>
  chatStore.conversations.find((c) => c.id === chatStore.currentConversationId) || null
)

onMounted(async () => {
  // 本地会话记录按登录用户隔离（session_id 是 /history 的唯一凭证），
  // 换账号时 bindUser 会丢弃上一个账号的会话并重新加载。
  chatStore.bindUser(authStore.userInfo?.username)
  // 刷新后 conversations 是从 localStorage 恢复出来的：
  // 只有「会话外壳」（sessionId / 标题 / 时间），消息体要向后端拉。
  if (chatStore.conversations.length === 0) {
    chatStore.createConversation()
    return
  }
  await ensureHistory(currentConv.value)
})

/**
 * 按需恢复某个会话的历史消息。
 *
 * 消息不持久化，所以刷新页面、或切到一个本次页面还没拉过的会话时，
 * 都要从 GET /history/{session_id} 拉一次（后端是 Redis 优先 + MySQL 回源）。
 * 用 store 里的 loadedHistory 做去重，避免来回切换时反复请求。
 */
async function ensureHistory(conv) {
  if (!conv?.sessionId) return          // 还没提问过的新会话，后端没有历史
  if (chatStore.hasLoadedHistory(conv.id)) return

  const convId = conv.id
  // 先打标记再请求：避免用户在加载过程中反复切换导致并发重复拉取
  chatStore.markHistoryLoaded(convId)
  restoringCount++
  restoring.value = true
  try {
    const res = await getChatHistory(conv.sessionId, HISTORY_LIMIT)
    const items = Array.isArray(res?.items) ? res.items : []
    const messages = items
      .filter((item) => item && item.content)
      .map((item, index) => ({
        // 历史消息没有后端 id，用「会话 id + 下标」保证 key 稳定且不重复
        id: `history-${convId}-${index}`,
        role: item.role === 'assistant' ? 'assistant' : 'user',
        content: item.content,
        // 缓存命中的消息没有 ts（Redis 里只存了 role/content），允许为空
        timestamp: item.ts || null,
      }))
    chatStore.mergeHistory(convId, messages)
    await scrollToBottom()
  } catch (e) {
    // 失败就取消标记，下次进入该会话时再试
    chatStore.unmarkHistoryLoaded(convId)
    console.warn('恢复历史对话失败（不影响继续提问）', e)
  } finally {
    restoringCount = Math.max(0, restoringCount - 1)
    if (restoringCount === 0) restoring.value = false
  }
}

function handleSelectConv(id) {
  chatStore.selectConversation(id)
  ensureHistory(chatStore.conversations.find((c) => c.id === id))
}

// 中断当前流式请求（切换会话、删除会话、离开页面时调用）
function cancelStream() {
  if (streamController) {
    streamController.close()
    streamController = null
  }
  aiLoading.value = false
  hasStreamedText.value = false
  streamStatus.value = ''
}

function handleNewChat() {
  cancelStream()
  // 新建对话前，先让后端给上一个对话收尾（沉淀长期记忆），
  // 不 await：收尾是异步的，不能卡住用户开新对话
  const prevSessionId = currentConv.value?.sessionId
  if (prevSessionId) {
    closeChatSession(prevSessionId, authStore.userInfo?.username).catch((e) => {
      console.warn('会话收尾失败（不影响开新对话）', e)
    })
  }
  chatStore.createConversation()
  inputMessage.value = ''
}

function handleDeleteConv(id) {
  ElMessageBox.confirm('确定删除该对话吗？', '确认', { type: 'warning' }).then(async () => {
    if (id === chatStore.currentConversationId) {
      cancelStream()
    }
    const conv = chatStore.conversations.find((c) => c.id === id)
    if (conv?.sessionId) {
      try {
        await deleteChatHistory(conv.sessionId)
      } catch (e) {
        // 本地仍允许删除，后端失败只做提示，不阻塞操作。
        console.warn('删除后端会话失败', e)
      }
    }
    chatStore.deleteConversation(id)
  }).catch(() => {})
}

function handleClearSession() {
  cancelStream()
  ElMessageBox.confirm('确定清空当前会话吗？', '确认', { type: 'warning' }).then(async () => {
    const conv = currentConv.value
    if (conv?.sessionId) {
      try {
        await deleteChatHistory(conv.sessionId)
      } catch (e) {
        console.warn('清空后端会话失败', e)
      }
    }
    if (conv) {
      chatStore.clearMessages(conv.id)
      // 后端那边已经被删空，标记成"已加载"，避免下次切回来又去拉一次空历史
      chatStore.markHistoryLoaded(conv.id)
    }
  }).catch(() => {})
}

// 把后端 progress 事件翻译成一行用户能看懂的进度文案
function describeProgress(progress) {
  const running = progress?.running_list || []
  const done = progress?.done_list || []
  if (running.length) return `正在处理：${running[running.length - 1]}`
  if (done.length) return `已完成：${done[done.length - 1]}`
  return '正在处理…'
}

async function handleSend() {
  const content = inputMessage.value.trim()
  if (!content || aiLoading.value) return
  if (restoring.value) {
    ElMessage.info('正在恢复历史对话，请稍候再发送')
    return
  }

  if (!currentConv.value) {
    chatStore.createConversation()
  }

  const conv = currentConv.value
  chatStore.sendMessage(content, 'user')
  inputMessage.value = ''
  await scrollToBottom()

  aiLoading.value = true
  hasStreamedText.value = false
  streamStatus.value = '正在理解问题…'

  // 先占一条空的助手消息，delta 到了往里追加，实现逐字输出
  chatStore.sendMessage('', 'assistant')
  const assistantMsg = conv.messages[conv.messages.length - 1]
  let failedMessage = ''
  let cancelled = false

  // 带上当前登录用户名：后端用它决定长期记忆的归属（user:admin / user:user）
  const controller = streamChatMessage(content, conv.sessionId, authStore.userInfo?.username, {
    onReady: (sessionId) => {
      // 首次对话回填后端 session_id，后续多轮必须带回
      // 走 store action：sessionId 要一并落盘，刷新后才知道去拉哪段历史
      chatStore.setSessionId(conv.id, sessionId)
    },
    onProgress: (progress) => {
      if (!hasStreamedText.value) streamStatus.value = describeProgress(progress)
    },
    onDelta: (delta) => {
      hasStreamedText.value = true
      streamStatus.value = ''
      assistantMsg.content += delta
      scheduleScroll()
    },
    onFinal: (data) => {
      if (data?.answer) assistantMsg.content = data.answer
    },
    onError: (message) => {
      failedMessage = message
    },
  })
  streamController = controller

  const outcome = await controller.done
  cancelled = outcome?.reason === 'cancelled'
  if (streamController === controller) streamController = null
  if (cancelled) return

  if (failedMessage) {
    if (assistantMsg.content) {
      ElMessage.warning(failedMessage)
    } else {
      assistantMsg.content = `抱歉，${failedMessage}。`
    }
  }
  // 后端异常时会返回 HTTP 200 + 空答案，这里兜底避免留下空气泡
  if (!assistantMsg.content) {
    assistantMsg.content = '抱歉，本次没有取到答案，请稍后重试。'
  }
  aiLoading.value = false
  streamStatus.value = ''
  await scrollToBottom()
}

function renderContent(text) {
  return text.replace(/\n/g, '<br>')
}

function formatTime(isoStr) {
  const d = new Date(isoStr)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

// delta 是逐字到达的，按帧节流滚动，避免每个 token 都触发一次 nextTick
let scrollScheduled = false
function scheduleScroll() {
  if (scrollScheduled) return
  scrollScheduled = true
  requestAnimationFrame(() => {
    scrollScheduled = false
    scrollToBottom()
  })
}

onBeforeUnmount(() => {
  cancelStream()
})
</script>

<style scoped>
.chat-page {
  height: calc(100vh - 96px);
  margin: -20px;
}

.chat-container {
  display: flex;
  height: 100%;
}

.chat-sidebar {
  width: 280px;
  background: #fff;
  border-right: 1px solid #e6e6e6;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 20px 16px;
  border-bottom: 1px solid #e6e6e6;
}

.new-chat-btn {
  width: 100%;
  font-size: 15px;
  padding: 12px 0;
  border-radius: 8px;
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  color: var(--text-regular);
  margin-bottom: 2px;
  transition: background 0.15s;
}

.conv-item:hover {
  background: #f5f7fa;
}

.conv-item.active {
  background: #ecf5ff;
  color: var(--color-primary);
}

.conv-icon {
  flex-shrink: 0;
  font-size: 18px;
  color: #909399;
}

.conv-item.active .conv-icon {
  color: var(--color-primary);
}

.conv-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.conv-title {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  font-size: 11px;
  color: #b0b3bb;
}

.conv-item.active .conv-time {
  color: #7a9ecc;
}

.conv-delete {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.2s;
  color: var(--text-secondary);
  font-size: 16px;
}

.conv-item:hover .conv-delete {
  opacity: 1;
}

.conv-delete:hover {
  color: #f56c6c;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

.message-item {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}

.message-item.user {
  flex-direction: row-reverse;
}

.message-content {
  max-width: 70%;
}

.message-text {
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
}

.message-item.user .message-text {
  background: #ecf5ff;
  color: var(--text-primary);
}

.message-item.assistant .message-text {
  background: #f5f7fa;
  color: var(--text-primary);
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 16px;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: typing 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

.stream-status {
  margin-top: 6px;
  padding-left: 4px;
  font-size: 12px;
  color: #909399;
}

@keyframes typing {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

.chat-input-area {
  border-top: 1px solid #e6e6e6;
  padding: 16px 24px;
}

.input-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}
</style>
