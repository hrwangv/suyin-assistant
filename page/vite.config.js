import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': '/src',
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/upload': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/status': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/files': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/query': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/history': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      // 新建对话时的会话收尾（长期记忆抽取）
      '/session/close': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      // Agent Memory 接口族（/memory/add、/memory/search 等）
      '/memory': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      // SSE 长连接：SSE 走 GET /stream/{session_id}
      '/stream': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
