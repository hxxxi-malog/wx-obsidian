"""任务管理：管线任务全局单飞 + 事件缓冲，供 SSE 消费。

两条硬约束：

1. **单飞**：`processed.json` / MOC / 概念页都不能并发写，故同一时刻只允许一个管线任务。
2. **线程安全**：`orchestrator.fetch_and_process` 的 `on_progress` 由
   `asyncio.to_thread` 的**工作线程**调用，绝不能直接碰 asyncio 对象，也不能直接改
   任务状态（事件循环线程正在读）。所有回调统一经 `call_soon_threadsafe` 回到事件循环，
   再由 `_apply_progress` 单点更新状态并广播。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from wx_obsidian.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# 事件缓冲上限：客户端重连（Last-Event-ID）时需要能回放最近的事件
_BUFFER_SIZE = 2000
_RECENT_JOBS = 10


class JobBusyError(RuntimeError):
    """已有任务在跑（单飞约束）。"""


@dataclass
class JobEvent:
    """一条任务事件。"""

    seq: int
    type: str
    data: dict[str, Any]
    ts: str

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "ts": self.ts, **self.data}


@dataclass
class JobSnapshot:
    """任务状态快照。"""

    job_id: str
    kind: str
    state: str  # running / finished / cancelled / error
    started_at: str
    finished_at: str | None = None
    completed: int = 0
    total: int = 0
    last_title: str = ""
    stage: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "completed": self.completed,
            "total": self.total,
            "last_title": self.last_title,
            "stage": self.stage,
            "result": self.result,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class _Job:
    """单个任务的内部状态。"""

    def __init__(self, kind: str) -> None:
        self.job_id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.state = "running"
        self.started_at = _now()
        self.finished_at: str | None = None
        self.completed = 0
        self.total = 0
        self.last_title = ""
        self.stage = ""
        self.result: dict[str, Any] = {}
        self.error = ""
        self.cancel = threading.Event()
        self.events: deque[JobEvent] = deque(maxlen=_BUFFER_SIZE)
        self.seq = 0

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(
            job_id=self.job_id,
            kind=self.kind,
            state=self.state,
            started_at=self.started_at,
            finished_at=self.finished_at,
            completed=self.completed,
            total=self.total,
            last_title=self.last_title,
            stage=self.stage,
            result=self.result,
            error=self.error,
        )


class JobManager:
    """管线任务管理器（进程内单飞）。

    事件分发用「缓冲 + seq 回放」而不是 asyncio.Queue：
    客户端断开/重连时按 seq 回放天然幂等，不会漏事件也不会重复。
    """

    def __init__(self, orchestrator: Orchestrator) -> None:
        self._orchestrator = orchestrator
        self._current: _Job | None = None
        self._recent: deque[_Job] = deque(maxlen=_RECENT_JOBS)
        self._tasks: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- 查询 ---------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._current is not None

    def current(self) -> JobSnapshot | None:
        return self._current.snapshot() if self._current else None

    def recent(self) -> list[JobSnapshot]:
        return [job.snapshot() for job in reversed(self._recent)]

    def get(self, job_id: str) -> _Job | None:
        if self._current and self._current.job_id == job_id:
            return self._current
        for job in self._recent:
            if job.job_id == job_id:
                return job
        return None

    def snapshot(self, job_id: str) -> JobSnapshot | None:
        job = self.get(job_id)
        return job.snapshot() if job else None

    # -- 提交与取消 ---------------------------------------------------------

    async def submit(
        self,
        kind: str,
        *,
        limit: int = 0,
        force: bool = False,
        article_id: str = "",
        article_ids: list[str] | None = None,
    ) -> str:
        """提交任务，返回 job_id。

        Raises:
            JobBusyError: 已有任务在跑。
            ValueError: 未知任务类型。
        """
        if kind not in ("fetch", "retry"):
            raise ValueError(f"未知任务类型: {kind}")
        # 单飞判定：本方法在 check 与赋值之间没有 await，事件循环内不会被打断
        if self._current is not None:
            raise JobBusyError(f"已有任务在运行: {self._current.job_id}")

        self._loop = asyncio.get_running_loop()
        job = _Job(kind)
        self._current = job
        self._recent.append(job)
        self._emit(job, "start", total=0, kind=kind)

        runner = self._run_fetch if kind == "fetch" else self._run_retry
        task = asyncio.create_task(
            runner(job, limit=limit, force=force, article_id=article_id, article_ids=article_ids)
        )
        self._tasks.add(task)  # 持强引用，防止运行中被 GC
        task.add_done_callback(self._tasks.discard)
        return job.job_id

    def cancel(self, job_id: str) -> bool:
        """请求取消：置位取消令牌，尚未开始的文章会被跳过（在跑的文章跑完）。"""
        job = self.get(job_id)
        if job is None or job.state != "running":
            return False
        job.cancel.set()
        self._emit_threadsafe(job, "log", message="已请求取消，等待当前文章处理完成")
        return True

    # -- 事件 ---------------------------------------------------------------

    def _emit(self, job: _Job, type_: str, **data: Any) -> None:
        """写事件到缓冲。必须在事件循环线程内调用（保证 seq 递增不串）。"""
        job.seq += 1
        job.events.append(JobEvent(seq=job.seq, type=type_, data=data, ts=_now()))

    def _emit_data(self, job: _Job, type_: str, data: dict[str, Any]) -> None:
        """`_emit` 的位置参数包装（供 call_soon_threadsafe 使用）。"""
        self._emit(job, type_, **data)

    def _emit_threadsafe(self, job: _Job, type_: str, **data: Any) -> None:
        """供工作线程调用的事件入口。

        注意 `loop.call_soon_threadsafe` 只接受位置参数（不支持 **kwargs），
        故事件负载整体作为一个 dict 传过去。
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            # 事件循环已退出（服务正在关闭）：只留缓冲，不再唤醒
            job.seq += 1
            job.events.append(JobEvent(seq=job.seq, type=type_, data=data, ts=_now()))
            return
        loop.call_soon_threadsafe(self._emit_data, job, type_, data)

    def _make_progress(self, job: _Job) -> Callable[[str, int, int], None]:
        """把 orchestrator 的哨兵式进度回调送回事件循环处理。"""

        def on_progress(title: str, completed: int, total: int) -> None:
            loop = self._loop
            if loop is None or loop.is_closed():
                return
            loop.call_soon_threadsafe(self._apply_progress, job, title, completed, total)

        return on_progress

    def _apply_progress(self, job: _Job, title: str, completed: int, total: int) -> None:
        """在事件循环线程内更新状态并广播（唯一的状态写入口）。"""
        if title == "_start":
            job.total = total
            self._emit(job, "start", total=total, kind=job.kind)
        elif title.startswith("_empty:"):
            self._emit(job, "empty", detail=title[len("_empty:") :])
        elif title == "_done":
            return
        elif title in ("_kg", "_related"):
            job.stage = "knowledge_graph" if title == "_kg" else "related"
            self._emit(job, "stage", stage=job.stage, state="begin")
        elif title in ("_kg_done", "_related_done"):
            stage = "knowledge_graph" if title == "_kg_done" else "related"
            job.stage = ""
            self._emit(job, "stage", stage=stage, state="end")
        else:
            # 非哨兵标题有两种：真正处理完一篇，或写入阶段的明细
            # （"更新分类: X" / "生成概念: Y"）。用完成数是否推进来区分，
            # 否则前端会把 6 个概念页误报成 6 篇文章。
            advanced = completed > job.completed
            if total:
                job.total = total
            job.completed = max(job.completed, completed)
            if advanced:
                job.last_title = title
                self._emit(job, "article", title=title, completed=job.completed, total=job.total)
            else:
                self._emit(job, "detail", message=title)

    async def events_after(self, job_id: str, last_seq: int) -> list[JobEvent]:
        """取 seq 大于 last_seq 的事件（断线重连回放）。"""
        job = self.get(job_id)
        if job is None:
            return []
        return [event for event in list(job.events) if event.seq > last_seq]

    # -- 任务实现 -----------------------------------------------------------

    async def _run_fetch(
        self,
        job: _Job,
        *,
        limit: int,
        force: bool,
        article_id: str,
        article_ids: list[str] | None,
    ) -> None:
        try:
            results = await self._orchestrator.fetch_and_process(
                limit=limit,
                on_progress=self._make_progress(job),
                force=force,
                article_id=article_id,
                cancel=job.cancel,
            )
        except Exception as exc:  # 单个任务失败不能拖垮服务
            logger.exception("抓取任务失败: %s", job.job_id)
            self._finish(job, state="error", error=f"{type(exc).__name__}: {exc}")
            return
        self._finish_with_results(job, results)

    async def _run_retry(
        self,
        job: _Job,
        *,
        limit: int,
        force: bool,
        article_id: str,
        article_ids: list[str] | None,
    ) -> None:
        try:
            results = await self._orchestrator.retry_failed(article_ids or [])
        except Exception as exc:
            logger.exception("重试任务失败: %s", job.job_id)
            self._finish(job, state="error", error=f"{type(exc).__name__}: {exc}")
            return
        self._finish_with_results(job, results)

    def _finish_with_results(self, job: _Job, results: list[Any]) -> None:
        items = [
            {
                "article_id": result.article_id,
                "title": result.title,
                "status": result.status,
                "category": result.category,
                "file_path": result.file_path,
                "error": result.error,
            }
            for result in results
        ]
        done = sum(1 for item in items if item["status"] == "done")
        failed = sum(1 for item in items if item["status"] in ("error", "failed", "skipped"))
        cancelled = sum(1 for item in items if item["status"] == "cancelled")

        self._finish(
            job,
            state="cancelled" if job.cancel.is_set() else "finished",
            result={
                "done": done,
                "failed": failed,
                "cancelled": cancelled,
                "total": len(items),
                "items": items,
            },
        )

    def _finish(
        self,
        job: _Job,
        *,
        state: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        job.state = state
        job.finished_at = _now()
        if result is not None:
            job.result = result
        if error:
            job.error = error
        self._current = None
        payload: dict[str, Any] = {"error": error} if error else {"result": job.result}
        self._emit_threadsafe(job, state, **payload)
