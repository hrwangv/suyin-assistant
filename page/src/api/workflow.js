// 询证函处理后端接口尚未提供，先移除前端 mock 数据。

export function uploadConfirmationLetter() {
  return Promise.reject(new Error('询证函识别后端接口未接入'))
}

export function submitForReview() {
  return Promise.reject(new Error('询证函审核后端接口未接入'))
}
