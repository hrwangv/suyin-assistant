// Knowledge Base API
import request from './index'

// 工具函数：字节数 → 可读大小
function formatSize(bytes) {
  if (!bytes || bytes === 0) return '0B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const k = 1024
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  const size = (bytes / Math.pow(k, i)).toFixed(i === 0 ? 0 : 1)
  return size + units[i]
}

// 查询文档接口：对应后端 GET /files
export function getFileList() {
  return request.get('/files')
}

// 批量上传文件：一次请求携带多个文件，字段名固定为 files（对应后端 /upload）
export function uploadFiles(files) {
  const fileList = Array.isArray(files) ? files : [files];
  const formData = new FormData();

  // 后端按 files 的顺序返回 task_ids，因此这里按同样的顺序 append
  fileList.forEach((file) => formData.append('files', file));

  return fetch('/upload', {  // 后端地址，可加 baseURL
    method: 'POST',
    body: formData,
  })
    .then(res => res.json())  // 将 HTTP 响应体解析为 JSON 对象。
    .then(res => {
      if (res.code !== 200) {
        throw new Error(res.message || '上传失败');
      }

      // 后端返回 { code:200, message: '...', task_ids: ['uuid', ...] }
      const fileIds = res.file_ids || [];
      const taskIds = res.task_ids || [];

      // 每个文件生成一条“上传中”记录并各自轮询对应 task_id
      const records = fileList.map((file, index) => {
        const fileId = fileIds[index];
        const taskId = taskIds[index];
        const tempId = `${Date.now()}_${index}`;   // 前端临时ID，用于列表渲染
        const fileData = {
          id: fileId || tempId,
          name: file.name,
          status: 'uploading',        // 初始状态
          size: formatSize(file.size),
          uploadTime: new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
          _taskId: taskId,            // 保存后端 task_id，用于轮询
        };

        if (taskId) {
          // 启动轮询任务，更新状态
          pollTaskStatus(taskId, fileData);
        } else {
          fileData.status = 'failed';
        }

        return fileData;
      });

      return { code: 200, data: records };
    })
    .catch(err => {
      console.error('上传失败:', err);
      throw err;  // 重新抛出，让调用方的 try/catch 能捕获到
    });
}

// 单文件上传兼容封装（复用批量逻辑）
export function uploadFile(file) {
  return uploadFiles([file]).then(res => ({ code: res.code, data: res.data[0] }));
}



// 后端状态 → 前端状态的映射
// 后端: pending → 任务已登记但后台尚未开始
//       processing → 图的节点正在执行中
//       completed/failed → 终端状态
const STATUS_MAP = {
  pending: 'uploading',
  processing: 'parsing',
  completed: 'completed',
  failed: 'failed',
}

// 轮询后端任务状态，更新文件记录
function pollTaskStatus(taskId, fileRecord) {
  const POLL_INTERVAL = 2000   // 每 2 秒轮询一次
  const MAX_RETRIES = 300      // 最多轮询 10 分钟

  let retries = 0

  const timer = setInterval(async () => {
    retries++

    try {
      const res = await fetch(`/status/${taskId}`).then(r => r.json())
      console.log(`[轮询 #${retries}] taskId=${taskId}, 后端status="${res.status}", 前端status="${fileRecord.status}"`)

      if (res.code === 200) {
        const backendStatus = res.status
        fileRecord.status = STATUS_MAP[backendStatus] || 'parsing'

        // 终端状态：停止轮询
        if (backendStatus === 'completed' || backendStatus === 'failed') {
          clearInterval(timer)
          window.dispatchEvent(
            new CustomEvent('knowledge-file-status-finished', {
              detail: {
                fileId: fileRecord.id,
                status: fileRecord.status,
              },
            })
          )
        }
      }
    } catch (e) {
      console.error(`[pollTaskStatus] 轮询失败 (${taskId}):`, e)
    }

    // 超时保护
    if (retries >= MAX_RETRIES) {
      clearInterval(timer)
      fileRecord.status = 'failed'
      console.error(`[pollTaskStatus] 轮询超时 (${taskId})`)
    }
  }, POLL_INTERVAL)

  // 把 timer 挂到 fileRecord 上，方便后续清理
  fileRecord._pollTimer = timer
}

// 删除文件接口：对应后端 DELETE /files/{id}
export function deleteFile(id) {
  return request.delete(`/files/${id}`)
}
