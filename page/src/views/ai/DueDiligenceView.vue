<template>
  <div class="due-diligence-page">
    <div class="page-header">
      <h2 class="page-title">企业尽调助手</h2>
    </div>

    <div class="content-card">
      <!-- Search -->
      <div class="search-section">
        <div class="search-form">
          <span class="search-label">企业名称</span>
          <el-input
            v-model="companyName"
            placeholder="请输入企业名称"
            size="large"
            style="width: 400px"
            :disabled="analyzing"
          />
          <el-button
            type="primary"
            size="large"
            :loading="analyzing"
            @click="handleAnalyze"
            :icon="Search"
            :disabled="!companyName.trim()"
          >
            {{ analyzing ? '分析中...' : '开始分析' }}
          </el-button>
        </div>
      </div>

      <!-- Progress -->
      <div v-if="progress.length > 0" class="progress-section">
        <h3 class="section-title">分析进度</h3>
        <div class="progress-steps">
          <div
            v-for="(step, index) in progress"
            :key="index"
            :class="['progress-step', step.status]"
          >
            <div class="step-indicator">
              <el-icon v-if="step.status === 'completed'" :size="20"><CircleCheckFilled /></el-icon>
              <el-icon v-else-if="step.status === 'in_progress'" :size="20" class="is-loading"><Loading /></el-icon>
              <span v-else class="step-dot"></span>
            </div>
            <span class="step-name">{{ step.step }}</span>
          </div>
        </div>
      </div>

      <!-- Result -->
      <div v-if="reportUrl" class="report-section">
        <el-divider />
        <div class="report-result">
          <h3 class="section-title">生成报告</h3>
          <div class="report-card">
            <el-icon :size="48" color="#67c23a"><CircleCheckFilled /></el-icon>
            <div class="report-info">
              <div class="report-name">{{ companyName }} - 尽调报告.pdf</div>
              <div class="report-desc">工商信息、司法信息、新闻信息分析完成</div>
            </div>
            <el-button type="primary" :icon="Download" @click="handleDownload">
              下载PDF
            </el-button>
          </div>
        </div>
      </div>

      <!-- Detail -->
      <div v-if="progress.length > 0 && !analyzing" class="detail-section">
        <el-divider />
        <h3 class="section-title">分析详情</h3>

        <el-collapse>
          <el-collapse-item title="工商信息" name="1">
            <el-descriptions :column="2" border>
              <el-descriptions-item label="企业名称">{{ companyName }}</el-descriptions-item>
              <el-descriptions-item label="统一社会信用代码">91110000MA00XXXXX</el-descriptions-item>
              <el-descriptions-item label="法定代表人">某某某</el-descriptions-item>
              <el-descriptions-item label="注册资本">5000万元人民币</el-descriptions-item>
              <el-descriptions-item label="成立日期">2010-05-18</el-descriptions-item>
              <el-descriptions-item label="经营状态">存续</el-descriptions-item>
            </el-descriptions>
          </el-collapse-item>

          <el-collapse-item title="司法信息" name="2">
            <el-table :data="mockLegalData" stripe>
              <el-table-column prop="type" label="类型" width="100" />
              <el-table-column prop="title" label="案由" />
              <el-table-column prop="date" label="日期" width="120" />
              <el-table-column prop="result" label="结果" width="120" />
            </el-table>
          </el-collapse-item>

          <el-collapse-item title="新闻信息" name="3">
            <el-timeline>
              <el-timeline-item
                v-for="(news, index) in mockMediaData"
                :key="index"
                :timestamp="news.time"
                placement="top"
              >
                <el-card shadow="hover">
                  <h4>{{ news.title }}</h4>
                  <p>{{ news.summary }}</p>
                </el-card>
              </el-timeline-item>
            </el-timeline>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { startDueDiligence } from '@/api/ai'

const companyName = ref('')
const analyzing = ref(false)
const progress = ref([])
const reportUrl = ref('')

const mockLegalData = [
  { type: '民事案件', title: '合同纠纷', date: '2025-12-10', result: '已结案' },
  { type: '行政案件', title: '行政处罚', date: '2025-06-20', result: '已执行' },
]

const mockMediaData = [
  { title: '公司完成新一轮融资', summary: '据媒体报道，该公司近期完成了B轮融资，金额达数亿元...', time: '2026-07-20' },
  { title: '与行业龙头签署战略合作协议', summary: '双方将在技术研发和市场拓展方面展开深度合作...', time: '2026-06-15' },
  { title: '获得国家级高新技术企业认定', summary: '公司凭借技术创新能力获得高新技术企业资质...', time: '2026-05-10' },
]

async function handleAnalyze() {
  if (!companyName.value.trim() || analyzing.value) return

  analyzing.value = true
  progress.value = []
  reportUrl.value = ''

  try {
    const res = await startDueDiligence(companyName.value.trim())
    if (res.code === 200) {
      progress.value = res.data.progress
      reportUrl.value = res.data.reportUrl
      ElMessage.success('分析完成')
    }
  } catch (e) {
    ElMessage.error('分析失败，请重试')
  } finally {
    analyzing.value = false
  }
}

function handleDownload() {
  ElMessage.success('开始下载（模拟）')
}
</script>

<style scoped>
.search-section {
  margin-bottom: 24px;
}

.search-form {
  display: flex;
  align-items: center;
  gap: 12px;
}

.search-label {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
}

.progress-section {
  margin-bottom: 16px;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.progress-steps {
  display: flex;
  gap: 40px;
}

.progress-step {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-indicator {
  display: flex;
  align-items: center;
}

.progress-step.completed .step-indicator {
  color: #67c23a;
}

.progress-step.in_progress .step-indicator {
  color: var(--color-primary);
}

.step-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #dcdfe6;
}

.step-name {
  font-size: 14px;
  color: var(--text-regular);
}

.progress-step.completed .step-name {
  color: #67c23a;
}

.progress-step.in_progress .step-name {
  color: var(--color-primary);
}

.report-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 24px;
  background: #f0f9eb;
  border-radius: 8px;
  border: 1px solid #e1f3d8;
}

.report-info {
  flex: 1;
}

.report-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.report-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.detail-section {
  margin-top: 8px;
}
</style>
