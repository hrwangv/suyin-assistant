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
        action="#"
        :auto-upload="false"
        :on-change="handleFileChange"
        :show-file-list="false"
        accept=".pdf,.docx,.doc,.md,.xlsx,.xls,.pptx,.ppt,.png,.jpg"
      >
        <el-icon :size="48" color="#c0c4cc"><UploadFilled /></el-icon>
        <div class="upload-text">
          <p>将文件拖到此处，或<em>点击上传</em></p>
          <p class="upload-hint">支持 PDF、Word、Markdown、Excel、PPT、图片 格式</p>
        </div>
      </el-upload>

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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getFileList, uploadFile, deleteFile } from '@/api/knowledge'

const fileList = ref([])
const uploadingFiles = ref([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  const res = await getFileList()
  if (res.code === 200) {
    fileList.value = res.data
  }
  loading.value = false
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

async function handleFileChange(file) {
  const uploadFileObj = {
    uid: file.uid,
    name: file.name,
    percentage: 0,
    status: '',
  }
  uploadingFiles.value.push(uploadFileObj)

  // Simulate upload progress
  const timer = setInterval(() => {
    uploadFileObj.percentage = Math.min(uploadFileObj.percentage + 20, 90)
  }, 300)

  try {
    const res = await uploadFile(file.raw)
    clearInterval(timer)
    uploadFileObj.percentage = 100
    uploadFileObj.status = 'success'

    if (res.code === 200) {
      fileList.value.unshift(res.data)
      ElMessage.success(`文件 ${file.name} 上传成功`)
    }
  } catch (e) {
    clearInterval(timer)
    uploadFileObj.status = 'exception'
    ElMessage.error(`文件 ${file.name} 上传失败`)
  } finally {
    // 上传结束 2 秒后移除进度条条目
    setTimeout(() => removeUploadItem(file.uid), 2000)
  }
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除文件 "${row.name}" 吗？`, '确认删除', { type: 'warning' }).then(async () => {
    await deleteFile(row.id)
    fileList.value = fileList.value.filter((f) => f.id !== row.id)
    ElMessage.success('删除成功')
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
</style>
