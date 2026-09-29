<template>
  <div class="chat-page">
    <h1 class="visually-hidden">苏银AI问答助手</h1>
    <div
      class="chat-container"
      ref="chatContainerRef"
      :class="{
        'is-resizing': !!resizing,
        'is-resizing-x': resizing === 'sidebar',
        'is-resizing-y': resizing === 'input',
      }"
    >
      <!-- Conversation Sidebar -->
      <div class="chat-sidebar" :style="{ width: sidebarWidth + 'px' }">
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

        <!-- 拖拽调整会话列表宽度 -->
        <button
          type="button"
          class="chat-resizer-v"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽或使用左右方向键调整会话列表宽度"
          :aria-valuenow="Math.round(sidebarWidth)"
          :aria-valuemin="SIDEBAR_MIN"
          :aria-valuemax="Math.round(maxSidebarWidth)"
          @pointerdown="startResize('sidebar', $event)"
          @keydown="handleResizeKey('sidebar', $event)"
        />
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
                <el-avatar v-if="msg.role === 'user'" :size="36" icon="UserFilled" class="avatar-user" />
                <el-avatar v-else :size="36" class="avatar-assistant">
                  <el-icon :size="20"><Cpu /></el-icon>
                </el-avatar>
              </div>
              <div class="message-content">
                <!-- 这一轮带的附件（只展示文件名，文件本体在后端） -->
                <div v-if="msg.attachments && msg.attachments.length" class="msg-attachments">
                  <span v-for="(att, i) in msg.attachments" :key="i" class="msg-attachment">
                    <el-icon :size="13"><Paperclip /></el-icon>{{ att.name }}
                  </span>
                </div>
                <div class="message-text" v-html="renderContent(msg.content)"></div>
                <!-- 知识库切片里的配图：正文里只有说明文字，图片走 image_urls 单独渲染 -->
                <div v-if="msg.images && msg.images.length" class="msg-images">
                  <el-image
                    v-for="(url, i) in msg.images"
                    :key="`${msg.id}-img-${i}`"
                    :src="url"
                    :preview-src-list="msg.images"
                    :initial-index="i"
                    fit="contain"
                    preview-teleported
                    class="msg-image"
                  />
                </div>
                <!-- 业务申请书生成的 DOCX 下载卡片 -->
                <div v-if="msg.file" class="msg-file">
                  <el-icon :size="22" class="msg-file-icon"><Document /></el-icon>
                  <div class="msg-file-info">
                    <div class="msg-file-name">{{ msg.file.name }}</div>
                    <div class="msg-file-tip">业务申请书已生成</div>
                  </div>
                  <a :href="msg.file.url" target="_blank" rel="noopener">
                    <el-button size="small" type="primary">下载</el-button>
                  </a>
                </div>
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
          <!-- HITL：需要用户确认 / 选择时的快捷回复 -->
          <div v-if="pendingInterrupt" class="quick-reply-area">
            <div class="quick-reply-tip">{{ pendingInterrupt.tip }}</div>
            <div v-if="pendingInterrupt.options.length" class="quick-reply-actions">
              <el-button
                v-for="option in pendingInterrupt.options"
                :key="option"
                size="small"
                @click="handleQuickReply(option)"
              >
                {{ option }}
              </el-button>
            </div>
          </div>
          <!-- 首个 delta 到达前显示"打字中"动画，并带一行后端节点进度 -->
          <div v-if="aiLoading && !hasStreamedText" class="message-item assistant">
            <div class="message-avatar">
              <el-avatar :size="36" class="avatar-assistant">
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
        <div class="chat-input-area" :style="{ height: inputHeight + 'px' }">
          <!-- 拖拽调整输入区高度 -->
          <button
            type="button"
            class="chat-resizer-h"
            role="separator"
            aria-orientation="horizontal"
            aria-label="拖拽或使用上下方向键调整输入区高度"
            :aria-valuenow="Math.round(inputHeight)"
            :aria-valuemin="INPUT_MIN"
            :aria-valuemax="Math.round(maxInputHeight)"
            @pointerdown="startResize('input', $event)"
            @keydown="handleResizeKey('input', $event)"
          />

          <!-- 待发送的附件：点发送时才上传换 file_id -->
          <div v-if="pendingFiles.length" class="pending-files">
            <span v-for="(file, i) in pendingFiles" :key="i" class="pending-file">
              <el-icon :size="13"><Paperclip /></el-icon>{{ file.name }}
              <el-icon class="pending-file-remove" @click="removePendingFile(i)"><Close /></el-icon>
            </span>
          </div>
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="3"
            class="chat-input-field"
            placeholder="请输入问题（知识问答 / 文档识别 / 生成业务申请书），按 Enter 发送，Shift+Enter 换行"
            resize="none"
            @keydown.enter.exact="handleSend"
          />
          <div class="input-actions">
            <el-upload
              action="#"
              :auto-upload="false"
              :show-file-list="false"
              :on-change="handleFileChange"
              accept=".png,.jpg,.jpeg"
              multiple
            >
              <el-button :disabled="aiLoading || restoring">
                <el-icon><Paperclip /></el-icon>添加附件
              </el-button>
            </el-upload>
            <span class="upload-hint">仅支持 PNG / JPEG 图片</span>
            <div class="input-actions-right">
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
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import { getChatHistory, deleteChatHistory, closeChatSession } from '@/api/ai'
// 统一聊天入口：POST /api/agent/chat。主 Agent 先做意图识别，再决定走
// 老 RAG 链路（知识问答）还是 Document / Application 两个子图。
import { streamAgentMessage, uploadAgentAttachments } from '@/api/agent'

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
// 待发送的附件：选中后先挂在这里，点发送时才上传换 file_id
const pendingFiles = ref([])
// HITL：需要用户确认 / 从多个候选里选时的提示与快捷回复
const pendingInterrupt = ref(null)

