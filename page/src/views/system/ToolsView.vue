<template>
  <div class="tools-page">
    <div class="page-header">
      <h2 class="page-title">工具管理</h2>
    </div>

    <div class="content-card">
      <el-table :data="toolList" stripe v-loading="loading">
        <el-table-column prop="name" label="工具名称" width="160" />
        <el-table-column prop="key" label="标识" width="180" />
        <el-table-column prop="description" label="描述" min-width="260" />
        <el-table-column prop="status" label="状态" width="140">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status"
              @change="(val) => handleToggle(row, val)"
              active-text="开启"
              inactive-text="关闭"
              style="--el-switch-on-color: #67c23a; --el-switch-off-color: #f56c6c"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleEdit(row)">修改描述</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Edit Dialog -->
    <el-dialog v-model="dialogVisible" title="修改工具描述" width="500px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="工具名称">
          <span>{{ editForm.name }}</span>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="3" />
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
    ElMessage.error('操作失败')
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
