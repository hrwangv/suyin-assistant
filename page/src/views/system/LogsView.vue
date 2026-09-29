<template>
  <div class="logs-page">
    <PageHeader title="系统日志" description="记录系统操作流水，可按用户与操作结果筛选">
      <template #extra>
        <el-button :icon="Refresh" :loading="loading" @click="fetchLogs">刷新</el-button>
      </template>
    </PageHeader>

    <div class="content-card">
      <div class="table-toolbar">
        <el-input
          v-model="searchUser"
          placeholder="搜索用户"
          :prefix-icon="Search"
          clearable
          class="filter-input"
        />
        <el-select
          v-model="filterResult"
          placeholder="操作结果"
          clearable
          class="filter-select"
        >
          <el-option label="成功" value="成功" />
          <el-option label="失败" value="失败" />
        </el-select>
        <span class="filter-count">筛选结果 {{ filteredLogs.length }} 条</span>
      </div>

      <el-table :data="filteredLogs" v-loading="loading">
        <el-table-column prop="user" label="用户" width="120" />
        <el-table-column prop="action" label="操作" min-width="220" />
        <el-table-column prop="time" label="时间" width="190" />
        <el-table-column prop="result" label="结果" width="130">
          <template #default="{ row }">
            <el-tag :type="row.result.includes('成功') ? 'success' : 'danger'" effect="plain">
              {{ row.result }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="IP地址" width="170" />
        <template #empty>
          <div class="empty-block">
            <el-icon :size="28"><Tickets /></el-icon>
            <p class="empty-title">暂无日志数据</p>
            <p class="empty-desc">后端日志接口接入后，操作记录将在此展示</p>
          </div>
        </template>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { getLogList } from '@/api/system'

const logList = ref([])
const loading = ref(false)
const searchUser = ref('')
const filterResult = ref('')

const filteredLogs = computed(() => {
  let list = logList.value
  if (searchUser.value) {
    list = list.filter((l) => (l.user || '').includes(searchUser.value))
  }
  if (filterResult.value) {
    list = list.filter((l) => (l.result || '').includes(filterResult.value))
  }
  return list
})

onMounted(fetchLogs)

async function fetchLogs() {
  loading.value = true
  const res = await getLogList()
  if (res.code === 200) logList.value = res.data
  loading.value = false
}
</script>

<style scoped>
.table-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.filter-input {
  width: 220px;
}

.filter-select {
  width: 140px;
}

.filter-count {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--text-secondary);
}

@media (max-width: 720px) {
  .table-toolbar {
    flex-wrap: wrap;
  }

  .filter-input,
  .filter-select {
    width: 100%;
  }

  .filter-count {
    margin-left: 0;
  }
}
</style>
