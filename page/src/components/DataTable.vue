<template>
  <div class="data-table-wrapper">
    <div v-if="showToolbar" class="table-toolbar flex-between">
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
          style="width: 220px"
          clearable
          @input="handleSearch"
        />
      </div>
    </div>

    <el-table
      v-bind="$attrs"
      :data="displayData"
      v-loading="loading"
      stripe
      border
    >
      <slot />
    </el-table>

    <div v-if="pagination" class="table-pagination flex-between">
      <span class="pagination-total">共 {{ total }} 条记录</span>
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="pageSizes"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="handleSizeChange"
        @current-change="handlePageChange"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

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
const total = computed(() => props.data.length)

const displayData = computed(() => {
  let list = props.data
  if (searchText.value) {
    emit('search', searchText.value)
  }
  if (props.pagination) {
    const start = (currentPage.value - 1) * pageSize.value
    return list.slice(start, start + pageSize.value)
  }
  return list
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

watch(() => props.data, () => {
  currentPage.value = 1
})
</script>

<style scoped>
.table-toolbar {
  margin-bottom: 16px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.table-pagination {
  margin-top: 16px;
  padding-right: 8px;
}

.pagination-total {
  font-size: 13px;
  color: var(--text-secondary);
}
</style>
