<template>
  <div class="dashboard-page">
    <!-- 欢迎区 -->
    <section class="hero">
      <div class="hero-grid" aria-hidden="true" />
      <div class="hero-body">
        <p class="hero-eyebrow">{{ currentDate }}</p>
        <h1 class="hero-title">{{ greeting }}，{{ authStore.userInfo?.name || '用户' }}</h1>
        <p class="hero-desc">
          苏银助手已就绪。你可以直接提问检索企业知识库，也可以上传文档做识别与申请书生成。
        </p>
        <div class="hero-actions">
          <el-button type="primary" size="large" :icon="ChatDotRound" @click="go('/ai/chat')">
            开始提问
          </el-button>
        </div>
      </div>

      <div class="hero-meta">
        <div class="meta-item">
          <span class="meta-label">当前账号</span>
          <span class="meta-value">{{ authStore.userInfo?.username || '-' }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">角色权限</span>
          <span class="meta-value">{{ authStore.isAdmin ? '管理员' : '普通用户' }}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">可访问功能</span>
          <span class="meta-value">{{ accessibleFeatures.length }} 项</span>
        </div>
      </div>
    </section>

    <!-- 使用指引 + 平台能力 -->
    <el-row :gutter="20">
      <el-col :xs="24" :lg="14">
        <div class="content-card usage-card">
          <div class="block-head">
            <h2 class="block-title">使用指引</h2>
          </div>
          <ol class="usage-steps">
            <li v-for="(step, index) in usageSteps" :key="step.title">
              <span class="step-index">{{ index + 1 }}</span>
              <span class="step-text">
                <strong>{{ step.title }}</strong>
                <em>{{ step.desc }}</em>
              </span>
            </li>
          </ol>
        </div>
      </el-col>

      <el-col :xs="24" :lg="10">
        <div class="content-card">
          <div class="block-head">
            <h2 class="block-title">平台能力</h2>
          </div>
          <ul class="capability-list">
            <li v-for="cap in capabilities" :key="cap.title">
              <el-icon :size="16" class="capability-icon">
                <component :is="cap.icon" />
              </el-icon>
              <span class="capability-text">
                <strong>{{ cap.title }}</strong>
                <em>{{ cap.desc }}</em>
              </span>
            </li>
          </ul>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ChatDotRound } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const currentDate = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
})

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '凌晨好'
  if (hour < 12) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

// 可访问的业务功能（与左侧导航一致），首页仅用于展示数量概览
const accessibleFeatures = computed(() => {
  const items = ['苏银AI问答助手', '业务申请书生成', '询证函处理', '经营晨报新闻中心']
  return authStore.isAdmin ? [...items, '企业知识库管理'] : items
})

const usageSteps = [
  { title: '上传企业资料', desc: '支持 PDF / Word / Excel / PPT / 图片，上传后自动解析入库' },
  { title: '提问或提出诉求', desc: '用自然语言描述问题，助手会先识别意图再选择处理链路' },
  { title: '核对引用与结论', desc: '回答附带来源片段，关键结论请与业务材料二次核对' },
  { title: '生成业务文档', desc: '申请书、询证函等文档生成后可直接下载使用' },
]

const capabilities = [
  { icon: 'Search', title: '混合检索', desc: '稠密 + 稀疏双路召回与精排，命中更准' },
  { icon: 'Files', title: '企业知识库', desc: '经营晨报、研报、立项材料统一管理' },
  { icon: 'MagicStick', title: '多模态理解', desc: '支持扫描件、图片与复杂版式文档' },
  { icon: 'Connection', title: '记忆式对话', desc: '跨对话保留上下文，支持持续追问' },
]

function go(path) {
  router.push(path)
}
</script>

<style scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

/* ---------------- 欢迎区 ---------------- */
.hero {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-8);
  padding: 34px 36px;
  overflow: hidden;
  color: #fff;
  border-radius: var(--radius-xl);
  background: linear-gradient(120deg, #1b3563 0%, #17294f 42%, #0f1f3f 100%);
  box-shadow: var(--shadow-md);
}

.hero-grid {
  position: absolute;
  inset: 0;
  background-image: linear-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(90% 120% at 85% 0%, #000 10%, transparent 72%);
  -webkit-mask-image: radial-gradient(90% 120% at 85% 0%, #000 10%, transparent 72%);
}

.hero::after {
  content: '';
  position: absolute;
  top: -140px;
  right: -80px;
  width: 380px;
  height: 380px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(78, 123, 255, 0.4) 0%, transparent 70%);
}

.hero-body {
  position: relative;
  z-index: 1;
  max-width: 620px;
}

.hero-eyebrow {
  font-size: 13px;
  letter-spacing: 0.06em;
  color: rgba(190, 210, 245, 0.75);
}

.hero-title {
  margin-top: 10px;
  font-size: 30px;
  font-weight: 650;
  letter-spacing: -0.01em;
  color: #fff;
}

.hero-desc {
  margin-top: 12px;
  font-size: 14px;
  line-height: 1.8;
  color: rgba(205, 221, 247, 0.8);
}

.hero-actions {
  display: flex;
  gap: 12px;
  margin-top: 26px;
}

.hero-meta {
  position: relative;
  z-index: 1;
  display: flex;
  flex-shrink: 0;
  gap: 28px;
  padding: 18px 24px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.06);
  backdrop-filter: blur(4px);
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.meta-label {
  font-size: 12px;
  color: rgba(190, 210, 245, 0.7);
}

.meta-value {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: #fff;
}

/* ---------------- 通用块标题 ---------------- */
.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.block-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

/* ---------------- 使用指引 ---------------- */
.usage-card {
  height: 100%;
}

.usage-steps {
  display: flex;
  flex-direction: column;
  list-style: none;
}

.usage-steps li {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 12px 0;
}

.usage-steps li + li {
  border-top: 1px dashed var(--color-border-light);
}

.step-index {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-top: 2px;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--brand-700);
  background: var(--brand-50);
  border-radius: var(--radius-xs);
}

.step-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.step-text strong {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.step-text em {
  font-size: 12.5px;
  font-style: normal;
  line-height: 1.6;
  color: var(--text-secondary);
}

/* ---------------- 平台能力 ---------------- */
.capability-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  list-style: none;
}

.capability-list li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.capability-icon {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--color-primary);
}

.capability-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.capability-text strong {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary);
}

.capability-text em {
  font-size: 12.5px;
  font-style: normal;
  line-height: 1.6;
  color: var(--text-secondary);
}

/* ---------------- 响应式 ---------------- */
@media (max-width: 1080px) {
  .hero {
    flex-direction: column;
    align-items: stretch;
  }

  .hero-meta {
    justify-content: space-between;
  }
}

@media (max-width: 720px) {
  .hero {
    padding: 26px 22px;
  }

  .hero-title {
    font-size: 24px;
  }

  .hero-meta {
    flex-wrap: wrap;
    gap: 18px;
  }
}
</style>
