<template>
  <div class="prompts-page">
    <PageHeader
      title="Prompt 管理"
      description="维护助手各链路的提示词模板，保存后对后续请求生效"
    />

    <el-row :gutter="20">
      <!-- 列表 -->
      <el-col :xs="24" :lg="8">
        <div class="content-card list-card">
          <div class="list-head">
            <h3 class="section-title">Prompt 列表</h3>
            <span class="list-count">{{ promptList.length }} 项</span>
          </div>

          <div v-if="promptList.length" class="prompt-list">
            <button
              v-for="prompt in promptList"
              :key="prompt.id"
              type="button"
              class="prompt-item"
              :class="{ 'is-active': selectedId === prompt.id }"
              @click="handleSelect(prompt)"
            >
              <span class="prompt-name">{{ prompt.name }}</span>
              <span class="prompt-time">{{ prompt.updatedAt }}</span>
            </button>
          </div>

          <div v-else class="empty-block">
            <el-icon :size="26"><Edit /></el-icon>
            <p class="empty-title">暂无 Prompt</p>
            <p class="empty-desc">后端 Prompt 接口接入后，模板将在此列出</p>
          </div>
        </div>
      </el-col>

      <!-- 编辑器 -->
      <el-col :xs="24" :lg="16">
        <div class="content-card editor-card">
          <div class="list-head">
            <h3 class="section-title">编辑 Prompt</h3>
            <span v-if="selectedPrompt" class="list-count">
              最后更新：{{ selectedPrompt.updatedAt }}
            </span>
          </div>

          <div v-if="!selectedPrompt" class="empty-block is-tall">
            <span class="empty-icon"><el-icon :size="24"><EditPen /></el-icon></span>
            <p class="empty-title">未选择 Prompt</p>
            <p class="empty-desc">从左侧列表选择一个模板后即可编辑内容</p>
          </div>

          <div v-else class="editor-content">
            <el-input
              v-model="content"
              type="textarea"
              :rows="16"
              resize="vertical"
              placeholder="请输入 Prompt 内容"
              class="editor-textarea"
            />
            <div class="editor-foot">
              <span class="editor-count">{{ content.length }} 字符</span>
              <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
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
import PageHeader from '@/components/PageHeader.vue'
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
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.list-card,
.editor-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 420px;
}

.list-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.list-count {
  font-size: 12.5px;
  color: var(--text-secondary);
}

.prompt-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 460px;
  overflow-y: auto;
}

.prompt-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  font-family: inherit;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-standard),
    border-color var(--duration-fast) var(--ease-standard);
}

.prompt-item:hover {
  background: var(--color-bg-sunken);
}

.prompt-item.is-active {
  background: var(--brand-50);
  border-color: var(--brand-200);
}

.prompt-name {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-primary);
}

.prompt-item.is-active .prompt-name {
  color: var(--brand-700);
}

.prompt-time {
  font-size: 11.5px;
  color: var(--text-placeholder);
}

.empty-block.is-tall {
  flex: 1;
  justify-content: center;
}

.editor-content {
  display: flex;
  flex: 1;
  flex-direction: column;
}

.editor-textarea {
  flex: 1;
}

.editor-textarea :deep(.el-textarea__inner) {
  font-family: 'IBM Plex Mono', Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.7;
}

.editor-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-4);
}

.editor-count {
  font-size: 12px;
  color: var(--text-placeholder);
}
</style>
