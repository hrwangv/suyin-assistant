<template>
  <div class="settings-page">
    <div class="page-header">
      <h2 class="page-title">系统配置</h2>
    </div>

    <div class="content-card">
      <el-form
        ref="formRef"
        :model="form"
        label-width="140px"
        style="max-width: 600px"
      >
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
              <el-icon :size="32"><Plus /></el-icon>
              <span>上传Logo</span>
            </div>
          </el-upload>
        </el-form-item>

        <el-form-item label="最大上传大小(MB)">
          <el-input-number v-model="form.maxUploadSize" :min="1" :max="500" />
        </el-form-item>

        <el-form-item label="会话超时(分钟)">
          <el-input-number v-model="form.sessionTimeout" :min="5" :max="1440" />
        </el-form-item>

        <el-form-item label="允许注册">
          <el-switch v-model="form.enableRegistration" />
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
    ElMessage.error('保存失败')
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
.logo-upload {
  border: 1px dashed #dcdfe6;
  border-radius: 6px;
  cursor: pointer;
}

.logo-placeholder {
  width: 100px;
  height: 100px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  font-size: 12px;
  gap: 4px;
}
</style>
