/** 导航信息架构：8 个页面按用途分组（路由路径保持不变，深链与启动器跳转都依赖它）。 */

import {
  PhArticle,
  PhClock,
  PhCloudArrowDown,
  PhGear,
  PhGauge,
  PhHardDrives,
  PhRss,
  PhUserCircle,
} from '@phosphor-icons/vue'
import type { Component } from 'vue'

export interface NavItem {
  key: string
  label: string
  path: string
  icon: Component
  /** 命令面板/悬浮提示里的一句话说明 */
  description: string
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: '工作台',
    items: [
      { key: 'dashboard', label: '状态总览', path: '/', icon: PhGauge, description: '组件连通性、处理统计与分类分布' },
      { key: 'run', label: '文章抓取', path: '/run', icon: PhCloudArrowDown, description: '发起抓取、看实时进度与日志' },
    ],
  },
  {
    label: '内容',
    items: [
      { key: 'articles', label: '文章管理', path: '/articles', icon: PhArticle, description: '搜索、筛选、打开与级联删除' },
    ],
  },
  {
    label: '数据源',
    items: [
      { key: 'feeds', label: '公众号管理', path: '/feeds', icon: PhRss, description: '订阅列表与按链接添加' },
      { key: 'source', label: '数据源状态', path: '/source', icon: PhHardDrives, description: 'we-mp-rss 探活与启动指引' },
      { key: 'account', label: '账号管理', path: '/account', icon: PhUserCircle, description: '登录态与访问令牌' },
    ],
  },
  {
    label: '自动化',
    items: [
      { key: 'scheduler', label: '定时任务', path: '/scheduler', icon: PhClock, description: '定时抓取与保活任务' },
    ],
  },
]

export const SETTINGS_ITEM: NavItem = {
  key: 'settings',
  label: '配置管理',
  path: '/settings',
  icon: PhGear,
  description: '模型、数据源、Vault 与 Web 服务',
}

export const ALL_NAV_ITEMS: NavItem[] = [...NAV_GROUPS.flatMap((group) => group.items), SETTINGS_ITEM]

export function navItemByKey(key: string | undefined): NavItem | undefined {
  return ALL_NAV_ITEMS.find((item) => item.key === key)
}
