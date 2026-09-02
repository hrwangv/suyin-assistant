<template>
  <div class="dashboard-page">
    <!-- Welcome -->
    <div class="welcome-section">
      <h2 class="welcome-text">欢迎回来，{{ authStore.userInfo?.name || '用户' }}<span class="welcome-date">{{ currentDate }}</span></h2>
    </div>

    <!-- Todo Lists: Today + Week -->
    <el-row :gutter="24" class="todo-row" tag="div">
      <!-- Today Tasks -->
      <el-col :span="12">
        <div class="content-card todo-panel">
          <div class="panel-header">
            <div class="panel-title">
              <el-icon :size="20" color="#409eff"><Sunny /></el-icon>
              <span>今日待办</span>
              <el-tag size="small" round>{{ todayTasks.filter((t) => !t.done).length }} 项待完成</el-tag>
            </div>
            <el-button type="primary" size="small" :icon="Plus" circle @click="showAddDialog('today')" />
          </div>

          <div class="todo-list" v-if="todayTasks.length">
            <div
              v-for="task in todayTasks"
              :key="task.id"
              :class="['todo-item', { done: task.done }]"
            >
              <el-checkbox
                v-model="task.done"
                @change="handleDone(task)"
              />
              <div class="todo-content">
                <span class="todo-title">{{ task.title }}</span>
                <span v-if="task.note" class="todo-note">{{ task.note }}</span>
              </div>
              <div class="todo-meta">
                <el-tag v-if="task.priority === 'high'" type="danger" size="small" effect="dark">高</el-tag>
                <el-tag v-else-if="task.priority === 'medium'" type="warning" size="small" effect="dark">中</el-tag>
                <el-tag v-else type="info" size="small">低</el-tag>
                <span class="todo-time" v-if="task.time">{{ task.time }}</span>
              </div>
            </div>
          </div>
          <el-empty v-else description="今日暂无待办" :image-size="60" />

          <!-- Add inline input -->
          <div v-if="addingToday" class="add-todo-form">
            <el-input
              v-model="newTodoTitle"
              placeholder="添加今日待办"
              @keydown.enter="handleAddTodo('today')"
              size="small"
            >
              <template #append>
                <el-button :icon="Check" @click="handleAddTodo('today')" />
              </template>
            </el-input>
            <div class="add-todo-extra">
              <el-select v-model="newTodoPriority" size="small" style="width: 80px">
                <el-option label="高" value="high" />
                <el-option label="中" value="medium" />
                <el-option label="低" value="low" />
              </el-select>
              <el-input v-model="newTodoTime" size="small" placeholder="时间（可选）" style="width: 130px" />
              <el-button size="small" @click="addingToday = false">取消</el-button>
            </div>
          </div>
        </div>
      </el-col>

      <!-- Week/Long-term Tasks -->
      <el-col :span="12">
        <div class="content-card todo-panel">
          <div class="panel-header">
            <div class="panel-title">
              <el-icon :size="20" color="#e6a23c"><Calendar /></el-icon>
              <span>本周待办</span>
              <el-tag size="small" round>{{ weekTasks.filter((t) => !t.done).length }} 项待完成</el-tag>
            </div>
            <el-button type="warning" size="small" :icon="Plus" circle @click="showAddDialog('week')" />
          </div>

          <div class="todo-list" v-if="weekTasks.length">
            <div
              v-for="task in weekTasks"
              :key="task.id"
              :class="['todo-item', { done: task.done }]"
            >
              <el-checkbox
                v-model="task.done"
                @change="handleDone(task)"
              />
              <div class="todo-content">
                <span class="todo-title">{{ task.title }}</span>
                <span v-if="task.note" class="todo-note">{{ task.note }}</span>
              </div>
              <div class="todo-meta">
                <el-tag v-if="task.priority === 'high'" type="danger" size="small" effect="dark">高</el-tag>
                <el-tag v-else-if="task.priority === 'medium'" type="warning" size="small" effect="dark">中</el-tag>
                <el-tag v-else type="info" size="small">低</el-tag>
                <span class="todo-date">{{ task.date }}</span>
              </div>
            </div>
          </div>
          <el-empty v-else description="本周暂无待办" :image-size="60" />

          <div v-if="addingWeek" class="add-todo-form">
            <el-input
              v-model="newTodoTitle"
              placeholder="添加本周待办"
              @keydown.enter="handleAddTodo('week')"
              size="small"
            >
              <template #append>
                <el-button :icon="Check" @click="handleAddTodo('week')" />
              </template>
            </el-input>
            <div class="add-todo-extra">
              <el-select v-model="newTodoPriority" size="small" style="width: 80px">
                <el-option label="高" value="high" />
                <el-option label="中" value="medium" />
                <el-option label="低" value="low" />
              </el-select>
              <el-date-picker v-model="newTodoDate" size="small" placeholder="选择日期" style="width: 130px" value-format="MM-DD" />
              <el-button size="small" @click="addingWeek = false">取消</el-button>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- Tool Recommendations -->
    <div class="tools-section">
      <h3 class="tools-title">工具推荐</h3>
      <div class="tools-bar">
        <div
          v-for="tool in quickTools"
          :key="tool.id"
          class="tool-item"
          @click="$router.push(tool.route)"
        >
          <div class="tool-icon-box">
            <el-icon :size="22" color="#409eff">
              <component :is="tool.icon" />
            </el-icon>
          </div>
          <span class="tool-label">{{ tool.name }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { getTodoTasks, getQuickTools } from '@/api/dashboard'

const authStore = useAuthStore()

const currentDate = new Date().toLocaleDateString('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
})

