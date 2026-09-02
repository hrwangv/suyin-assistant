// Workflow mock API (Confirmation Letter)

export function uploadConfirmationLetter(file) {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        code: 200,
        data: {
          taskId: 'cl-' + Date.now(),
          ocrResult: {
            companyName: 'XX科技有限公司',
            replyAddress: '北京市朝阳区XX路XX号',
            balance: '￥1,250,000.00',
            contactPerson: '李四',
            phone: '010-8888XXXX',
          },
          steps: [
            { title: '上传文件', status: 'completed' },
            { title: 'OCR识别', status: 'completed' },
            { title: '数据核验', status: 'in_progress' },
            { title: '审批', status: 'pending' },
            { title: '完成', status: 'pending' },
          ],
        },
      })
    }, 1500)
  })
}

export function submitForReview(taskId) {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({ code: 200, data: { status: 'submitted' } })
    }, 500)
  })
}
