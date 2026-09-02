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
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/upload': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/status': {
        target: 'http://127.0.0.1:8000',
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
      '/stream': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
