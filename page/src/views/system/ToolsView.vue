<template>
  <div class="tools-page">
    <PageHeader title="工具管理" description="查看助手可调用的工具，并控制其启用状态" />

    <div class="content-card">
      <div class="list-head">
        <h3 class="section-title">工具列表</h3>
        <span class="list-count">共 {{ toolList.length }} 个工具</span>
      </div>

      <el-table :data="toolList" v-loading="loading">
        <el-table-column prop="name" label="工具名称" width="180" />
        <el-table-column prop="key" label="标识" width="200">
          <template #default="{ row }">
            <code class="code-chip">{{ row.key }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="260" />
        <el-table-column prop="status" label="状态" width="150">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status"
              active-text="开启"
              inactive-text="关闭"
              inline-prompt
              @change="(val) => handleToggle(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="handleEdit(row)">修改描述</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-block">
            <el-icon :size="28"><Switch /></el-icon>
            <p class="empty-title">暂无工具数据</p>
            <p class="empty-desc">后端工具接口接入后，可在此统一管理工具开关</p>
          </div>
        </template>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" title="修改工具描述" width="520px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="工具名称">
          <span>{{ editForm.name }}</span>
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="editForm.description"
            type="textarea"
            :rows="3"
            placeholder="请输入工具用途说明"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { getToolList, toggleToolStatus } from '@/api/system'

const toolList = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editForm = reactive({ id: null, name: '', description: '' })

onMounted(async () => {
  loading.value = true
  const res = await getToolList()
  if (res.code === 200) toolList.value = res.data
  loading.value = false
})

async function handleToggle(row, val) {
  try {
    await toggleToolStatus(row.id, val)
    row.status = val
    ElMessage.success(`${row.name} 已${val ? '开启' : '关闭'}`)
  } catch (e) {
    ElMessage.error(e?.message || '操作失败')
  }
}

function handleEdit(row) {
  editForm.id = row.id
  editForm.name = row.name
  editForm.description = row.description
  dialogVisible.value = true
}

function handleSave() {
  const tool = toolList.value.find((t) => t.id === editForm.id)
  if (tool) tool.description = editForm.description
  dialogVisible.value = false
  ElMessage.success('已保存')
}
</script>

<style scoped>
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

.code-chip {
  padding: 3px 8px;
  font-family: 'IBM Plex Mono', Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  color: var(--brand-700);
  background: var(--brand-50);
  border-radius: var(--radius-xs);
}
</style>
