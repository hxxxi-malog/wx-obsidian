import { describe, expect, it } from 'vitest'

import type { JobEvent, JobSnapshot } from '@/lib/api/types'
import { initialJobsState, reduceJobEvent, type JobsRuntimeState } from '@/stores/jobs'

function withJob(overrides: Partial<JobSnapshot> = {}): JobsRuntimeState {
  const job: JobSnapshot = {
    job_id: 'job-1',
    kind: 'fetch',
    state: 'running',
    started_at: '2026-10-12T00:00:00+08:00',
    finished_at: null,
    completed: 0,
    total: 0,
    last_title: '',
    stage: '',
    result: {},
    error: '',
    ...overrides,
  }
  return { ...initialJobsState(), job, connected: true }
}

function event(partial: Partial<JobEvent> & { type: string; seq: number }): JobEvent {
  return { ts: '2026-10-12T00:00:01+08:00', ...partial }
}

describe('reduceJobEvent：进度与阶段', () => {
  it('start 事件写日志，并在有 total 时给出篇数', () => {
    const next = reduceJobEvent(withJob(), event({ type: 'start', seq: 1, total: 7 }))
    expect(next.job?.total).toBe(7)
    expect(next.logLines.at(-1)?.text).toBe('开始处理 7 篇')
  })

  it('article 事件更新计数与当前标题', () => {
    const next = reduceJobEvent(
      withJob({ total: 7 }),
      event({ type: 'article', seq: 2, completed: 3, total: 7, title: '大模型推理优化' }),
    )
    expect(next.job?.completed).toBe(3)
    expect(next.job?.last_title).toBe('大模型推理优化')
    expect(next.logLines.at(-1)?.text).toBe('[3/7] 大模型推理优化')
  })

  it('stage begin 设置阶段，stage end 清空', () => {
    const begin = reduceJobEvent(
      withJob(),
      event({ type: 'stage', seq: 3, stage: 'knowledge_graph', state: 'begin' }),
    )
    expect(begin.job?.stage).toBe('knowledge_graph')
    expect(begin.logLines.at(-1)?.text).toBe('正在更新知识图谱…')

    const end = reduceJobEvent(begin, event({ type: 'stage', seq: 4, stage: 'knowledge_graph', state: 'end' }))
    expect(end.job?.stage).toBe('')
  })

  it('detail / empty / log 三种文本事件', () => {
    const detail = reduceJobEvent(withJob(), event({ type: 'detail', seq: 1, message: '图片描述 3/5' }))
    expect(detail.logLines.at(-1)?.text).toBe('图片描述 3/5')

    const empty = reduceJobEvent(withJob(), event({ type: 'empty', seq: 1, detail: '全部已处理' }))
    expect(empty.logLines.at(-1)?.text).toBe('没有新文章需要处理（全部已处理）')
    expect(empty.logLines.at(-1)?.level).toBe('WARNING')

    const log = reduceJobEvent(
      withJob(),
      event({ type: 'log', seq: 1, message: '上游 502', level: 'error' }),
    )
    expect(log.logLines.at(-1)?.level).toBe('ERROR')
  })
})

describe('reduceJobEvent：终态', () => {
  it('finished 落结果、断流、写摘要', () => {
    const next = reduceJobEvent(
      withJob(),
      event({
        type: 'finished',
        seq: 9,
        result: { done: 4, failed: 1, items: [{ article_id: 'a', title: 't', status: 'done', category: 'c', file_path: 'p', error: null }] },
      }),
    )
    expect(next.job?.state).toBe('finished')
    expect(next.job?.result.done).toBe(4)
    expect(next.items).toHaveLength(1)
    expect(next.connected).toBe(false)
    expect(next.logLines.at(-1)?.text).toBe('任务完成：成功 4 篇，失败 1 篇')
  })

  it('cancelled 与 error 各有独立文案', () => {
    const cancelled = reduceJobEvent(withJob(), event({ type: 'cancelled', seq: 5, result: { done: 2, failed: 0, items: [] } }))
    expect(cancelled.job?.state).toBe('cancelled')
    expect(cancelled.logLines.at(-1)?.text).toBe('任务已取消（完成 2 篇）')

    const failed = reduceJobEvent(withJob(), event({ type: 'error', seq: 6, error: 'DeepSeek 超时' }))
    expect(failed.job?.state).toBe('error')
    expect(failed.error).toBe('DeepSeek 超时')
    expect(failed.logLines.at(-1)?.level).toBe('ERROR')
  })

  it('error 无 message 时用兜底文案', () => {
    const failed = reduceJobEvent(withJob(), event({ type: 'error', seq: 6 }))
    expect(failed.error).toBe('任务失败')
  })
})

describe('reduceJobEvent：重放去重', () => {
  it('重复 seq 的帧被丢弃（SSE 重连会回放）', () => {
    const first = reduceJobEvent(withJob(), event({ type: 'article', seq: 4, completed: 1, title: 'A' }))
    const again = reduceJobEvent(first, event({ type: 'article', seq: 4, completed: 1, title: 'A' }))
    expect(again).toBe(first)
    expect(again.logLines).toHaveLength(1)
  })

  it('更大的 seq 正常推进', () => {
    const first = reduceJobEvent(withJob(), event({ type: 'article', seq: 4, completed: 1, title: 'A' }))
    const second = reduceJobEvent(first, event({ type: 'article', seq: 5, completed: 2, title: 'B' }))
    expect(second.logLines).toHaveLength(2)
    expect(second.lastSeq).toBe(5)
  })
})

describe('reduceJobEvent：边界', () => {
  it('没有 job 时也记录日志，不抛错', () => {
    const next = reduceJobEvent(initialJobsState(), event({ type: 'log', seq: 1, message: '独立日志' }))
    expect(next.job).toBeNull()
    expect(next.logLines).toHaveLength(1)
  })

  it('未知事件类型只推进 seq', () => {
    const next = reduceJobEvent(withJob(), event({ type: 'whatever', seq: 3 }))
    expect(next.lastSeq).toBe(3)
    expect(next.logLines).toHaveLength(0)
  })

  it('total 为 0 的 start 事件不覆盖已有 total', () => {
    const next = reduceJobEvent(withJob({ total: 5 }), event({ type: 'start', seq: 1, total: 0 }))
    expect(next.job?.total).toBe(5)
    expect(next.logLines.at(-1)?.text).toBe('任务已启动')
  })
})