// 每轮最多恢复多少条。后端 MySQL 每个会话只保留 50 条（CONTEXT_MYSQL_KEEP），
// 请求再大也只会返回 50 条，这里直接对齐上限。
const HISTORY_LIMIT = 50
const messagesRef = ref(null)
// 当前进行中的流式请求，用于切换会话 / 卸载组件时断开
let streamController = null

/* ---------------- 面板尺寸：支持拖拽调整并记住用户偏好 ---------------- */
const SIDEBAR_MIN = 200
const SIDEBAR_MAX = 460
const INPUT_MIN = 96
const INPUT_MAX = 420
const PANEL_SIZE_KEY = 'suyin:chat-panel-size'

const chatContainerRef = ref(null)

const savedPanelSize = loadPanelSize()
// pref = 用户偏好尺寸（持久化）；实际渲染尺寸会随可用空间自动夹紧
const sidebarPref = ref(savedPanelSize.sidebar)
const inputPref = ref(savedPanelSize.input)
// 对话区可用空间，由 ResizeObserver 维护
const containerSize = ref({ width: 0, height: 0 })
// '' | 'sidebar' | 'input'
const resizing = ref('')
let resizeOrigin = null
let containerObserver = null

function clampSize(value, min, max, fallback) {
  const num = Number(value)
  if (!Number.isFinite(num)) return fallback
  return Math.min(Math.max(num, min), Math.max(min, max))
}

// 会话列表最多占 460px，且给对话区保留至少 420px
const maxSidebarWidth = computed(() =>
  Math.max(
    SIDEBAR_MIN,
    Math.min(SIDEBAR_MAX, (containerSize.value.width || 1148) - 420)
  )
)

// 输入区最多占 420px，且给消息区保留至少 200px
const maxInputHeight = computed(() =>
  Math.max(
    INPUT_MIN,
    Math.min(INPUT_MAX, (containerSize.value.height || 880) - 200)
  )
)

// 渲染尺寸：不超过当前可用空间（窗口变大后会自动恢复用户偏好）
const sidebarWidth = computed(() => Math.min(sidebarPref.value, maxSidebarWidth.value))
const inputHeight = computed(() => Math.min(inputPref.value, maxInputHeight.value))

