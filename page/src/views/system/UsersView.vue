<template>
  <div class="users-page">
    <PageHeader title="用户管理" description="维护系统账号、角色与启用状态">
      <template #extra>
        <el-button type="primary" :icon="Plus" @click="handleAdd">新增用户</el-button>
      </template>
    </PageHeader>

    <div class="content-card">
      <div class="list-head">
        <h3 class="section-title">账号列表</h3>
        <span class="list-count">共 {{ userList.length }} 个账号</span>
      </div>

      <el-table :data="userList" v-loading="loading">
        <el-table-column prop="username" label="用户名" width="160" />
        <el-table-column prop="role" label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="row.roleType === 'admin' ? 'warning' : 'primary'" effect="plain">
              {{ row.role }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="150">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === '正常'"
              active-text="正常"
              inactive-text="禁用"
              inline-prompt
              @change="(val) => handleToggleStatus(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="200" />
        <el-table-column prop="createdAt" label="创建时间" width="160" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="handleEdit(row)">修改</el-button>
            <el-button type="danger" link @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-block">
            <el-icon :size="28"><User /></el-icon>
            <p class="empty-title">暂无账号数据</p>
            <p class="empty-desc">后端用户接口接入后，账号列表将在此展示</p>
          </div>
        </template>
      </el-table>
    </div>

    <!-- 新增 / 修改 -->
    <el-dialog v-model="dialogVisible" :title="isEdit ? '修改用户' : '新增用户'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="角色" prop="roleType">
          <el-select v-model="form.roleType" placeholder="请选择角色" style="width: 100%">
            <el-option label="员工" value="employee" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" />
        </el-form-item>
        <el-form-item v-if="!isEdit" label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import PageHeader from '@/components/PageHeader.vue'
import { getUserList, createUser, updateUser, deleteUser } from '@/api/system'

const userList = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const isEdit = ref(false)
const editId = ref(null)
const formRef = ref(null)

const form = reactive({
  username: '',
  roleType: 'employee',
  email: '',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  roleType: [{ required: true, message: '请选择角色', trigger: 'change' }],
}

onMounted(async () => {
  loading.value = true
  const res = await getUserList()
  if (res.code === 200) userList.value = res.data
  loading.value = false
})

function resetForm() {
  form.username = ''
  form.roleType = 'employee'
  form.email = ''
  form.password = ''
  editId.value = null
  isEdit.value = false
}

function handleAdd() {
  resetForm()
  dialogVisible.value = true
}

function handleEdit(row) {
  isEdit.value = true
  editId.value = row.id
  form.username = row.username
  form.roleType = row.roleType
  form.email = row.email
  form.password = ''
  dialogVisible.value = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  try {
    if (isEdit.value) {
      await updateUser(editId.value, { ...form })
      const user = userList.value.find((u) => u.id === editId.value)
      if (user) {
        user.username = form.username
        user.roleType = form.roleType
        user.role = form.roleType === 'admin' ? '管理员' : '员工'
        user.email = form.email
      }
      ElMessage.success('修改成功')
    } else {
      const res = await createUser({ ...form })
      userList.value.push({
        id: res.data.id,
        username: form.username,
        role: form.roleType === 'admin' ? '管理员' : '员工',
        roleType: form.roleType,
        status: '正常',
        email: form.email,
        createdAt: new Date().toISOString().slice(0, 10),
      })
      ElMessage.success('新增成功')
    }
    dialogVisible.value = false
  } catch (e) {
    ElMessage.error(e?.message || '操作失败')
  }
}

function handleToggleStatus(row, val) {
  row.status = val ? '正常' : '禁用'
  ElMessage.success(`已${val ? '启用' : '禁用'}用户 ${row.username}`)
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除用户 "${row.username}" 吗？`, '确认删除', { type: 'warning' })
    .then(async () => {
      try {
        await deleteUser(row.id)
        userList.value = userList.value.filter((u) => u.id !== row.id)
        ElMessage.success('删除成功')
      } catch (e) {
        ElMessage.error(e?.message || '删除失败')
      }
    })
    .catch(() => {})
}
</script>

<style scoped>
.list-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.list-count {
  font-size: 12.5px;
  color: var(--text-secondary);
}

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
</style>
