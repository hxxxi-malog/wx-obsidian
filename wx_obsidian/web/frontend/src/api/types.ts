/** 接口 DTO 类型（与 wx_obsidian/web/api.py 的响应结构一一对应）。 */

export interface ConnectionTestResult {
  success: boolean
  latency_ms: number
  message: string
  details?: Record<string, string> | null
}

export interface EnvironmentWarning {
  /** 机器可读的原因码，例如 vault_unset / vault_in_tcc_dir */
  code: string
  message: string
  /** 该怎么处理（给用户看的动作建议） */
  action: string
}

export interface HealthPayload {
  source: ConnectionTestResult
  llm_api: ConnectionTestResult
  vision_api: ConnectionTestResult | null
  vault_path: ConnectionTestResult | null
  overall: boolean
  /** 首次运行（config.json 与 .env 都不存在） */
  first_run: boolean
  /** 环境前置条件告警：vault 未设置、落在 TCC 敏感目录、不可写等 */
  warnings: EnvironmentWarning[]
}

export interface StatsPayload {
  total_articles: number
  processed_articles: number
  failed_articles: number
  categories: Record<string, number>
  last_fetch_time: string | null
}

export interface JobResultItem {
  article_id: string
  title: string
  status: string
  category: string | null
  file_path: string | null
  error: string | null
}

export interface JobResult {
  done: number
  failed: number
  cancelled?: number
  total?: number
  items: JobResultItem[]
}

export interface JobSnapshot {
  job_id: string
  kind: string
  state: 'running' | 'finished' | 'cancelled' | 'error'
  started_at: string
  finished_at: string | null
  completed: number
  total: number
  last_title: string
  stage: string
  result: Partial<JobResult>
  error: string
}

export interface JobList {
  current: JobSnapshot | null
  recent: JobSnapshot[]
}

export interface JobEvent {
  seq: number
  type: string
  ts: string
  title?: string
  completed?: number
  total?: number
  stage?: string
  state?: string
  detail?: string
  message?: string
  error?: string
  result?: JobResult
}

export interface ArticleItem {
  article_id: string
  title: string
  category: string
  sub_topic: string
  status: string
  file_path: string
  file_exists: boolean
  processed_at: string
  error: string
  retry_count: number
}

export interface ArticleList {
  total: number
  limit: number
  offset: number
  categories: string[]
  items: ArticleItem[]
}

export interface ProviderView {
  base_url: string
  model: string
  api_key: string
  api_key_env: string
}

export interface ConfigSnapshot {
  llm: {
    active: string
    available: string[]
    providers: Record<string, ProviderView>
    effective: { provider: string; base_url: string; model: string; api_key: string }
    api_key: string
    max_tokens: number
    temperature: number
  }
  vision: {
    enabled: boolean
    active: string
    available: string[]
    providers: Record<string, ProviderView>
    effective: { provider: string; base_url: string; model: string; api_key: string }
    api_key: string
    max_concurrency: number
    timeout: number
  }
  source: { type: string; base_url: string; timeout: number; token: string }
  obsidian: { vault_path: string; articles_dir: string }
  fetch: { max_days: number; max_workers: number; feed_limit: number }
  web: { port: number; open_browser: boolean }
}

export interface ConfigSaveResult {
  saved: boolean
  applied: string[]
  skipped: string[]
  restart_required: boolean
}

export interface FsEntry {
  name: string
  is_dir: boolean
}

export interface FsListing {
  path: string
  parent: string
  entries: FsEntry[]
  truncated: boolean
}

/** POST /api/fs/pick：用户取消时 path 为空串且 cancelled=true */
export interface FsPickResult {
  path: string
  cancelled: boolean
}

export interface FeedItem {
  id: string
  name: string
  intro: string
  cover: string
  sync_time: string | null
  update_time: string | null
}

/** 数据源访问令牌状态（不回显令牌本身） */
export interface SourceTokenState {
  configured: boolean
  /** JWT 的 exp，空串表示解析不出（非 JWT 或未配置） */
  expires_at: string
  expired: boolean
}

export interface AccountStatus {
  is_logged_in: boolean
  username: string | null
  expire_at: string | null
  need_refresh: boolean
  /** false = 压根查不到（未配置令牌 / 上游 401），**不等于**未登录 */
  queried: boolean
  token: SourceTokenState
}

export interface SourceHealth {
  healthy: boolean
  type: string
  base_url: string
  docker_hint: string
}

export interface SchedulerJob {
  job_id: string
  name: string
  trigger: string
  is_running: boolean
  next_run: string | null
  last_run: string | null
  last_error: string | null
  error_count: number
}

export interface SchedulerState {
  fetch_hours: number
  keepalive_enabled: boolean
  allowed_hours: number[]
  keepalive_days: number
}

export interface SchedulerPayload {
  jobs: SchedulerJob[]
  state: SchedulerState
}

export interface LogEntry {
  seq: number
  ts: string
  level: string
  logger: string
  message: string
}

export interface LogPayload {
  entries: LogEntry[]
  last_seq: number
}

export interface FailedArticle {
  article_id: string
  title: string
  error: string
  failed_at: string | null
  retry_count: number
}
