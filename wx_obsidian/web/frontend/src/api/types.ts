/** 接口 DTO 类型（与 wx_obsidian/web/api.py 的响应结构一一对应）。 */

export interface ConnectionTestResult {
  success: boolean
  latency_ms: number
  message: string
  details?: Record<string, string> | null
}

export interface HealthPayload {
  source: ConnectionTestResult
  llm_api: ConnectionTestResult
  vision_api: ConnectionTestResult | null
  vault_path: ConnectionTestResult | null
  overall: boolean
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
  web: { port: number }
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
