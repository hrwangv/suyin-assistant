/**
 * 会话列表本地持久化 + 按需恢复历史的自测。
 *
 * 覆盖两件事：
 * 1. 会话外壳（sessionId / 标题 / 创建时间 / 自增 id）能落盘并在刷新后恢复，
 *    消息正文不落盘（刷新后由 GET /history/{session_id} 拉回来）；
 * 2. 本地记录按登录用户隔离——session_id 是 /history 的唯一凭证（该接口无鉴权），
 *    同一浏览器换账号不能读到上一个账号的会话。
 *
 * 不依赖浏览器和后端：用内存里的 localStorage 替身 + 真实 pinia store。
 * 运行：cd page && npm run test:chat
 */
const store = new Map()

globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
}

const read = (key) => JSON.parse(store.get(key))

const { createPinia, setActivePinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.js')

const ADMIN_KEY = 'suyin:chat:v1:admin'
const GUEST_KEY = 'suyin:chat:v1:guest'

let passed = 0
const failed = []

function check(name, actual, expected) {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    passed++
  } else {
    failed.push(`${name}\n     期望：${JSON.stringify(expected)}\n     实际：${JSON.stringify(actual)}`)
  }
}

// 模拟"重新打开页面"：重新 import 模块（nextId 等模块级状态归零）+ 新建 pinia
async function freshStore(tag) {
  const { useChatStore: useFresh } = await import(`../src/stores/chat.js?${tag}`)
  setActivePinia(createPinia())
  return useFresh()
}

async function main() {
  // ---------- 1. 落盘 ----------
  setActivePinia(createPinia())
  let chat = useChatStore()
  check('未绑定用户时不写 localStorage', store.size, 0)

  chat.bindUser('admin')
  const conv = chat.createConversation()
  chat.setSessionId(conv.id, 'sess-abc-123')
  chat.sendMessage('润泽科技最近怎么样', 'user')
  chat.sendMessage('润泽科技在苏州自建了数据中心', 'assistant')

  check('sessionId 落盘', read(ADMIN_KEY).conversations[0].sessionId, 'sess-abc-123')
  check('标题随首条提问落盘', read(ADMIN_KEY).conversations[0].title, '润泽科技最近怎么样')
  check('消息正文不落盘', 'messages' in read(ADMIN_KEY).conversations[0], false)
  check('自增 id 落盘', read(ADMIN_KEY).nextId, 2)

  // ---------- 2. 刷新恢复 ----------
  chat = await freshStore('refresh')
  chat.bindUser('admin')
  const restored = chat.conversations[0]
  check('刷新后会话恢复', chat.conversations.length, 1)
  check('sessionId 恢复', restored.sessionId, 'sess-abc-123')
  check('标题恢复', restored.title, '润泽科技最近怎么样')
  check('消息为空（等后端历史填充）', restored.messages.length, 0)
  check('当前会话指向恢复段', chat.currentConversationId, restored.id)

  const newConv = chat.createConversation()
  check('刷新后新建会话 id 不与旧会话撞车', newConv.id !== restored.id, true)

  // ---------- 3. 历史到达时的合并顺序 ----------
  chat.selectConversation(restored.id)
  chat.mergeHistory(restored.id, [
    { id: 'h0', role: 'user', content: '历史问题' },
    { id: 'h1', role: 'assistant', content: '历史回答' },
  ])
  chat.sendMessage('新问题', 'user')
  chat.mergeHistory(restored.id, [{ id: 'h-1', role: 'user', content: '更早的历史' }])
  check(
    '历史排在已有消息之前',
    chat.currentConversation().messages.map((m) => m.content),
    ['更早的历史', '历史问题', '历史回答', '新问题']
  )

  // ---------- 4. 切账号隔离 ----------
  chat.bindUser('guest')
  check('换账号后内存态被清空', chat.conversations.length, 0)
  const guestConv = chat.createConversation()
  chat.setSessionId(guestConv.id, 'sess-guest-1')
  check('guest 写入自己的 key', read(GUEST_KEY).conversations[0].sessionId, 'sess-guest-1')
  check('admin 的记录未被污染', read(ADMIN_KEY).conversations.length, 2)

  chat.bindUser('admin')
  check('切回 admin 能看到自己的会话', chat.conversations.length, 2)
  check('loadedHistory 按会话重置', chat.hasLoadedHistory(chat.conversations[0].id), false)
  chat.bindUser('admin')
  check('同一用户重复 bindUser 不清空内存态', chat.conversations.length, 2)

  // ---------- 5. 刷新后必须重新拉历史 ----------
  chat.markHistoryLoaded(chat.conversations[0].id)
  const afterReload = await freshStore('refresh2')
  afterReload.bindUser('admin')
  check(
    'loadedHistory 不持久化（刷新后重新拉）',
    afterReload.hasLoadedHistory(afterReload.conversations[0].id),
    false
  )

  console.log(`\n通过 ${passed} 项，失败 ${failed.length} 项`)
  failed.forEach((item) => console.log('  [FAIL]', item))
  return failed.length ? 1 : 0
}

process.exit(await main())
