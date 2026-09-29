<template>
  <div class="news-page">
    <PageHeader
      title="经营晨报新闻中心"
      description="每日经营资讯汇总，支持按标题或来源检索"
    >
      <template #extra>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <!-- 概览 -->
    <div class="overview">
      <div v-for="stat in stats" :key="stat.label" class="overview-item">
        <span class="overview-icon" :style="{ '--tone': stat.tone }">
          <el-icon :size="18"><component :is="stat.icon" /></el-icon>
        </span>
        <span class="overview-body">
          <span class="overview-value">{{ stat.value }}</span>
          <span class="overview-label">{{ stat.label }}</span>
        </span>
      </div>
    </div>

    <!-- 新闻列表 -->
    <div class="content-card">
      <div class="card-head">
        <h3 class="section-title">新闻列表</h3>
        <el-input
          v-model="searchKeyword"
          placeholder="搜索标题或来源"
          :prefix-icon="Search"
          clearable
          class="search-input"
        />
      </div>

      <el-table :data="filteredNews" v-loading="loading">
        <el-table-column prop="title" label="标题" min-width="280">
          <template #default="{ row }">
            <button type="button" class="news-title" @click="handleDetail(row)">
              {{ row.title }}
            </button>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="140">
          <template #default="{ row }">
            <el-tag type="info" size="small" effect="plain">{{ row.source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="time" label="时间" width="180" />
        <el-table-column prop="summary" label="摘要" min-width="280" show-overflow-tooltip />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="handleDetail(row)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-block">
            <el-icon :size="28"><Notebook /></el-icon>
            <p class="empty-title">暂无新闻数据</p>
            <p class="empty-desc">{{ searchKeyword ? '换个关键词试试' : '后端新闻接口接入后将在此展示' }}</p>
          </div>
        </template>
      </el-table>
    </div>

    <!-- 详情 -->
    <el-dialog v-model="dialogVisible" :title="currentNews?.title" width="720px">
      <div v-if="currentNews" class="news-detail">
        <div class="detail-meta">
          <el-tag type="info" size="small" effect="plain">{{ currentNews.source }}</el-tag>
          <span class="detail-time">{{ currentNews.time }}</span>
        </div>
        <p class="detail-summary">{{ currentNews.summary }}</p>
        <p class="detail-note">新闻详情后端接口待接入。</p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { getTodayNewsCount, getNewsList } from '@/api/news'

const todayCount = ref(0)
const newsList = ref([])
const loading = ref(false)
const searchKeyword = ref('')
const dialogVisible = ref(false)
const currentNews = ref(null)

const sourceCount = computed(
  () => new Set(newsList.value.map((n) => n.source).filter(Boolean)).size
)

const stats = computed(() => [
  { label: '今日新闻', value: todayCount.value, icon: 'Sunny', tone: '#b45309' },
  { label: '列表总数', value: newsList.value.length, icon: 'Files', tone: '#2447d8' },
  { label: '信息来源', value: sourceCount.value, icon: 'Connection', tone: '#0f766e' },
])

const filteredNews = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  if (!keyword) return newsList.value
  return newsList.value.filter(
    (n) =>
      (n.title || '').toLowerCase().includes(keyword) ||
      (n.source || '').toLowerCase().includes(keyword)
  )
})

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [countRes, listRes] = await Promise.all([getTodayNewsCount(), getNewsList()])
    if (countRes.code === 200) todayCount.value = countRes.data.count
    if (listRes.code === 200) newsList.value = listRes.data
  } finally {
    loading.value = false
  }
}

function handleDetail(row) {
  currentNews.value = row
  dialogVisible.value = true
}
</script>

<style scoped>
/* 概览指标 */
.overview {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

.overview-item {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: 18px 20px;
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
}

.overview-icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  color: var(--tone, var(--color-primary));
  background: color-mix(in srgb, var(--tone, #2447d8) 10%, #fff);
  border-radius: var(--radius-md);
}

.overview-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.overview-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
}

.overview-label {
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* 列表 */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.search-input {
  width: 260px;
}

.news-title {
  padding: 0;
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-standard);
}

.news-title:hover {
  color: var(--color-primary);
}

/* 空状态 */
.empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 46px 0;
  color: var(--text-placeholder);
}

.empty-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-regular);
}

.empty-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
}

/* 详情 */
.detail-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: var(--space-4);
}

.detail-time {
  font-size: 13px;
  color: var(--text-secondary);
}

.detail-summary {
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.85;
  color: var(--text-regular);
  background: var(--color-bg-sunken);
  border-left: 3px solid var(--brand-300);
  border-radius: var(--radius-sm);
}

.detail-note {
  margin-top: 14px;
  font-size: 13px;
  color: var(--text-secondary);
}

@media (max-width: 720px) {
  .card-head {
    flex-direction: column;
    align-items: stretch;
  }

  .search-input {
    width: 100%;
  }
}
</style>
