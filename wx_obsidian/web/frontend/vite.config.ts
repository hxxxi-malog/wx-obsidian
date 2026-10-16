import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
// vitest/config 的 defineConfig 与 vite 的等价，但额外认识 `test` 字段
import { defineConfig } from 'vitest/config'

// 构建产物直接落到 wx_obsidian/web/static/，由 FastAPI 同源托管（无 CORS）。
// base 与 outDir 是 web/api.py 的 _mount_frontend 与 Dockerfile 前端构建阶段
// 的共同前提（spec 的 R1），改动前先确认这两处。
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  base: '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // 框架代码与业务代码分开，业务改动不失效框架缓存
        manualChunks: { vendor: ['vue', 'vue-router', 'pinia'] },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    // 开发态走代理，免 CORS；SSE（任务进度、日志）也走这条代理
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['tests/**/*.spec.ts'],
    globals: true,
    restoreMocks: true,
  },
})
