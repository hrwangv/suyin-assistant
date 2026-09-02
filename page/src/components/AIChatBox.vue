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
          <el-avatar v-if="msg.role === 'user'" :size="36" icon="UserFilled" />
          <el-avatar v-else :size="36" style="background: #409eff">
            <el-icon :size="20"><Cpu /></el-icon>
          </el-avatar>
        </div>
        <div class="message-bubble" v-html="formatContent(msg.content)" />
      </div>

      <div v-if="loading" class="message-item assistant">
        <div class="message-avatar">
          <el-avatar :size="36" style="background: #409eff">
            <el-icon :size="20"><Cpu /></el-icon>
          </el-avatar>
        </div>
        <div class="message-bubble">
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
            <el-button
              :icon="Promotion"
              :loading="loading"
              @click="handleSend"
            />
          </template>
        </el-input>
      </slot>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'

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
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.message-item {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.message-item.user {
  flex-direction: row-reverse;
}

.message-bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
}

.message-item.user .message-bubble {
  background: #ecf5ff;
}

.message-item.assistant .message-bubble {
  background: #f5f7fa;
}

.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 8px;
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

.chat-input {
  padding: 12px 20px;
  border-top: 1px solid #ebeef5;
}
</style>
