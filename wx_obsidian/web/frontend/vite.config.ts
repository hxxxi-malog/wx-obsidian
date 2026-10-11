import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物直接落到 wx_obsidian/web/static/，由 FastAPI 同源托管（无 CORS）
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
    // 开发态走代理，免 CORS
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