function loadPanelSize() {
  try {
    const saved = JSON.parse(localStorage.getItem(PANEL_SIZE_KEY) || '{}')
    return {
      sidebar: clampSize(saved.sidebar, SIDEBAR_MIN, SIDEBAR_MAX, 288),
      input: clampSize(saved.input, INPUT_MIN, INPUT_MAX, 176),
    }
  } catch (e) {
    return { sidebar: 288, input: 176 }
  }
}

function persistPanelSize() {
  try {
    localStorage.setItem(
      PANEL_SIZE_KEY,
      JSON.stringify({
        sidebar: Math.round(sidebarPref.value),
        input: Math.round(inputPref.value),
      })
    )
  } catch (e) {
    // 隐私模式下 localStorage 可能不可写，忽略即可
  }
}

// 对话区可用空间变化时（窗口缩放 / 侧栏收起）重新计算可拖拽上限
function observeContainer() {
  if (!chatContainerRef.value || typeof ResizeObserver === 'undefined') return
  containerObserver = new ResizeObserver((entries) => {
    const rect = entries[0]?.contentRect
    if (!rect) return
    containerSize.value = { width: rect.width, height: rect.height }
  })
  containerObserver.observe(chatContainerRef.value)
}

function stopObservingContainer() {
  containerObserver?.disconnect()
  containerObserver = null
}

function startResize(kind, event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  resizeOrigin = {
    kind,
    x: event.clientX,
    y: event.clientY,
    value: kind === 'sidebar' ? sidebarWidth.value : inputHeight.value,
  }
  resizing.value = kind
  window.addEventListener('pointermove', handleResizeMove)
  window.addEventListener('pointerup', stopResize)
  window.addEventListener('pointercancel', stopResize)
  event.preventDefault()
}

function handleResizeMove(event) {
  if (!resizeOrigin) return
  if (resizeOrigin.kind === 'sidebar') {
    sidebarPref.value = clampSize(
      resizeOrigin.value + (event.clientX - resizeOrigin.x),
      SIDEBAR_MIN,
      maxSidebarWidth.value,
      sidebarPref.value
    )
  } else {
    // 输入区往上拖是变高，所以用反向增量
    inputPref.value = clampSize(
      resizeOrigin.value - (event.clientY - resizeOrigin.y),
      INPUT_MIN,
      maxInputHeight.value,
      inputPref.value
    )
  }
}

