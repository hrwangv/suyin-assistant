<template>
  <div class="file-upload">
    <el-upload
      class="upload-component"
      drag
      :action="action"
      :accept="accept"
      :limit="limit"
      :auto-upload="autoUpload"
      :show-file-list="showFileList"
      :on-change="handleChange"
      :on-success="handleSuccess"
      :on-error="handleError"
      :before-upload="beforeUpload"
      v-bind="$attrs"
    >
      <el-icon :size="48" color="#c0c4cc"><UploadFilled /></el-icon>
      <div class="upload-text">
        <p>将文件拖到此处，或<em>点击上传</em></p>
        <p v-if="tip" class="upload-tip">{{ tip }}</p>
      </div>
    </el-upload>

    <div v-if="uploadingList.length" class="upload-progress">
      <div v-for="item in uploadingList" :key="item.uid" class="progress-item">
        <div class="progress-info">
          <el-icon><Document /></el-icon>
          <span class="progress-name">{{ item.name }}</span>
        </div>
        <el-progress
          :percentage="item.percentage"
          :status="item.status"
          style="width: 200px"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  action: { type: String, default: '#' },
  accept: { type: String, default: '' },
  limit: { type: Number, default: 1 },
  autoUpload: { type: Boolean, default: false },
  showFileList: { type: Boolean, default: true },
  tip: { type: String, default: '' },
})

const emit = defineEmits(['file-change', 'upload-success', 'upload-error'])

const uploadingList = ref([])

function handleChange(file, fileList) {
  emit('file-change', file, fileList)
}

function handleSuccess(response, file, fileList) {
  const item = uploadingList.value.find((f) => f.uid === file.uid)
  if (item) {
    item.percentage = 100
    item.status = 'success'
  }
  emit('upload-success', response, file, fileList)
}

function handleError(error, file, fileList) {
  const item = uploadingList.value.find((f) => f.uid === file.uid)
  if (item) {
    item.status = 'exception'
  }
  emit('upload-error', error, file, fileList)
}

function beforeUpload(file) {
  uploadingList.value.push({
    uid: file.uid,
    name: file.name,
    percentage: 0,
    status: '',
  })
  return true
}
</script>

<style scoped>
.upload-component {
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

.upload-tip {
  font-size: 12px;
  color: var(--text-placeholder);
  margin-top: 4px;
}

.upload-progress {
  margin-top: 16px;
}

.progress-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #f0f0f0;
}

.progress-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text-regular);
}

.progress-name {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
