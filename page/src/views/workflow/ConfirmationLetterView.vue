<template>
  <div class="confirmation-page">
    <div class="page-header">
      <h2 class="page-title">询证函处理</h2>
    </div>

    <el-row :gutter="24">
      <!-- Upload -->
      <el-col :span="8">
        <div class="content-card">
          <h3 class="section-title">第一步：上传扫描文件</h3>
          <el-upload
            class="upload-area"
            drag
            action="#"
            :auto-upload="false"
            :on-change="handleFileChange"
            :show-file-list="true"
            :limit="1"
            accept=".pdf,.png,.jpg,.jpeg"
          >
            <el-icon :size="48" color="#c0c4cc"><UploadFilled /></el-icon>
            <div class="upload-text">
              <p>将询证函扫描件拖到此处</p>
              <p class="upload-hint">支持 PDF、图片格式</p>
            </div>
          </el-upload>
          <div v-if="processing" class="processing-status">
            <el-icon class="is-loading" :size="20"><Loading /></el-icon>
            <span>正在识别中...</span>
          </div>
        </div>
      </el-col>

      <!-- OCR Results -->
      <el-col :span="8">
        <div class="content-card">
          <h3 class="section-title">识别结果</h3>
          <div v-if="!ocrResult" class="result-empty">
            <el-empty description="请先上传扫描文件" :image-size="80" />
          </div>
          <el-descriptions v-else :column="1" border size="large">
            <el-descriptions-item label="企业名称" label-class-name="desc-label">
              {{ ocrResult.companyName }}
            </el-descriptions-item>
            <el-descriptions-item label="回函地址" label-class-name="desc-label">
              {{ ocrResult.replyAddress }}
            </el-descriptions-item>
            <el-descriptions-item label="余额" label-class-name="desc-label">
              <span style="color: #67c23a; font-weight: 600; font-size: 16px">
                {{ ocrResult.balance }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="联系人" label-class-name="desc-label">
              {{ ocrResult.contactPerson }}
            </el-descriptions-item>
            <el-descriptions-item label="联系电话" label-class-name="desc-label">
              {{ ocrResult.phone }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </el-col>

      <!-- Steps & Submit -->
      <el-col :span="8">
        <div class="content-card">
          <h3 class="section-title">处理进度</h3>
          <el-steps v-if="steps.length" :active="activeStep" direction="vertical" finish-status="success">
            <el-step
              v-for="(step, index) in steps"
              :key="index"
              :title="step.title"
              :status="step.status === 'completed' ? 'success' : step.status === 'in_progress' ? 'process' : 'wait'"
            />
          </el-steps>
          <el-empty v-else description="等待上传" :image-size="60" />
        </div>

        <div v-if="ocrResult" class="submit-section">
          <el-button
            type="primary"
            size="large"
            style="width: 100%"
            @click="handleSubmit"
          >
            提交审核
          </el-button>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { uploadConfirmationLetter, submitForReview } from '@/api/workflow'

const processing = ref(false)
const ocrResult = ref(null)
const steps = ref([])

const activeStep = ref(0)

async function handleFileChange(file) {
  processing.value = true
  ocrResult.value = null
  steps.value = []

  try {
    const res = await uploadConfirmationLetter(file.raw)
    if (res.code === 200) {
      ocrResult.value = res.data.ocrResult
      steps.value = res.data.steps
      activeStep.value = steps.value.findIndex((s) => s.status === 'in_progress')
      ElMessage.success('文件识别完成')
    }
  } catch (e) {
    ElMessage.error('识别失败，请重试')
  } finally {
    processing.value = false
  }
}

async function handleSubmit() {
  try {
    await submitForReview('mock-task-id')
    ElMessage.success('已提交审核')
    // Mark next step as in_progress
    const reviewStep = steps.value.find((s) => s.title === '审批')
    if (reviewStep) {
      reviewStep.status = 'in_progress'
      // Update data review step
      const verifyStep = steps.value.find((s) => s.title === '数据核验')
      if (verifyStep) verifyStep.status = 'completed'
      activeStep.value = steps.value.findIndex((s) => s.status === 'in_progress')
    }
  } catch (e) {
    ElMessage.error('提交失败')
  }
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

.upload-hint {
  font-size: 12px;
  color: var(--text-placeholder);
  margin-top: 4px;
}

.processing-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  color: var(--color-primary);
  font-size: 14px;
}

.result-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.desc-label {
  font-weight: 600;
}

.submit-section {
  margin-top: 16px;
}
</style>
