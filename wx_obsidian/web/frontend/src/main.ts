// 字体自托管（不引 CDN：本地应用必须离线可用；不加载 CJK webfont，汉字回落系统字体）
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'

import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { initToken } from './lib/api/client'
import { router } from './router'
import './styles/index.css'

// 先取 token 再挂载：首屏请求与 SSE 都要用它。
// 注意 router.ts 里还有一次（它必须早于 createWebHistory），这里保留是为了「谁先加载都不会漏」。
initToken()

createApp(App).use(createPinia()).use(router).mount('#app')
