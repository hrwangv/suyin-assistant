<template>
  <div class="settings-page">
    <PageHeader title="系统配置" description="配置系统名称、上传限制与会话策略" />

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="settings-alert"
      title="系统配置后端接口尚未接入，当前保存仅在前端生效"
    />

    <div class="content-card">
      <h3 class="section-title">基础配置</h3>

      <el-form ref="formRef" :model="form" label-width="150px" class="settings-form">
        <el-form-item label="系统名称">
          <el-input v-model="form.systemName" placeholder="请输入系统名称" />
        </el-form-item>

        <el-form-item label="系统Logo">
          <el-upload
            class="logo-upload"
            action="#"
            :show-file-list="false"
            :auto-upload="false"
          >
            <div class="logo-placeholder">
              <el-icon :size="20"><Plus /></el-icon>
              <span>上传 Logo</span>
            </div>
          </el-upload>
        </el-form-item>

        <el-form-item label="最大上传大小(MB)">
          <el-input-number v-model="form.maxUploadSize" :min="1" :max="500" />
          <span class="field-tip">单次上传文件的体积上限</span>
        </el-form-item>

        <el-form-item label="会话超时(分钟)">
          <el-input-number v-model="form.sessionTimeout" :min="5" :max="1440" />
          <span class="field-tip">超过该时长无操作需重新登录</span>
        </el-form-item>

        <el-form-item label="允许注册">
          <el-switch v-model="form.enableRegistration" />
          <span class="field-tip">开启后允许用户自行注册账号</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="handleSave">保存配置</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { getSettings, updateSettings } from '@/api/system'

const formRef = ref(null)
const saving = ref(false)
const originalForm = ref(null)

const form = reactive({
  systemName: '',
  logo: '',
  maxUploadSize: 50,
  sessionTimeout: 30,
  enableRegistration: false,
})

onMounted(async () => {
  const res = await getSettings()
  if (res.code === 200) {
    Object.assign(form, res.data)
    originalForm.value = { ...res.data }
  }
})

async function handleSave() {
  saving.value = true
  try {
    await updateSettings({ ...form })
    originalForm.value = { ...form }
    ElMessage.success('配置已保存')
  } catch (e) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

function handleReset() {
  if (originalForm.value) {
    Object.assign(form, originalForm.value)
    ElMessage.info('已重置')
  }
}
</script>

<style scoped>
.settings-alert {
  margin-bottom: var(--space-5);
  border-radius: var(--radius-md);
}

.settings-form {
  max-width: 620px;
}

.logo-upload :deep(.el-upload) {
  border: 1px dashed var(--el-border-color);
  border-radius: var(--radius-md);
  transition: border-color var(--duration-fast) var(--ease-standard);
}

.logo-upload :deep(.el-upload:hover) {
  border-color: var(--brand-400);
}

.logo-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 96px;
  height: 96px;
  font-size: 12px;
  color: var(--text-placeholder);
}

.field-tip {
  margin-left: 12px;
  font-size: 12.5px;
  color: var(--text-placeholder);
}
</style>
