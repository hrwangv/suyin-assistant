<template>
  <div class="ai-chat-box">
    <!-- Messages -->
    <div class="chat-messages" ref="messagesRef">
      <div
        v-for="(msg, index) in messages"
        :key="index"
        :class="['message-item', msg.role]"
      >
        <div class="message-avatar">
          <el-avatar v-if="msg.role === 'user'" :size="36" icon="UserFilled" class="avatar-user" />
          <el-avatar v-else :size="36" class="avatar-assistant">
            <el-icon :size="20"><Cpu /></el-icon>
          </el-avatar>
        </div>
        <div class="message-bubble" v-html="formatContent(msg.content)" />
      </div>

      <div v-if="loading" class="message-item assistant">
        <div class="message-avatar">
          <el-avatar :size="36" class="avatar-assistant">
            <el-icon :size="20"><Cpu /></el-icon>
          </el-avatar>
        </div>
        <div class="message-bubble is-typing">
          <div class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>
    </div>

    <!-- Input -->
    <div class="chat-input">
      <slot name="input">
        <el-input
          v-model="inputText"
          :placeholder="placeholder"
          @keydown.enter="handleSend"
        >
          <template #append>
            <el-button :icon="Promotion" :loading="loading" @click="handleSend" />
          </template>
        </el-input>
      </slot>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'
import { Promotion } from '@element-plus/icons-vue'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  placeholder: { type: String, default: '请输入消息...' },
})

const emit = defineEmits(['send'])

const inputText = ref('')
const messagesRef = ref(null)

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.loading) return
  emit('send', text)
  inputText.value = ''
}

function formatContent(text) {
  if (!text) return ''
  return text.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
}

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  }
)
</script>

<style scoped>
.ai-chat-box {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
}

.chat-messages {
  flex: 1;
  padding: var(--space-5);
  overflow-y: auto;
}

.message-item {
  display: flex;
  gap: 14px;
  margin-bottom: 20px;
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

.message-bubble {
  max-width: 70%;
  padding: 12px 16px;
  font-size: 14px;
  line-height: 1.75;
  border-radius: var(--radius-md);
  word-break: break-word;
}

.message-item.user .message-bubble {
  color: #fff;
  background: linear-gradient(135deg, #3358e8, #1d38b0);
  border-top-right-radius: var(--radius-xs);
}

.message-item.assistant .message-bubble {
  color: var(--text-primary);
  background: var(--color-bg-sunken);
  border: 1px solid var(--color-border-light);
  border-top-left-radius: var(--radius-xs);
}

.message-bubble.is-typing {
  padding: 0;
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 14px 16px;
}

.typing-indicator span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--brand-300);
  animation: typing 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%,
  60%,
  100% {
    transform: translateY(0);
  }
  30% {
    transform: translateY(-6px);
  }
}

.chat-input {
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--color-border-light);
}
</style>
