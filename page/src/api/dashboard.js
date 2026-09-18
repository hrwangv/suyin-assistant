// 首页快捷工具目前没有后端接口，先返回空数据。

export function getQuickTools() {
  return Promise.resolve({
    code: 200,
    data: [],
  })
}
