import { createRouter, createWebHistory } from 'vue-router'

import Articles from './views/Articles.vue'
import Dashboard from './views/Dashboard.vue'
import Run from './views/Run.vue'
import Settings from './views/Settings.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/run', name: 'run', component: Run },
    { path: '/articles', name: 'articles', component: Articles },
    { path: '/settings', name: 'settings', component: Settings },
    // 公众号/数据源/定时任务在 P2 落地；未匹配路径回首页，避免 404 白屏
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
