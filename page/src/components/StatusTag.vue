<template>
  <el-tag
    :type="tagType"
    :size="size"
    :effect="effect"
    :round="round"
  >
    <slot>{{ label }}</slot>
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, required: true },
  size: { type: String, default: 'small' },
  effect: { type: String, default: 'light' },
  round: { type: Boolean, default: false },
})

const statusMap = {
  // Generic
  completed: { type: 'success', label: '已完成' },
  success: { type: 'success', label: '成功' },
  active: { type: 'success', label: '正常' },
  enabled: { type: 'success', label: '开启' },

  // Warning/In-progress
  processing: { type: 'warning', label: '处理中' },
  parsing: { type: 'warning', label: '解析中' },
  embedding: { type: 'warning', label: 'Embedding中' },
  uploading: { type: 'info', label: '上传中' },
  pending: { type: 'warning', label: '待处理' },
  in_progress: { type: 'warning', label: '进行中' },

  // Error/Disabled
  failed: { type: 'danger', label: '失败' },
  error: { type: 'danger', label: '错误' },
  disabled: { type: 'danger', label: '禁用' },
  inactive: { type: 'info', label: '未激活' },
}

const tagType = computed(() => statusMap[props.status]?.type || 'info')
const label = computed(() => statusMap[props.status]?.label || props.status)
</script>
