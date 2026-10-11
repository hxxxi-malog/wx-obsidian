import { createRouter, createWebHistory } from 'vue-router'

import Account from './views/Account.vue'
import Articles from './views/Articles.vue'
import Dashboard from './views/Dashboard.vue'
import Feeds from './views/Feeds.vue'
import Run from './views/Run.vue'
import Scheduler from './views/Scheduler.vue'
import Settings from './views/Settings.vue'
import Source from './views/Source.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/run', name: 'run', component: Run },
    { path: '/articles', name: 'articles', component: Articles },
    { path: '/settings', name: 'settings', component: Settings },
    { path: '/feeds', name: 'feeds', component: Feeds },
    { path: '/source', name: 'source', component: Source },
    { path: '/account', name: 'account', component: Account },
    { path: '/scheduler', name: 'scheduler', component: Scheduler },
    // 未匹配路径回首页，避免 404 白屏
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
