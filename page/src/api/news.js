// 新闻中心后端接口尚未提供，先返回空数据。

export function getTodayNewsCount() {
  return Promise.resolve({ code: 200, data: { count: 0 } })
}

export function getNewsList() {
  return Promise.resolve({
    code: 200,
    data: [],
  })
}
