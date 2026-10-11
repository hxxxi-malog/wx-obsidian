<script setup lang="ts">
interface NavItem {
  key: string
  label: string
  path: string
  ready: boolean
  hint?: string
}

// ready=false 的条目是 P2 待落地页面，先只做路线图提示，不注册死路由
const NAV: NavItem[] = [
  { key: 'dashboard', label: '状态总览', path: '/', ready: true },
  { key: 'run', label: '文章抓取', path: '/run', ready: true },
  { key: 'articles', label: '文章管理', path: '/articles', ready: true },
  { key: 'settings', label: '配置管理', path: '/settings', ready: true },
  { key: 'feeds', label: '公众号管理', path: '/feeds', ready: false, hint: 'P2' },
  { key: 'source', label: '数据源状态', path: '/source', ready: false, hint: 'P2' },
  { key: 'scheduler', label: '定时任务', path: '/scheduler', ready: false, hint: 'P2' },
]
</script>

<template>
  <el-container class="app">
    <el-header class="app__header">
      <span class="app__brand">wx-obsidian</span>
      <span class="app__sub">公众号文章 → Obsidian 知识库</span>
    </el-header>

    <el-container>
      <el-aside width="190px" class="app__aside">
        <ul class="nav">
          <li v-for="item in NAV" :key="item.key">
            <router-link v-if="item.ready" class="nav__item nav__item--ready" :to="item.path">
              {{ item.label }}
            </router-link>
            <span v-else class="nav__item nav__item--soon">
              {{ item.label }}
              <em class="nav__hint">{{ item.hint }}</em>
            </span>
          </li>
        </ul>
      </el-aside>

      <el-main class="app__main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style>
:root {
  --wx-border: #dcdfe6;
  --wx-muted: #909399;
}

body {
  margin: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Helvetica Neue', Arial, sans-serif;
  background: #f5f7fa;
  color: #303133;
}
</style>

<style scoped>
.app {
  min-height: 100vh;
}

.app__header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  background: #1f2d3d;
  color: #fff;
}

.app__brand {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.app__sub {
  font-size: 12px;
  color: #a3b1c2;
}

.app__aside {
  background: #fff;
  border-right: 1px solid var(--wx-border);
  padding: 12px 0;
}

.app__main {
  padding: 20px 24px;
}

.nav {
  margin: 0;
  padding: 0;
  list-style: none;
}

.nav__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  font-size: 14px;
  text-decoration: none;
}

.nav__item--ready {
  color: #1f2d3d;
}

.nav__item--ready.router-link-active {
  background: #ecf5ff;
  border-right: 3px solid #409eff;
  font-weight: 600;
}

.nav__item--soon {
  color: #c0c4cc;
  cursor: not-allowed;
}

.nav__hint {
  font-size: 11px;
  font-style: normal;
  color: #c0c4cc;
}
</style>
