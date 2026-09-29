<template>
  <div class="application-page">
    <PageHeader
      title="业务申请书生成"
      description="填写申请要素，助手按标准格式生成申请书文本并支持下载"
    />

    <el-row :gutter="20">
      <!-- 表单 -->
      <el-col :xs="24" :lg="9">
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
              <el-input
                v-model="form.companyName"
                placeholder="请输入企业全称"
                maxlength="60"
                show-word-limit
              />
            </el-form-item>

            <el-form-item label="业务类型" prop="businessType">
              <el-select
                v-model="form.businessType"
                placeholder="请选择业务类型"
                style="width: 100%"
              >
                <el-option
                  v-for="type in businessTypes"
                  :key="type"
                  :label="type"
                  :value="type"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="其他说明" prop="notes">
              <el-input
                v-model="form.notes"
                type="textarea"
                :rows="4"
                maxlength="300"
                show-word-limit
                placeholder="补充说明申请背景、金额、期限等信息（选填）"
              />
            </el-form-item>

            <el-button
              type="primary"
              size="large"
              class="submit-btn"
              :loading="generating"
              @click="handleGenerate"
            >
              {{ generating ? '生成中...' : '生成申请书' }}
            </el-button>
          </el-form>
        </div>
      </el-col>

      <!-- 结果 -->
      <el-col :xs="24" :lg="15">
        <div class="content-card result-card">
          <div class="result-head">
            <h3 class="section-title">生成结果</h3>
            <el-tag v-if="result" type="success" size="small" effect="plain">
              已生成
            </el-tag>
          </div>

          <div v-if="!result" class="result-empty">
            <span class="empty-icon"><el-icon :size="26"><DocumentAdd /></el-icon></span>
            <p class="empty-title">尚未生成申请书</p>
            <p class="empty-desc">在左侧填写企业名称与业务类型后，点击「生成申请书」</p>
          </div>

          <div v-else class="result-content">
            <div class="paper">
              <h4 class="paper-title">业务申请书</h4>

              <dl class="paper-fields">
                <div class="paper-field">
                  <dt>申请企业</dt>
                  <dd>{{ form.companyName || '—' }}</dd>
                </div>
                <div class="paper-field">
                  <dt>业务类型</dt>
                  <dd>{{ form.businessType || '—' }}</dd>
                </div>
                <div class="paper-field">
                  <dt>申请日期</dt>
                  <dd>{{ currentDate }}</dd>
                </div>
                <div v-if="form.notes" class="paper-field">
                  <dt>补充说明</dt>
                  <dd>{{ form.notes }}</dd>
                </div>
              </dl>

              <div class="paper-body">
                <p>尊敬的审批部门：</p>
                <p>
                  兹有我司向贵单位申请办理上述业务，特此提交申请书。我司承诺所提供材料真实有效，
                  并将严格遵守相关规定。
                </p>
                <p>恳请审批。</p>
              </div>

              <div class="paper-sign">
                <span>申请单位：{{ form.companyName || '—' }}</span>
                <span>{{ currentDate }}</span>
              </div>

              <footer class="paper-file">
                <el-icon :size="14"><Document /></el-icon>
                {{ result.filename }}
              </footer>
            </div>

            <div class="result-actions">
              <el-button type="primary" :icon="Download" @click="handleDownload">
                下载申请书
              </el-button>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { Download } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { generateApplication } from '@/api/ai'

const formRef = ref(null)
const generating = ref(false)
const result = ref(null)

const businessTypes = ['融资申请', '立项申请', '合作申请', '授信申请', '投资申请']

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
    ElMessage.error(e?.message || '生成失败，请重试')
  } finally {
    generating.value = false
  }
}

function handleDownload() {
  ElMessage.warning('下载功能待接入后端')
}
</script>

<style scoped>
.submit-btn {
  width: 100%;
  font-weight: 600;
}

.result-card {
  min-height: 100%;
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

/* 空状态 */
.result-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 340px;
  padding: 40px 20px;
  text-align: center;
}

.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin-bottom: 6px;
  color: var(--brand-500);
  background: var(--brand-50);
  border-radius: var(--radius-lg);
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.empty-desc {
  max-width: 320px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-secondary);
}

/* 文档预览 */
.paper {
  padding: 32px 34px;
  background: #fff;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}

.paper-title {
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.32em;
  text-align: center;
  color: var(--text-primary);
}

.paper-title::after {
  content: '';
  display: block;
  width: 100%;
  height: 1px;
  margin-top: 18px;
  background: var(--color-border);
}

.paper-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 22px;
}

.paper-field {
  display: flex;
  gap: 12px;
  font-size: 14px;
  line-height: 1.8;
}

.paper-field dt {
  flex-shrink: 0;
  width: 76px;
  font-weight: 600;
  color: var(--text-regular);
}

.paper-field dd {
  color: var(--text-primary);
  word-break: break-word;
}

.paper-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 24px;
  font-size: 14px;
  line-height: 2;
  color: var(--text-regular);
  text-align: justify;
}

.paper-body p {
  text-indent: 2em;
}

.paper-sign {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 30px;
  font-size: 14px;
  color: var(--text-regular);
  text-align: right;
}

.paper-file {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 26px;
  padding-top: 14px;
  font-size: 12px;
  color: var(--text-placeholder);
  border-top: 1px dashed var(--color-border-light);
}

.result-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-4);
}
</style>
