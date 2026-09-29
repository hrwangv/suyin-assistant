<template>
  <div class="data-table-wrapper">
    <div v-if="showToolbar" class="table-toolbar">
      <div class="toolbar-left">
        <slot name="toolbar-left" />
      </div>
      <div class="toolbar-right">
        <slot name="toolbar-right" />
        <el-input
          v-if="searchable"
          v-model="searchText"
          placeholder="搜索..."
          :prefix-icon="Search"
          class="toolbar-search"
          clearable
          @input="handleSearch"
        />
      </div>
    </div>

    <el-table v-bind="$attrs" :data="displayData" v-loading="loading">
      <slot />
    </el-table>

    <div v-if="pagination" class="table-pagination">
      <span class="pagination-total">共 {{ filteredData.length }} 条记录</span>
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="pageSizes"
        :total="filteredData.length"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handlePageChange"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  pagination: { type: Boolean, default: true },
  searchable: { type: Boolean, default: false },
  showToolbar: { type: Boolean, default: false },
  pageSizes: { type: Array, default: () => [10, 20, 50, 100] },
})

const emit = defineEmits(['search', 'page-change', 'size-change'])

const searchText = ref('')
const currentPage = ref(1)
const pageSize = ref(10)

// 本地按所有字段做一次模糊匹配，父组件仍可通过 search 事件接管远程搜索
const filteredData = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  if (!keyword) return props.data
  return props.data.filter((row) =>
    Object.values(row || {}).some((value) =>
      String(value ?? '').toLowerCase().includes(keyword)
    )
  )
})

const displayData = computed(() => {
  if (!props.pagination) return filteredData.value
  const start = (currentPage.value - 1) * pageSize.value
  return filteredData.value.slice(start, start + pageSize.value)
})

function handleSearch(val) {
  currentPage.value = 1
  emit('search', val)
}

function handlePageChange(page) {
  currentPage.value = page
  emit('page-change', page)
}

function handleSizeChange(size) {
  pageSize.value = size
  currentPage.value = 1
  emit('size-change', size)
}

watch(
  () => props.data,
  () => {
    currentPage.value = 1
  }
)
</script>

<style scoped>
.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.toolbar-search {
  width: 220px;
}

.table-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-top: var(--space-4);
  padding-right: 4px;
}

.pagination-total {
  font-size: 13px;
  color: var(--text-secondary);
}
</style>