function stopResize() {
  if (!resizeOrigin) return
  resizeOrigin = null
  resizing.value = ''
  window.removeEventListener('pointermove', handleResizeMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
  persistPanelSize()
}

// 鼠标拖拽之外的键盘替代方案：方向键 16px，Shift + 方向键 48px
function handleResizeKey(kind, event) {
  const step = event.shiftKey ? 48 : 16
  const isSidebar = kind === 'sidebar'
  const grow = isSidebar ? 'ArrowRight' : 'ArrowUp'
  const shrink = isSidebar ? 'ArrowLeft' : 'ArrowDown'
  if (event.key !== grow && event.key !== shrink) return

  event.preventDefault()
  const delta = event.key === grow ? step : -step
  if (isSidebar) {
    sidebarPref.value = clampSize(
      sidebarWidth.value + delta,
      SIDEBAR_MIN,
      maxSidebarWidth.value,
      sidebarPref.value
    )
  } else {
    inputPref.value = clampSize(
      inputHeight.value + delta,
      INPUT_MIN,
      maxInputHeight.value,
      inputPref.value
    )
  }
  persistPanelSize()
}

const currentConv = computed(() =>
  chatStore.conversations.find((c) => c.id === chatStore.currentConversationId) || null
)

onMounted(async () => {
  // 面板尺寸：跟随对话区可用空间自动夹紧
  observeContainer()
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
  pendingInterrupt.value = null
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
  pendingFiles.value = []
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

/**
 * 发送一条消息（统一入口 POST /api/agent/chat）。
 *
 * 主 Agent 先做意图识别，再决定走老 RAG 链路（知识问答）还是 Document /
 * Application 两个子图；前端只负责三件事：
 *   1) 有附件就先传 /api/agent/upload 换 file_id；
 *   2) 流式收 delta / final；
 *   3) 把 HITL（interrupt）渲染成快捷回复。
 */
async function handleSend() {
  const content = inputMessage.value.trim()
  const files = pendingFiles.value.slice()
  if ((!content && files.length === 0) || aiLoading.value) return
  if (restoring.value) {
    ElMessage.info('正在恢复历史对话，请稍候再发送')
    return
  }

  if (!currentConv.value) {
    chatStore.createConversation()
  }
  const conv = currentConv.value

  // 附件先上传：后端只认 file_id，文件落在 output/agent_files/（不进知识库）
  let attachments = []
  if (files.length) {
    try {
      const uploaded = await uploadAgentAttachments(files)
      attachments = (uploaded?.files || []).map((item) => ({
        file_id: item.file_id,
        filename: item.filename,
        mime_type: item.mime_type,
      }))
      pendingFiles.value = []
    } catch (e) {
      ElMessage.error('附件上传失败，请重试')
      return
    }
  }

  // 只有附件、没有文字时给个占位文本，气泡里能看出这一轮做了什么
  const userText = content || '（见附件）'
  pendingInterrupt.value = null
  chatStore.sendMessage(userText, 'user', {
    attachments: attachments.map((item) => ({ name: item.filename })),
  })
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
  const controller = streamAgentMessage(
    content,
    {
      threadId: conv.sessionId,
      userId: authStore.userInfo?.username,
      attachments,
    },
    {
      onReady: (threadId) => {
        // 首次对话回填后端 thread_id（= 记忆的 session_id），后续多轮必须带回
        chatStore.setSessionId(conv.id, threadId)
      },
      onProgress: (progress) => {
        if (!hasStreamedText.value) streamStatus.value = describeProgress(progress)
      },
      onAgentEvent: (name, data) => {
        const text = agentEventText(name, data)
        if (text && !hasStreamedText.value) streamStatus.value = text
      },
      onDelta: (delta) => {
        hasStreamedText.value = true
        streamStatus.value = ''
        assistantMsg.content += delta
        scheduleScroll()
      },
      onFinal: (data) => {
        handleFinal(assistantMsg, data)
      },
      onError: (message) => {
        failedMessage = message
      },
    },
  )
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

/** final 事件分三类：普通回答 / 生成文件 / HITL 暂停 */
function handleFinal(assistantMsg, data) {
  if (!data) return
  // 配图由后端单独下发（image_urls），不再夹在正文里当链接
  if (Array.isArray(data.image_urls) && data.image_urls.length) {
    // 已经用 Markdown 语法插进正文的图不再进图库，避免同一张显示两遍
    assistantMsg.images = dedupeImages(data.image_urls, data.answer || data.content || '')
  }
  if (data.type === 'file' && data.file) {
    assistantMsg.content = data.answer || data.content || '业务申请书已生成。'
    assistantMsg.file = { name: data.file.name, url: data.file.url }
    ElMessage.success('业务申请书已生成')
    return
  }
  if (data.type === 'interrupt') {
    const text = data.answer || data.content
    if (text) assistantMsg.content = text
    pendingInterrupt.value = buildInterrupt(data)
    return
  }
  const answer = data.answer || data.content
  if (answer) assistantMsg.content = answer
}

/** 去掉正文里已经出现过的图片（按去掉签名参数后的路径比对） */
function dedupeImages(urls, text) {
  const body = String(text || '')
  return urls.filter((url) => {
    const key = String(url || '').split('?')[0]
    return !(key && body.includes(key))
  })
}

/** 把 interrupt 载荷翻译成「一行提示 + 快捷回复按钮」 */
function buildInterrupt(data) {
  const payload = data.interrupt || {}
  const reason = data.reason || payload.reason
  const candidates = data.candidates || payload.candidates || []
  if (reason === 'multiple_company_candidates' && candidates.length) {
    return {
      tip: '检测到多个企业主体，请选择序号：',
      options: candidates.map((item, index) => String(index + 1)),
    }
  }
  if (reason === 'multiple_address_candidates' && candidates.length) {
    return {
      tip: '识别到多个同样优先级的地址，请选择序号：',
      options: candidates.map((item, index) => String(index + 1)),
    }
  }
  if (reason === 'confirm_before_generate') {
    return { tip: '确认信息无误后点「确认」，我就生成申请书。', options: ['确认', '取消'] }
  }
  if (reason === 'missing_application_fields') {
    return { tip: '还缺必填信息，可直接输入，或上传营业执照自动识别。', options: [] }
  }
  if (reason === 'company_not_found') {
    return { tip: '没找到匹配的企业，请给全称或上传营业执照。', options: [] }
  }
  return { tip: '需要你确认后继续。', options: [] }
}

/** Agent 过程事件 → 一行中文进度（首个 delta 之前展示） */
function agentEventText(name, data) {
  switch (name) {
    case 'agent_started':
      return '正在理解你的问题…'
    case 'routing':
      return {
        knowledge: '正在检索企业知识库…',
        document: '正在理解文档…',
        application: '正在处理业务申请书…',
        answer: '正在整理回答…',
        ask_user: '正在整理需要你补充的信息…',
      }[data?.next_action] || ''
    case 'tool_call':
      return data?.tool === 'rag_chain' ? '正在检索企业知识库…' : '正在调用工具…'
    case 'tool_result':
      return data?.count ? `已找到 ${data.count} 条相关资料` : ''
    case 'document_processing':
      return '正在识别文档内容…'
    case 'ocr_completed':
      return '文档识别完成，正在抽取字段…'
    case 'company_candidates':
      return '正在确认企业主体…'
    case 'template_selected':
      return '正在选择申请书模板…'
    case 'document_generating':
      return '正在生成申请书…'
    case 'document_ready':
      return '申请书已生成'
    // human_confirmation_required 的提示由 final 事件统一给出，避免重复
    default:
      return ''
  }
}

/** 选择附件（不自动上传，等点发送时再传） */
function handleFileChange(uploadFile) {
  const raw = uploadFile?.raw
  if (!raw) return
  // 只收图片：解析走 OCR MCP，它只认图片链接（PDF / Word 请用户先转图）
  const IMAGE_ACCEPT = ['.png', '.jpg', '.jpeg']
  const IMAGE_MIME = ['image/png', 'image/jpeg', 'image/jpg']
  const name = (raw.name || '').toLowerCase()
  const suffixOk = IMAGE_ACCEPT.some((ext) => name.endsWith(ext))
  const mimeOk = !raw.type || IMAGE_MIME.includes(raw.type)
  if (!suffixOk || !mimeOk) {
    ElMessage.warning('请上传图片格式（PNG / JPEG）以供解析')
    return
  }
  const MAX_MB = 20
  if (raw.size > MAX_MB * 1024 * 1024) {
    ElMessage.warning(`单个文件不能超过 ${MAX_MB}MB`)
    return
  }
  pendingFiles.value.push(raw)
}

function removePendingFile(index) {
  pendingFiles.value.splice(index, 1)
}

/** HITL 快捷回复：把选项当成一条普通消息发出去（后端自动识别为 resume） */
function handleQuickReply(text) {
  inputMessage.value = text
  handleSend()
}

// 模型按老 prompt 约定追加的图片区块（【图片】+ 每行一个 URL）。
// 图片地址已由后端单独下发，正文里这块属于噪音，渲染前先摘掉。
const IMAGE_BLOCK_MARKER = '【图片】'

function stripImageBlock(text) {
  const index = text.indexOf(IMAGE_BLOCK_MARKER)
  if (index === -1) return text
  const head = text.slice(0, index).trimEnd()
  const rest = text
    .slice(index + IMAGE_BLOCK_MARKER.length)
    .split('\n')
    .filter((line) => {
      const trimmed = line.trim()
      // 纯链接行是图片区块的一部分；其余内容（模型额外写的正文）保留
      return trimmed && !/^(https?:\/\/|www\.)\S+$/i.test(trimmed)
    })
  return rest.length ? `${head}\n${rest.join('\n')}` : head
}

/**
 * 消息正文渲染：转义 HTML + 极少量 Markdown。
 *
 * 消息里既有模型输出也有文件内容，不能直接当 HTML 用（XSS），所以先转义。
 * 图片是唯一必须真正渲染的元素：知识库配图给的是 COS 外链，
 * 只转成 <br> 的话用户看到的就是一行链接而不是图。
 */
function renderContent(text) {
  const safe = stripImageBlock(String(text || ''))
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return safe
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (match, alt, url) => {
      // 只放行图片外链与站内相对路径，挡掉 javascript: 这类协议
      if (!/^(https?:\/\/|\/\/|\/)/i.test(url)) return match
      return `<img class="msg-inline-image" src="${url}" alt="${alt}" loading="lazy">`
    })
    .replace(/\n/g, '<br>')
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
  // 拖拽中卸载组件时清理挂在 window 上的监听
  stopResize()
  stopObservingContainer()
})
</script>

<style scoped>
.chat-page {
  height: 100%;
  background: var(--color-bg-app);
}

.chat-container {
  display: flex;
  height: 100%;
  padding: var(--space-5);
  gap: var(--space-5);
}

.chat-sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  width: 288px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

/* 会话列表宽度拖拽条：位于侧栏与对话区之间的间隙中 */
.chat-resizer-v {
  position: absolute;
  top: 0;
  right: -12px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 100%;
  padding: 0;
  background: transparent;
  border: none;
  cursor: col-resize;
  touch-action: none;
}

.chat-resizer-v::after {
  content: '';
  width: 3px;
  height: 38px;
  border-radius: var(--radius-pill);
  background: var(--color-border);
  transition: height var(--duration-fast) var(--ease-standard),
    background var(--duration-fast) var(--ease-standard);
}

.chat-resizer-v:hover::after,
.chat-resizer-v:focus-visible::after {
  height: 62px;
  background: var(--brand-400);
}

.chat-container.is-resizing {
  user-select: none;
}

.chat-container.is-resizing-x {
  cursor: col-resize;
}

.chat-container.is-resizing-y {
  cursor: row-resize;
}

.sidebar-header {
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
}

.new-chat-btn {
  width: 100%;
  height: 42px;
  font-weight: 600;
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-3);
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  color: var(--text-regular);
  margin-bottom: 4px;
  transition: background var(--duration-fast) var(--ease-standard),
    border-color var(--duration-fast) var(--ease-standard);
}

.conv-item:hover {
  background: var(--color-bg-sunken);
}

.conv-item.active {
  background: var(--brand-50);
  border-color: var(--brand-200);
  color: var(--color-primary);
}

.conv-icon {
  flex-shrink: 0;
  font-size: 17px;
  color: var(--text-placeholder);
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
  color: var(--text-placeholder);
}

.conv-item.active .conv-time {
  color: var(--brand-400);
}

.conv-delete {
  flex-shrink: 0;
  opacity: 0;
  font-size: 15px;
  color: var(--text-secondary);
  transition: opacity var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
}

.conv-item:focus-within .conv-delete,
.conv-item:hover .conv-delete {
  opacity: 1;
}

.conv-delete:hover {
  color: var(--color-danger);
}

.chat-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}

