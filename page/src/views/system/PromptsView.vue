<template>
  <div class="prompts-page">
    <div class="page-header">
      <h2 class="page-title">Prompt管理</h2>
    </div>

    <el-row :gutter="24">
      <!-- Prompt List -->
      <el-col :span="8">
        <div class="content-card">
          <h3 class="section-title">Prompt列表</h3>
          <div class="prompt-list">
            <div
              v-for="prompt in promptList"
              :key="prompt.id"
              :class="['prompt-item', { active: selectedId === prompt.id }]"
              @click="handleSelect(prompt)"
            >
              <div class="prompt-name">{{ prompt.name }}</div>
              <div class="prompt-time">{{ prompt.updatedAt }}</div>
            </div>
          </div>
        </div>
      </el-col>

      <!-- Editor -->
      <el-col :span="16">
        <div class="content-card editor-card">
          <h3 class="section-title">编辑Prompt</h3>
          <div v-if="!selectedPrompt" class="editor-empty">
            <el-empty description="请从左侧选择一个Prompt" :image-size="80" />
          </div>
          <div v-else class="editor-content">
            <div class="editor-header flex-between">
              <span class="editor-title">{{ selectedPrompt.name }}</span>
              <span class="editor-time">最后更新：{{ selectedPrompt.updatedAt }}</span>
            </div>
            <el-input
              v-model="content"
              type="textarea"
              :rows="14"
              placeholder="请输入Prompt内容"
              class="editor-textarea"
            />
            <div class="editor-actions">
              <el-button type="primary" :loading="saving" @click="handleSave">
                保存
              </el-button>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getPromptList, getPromptDetail, updatePrompt } from '@/api/system'

const promptList = ref([])
const selectedId = ref(null)
const selectedPrompt = ref(null)
const content = ref('')
const saving = ref(false)

onMounted(async () => {
  const res = await getPromptList()
  if (res.code === 200) promptList.value = res.data
})

async function handleSelect(prompt) {
  selectedId.value = prompt.id
  const res = await getPromptDetail(prompt.id)
  if (res.code === 200) {
    selectedPrompt.value = res.data
    content.value = res.data.content
  }
}

async function handleSave() {
  if (!selectedPrompt.value) return
  saving.value = true
  try {
    await updatePrompt(selectedPrompt.value.id, content.value)
    ElMessage.success('保存成功')
    selectedPrompt.value.updatedAt = new Date().toLocaleString('zh-CN')
    const item = promptList.value.find((p) => p.id === selectedPrompt.value.id)
    if (item) item.updatedAt = selectedPrompt.value.updatedAt
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.prompt-list {
  max-height: 500px;
  overflow-y: auto;
}

.prompt-item {
  padding: 14px 16px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  border: 1px solid transparent;
  transition: all 0.2s;
}

.prompt-item:hover {
  background: #f5f7fa;
}

.prompt-item.active {
  background: #ecf5ff;
  border-color: var(--color-primary);
}

.prompt-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.prompt-time {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.editor-card {
  min-height: 400px;
}

.editor-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

.editor-header {
  margin-bottom: 16px;
}

.editor-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.editor-time {
  font-size: 13px;
  color: var(--text-secondary);
}

.editor-textarea {
  margin-bottom: 16px;
}

.editor-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
