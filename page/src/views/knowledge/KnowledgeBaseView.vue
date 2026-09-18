<template>
  <div class="knowledge-page">
    <div class="page-header">
      <h2 class="page-title">企业知识库</h2>
    </div>

    <!-- Upload -->
    <div class="content-card">
      <h3 class="section-title">上传文件</h3>
      <el-upload
        class="upload-area"
        drag
        multiple
        action="#"
        :auto-upload="false"
        :on-change="handleFileChange"
        :show-file-list="false"
        accept=".pdf,.docx,.doc,.md,.xlsx,.xls,.pptx,.ppt,.png,.jpg,.jpeg,.gif,.bmp,.webp"
      >
        <el-icon :size="48" color="#c0c4cc"><UploadFilled /></el-icon>
        <div class="upload-text">
          <p>将文件拖到此处，或<em>点击上传</em></p>
          <p class="upload-hint">可一次选择或拖入多个文件，支持 PDF、Word、Markdown、Excel、PPT、图片 格式</p>
        </div>
      </el-upload>

      <div v-if="pendingFiles.length" class="uploading-list">
        <div v-for="file in pendingFiles" :key="file.uid" class="uploading-item">
          <span class="uploading-name">
            <el-icon><Document /></el-icon>
            {{ file.name }}
          </span>
          <el-button type="danger" link size="small" @click="removePendingFile(file.uid)">
            移除
          </el-button>
        </div>
      </div>

      <div v-if="pendingFiles.length" class="batch-actions">
        <el-button type="primary" @click="startBatchUpload">
          开始上传（{{ pendingFiles.length }} 个文件）
        </el-button>
      </div>

      <div v-if="uploadingFiles.length" class="uploading-list">
        <div v-for="file in uploadingFiles" :key="file.uid" class="uploading-item">
          <span class="uploading-name">
            <el-icon><Document /></el-icon>
            {{ file.name }}
          </span>
          <el-progress :percentage="file.percentage" :status="file.status" style="width: 200px" />
        </div>
      </div>
    </div>

    <!-- File List -->
    <div class="content-card">
      <h3 class="section-title">文件列表</h3>
      <el-table :data="fileList" stripe v-loading="loading">
        <el-table-column prop="name" label="名称" min-width="200">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 8px">
              <el-icon color="#409eff"><Document /></el-icon>
              <span>{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag
              :type="statusType(row.status)"
              size="small"
            >
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="大小" width="100" />
        <el-table-column prop="uploadTime" label="上传时间" width="180" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" link size="small" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getFileList, uploadFiles, deleteFile } from '@/api/knowledge'

const fileList = ref([])
const pendingFiles = ref([])
const uploadingFiles = ref([])
const loading = ref(false)

async function loadFileList() {
  loading.value = true
  try {
    const res = await getFileList()
    if (res.code === 200) {
      fileList.value = res.data
    }
  } finally {
    loading.value = false
  }
}

function handleFileStatusFinished() {
  loadFileList()
}

onMounted(() => {
  loadFileList()
  window.addEventListener('knowledge-file-status-finished', handleFileStatusFinished)
})

onBeforeUnmount(() => {
  window.removeEventListener('knowledge-file-status-finished', handleFileStatusFinished)
})

function statusType(status) {
  const map = {
    completed: 'success',
    parsing: 'warning',
    embedding: 'warning',
    uploading: 'info',
    failed: 'danger',
  }
  return map[status] || 'info'
}

function statusText(status) {
  const map = {
    completed: '已完成',
    parsing: '解析中',
    embedding: 'Embedding中',
    uploading: '上传中',
    failed: '失败',
  }
  return map[status] || status
}

function removeUploadItem(uid) {
  uploadingFiles.value = uploadingFiles.value.filter(f => f.uid !== uid)
}

function removePendingFile(uid) {
  pendingFiles.value = pendingFiles.value.filter(f => f.uid !== uid)
}

// 选择文件后先放入待传队列，由“开始上传”统一提交
function handleFileChange(file) {
  if (pendingFiles.value.some(f => f.uid === file.uid)) {
    return
  }
  pendingFiles.value.push({
    uid: file.uid,
    name: file.name,
    raw: file.raw,
  })
}

async function startBatchUpload() {
  if (!pendingFiles.value.length) {
    return
  }

  const batch = pendingFiles.value.slice()
  pendingFiles.value = []

  // 待传文件对应的进度展示项
  const uploadItems = batch.map(file => ({
    uid: file.uid,
    name: file.name,
    percentage: 0,
    status: '',
  }))
  uploadItems.forEach(item => uploadingFiles.value.push(item))

  // 上传提交前展示乐观进度，最终状态以后端轮询结果为准
  const timers = uploadItems.map(item => setInterval(() => {
    item.percentage = Math.min(item.percentage + 20, 90)
  }, 300))

  try {
    // 一个请求携带本次所有文件，后端会按顺序返回每个文件对应的 task_id
    const res = await uploadFiles(batch.map(file => file.raw))
    if (res.code === 200) {
      uploadItems.forEach(item => {
        item.percentage = 100
        item.status = 'success'
      })
      // 记录会由 knowledge.js 按各自 task_id 轮询更新状态
      fileList.value.unshift(...res.data)
      ElMessage.success(`已提交 ${res.data.length} 个文件，开始解析入库`)
    } else {
      throw new Error(res.message || '上传失败')
    }
  } catch (e) {
    uploadItems.forEach(item => { item.status = 'exception' })
    // 上传失败时放回待传队列，方便重试
    pendingFiles.value.push(...batch)
    ElMessage.error(`批量上传失败：${e.message || '请稍后重试'}`)
  } finally {
    timers.forEach(timer => clearInterval(timer))
    // 上传结束 2 秒后移除进度条条目
    setTimeout(() => {
      uploadItems.forEach(item => removeUploadItem(item.uid))
    }, 2000)
  }
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除文件 "${row.name}" 吗？`, '确认删除', { type: 'warning' }).then(async () => {
    try {
      await deleteFile(row.id)
      fileList.value = fileList.value.filter((f) => f.id !== row.id)
      ElMessage.success('删除成功')
    } catch (e) {
      ElMessage.error('删除失败')
    }
  }).catch(() => {})
}
</script>

<style scoped>
.upload-area {
  width: 100%;
}

.upload-text {
  margin-top: 12px;
  font-size: 14px;
  color: var(--text-regular);
}

.upload-text em {
  color: var(--color-primary);
  font-style: normal;
}

.upload-hint {
  font-size: 12px;
  color: var(--text-placeholder);
  margin-top: 4px;
}

.uploading-list {
  margin-top: 16px;
}

.uploading-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}

.uploading-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: var(--text-regular);
}

.batch-actions {
  margin-top: 12px;
}
</style>
