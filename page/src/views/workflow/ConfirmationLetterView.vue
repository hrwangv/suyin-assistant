<template>
  <div class="confirmation-page">
    <PageHeader
      title="询证函处理"
      description="上传询证函扫描件，自动识别关键字段并提交审核"
    />

    <el-row :gutter="20">
      <!-- 第一步：上传 -->
      <el-col :xs="24" :lg="8">
        <div class="content-card step-card">
          <div class="step-head">
            <span class="step-badge">01</span>
            <div class="step-head__text">
              <h3 class="section-title">上传扫描文件</h3>
              <p class="step-hint">支持 PDF 与常见图片格式，单个文件</p>
            </div>
          </div>

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
            <el-icon :size="34" class="upload-icon"><UploadFilled /></el-icon>
            <p class="upload-title">将询证函扫描件拖到此处</p>
            <p class="upload-sub">或点击选择文件 · PDF / PNG / JPG</p>
          </el-upload>

          <div v-if="processing" class="processing">
            <el-icon class="is-loading" :size="16"><Loading /></el-icon>
            <span>正在识别中，请稍候…</span>
          </div>
        </div>
      </el-col>

      <!-- 第二步：识别结果 -->
      <el-col :xs="24" :lg="8">
        <div class="content-card step-card">
          <div class="step-head">
            <span class="step-badge">02</span>
            <div class="step-head__text">
              <h3 class="section-title">识别结果</h3>
              <p class="step-hint">请核对关键字段是否准确</p>
            </div>
          </div>

          <div v-if="!ocrResult" class="step-empty">
            <span class="empty-icon"><el-icon :size="24"><Document /></el-icon></span>
            <p class="empty-title">等待识别结果</p>
            <p class="empty-desc">上传扫描件后，识别到的企业名称、余额等信息将展示在这里</p>
          </div>

          <ul v-else class="field-list">
            <li v-for="field in ocrFields" :key="field.label" class="field-item">
              <span class="field-label">{{ field.label }}</span>
              <span class="field-value" :class="{ 'is-strong': field.strong }">
                {{ field.value || '—' }}
              </span>
            </li>
          </ul>
        </div>
      </el-col>

      <!-- 第三步：进度 -->
      <el-col :xs="24" :lg="8">
        <div class="content-card step-card">
          <div class="step-head">
            <span class="step-badge">03</span>
            <div class="step-head__text">
              <h3 class="section-title">处理进度</h3>
              <p class="step-hint">核对完成后提交审核</p>
            </div>
          </div>

          <div v-if="!steps.length" class="step-empty">
            <span class="empty-icon"><el-icon :size="24"><Tickets /></el-icon></span>
            <p class="empty-title">等待上传</p>
            <p class="empty-desc">流程节点会在文件识别完成后自动同步</p>
          </div>

          <el-steps v-else :active="activeStep" direction="vertical" finish-status="success">
            <el-step
              v-for="(step, index) in steps"
              :key="index"
              :title="step.title"
              :status="stepStatus(step)"
            />
          </el-steps>

          <el-button
            v-if="ocrResult"
            type="primary"
            size="large"
            class="submit-btn"
            :loading="submitting"
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
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/PageHeader.vue'
import { uploadConfirmationLetter, submitForReview } from '@/api/workflow'

const processing = ref(false)
const submitting = ref(false)
const ocrResult = ref(null)
const steps = ref([])
const activeStep = ref(0)

const ocrFields = computed(() => {
  const data = ocrResult.value || {}
  return [
    { label: '企业名称', value: data.companyName },
    { label: '回函地址', value: data.replyAddress },
    { label: '账户余额', value: data.balance, strong: true },
    { label: '联系人', value: data.contactPerson },
    { label: '联系电话', value: data.phone },
  ]
})

function stepStatus(step) {
  if (step.status === 'completed') return 'success'
  if (step.status === 'in_progress') return 'process'
  return 'wait'
}

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
    ElMessage.error(e?.message || '识别失败，请重试')
  } finally {
    processing.value = false
  }
}

async function handleSubmit() {
  submitting.value = true
  try {
    await submitForReview()
    ElMessage.success('已提交审核')
    const reviewStep = steps.value.find((s) => s.title === '审批')
    const verifyStep = steps.value.find((s) => s.title === '数据核验')
    if (verifyStep) verifyStep.status = 'completed'
    if (reviewStep) {
      reviewStep.status = 'in_progress'
      activeStep.value = steps.value.findIndex((s) => s.status === 'in_progress')
    }
  } catch (e) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.step-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 380px;
}

.step-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: var(--space-5);
}

.step-badge {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--brand-700);
  background: var(--brand-50);
  border: 1px solid var(--brand-100);
  border-radius: var(--radius-sm);
}

.step-head__text {
  min-width: 0;
}

.step-hint {
  margin-top: 3px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* 上传 */
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

.upload-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-placeholder);
}

.processing {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: var(--space-4);
  padding: 12px;
  font-size: 13px;
  color: var(--color-primary);
  background: var(--brand-50);
  border-radius: var(--radius-sm);
}

/* 空状态 */
.step-empty {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 16px;
  text-align: center;
}

.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  margin-bottom: 4px;
  color: var(--text-placeholder);
  background: var(--color-bg-sunken);
  border-radius: var(--radius-lg);
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-regular);
}

.empty-desc {
  max-width: 260px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-secondary);
}

/* 字段列表 */
.field-list {
  display: flex;
  flex-direction: column;
  list-style: none;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.field-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 13px 16px;
}

.field-item + .field-item {
  border-top: 1px solid var(--color-border-light);
}

.field-item:nth-child(odd) {
  background: var(--color-bg-sunken);
}

.field-label {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.field-value {
  font-size: 13.5px;
  color: var(--text-primary);
  text-align: right;
  word-break: break-word;
}

.field-value.is-strong {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--color-success);
}

.submit-btn {
  width: 100%;
  margin-top: auto;
  padding-top: 0;
  font-weight: 600;
}

.step-card :deep(.el-steps) {
  height: auto;
}
</style>
