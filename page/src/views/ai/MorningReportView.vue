<template>
  <div class="morning-report-page">
    <div class="page-header">
      <h2 class="page-title">经营晨报助手</h2>
    </div>

    <div class="report-container">
      <!-- KB Selector -->
      <div class="kb-selector">
        <span class="selector-label">知识库：</span>
        <el-select v-model="selectedKB" placeholder="选择知识库" style="width: 240px">
          <el-option
            v-for="kb in knowledgeBases"
            :key="kb.id"
            :label="kb.name"
            :value="kb.name"
          />
        </el-select>
      </div>

      <el-divider />

      <!-- Chat History -->
      <div class="qa-list" ref="qaListRef">
        <div v-for="(item, index) in qaHistory" :key="index" class="qa-item">
          <!-- User Question -->
          <div class="qa-question">
            <div class="qa-avatar user-avatar">
              <el-icon :size="18"><UserFilled /></el-icon>
            </div>
            <div class="qa-bubble user-bubble">{{ item.question }}</div>
          </div>

          <!-- AI Answer -->
          <div class="qa-answer">
            <div class="qa-avatar ai-avatar">
              <el-icon :size="18"><Cpu /></el-icon>
            </div>
            <div class="qa-content">
              <div class="qa-bubble ai-bubble" v-html="renderContent(item.answer)"></div>

              <!-- Sources -->
              <div v-if="item.sources && item.sources.length" class="qa-sources">
                <div class="sources-title">引用来源：</div>
                <div v-for="(src, si) in item.sources" :key="si" class="source-item">
                  <div class="source-file">
                    <el-icon><Document /></el-icon>
                    {{ src.file }}
                  </div>
                  <div class="source-page">第{{ src.page }}页</div>
                  <div class="source-snippet">相关内容：{{ src.snippet }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Loading -->
        <div v-if="loading" class="qa-answer">
          <div class="qa-avatar ai-avatar">
            <el-icon :size="18"><Cpu /></el-icon>
          </div>
          <div class="qa-bubble ai-bubble">
            <div class="typing-indicator">
              <span></span><span></span><span></span>
            </div>
          </div>
        </div>
      </div>

      <!-- Input -->
      <div class="qa-input-area">
        <el-input
          v-model="question"
          placeholder="请输入问题，例如：最近三个月经营情况如何？"
          @keydown.enter="handleQuery"
          size="large"
        >
          <template #append>
            <el-button
              type="primary"
              @click="handleQuery"
              :loading="loading"
              :icon="Promotion"
            >
              发送
            </el-button>
          </template>
        </el-input>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, nextTick } from 'vue'
import { queryMorningReport, getKnowledgeBases } from '@/api/ai'

const selectedKB = ref('晨报知识库')
const knowledgeBases = ref([])
const question = ref('')
const loading = ref(false)
const qaHistory = reactive([])
const qaListRef = ref(null)

onMounted(async () => {
  const res = await getKnowledgeBases()
  if (res.code === 200) {
    knowledgeBases.value = res.data
  }
})

async function handleQuery() {
  const q = question.value.trim()
  if (!q || loading.value) return

  loading.value = true
  question.value = ''

  try {
    const res = await queryMorningReport(q, selectedKB.value)
    if (res.code === 200) {
      qaHistory.push({
        question: q,
        answer: res.data.answer,
        sources: res.data.sources || [],
      })
      await scrollToBottom()
    }
  } catch (e) {
    qaHistory.push({
      question: q,
      answer: '抱歉，查询失败，请稍后重试。',
      sources: [],
    })
  } finally {
    loading.value = false
  }
}

function renderContent(text) {
  return text.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
}

async function scrollToBottom() {
  await nextTick()
  if (qaListRef.value) {
    qaListRef.value.scrollTop = qaListRef.value.scrollHeight
  }
}
</script>

<style scoped>
.report-container {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  padding: 20px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 180px);
}

.kb-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}

.selector-label {
  font-size: 14px;
  color: var(--text-regular);
  font-weight: 500;
}

.qa-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.qa-item {
  margin-bottom: 32px;
}

.qa-question,
.qa-answer {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.qa-question {
  justify-content: flex-end;
}

.qa-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-avatar {
  background: #ecf5ff;
  color: #409eff;
}

.ai-avatar {
  background: #409eff;
  color: #fff;
}

.qa-bubble {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
}

.user-bubble {
  background: #ecf5ff;
  color: var(--text-primary);
}

.ai-bubble {
  background: #f5f7fa;
  color: var(--text-primary);
}

.qa-content {
  flex: 1;
  min-width: 0;
}

.qa-sources {
  margin-top: 12px;
  padding: 12px;
  background: #fafafa;
  border-radius: 6px;
  border-left: 3px solid var(--color-primary);
}

.sources-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.source-item {
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed #e6e6e6;
}

.source-item:last-child {
  margin-bottom: 0;
  border-bottom: none;
}

.source-file {
  font-size: 13px;
  color: var(--color-primary);
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 500;
}

.source-page {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.source-snippet {
  font-size: 12px;
  color: var(--text-regular);
  margin-top: 4px;
  padding: 4px 8px;
  background: #f0f2f5;
  border-radius: 4px;
}

.qa-input-area {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e6e6e6;
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
</style>
