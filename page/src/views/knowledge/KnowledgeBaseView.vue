<template>
  <div class="knowledge-page">
    <PageHeader
      title="企业知识库"
      description="上传企业资料并跟踪解析入库状态，资料入库后即可被问答检索"
    >
      <template #extra>
        <el-button :icon="Refresh" :loading="loading" @click="loadFileList">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 上传 -->
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
        <el-icon :size="34" class="upload-icon"><UploadFilled /></el-icon>
        <p class="upload-title">将文件拖到此处，或<em>点击选择</em></p>
        <p class="upload-sub">
          支持 PDF、Word、Markdown、Excel、PPT、图片，可一次选择多个文件
        </p>
      </el-upload>

      <!-- 待上传队列 -->
      <div v-if="pendingFiles.length" class="pending">
        <div class="pending-head">
          <span class="pending-title">待上传 {{ pendingFiles.length }} 个文件</span>
          <el-button type="primary" size="small" @click="startBatchUpload">
            开始上传
          </el-button>
        </div>
        <ul class="pending-list">
          <li v-for="file in pendingFiles" :key="file.uid" class="pending-item">
            <span class="file-name">
              <el-icon :size="14"><Document /></el-icon>
              {{ file.name }}
            </span>
            <el-button type="danger" link size="small" @click="removePendingFile(file.uid)">
              移除
            </el-button>
          </li>
        </ul>
      </div>

      <!-- 上传进度 -->
      <ul v-if="uploadingFiles.length" class="pending-list is-progress">
        <li v-for="file in uploadingFiles" :key="file.uid" class="pending-item">
          <span class="file-name">
            <el-icon :size="14"><Document /></el-icon>
            {{ file.name }}
          </span>
          <el-progress
            :percentage="file.percentage"
            :status="file.status"
            :stroke-width="6"
            class="file-progress"
          />
        </li>
      </ul>
    </div>

    <!-- 文件列表 -->
    <div class="content-card">
      <div class="list-head">
        <h3 class="section-title">文件列表</h3>
        <span class="list-count">共 {{ fileList.length }} 个文件</span>
      </div>

      <el-alert
        v-if="loadError"
        type="warning"
        :closable="false"
        show-icon
        class="load-alert"
        title="无法获取文件列表，请确认后端服务已启动后重试"
      />

      <el-table :data="fileList" v-loading="loading">
        <el-table-column prop="name" label="名称" min-width="240">
          <template #default="{ row }">
            <div class="file-cell">
              <el-icon :size="15" class="file-cell__icon"><Document /></el-icon>
              <span>{{ row.name }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="130">
          <template #default="{ row }">
            <StatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column prop="size" label="大小" width="110" />
        <el-table-column prop="uploadTime" label="上传时间" width="190" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" link @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-block">
            <el-icon :size="28"><FolderOpened /></el-icon>
            <p class="empty-title">知识库暂无文件</p>
            <p class="empty-desc">上传企业资料后，解析入库进度会在这里实时更新</p>
          </div>
        </template>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/StatusTag.vue'
import { getFileList, uploadFiles, deleteFile } from '@/api/knowledge'

const fileList = ref([])
const pendingFiles = ref([])
const uploadingFiles = ref([])
const loading = ref(false)
const loadError = ref(false)

async function loadFileList() {
  loading.value = true
  loadError.value = false
  try {
    const res = await getFileList()
    if (res.code === 200) {
      fileList.value = res.data
    }
  } catch (e) {
    // 后端未启动时 /files 会返回 5xx，这里降级为页面内提示而不是抛出未捕获异常
    loadError.value = true
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

function removeUploadItem(uid) {
  uploadingFiles.value = uploadingFiles.value.filter((f) => f.uid !== uid)
}

function removePendingFile(uid) {
  pendingFiles.value = pendingFiles.value.filter((f) => f.uid !== uid)
}

// 选择文件后先放入待传队列，由「开始上传」统一提交
function handleFileChange(file) {
  if (pendingFiles.value.some((f) => f.uid === file.uid)) {
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
  const uploadItems = batch.map((file) => ({
    uid: file.uid,
    name: file.name,
    percentage: 0,
    status: '',
  }))
  uploadItems.forEach((item) => uploadingFiles.value.push(item))

  // 上传提交前展示乐观进度，最终状态以后端轮询结果为准
  const timers = uploadItems.map((item) =>
    setInterval(() => {
      item.percentage = Math.min(item.percentage + 20, 90)
    }, 300)
  )

  try {
    // 一个请求携带本次所有文件，后端会按顺序返回每个文件对应的 task_id
    const res = await uploadFiles(batch.map((file) => file.raw))
    if (res.code === 200) {
      uploadItems.forEach((item) => {
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
    uploadItems.forEach((item) => {
      item.status = 'exception'
    })
    // 上传失败时放回待传队列，方便重试
    pendingFiles.value.push(...batch)
    ElMessage.error(`批量上传失败：${e.message || '请稍后重试'}`)
  } finally {
    timers.forEach((timer) => clearInterval(timer))
    // 上传结束 2 秒后移除进度条条目
    setTimeout(() => {
      uploadItems.forEach((item) => removeUploadItem(item.uid))
    }, 2000)
  }
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除文件 "${row.name}" 吗？`, '确认删除', { type: 'warning' })
    .then(async () => {
      try {
        await deleteFile(row.id)
        fileList.value = fileList.value.filter((f) => f.id !== row.id)
        ElMessage.success('删除成功')
      } catch (e) {
        ElMessage.error(e?.message || '删除失败')
      }
    })
    .catch(() => {})
}
</script>

<style scoped>
.upload-area {
  width: 100%;
}

.upload-icon {
  color: var(--brand-400);
}

.upload-title {
  margin-top: 10px;
  font-size: 14px;
  color: var(--text-regular);
}

.upload-title em {
  font-style: normal;
  color: var(--color-primary);
}

.upload-sub {
  margin-top: 5px;
  font-size: 12px;
  color: var(--text-placeholder);
}

/* 待上传 / 进度 */
.pending {
  margin-top: var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.pending-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--brand-50);
}

.pending-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--brand-800);
}

.pending-list {
  display: flex;
  flex-direction: column;
  list-style: none;
}

.pending-list.is-progress {
  margin-top: var(--space-4);
}

.pending-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: 10px 16px;
}

.pending-list:not(.is-progress) .pending-item + .pending-item,
.pending-list.is-progress .pending-item + .pending-item {
  border-top: 1px solid var(--color-border-light);
}

.file-name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 13.5px;
  color: var(--text-regular);
}

.file-name span,
.file-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.file-progress {
  flex-shrink: 0;
  width: 180px;
}

/* 列表 */
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

.load-alert {
  margin-bottom: var(--space-4);
  border-radius: var(--radius-md);
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.file-cell span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.file-cell__icon {
  flex-shrink: 0;
  color: var(--color-primary);
}

.empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 46px 0;
  color: var(--text-placeholder);
}

.empty-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-regular);
}

.empty-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
}
</style>
