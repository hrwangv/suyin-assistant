<template>
  <div class="users-page">
    <div class="page-header flex-between">
      <h2 class="page-title">用户管理</h2>
      <el-button type="primary" :icon="Plus" @click="handleAdd">新增用户</el-button>
    </div>

    <div class="content-card">
      <el-table :data="userList" stripe v-loading="loading" style="width: 100%">
        <el-table-column prop="username" label="用户名" width="140" />
        <el-table-column prop="role" label="角色" width="100">
          <template #default="{ row }">
            <el-tag :type="row.roleType === 'admin' ? 'danger' : 'primary'" size="small">
              {{ row.role }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="140">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === '正常'"
              @change="(val) => handleToggleStatus(row, val)"
              active-text="正常"
              inactive-text="禁用"
              style="--el-switch-on-color: #67c23a; --el-switch-off-color: #f56c6c"
            />
          </template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" min-width="200" />
        <el-table-column prop="createdAt" label="创建时间" width="150" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleEdit(row)">修改</el-button>
            <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Add/Edit Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '修改用户' : '新增用户'"
      width="500px"
    >
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
          <el-input v-model="form.password" type="password" placeholder="请输入密码" show-password />
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
    ElMessage.error('操作失败')
  }
}

function handleToggleStatus(row, val) {
  row.status = val ? '正常' : '禁用'
  ElMessage.success(`已${val ? '启用' : '禁用'}用户 ${row.username}`)
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除用户 "${row.username}" 吗？`, '确认删除', { type: 'warning' }).then(async () => {
    await deleteUser(row.id)
    userList.value = userList.value.filter((u) => u.id !== row.id)
    ElMessage.success('删除成功')
  }).catch(() => {})
}
</script>
