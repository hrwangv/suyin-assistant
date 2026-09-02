<template>
  <div class="logs-page">
    <div class="page-header">
      <h2 class="page-title">系统日志</h2>
    </div>

    <div class="content-card">
      <div class="table-header flex-between">
        <div class="filter-group">
          <el-input
            v-model="searchUser"
            placeholder="搜索用户"
            :prefix-icon="Search"
            style="width: 200px"
            clearable
          />
          <el-select v-model="filterResult" placeholder="操作结果" clearable style="width: 140px; margin-left: 12px">
            <el-option label="成功" value="成功" />
            <el-option label="失败" value="失败" />
          </el-select>
        </div>
        <el-button :icon="Refresh" @click="fetchLogs">刷新</el-button>
      </div>

      <el-table :data="filteredLogs" stripe v-loading="loading">
        <el-table-column prop="user" label="用户" width="100" />
        <el-table-column prop="action" label="操作" min-width="200" />
        <el-table-column prop="time" label="时间" width="180" />
        <el-table-column prop="result" label="结果" width="140">
          <template #default="{ row }">
            <el-tag :type="row.result.includes('成功') ? 'success' : 'danger'" size="small">
              {{ row.result }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="IP地址" width="160" />
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getLogList } from '@/api/system'

const logList = ref([])
const loading = ref(false)
const searchUser = ref('')
const filterResult = ref('')

const filteredLogs = computed(() => {
  let list = logList.value
  if (searchUser.value) {
    list = list.filter((l) => l.user.includes(searchUser.value))
  }
  if (filterResult.value) {
    list = list.filter((l) => l.result.includes(filterResult.value))
  }
  return list
})

onMounted(() => fetchLogs())

async function fetchLogs() {
  loading.value = true
  const res = await getLogList()
  if (res.code === 200) logList.value = res.data
  loading.value = false
}
</script>

<style scoped>
.filter-group {
  display: flex;
  align-items: center;
}

.table-header {
  margin-bottom: 16px;
}
</style>