.chat-messages {
  flex: 1;
  padding: var(--space-6);
  overflow-y: auto;
  scroll-behavior: smooth;
}

.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

@media (max-width: 900px) {
  .chat-container {
    padding: var(--space-3);
  }
}

@media (max-width: 640px) {
  .chat-sidebar {
    display: none;
  }
}

.message-item {
  display: flex;
  gap: 14px;
  margin-bottom: 22px;
}

.message-item.user {
  flex-direction: row-reverse;
}

.message-avatar {
  flex-shrink: 0;
}

.avatar-user {
  color: var(--brand-700);
  background: var(--brand-100);
}

.avatar-assistant {
  color: #fff;
  background: linear-gradient(135deg, #4e7bff, #1c3190);
}

.message-content {
  max-width: 70%;
  min-width: 0;
}

.message-text {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 14px;
  line-height: 1.75;
  word-break: break-word;
}

.message-item.user .message-text {
  color: #fff;
  background: linear-gradient(135deg, #3358e8, #1d38b0);
  border-top-right-radius: var(--radius-xs);
  box-shadow: 0 4px 14px rgba(36, 71, 216, 0.18);
}

.message-item.assistant .message-text {
  background: var(--color-bg-sunken);
  border: 1px solid var(--color-border-light);
  border-top-left-radius: var(--radius-xs);
  color: var(--text-primary);
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 14px 16px;
  background: var(--color-bg-sunken);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  border-top-left-radius: var(--radius-xs);
}

.typing-indicator span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--brand-300);
  animation: typing 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

.stream-status {
  margin-top: 6px;
  padding-left: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}

@keyframes typing {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

.chat-input-area {
  position: relative;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  padding: var(--space-4) var(--space-5) var(--space-5);
  background: var(--color-bg-elevated);
  border-top: 1px solid var(--color-border-light);
}

/* 输入区高度拖拽条：贴在输入区上沿 */
.chat-resizer-h {
  position: absolute;
  top: -9px;
  left: 0;
  right: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 18px;
  padding: 0;
  background: transparent;
  border: none;
  cursor: row-resize;
  touch-action: none;
}

.chat-resizer-h::after {
  content: '';
  width: 46px;
  height: 3px;
  border-radius: var(--radius-pill);
  background: var(--color-border);
  transition: width var(--duration-fast) var(--ease-standard),
    background var(--duration-fast) var(--ease-standard);
}

.chat-resizer-h:hover::after,
.chat-resizer-h:focus-visible::after {
  width: 70px;
  background: var(--brand-400);
}

.chat-input-field {
  flex: 1;
  min-height: 0;
}

.chat-input-field :deep(.el-textarea__inner) {
  height: 100%;
}

.input-actions {
  display: flex;
  flex-shrink: 0;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-top: var(--space-3);
}

.input-actions-right {
  display: flex;
  gap: 8px;
}

.upload-hint {
  font-size: 12px;
  color: var(--text-placeholder);
  margin-left: 4px;
}

/* 附件展示 */
.msg-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.msg-attachment {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: rgba(255, 255, 255, 0.18);
  color: inherit;
  font-size: 12px;
}

.message-item.assistant .msg-attachment {
  background: var(--color-info-soft);
  color: var(--text-regular);
}

/* 生成文件下载卡片 */
.msg-file {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding: 10px 12px;
  max-width: 420px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-elevated);
}

.msg-file-icon {
  flex-shrink: 0;
  color: var(--color-primary);
}

.msg-file-info {
  flex: 1;
  min-width: 0;
}

.msg-file-name {
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg-file-tip {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-secondary);
}

/* 回答里的配图（COS 外链，走 el-image 带点击预览） */
.msg-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.msg-image {
  width: 200px;
  height: 150px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
  background: var(--color-bg-sunken);
  overflow: hidden;
}

/* Markdown 图片语法写在正文里的情况 */
.message-text :deep(.msg-inline-image) {
  display: block;
  max-width: 100%;
  margin: 8px 0;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-sm);
}

/* HITL 快捷回复 */
.quick-reply-area {
  padding: 0 var(--space-6) var(--space-3) 62px;
}

.quick-reply-tip {
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--text-regular);
}

.quick-reply-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 待发送附件 */
.pending-files {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.pending-file {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: var(--radius-pill);
  background: var(--brand-50);
  color: var(--brand-700);
  font-size: 12px;
}

.pending-file-remove {
  cursor: pointer;
}

.pending-file-remove:hover {
  color: var(--color-danger);
}
</style>
