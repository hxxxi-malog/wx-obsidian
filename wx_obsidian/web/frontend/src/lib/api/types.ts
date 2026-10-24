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

export type JobState = 'running' | 'finished' | 'cancelled' | 'error'

export interface JobSnapshot {
  job_id: string
  kind: string
  state: JobState
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
  level?: string
  logger?: string
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

/** GET /api/articles 的排序键（后端白名单，非法值会被忽略） */
export type ArticleSort = 'processed_at' | 'title' | 'category'
export type SortOrder = 'asc' | 'desc'

export interface ArticleQuery {
  q?: string
  category?: string
  status?: string
  limit?: number
  offset?: number
  sort?: ArticleSort
  order?: SortOrder
}

export interface ArticleList {
  total: number
  limit: number
  offset: number
  categories: string[]
  items: ArticleItem[]
}

/** POST /api/articles/bulk-delete 的响应；未知 id 不整体失败，逐条进 failed */
export interface BulkDeleteFailure {
  article_id: string
  error: string
}

export interface BulkDeleteResult {
  ok: boolean
  deleted: number
  failed: BulkDeleteFailure[]
  actions: string[]
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
  source: {
    type: string
    base_url: string
    timeout: number
    token: string
    /** Web 服务按间隔自动触发数据源采集 */
    auto_sync: boolean
    /** 自动采集间隔（分钟） */
    sync_interval_minutes: number
  }
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

/** PUT /api/config 的入参：patch 走配置键白名单，env 走环境变量白名单 */
export interface ConfigPatchPayload {
  patch: Record<string, string | number | boolean>
  env: Record<string, string>
}

export type ConnectionTestTarget = 'llm' | 'vision' | 'source' | 'all'

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

/** GET /api/source/articles 返回的一篇文章（列表投影，不含正文） */
export interface SourceArticleItem {
  article_id: string
  title: string
  url: string
  account: string
  account_id: string
  /** 上游没有给出发布时间时为 null，界面归入「时间未知」 */
  published_at: string | null
  cover: string
  has_content: boolean
  /** 这篇已在 processed.json 里入库 */
  processed: boolean
}

/** GET /api/source/articles 的查询参数；mp_id 为空串表示全部公众号 */
export interface SourceArticleQuery {
  offset?: number
  limit?: number
  mp_id?: string
}

/** GET /api/source/articles 的成功响应 */
export interface SourceArticlePage {
  total: number
  offset: number
  limit: number
  token_configured: boolean
  items: SourceArticleItem[]
}

/**
 * GET /api/source/articles 的失败响应：HTTP 仍是 200，靠 error 字段区分原因。
 *
 * 已知取值：source_token_invalid（访问令牌未配置或失效）、source_unavailable（上游不可达）。
 */
export interface SourceArticleListError {
  error: string
  detail: string
  token_configured: boolean
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

export interface SourceRefreshResult {
  ok: boolean
  error: string
  account: AccountStatus
}

/**
 * POST /api/source/probe：真实校验一次微信读书凭据。
 *
 * 与 `AccountStatus.is_logged_in` 的区别：后者只说明上游存了 Cookie，这个说明 Cookie
 * 还能不能用。只有 `expired` 应该弹扫码对话框；`token_invalid` / `unreachable` 弹了
 * 只会误导（问题不在二维码）。
 */
export interface AccountProbe {
  state: 'valid' | 'expired' | 'token_invalid' | 'unreachable' | 'unknown'
  message: string
  /** 探测用的公众号名（可能为空串） */
  feed_name: string
  /** ISO 时间；空串表示上游没给 */
  checked_at: string
}

/** GET /api/source/login-qr：成功给 image（data URL）+ uid；失败是 200 + {error, detail} */
export interface QrLoginImage {
  image: string
  uid: string
  error?: string
  detail?: string
}

/** GET /api/source/login-qr/status：`logic_code` 为 COOKIE_INVALID 表示扫码后 Cookie 校验失败 */
export interface QrLoginStatus {
  login_status: boolean
  message: string
  logic_code: string
}

/** POST /api/source/login-qr/over：后端在登录成功时会自动触发公众号采集 */
export interface QrLoginOver {
  login_status: boolean
  message: string
  sync_started: boolean
}

/** GET /api/source/sync：公众号采集的进度与自动采集状态 */
export interface FeedSyncState {
  running: boolean
  total: number
  triggered: number
  skipped: number
  failed: number
  started_at: string
  finished_at: string
  error: string
  /** 是否开启自动采集（0 间隔时后端返回 false） */
  auto_enabled?: boolean
  /** 自动采集间隔（分钟）；关闭时为 0 */
  auto_interval_minutes?: number
  /** 下一轮自动采集时间（UTC ISO 8601）；关闭或尚未排期时为空串 */
  next_run_at?: string
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

/** 任务明细里的状态码 → 中文标签与色调 */
export const JOB_ITEM_STATUS: Record<string, { label: string; tone: 'success' | 'danger' | 'warning' | 'info' }> =
  {
    done: { label: '成功', tone: 'success' },
    error: { label: '失败', tone: 'danger' },
    failed: { label: '失败', tone: 'danger' },
    skipped: { label: '跳过', tone: 'warning' },
    cancelled: { label: '已取消', tone: 'info' },
  }
