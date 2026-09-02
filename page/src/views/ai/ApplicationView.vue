<template>
  <div class="application-page">
    <div class="page-header">
      <h2 class="page-title">业务申请书生成</h2>
    </div>

    <el-row :gutter="24">
      <!-- Form -->
      <el-col :span="10">
        <div class="content-card">
          <h3 class="section-title">填写申请信息</h3>
          <el-form
            ref="formRef"
            :model="form"
            :rules="rules"
            label-position="top"
            size="large"
          >
            <el-form-item label="企业名称" prop="companyName">
              <el-input v-model="form.companyName" placeholder="请输入企业名称" />
            </el-form-item>

            <el-form-item label="业务类型" prop="businessType">
              <el-select v-model="form.businessType" placeholder="请选择业务类型" style="width: 100%">
                <el-option label="融资申请" value="融资申请" />
                <el-option label="立项申请" value="立项申请" />
                <el-option label="合作申请" value="合作申请" />
                <el-option label="授信申请" value="授信申请" />
                <el-option label="投资申请" value="投资申请" />
              </el-select>
            </el-form-item>

            <el-form-item label="其他说明" prop="notes">
              <el-input
                v-model="form.notes"
                type="textarea"
                :rows="4"
                placeholder="请输入其他补充说明（选填）"
              />
            </el-form-item>

            <el-form-item>
              <el-button
                type="primary"
                :loading="generating"
                @click="handleGenerate"
                style="width: 100%"
              >
                {{ generating ? '生成中...' : '生成申请书' }}
              </el-button>
            </el-form-item>
          </el-form>
        </div>
      </el-col>

      <!-- Result -->
      <el-col :span="14">
        <div class="content-card result-card">
          <h3 class="section-title">生成结果</h3>
          <div v-if="!result" class="result-empty">
            <el-empty description="填写信息后点击生成按钮" :image-size="100" />
          </div>
          <div v-else class="result-content">
            <div class="result-preview">
              <div class="preview-header">
                <el-icon :size="48" color="#409eff"><Document /></el-icon>
                <div class="preview-info">
                  <div class="preview-filename">{{ result.filename }}</div>
                  <div class="preview-meta">业务申请书 · {{ form.businessType }}</div>
                </div>
              </div>

              <div class="preview-body">
                <h4>业务申请书</h4>
                <div class="preview-field">
                  <span class="field-label">申请企业：</span>
                  <span>{{ form.companyName || '___________' }}</span>
                </div>
                <div class="preview-field">
                  <span class="field-label">业务类型：</span>
                  <span>{{ form.businessType || '___________' }}</span>
                </div>
                <div class="preview-field">
                  <span class="field-label">申请日期：</span>
                  <span>{{ currentDate }}</span>
                </div>
                <div v-if="form.notes" class="preview-field">
                  <span class="field-label">补充说明：</span>
                  <span>{{ form.notes }}</span>
                </div>
                <div class="preview-content">
                  <p>尊敬的审批部门：</p>
                  <p>兹有我司向贵单位申请办理上述业务，特此提交申请书。我司承诺所提供材料真实有效，并将严格遵守相关规定。</p>
                  <p>恳请审批。</p>
                </div>
              </div>
            </div>
            <el-button type="primary" :icon="Download" @click="handleDownload" style="margin-top: 16px">
              下载申请书
            </el-button>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { generateApplication } from '@/api/ai'

const formRef = ref(null)
const generating = ref(false)
const result = ref(null)

const currentDate = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
})

const form = reactive({
  companyName: '',
  businessType: '',
  notes: '',
})

const rules = {
  companyName: [{ required: true, message: '请输入企业名称', trigger: 'blur' }],
  businessType: [{ required: true, message: '请选择业务类型', trigger: 'change' }],
}

async function handleGenerate() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  generating.value = true
  try {
    const res = await generateApplication({ ...form })
    if (res.code === 200) {
      result.value = res.data
      ElMessage.success('申请书生成成功')
    }
  } catch (e) {
    ElMessage.error('生成失败，请重试')
  } finally {
    generating.value = false
  }
}

function handleDownload() {
  ElMessage.success('开始下载（模拟）')
}
</script>

<style scoped>
.result-card {
  min-height: 400px;
}

.result-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}

.result-preview {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
}

.preview-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
}

.preview-info {
  flex: 1;
}

.preview-filename {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.preview-meta {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.preview-body {
  padding: 24px;
}

.preview-body h4 {
  text-align: center;
  font-size: 18px;
  margin-bottom: 24px;
  letter-spacing: 4px;
}

.preview-field {
  margin-bottom: 12px;
  font-size: 14px;
  line-height: 1.8;
}

.field-label {
  font-weight: 600;
  color: var(--text-primary);
}

.preview-content {
  margin-top: 24px;
  line-height: 2;
  color: var(--text-regular);
  font-size: 14px;
}
</style>
