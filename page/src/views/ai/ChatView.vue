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
            @click="chatStore.selectConversation(conv.id)"
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
          <div v-else class="chat-empty">
            <el-empty description="开始一段新对话吧" :image-size="120" />
          </div>
          <div v-if="aiLoading" class="message-item assistant">
            <div class="message-avatar">
              <el-avatar :size="36" style="background: #409eff">
                <el-icon :size="20"><Cpu /></el-icon>
              </el-avatar>
            </div>
            <div class="message-content">
              <div class="typing-indicator">
                <span></span><span></span><span></span>
              </div>
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
            <el-button type="primary" @click="handleSend" :loading="aiLoading" :icon="Promotion">
              发送
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessageBox } from 'element-plus'
import { useChatStore } from '@/stores/chat'
import { sendChatMessage } from '@/api/ai'

const chatStore = useChatStore()
const inputMessage = ref('')
const aiLoading = ref(false)
const messagesRef = ref(null)

const currentConv = computed(() =>
  chatStore.conversations.find((c) => c.id === chatStore.currentConversationId) || null
)

onMounted(() => {
  if (chatStore.conversations.length === 0) {
    chatStore.createConversation()
  }
})

function handleNewChat() {
  chatStore.createConversation()
  inputMessage.value = ''
}

function handleDeleteConv(id) {
  ElMessageBox.confirm('确定删除该对话吗？', '确认', { type: 'warning' }).then(() => {
    chatStore.deleteConversation(id)
  }).catch(() => {})
}

function handleClearSession() {
  ElMessageBox.confirm('确定清空当前会话吗？', '确认', { type: 'warning' }).then(() => {
    const conv = currentConv.value
    if (conv) {
      conv.messages = []
    }
  }).catch(() => {})
}

async function handleSend() {
  const content = inputMessage.value.trim()
  if (!content || aiLoading.value) return

  if (!currentConv.value) {
    chatStore.createConversation()
  }

  chatStore.sendMessage(content, 'user')
  inputMessage.value = ''
  await scrollToBottom()

  aiLoading.value = true
  try {
    const conv = currentConv.value
    const res = await sendChatMessage(content, conv.sessionId)
    // 后端返回: { answer, session_id, message, done_list }
    // 首次对话回填后端 session_id
    if (!conv.sessionId) {
      conv.sessionId = res.session_id
    }
    chatStore.sendMessage(res.answer, 'assistant')
    await scrollToBottom()
  } catch (e) {
    chatStore.sendMessage('抱歉，请求失败，请稍后重试。', 'assistant')
  } finally {
    aiLoading.value = false
  }
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
