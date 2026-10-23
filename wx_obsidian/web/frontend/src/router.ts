import { createRouter, createWebHistory } from 'vue-router'

import { initToken } from './lib/api/client'
import { navItemByKey } from './lib/navigation'

// ⚠️ 必须在 createWebHistory() **之前**抹掉地址栏里的 token：
// createWebHistory() 会在创建时把当前 URL 记进自己的状态，router 安装时再 replaceState 写回，
// 于是「已抹掉」的 token 会被它原样恢复（实测：sessionStorage 有值但地址栏仍带 token）。
// 这里先清一次，main.ts 里那次调用就只是幂等的兜底。
initToken()

// 路由路径**保持不变**：启动器的 `/settings?first_run=1` 深链、文档里写过的地址、
// 以及用户收藏夹都依赖它（spec 的 R5）。视图改为懒加载，首屏只下载当前页。
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('./views/Dashboard.vue'),
      meta: { navKey: 'dashboard' },
    },
    {
      path: '/run',
      name: 'run',
      component: () => import('./views/Run.vue'),
      meta: { navKey: 'run' },
    },
    {
      path: '/latest',
      name: 'latest',
      component: () => import('./views/Latest.vue'),
      meta: { navKey: 'latest' },
    },
    {
      path: '/articles',
      name: 'articles',
      component: () => import('./views/Articles.vue'),
      meta: { navKey: 'articles' },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('./views/Settings.vue'),
      meta: { navKey: 'settings' },
    },
    {
      path: '/feeds',
      name: 'feeds',
      component: () => import('./views/Feeds.vue'),
      meta: { navKey: 'feeds' },
    },
    {
      path: '/source',
      name: 'source',
      component: () => import('./views/Source.vue'),
      meta: { navKey: 'source' },
    },
    {
      path: '/account',
      name: 'account',
      component: () => import('./views/Account.vue'),
      meta: { navKey: 'account' },
    },
    // 未匹配路径回首页，避免 404 白屏
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const item = navItemByKey(to.meta.navKey as string | undefined)
  document.title = item ? `${item.label} · wx-obsidian` : 'wx-obsidian'
})