const todayTasks = ref([])
const weekTasks = ref([])
const quickTools = ref([])

// Add new todo
let todoIdCounter = 100
const addingToday = ref(false)
const addingWeek = ref(false)
const newTodoTitle = ref('')
const newTodoPriority = ref('medium')
const newTodoTime = ref('')
const newTodoDate = ref('')

onMounted(async () => {
  const [todoRes, toolsRes] = await Promise.all([getTodoTasks(), getQuickTools()])
  if (todoRes.code === 200) {
    todayTasks.value = todoRes.data.today
    weekTasks.value = todoRes.data.week
  }
  if (toolsRes.code === 200) {
    quickTools.value = toolsRes.data
  }
})

function handleDone(task) {
  if (task.done) {
    ElMessage.success(`已完成：${task.title}`)
  }
}

function showAddDialog(type) {
  newTodoTitle.value = ''
  newTodoPriority.value = 'medium'
  newTodoTime.value = ''
  newTodoDate.value = ''
  if (type === 'today') {
    addingToday.value = true
    addingWeek.value = false
  } else {
    addingWeek.value = true
    addingToday.value = false
  }
}

function handleAddTodo(type) {
  const title = newTodoTitle.value.trim()
  if (!title) return

  const task = {
    id: ++todoIdCounter,
    title,
    done: false,
    priority: newTodoPriority.value,
    note: '',
  }

  if (type === 'today') {
    task.time = newTodoTime.value || undefined
    todayTasks.value.unshift(task)
    addingToday.value = false
  } else {
    task.date = newTodoDate.value || new Date().toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
    weekTasks.value.unshift(task)
    addingWeek.value = false
  }
  ElMessage.success('已添加')
}
</script>

<style scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 96px);
  margin: -20px;
  padding: 20px;
}

.welcome-section {
  flex-shrink: 0;
  margin-bottom: 8px;
}

.welcome-text {
  font-size: 26px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 14px;
}

.welcome-date {
  font-size: 14px;
  font-weight: 400;
  color: var(--text-secondary);
}

.todo-row {
  flex: 1;
  min-height: 0;
}

.todo-row .el-col {
  height: 100%;
}

.todo-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-shrink: 0;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.todo-list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.todo-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 4px;
  border-bottom: 1px solid #f5f5f5;
  transition: all 0.3s;
}

.todo-item:last-child {
  border-bottom: none;
}

.todo-item.done {
  opacity: 0.45;
}

.todo-item.done .todo-title {
  text-decoration: line-through;
  color: var(--text-placeholder);
}

.todo-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.todo-title {
  font-size: 14px;
  color: var(--text-primary);
}

.todo-note {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.todo-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.todo-time,
.todo-date {
  font-size: 12px;
  color: var(--text-placeholder);
  white-space: nowrap;
}

.add-todo-form {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e6e6e6;
  flex-shrink: 0;
}

.add-todo-extra {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

/* Tools */
.tools-section {
  flex-shrink: 0;
  margin-top: 40px;
}

.tools-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.tools-bar {
  display: flex;
  gap: 14px;
}

.tool-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px 12px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s;
}

.tool-item:hover {
  border-color: var(--color-primary);
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.15);
  transform: translateY(-3px);
}

.tool-icon-box {
  width: 46px;
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #ecf5ff;
  border-radius: 10px;
}

.tool-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
</style>
