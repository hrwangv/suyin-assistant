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
      <el-icon :size="34" class="upload-icon"><UploadFilled /></el-icon>
      <p class="upload-title">将文件拖到此处，或<em>点击上传</em></p>
      <p v-if="tip" class="upload-tip">{{ tip }}</p>
    </el-upload>

    <ul v-if="uploadingList.length" class="upload-progress">
      <li v-for="item in uploadingList" :key="item.uid" class="progress-item">
        <span class="progress-info">
          <el-icon :size="14"><Document /></el-icon>
          <span class="progress-name">{{ item.name }}</span>
        </span>
        <el-progress
          :percentage="item.percentage"
          :status="item.status"
          :stroke-width="6"
          class="progress-bar"
        />
      </li>
    </ul>
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

.upload-tip {
  margin-top: 5px;
  font-size: 12px;
  color: var(--text-placeholder);
}

.upload-progress {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: var(--space-4);
  list-style: none;
}

.progress-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: 10px 0;
}

.progress-item + .progress-item {
  border-top: 1px solid var(--color-border-light);
}

.progress-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 13.5px;
  color: var(--text-regular);
}

.progress-name {
  max-width: 320px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.progress-bar {
  flex-shrink: 0;
  width: 200px;
}
</style>
