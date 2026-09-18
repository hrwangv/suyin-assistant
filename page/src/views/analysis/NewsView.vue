<template>
  <div class="news-page">
    <div class="page-header">
      <h2 class="page-title">经营晨报新闻中心</h2>
    </div>

    <!-- Stats -->
    <div class="stats-bar">
      <div class="stat-item">
        <div class="stat-num">{{ todayCount }}</div>
        <div class="stat-txt">今日新闻数量</div>
      </div>
    </div>

    <!-- News List -->
    <div class="content-card">
      <div class="table-header flex-between">
        <h3 class="section-title">新闻列表</h3>
        <el-input
          v-model="searchKeyword"
          placeholder="搜索新闻标题"
          :prefix-icon="Search"
          style="width: 260px"
          clearable
        />
      </div>

      <el-table :data="filteredNews" stripe v-loading="loading">
        <el-table-column prop="title" label="标题" min-width="280">
          <template #default="{ row }">
            <span class="news-title">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="120">
          <template #default="{ row }">
            <el-tag type="info" size="small">{{ row.source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="time" label="时间" width="180" />
        <el-table-column prop="summary" label="摘要" min-width="300" show-overflow-tooltip />
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleDetail(row)">
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Detail Dialog -->
    <el-dialog v-model="dialogVisible" :title="currentNews?.title" width="700px">
      <div v-if="currentNews" class="news-detail">
        <div class="detail-meta">
          <el-tag type="info" size="small">{{ currentNews.source }}</el-tag>
          <span class="detail-time">{{ currentNews.time }}</span>
        </div>
        <div class="detail-summary">{{ currentNews.summary }}</div>
        <div class="detail-body">
          <p>{{ currentNews.summary }}</p>
          <p style="margin-top: 12px">新闻详情后端接口待接入。</p>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getTodayNewsCount, getNewsList } from '@/api/news'

const todayCount = ref(0)
const newsList = ref([])
const loading = ref(false)
const searchKeyword = ref('')
const dialogVisible = ref(false)
const currentNews = ref(null)

const filteredNews = computed(() => {
  if (!searchKeyword.value) return newsList.value
  const keyword = searchKeyword.value.toLowerCase()
  return newsList.value.filter(
    (n) => n.title.toLowerCase().includes(keyword) || n.source.toLowerCase().includes(keyword)
  )
})

onMounted(async () => {
  loading.value = true
  const [countRes, listRes] = await Promise.all([getTodayNewsCount(), getNewsList()])
  if (countRes.code === 200) todayCount.value = countRes.data.count
  if (listRes.code === 200) newsList.value = listRes.data
  loading.value = false
})

function handleDetail(row) {
  currentNews.value = row
  dialogVisible.value = true
}
</script>

<style scoped>
.stats-bar {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.stat-item {
  background: var(--card-bg);
  border-radius: var(--card-radius);
  box-shadow: var(--card-shadow);
  padding: 20px 32px;
  text-align: center;
  min-width: 160px;
}

.stat-num {
  font-size: 36px;
  font-weight: 700;
  color: var(--color-primary);
}

.stat-txt {
  font-size: 14px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.table-header {
  margin-bottom: 16px;
}

.news-title {
  color: var(--text-primary);
  font-weight: 500;
  cursor: pointer;
}

.news-title:hover {
  color: var(--color-primary);
}

.news-detail .detail-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.detail-time {
  font-size: 13px;
  color: var(--text-secondary);
}

.detail-summary {
  font-size: 15px;
  color: var(--text-primary);
  line-height: 1.8;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
  margin-bottom: 16px;
}

.detail-body {
  font-size: 14px;
  color: var(--text-regular);
  line-height: 1.8;
}
</style>
