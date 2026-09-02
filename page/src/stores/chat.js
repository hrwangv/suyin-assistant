import { defineStore } from 'pinia'
import { ref } from 'vue'

let nextId = 1

export const useChatStore = defineStore('chat', () => {
  const conversations = ref([])
  const currentConversationId = ref(null)

  function createConversation(title = '新对话') {
    const conv = {
      id: nextId++,
      sessionId: null,   // 后端 UUID，首次提问后回填，多轮对话必须带回
      title,
      messages: [],
      createdAt: new Date().toISOString(),
    }
    conversations.value.unshift(conv)
    currentConversationId.value = conv.id
    return conv
  }

  function deleteConversation(id) {
    const index = conversations.value.findIndex((c) => c.id === id)
    if (index > -1) {
      conversations.value.splice(index, 1)
      if (currentConversationId.value === id) {
        currentConversationId.value = conversations.value[0]?.id || null
      }
    }
  }

  function selectConversation(id) {
    currentConversationId.value = id
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
    }
  }

  return {
    conversations,
    currentConversationId,
    createConversation,
    deleteConversation,
    selectConversation,
    currentConversation,
    sendMessage,
  }
})
