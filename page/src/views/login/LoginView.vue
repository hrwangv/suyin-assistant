<template>
  <div class="login-page">
    <!-- 品牌侧：产品定位与能力说明 -->
    <section class="login-brand">
      <div class="brand-grid" aria-hidden="true" />
      <div class="brand-glow" aria-hidden="true" />

      <div class="brand-inner">
        <div class="brand-head">
          <BrandMark :size="46" />
          <div class="brand-head__text">
            <span class="brand-head__name">苏银助手</span>
            <span class="brand-head__sub">SuYin Assistant</span>
          </div>
        </div>

        <p class="brand-title">企业知识库 · 智能业务助手</p>
        <p class="brand-desc">
          聚合经营晨报、行业研报、立项材料与产品手册，为业务人员提供可追溯的知识问答、
          文档识别与申请书生成能力。
        </p>

        <ul class="brand-features">
          <li v-for="feature in features" :key="feature.title">
            <span class="feature-icon">
              <el-icon :size="16"><component :is="feature.icon" /></el-icon>
            </span>
            <span class="feature-text">
              <strong>{{ feature.title }}</strong>
              <em>{{ feature.desc }}</em>
            </span>
          </li>
        </ul>
      </div>

      <footer class="brand-foot">内部系统 · 请勿外传 · v1.0.0</footer>
    </section>

    <!-- 表单侧 -->
    <section class="login-panel">
      <div class="login-card">
        <div class="card-head">
          <BrandMark class="card-head__mark" :size="40" />
          <h1 class="card-title">欢迎登录</h1>
          <p class="card-subtitle">请使用内部账号访问工作台</p>
        </div>

        <el-form
          ref="formRef"
          :model="loginForm"
          :rules="rules"
          label-position="top"
          size="large"
          class="login-form"
          @keyup.enter="handleLogin"
        >
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="loginForm.username"
              placeholder="请输入用户名"
              name="username"
              autocomplete="username"
              :prefix-icon="User"
            />
          </el-form-item>

          <el-form-item label="密码" prop="password">
            <el-input
              v-model="loginForm.password"
              type="password"
              placeholder="请输入密码"
              name="password"
              autocomplete="current-password"
              show-password
              :prefix-icon="Lock"
            />
          </el-form-item>

          <p v-if="errorMessage" class="login-error" role="alert">
            <el-icon :size="14"><WarningFilled /></el-icon>
            {{ errorMessage }}
          </p>

          <el-button
            type="primary"
            size="large"
            class="login-btn"
            :loading="loading"
            @click="handleLogin"
          >
            {{ loading ? '登录中...' : '登 录' }}
          </el-button>
        </el-form>

        <p class="login-help">忘记密码或需要开通账号，请联系系统管理员。</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import BrandMark from '@/components/BrandMark.vue'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref(null)
const loading = ref(false)
const errorMessage = ref('')

const features = [
  { icon: 'ChatDotRound', title: '知识问答', desc: '基于企业知识库的可溯源回答' },
  { icon: 'DocumentAdd', title: '文档智能', desc: '询证函识别与申请书自动生成' },
  { icon: 'Notebook', title: '经营晨报', desc: '每日资讯速览与要点提炼' },
]

const loginForm = reactive({
  username: '',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  errorMessage.value = ''
  loading.value = true
  try {
    await authStore.login(loginForm)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (error) {
    // userFacing 标记的错误来自本地鉴权模块，文案可直接展示；
    // 其他异常（网络 / 后端）统一给中性提示，避免暴露内部信息
    errorMessage.value = error?.userFacing
      ? error.message
      : '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: minmax(420px, 1.05fr) minmax(420px, 0.95fr);
  min-height: 100vh;
  background: var(--color-bg-app);
}

/* ---------------- 品牌侧 ---------------- */
.login-brand {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 56px 64px;
  overflow: hidden;
  color: #fff;
  background: linear-gradient(155deg, #17305c 0%, #10224a 45%, #0a1730 100%);
}

.brand-grid {
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(255, 255, 255, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.055) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(120% 100% at 20% 10%, #000 30%, transparent 75%);
  -webkit-mask-image: radial-gradient(120% 100% at 20% 10%, #000 30%, transparent 75%);
}

.brand-glow {
  position: absolute;
  top: -160px;
  right: -120px;
  width: 460px;
  height: 460px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(78, 123, 255, 0.42) 0%, transparent 68%);
  filter: blur(6px);
}

.brand-inner {
  position: relative;
  z-index: 1;
  max-width: 460px;
}

.brand-head {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand-head__text {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.brand-head__name {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.brand-head__sub {
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: rgba(200, 214, 242, 0.6);
}

.brand-title {
  margin-top: 48px;
  font-size: 34px;
  font-weight: 650;
  line-height: 1.35;
  letter-spacing: -0.01em;
  color: #fff;
}

.brand-desc {
  margin-top: 18px;
  font-size: 14px;
  line-height: 1.85;
  color: rgba(206, 219, 243, 0.78);
}

.brand-features {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 40px;
  list-style: none;
}

.brand-features li {
  display: flex;
  align-items: center;
  gap: 14px;
}

.feature-icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  color: #a9c2ff;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: var(--radius-sm);
}

.feature-text {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.feature-text strong {
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.94);
}

.feature-text em {
  font-size: 12px;
  font-style: normal;
  color: rgba(190, 206, 236, 0.62);
}

.brand-foot {
  position: relative;
  z-index: 1;
  font-size: 12px;
  letter-spacing: 0.08em;
  color: rgba(170, 189, 224, 0.5);
}

/* ---------------- 表单侧 ---------------- */
.login-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 40px;
}

.login-card {
  width: 100%;
  max-width: 400px;
}

.card-head__mark {
  display: none;
}

.card-title {
  font-size: 26px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

.card-subtitle {
  margin-top: 8px;
  font-size: 14px;
  color: var(--text-secondary);
}

.login-form {
  margin-top: 32px;
}

.login-btn {
  width: 100%;
  margin-top: 4px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.24em;
}

.login-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: -8px 0 14px;
  padding: 9px 12px;
  font-size: 13px;
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border: 1px solid #f6d0d0;
  border-radius: var(--radius-sm);
}

.login-help {
  margin-top: 24px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-secondary);
  text-align: center;
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 980px) {
  .login-page {
    grid-template-columns: 1fr;
  }

  .login-brand {
    display: none;
  }

  .card-head__mark {
    display: block;
    margin-bottom: 16px;
  }
}

@media (max-width: 520px) {
  .login-panel {
    padding: 32px 20px;
  }
}
</style>
