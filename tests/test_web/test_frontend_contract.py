"""前后端契约测试：前端用到的每一条 /api 路径都必须真实存在。

存在意义：前端把路径集中到 `frontend/src/lib/api/endpoints.ts` 之后，**后端改路由
不会再静默地让用户点到 404**——这里会直接红灯。同时防止路径字面量重新散落到视图里。

不依赖真实 runtime：用 tests/test_web/helpers.py 的 fake runtime 建 app，只读路由表。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_web.helpers import make_fake_runtime
from wx_obsidian.web.api import create_app

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "wx_obsidian" / "web" / "frontend" / "src"
ENDPOINTS_FILE = FRONTEND_SRC / "lib" / "api" / "endpoints.ts"

# 字符串字面量里的 /api 路径（模板串的反引号同样算）
_STRING_LITERAL = re.compile(r"""["'`](/api/[^"'`]*)["'`]""")
# 视图/组件里出现的路径字面量（应该只出现在 endpoints.ts）
_ANY_API_LITERAL = re.compile(r"""["'`](/api/)""")
_PARAM = re.compile(r"\{[^}]*\}")


def _normalize(path: str) -> str:
    """把路径参数统一成 {}，并把模板串里的查询串拼接截掉。

    - `/api/jobs/${seg(jobId)}/cancel` → `/api/jobs/{}/cancel`
    - `/api/articles${buildQuery({...})}` → `/api/articles`（含括号的插值是查询串）
    - `/api/jobs/{job_id}/events` → `/api/jobs/{}/events`
    """
    out: list[str] = []
    index = 0
    while index < len(path):
        if path.startswith("${", index):
            depth = 1
            cursor = index + 2
            while cursor < len(path) and depth:
                if path[cursor] == "{":
                    depth += 1
                elif path[cursor] == "}":
                    depth -= 1
                cursor += 1
            inner = path[index + 2 : cursor - 1]
            if "buildQuery" in inner:
                # 查询串由 buildQuery 拼在路径末尾：路径到此为止
                break
            out.append("{}")
            index = cursor
            continue
        out.append(path[index])
        index += 1

    text = _PARAM.sub("{}", "".join(out))
    return text.rstrip("/") or "/"


def _frontend_paths() -> set[str]:
    """endpoints.ts 里声明的全部路径（归一化后）。跳过注释行，避免文档里的示例路径被当成声明。"""
    content = ENDPOINTS_FILE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in content.splitlines() if not line.strip().startswith(("//", "*", "/*"))
    )
    return {_normalize(match) for match in _STRING_LITERAL.findall(code)}


def _backend_paths() -> set[str]:
    """FastAPI 路由表里的全部路径（归一化后）。"""
    app = create_app(make_fake_runtime(), token="test-token")
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str) and path.startswith("/api/"):
            paths.add(_normalize(path))
    return paths


@pytest.fixture(scope="module")
def frontend_paths() -> set[str]:
    assert ENDPOINTS_FILE.is_file(), f"缺少前端接口定义：{ENDPOINTS_FILE}"
    return _frontend_paths()


def test_frontend_endpoints_file_is_single_source_of_truth() -> None:
    """除 endpoints.ts 外，前端源码里不允许再出现 /api/ 字面量。"""
    offenders: list[str] = []
    for file in sorted(FRONTEND_SRC.rglob("*")):
        if file.suffix not in {".ts", ".vue", ".js"} or file == ENDPOINTS_FILE:
            continue
        text = file.read_text(encoding="utf-8")
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if _ANY_API_LITERAL.search(line):
                offenders.append(f"{file.relative_to(FRONTEND_SRC)}:{index}")
    assert not offenders, (
        "接口路径必须集中在 lib/api/endpoints.ts，以下位置出现了字面量：" + ", ".join(offenders)
    )


def test_every_frontend_path_exists_on_backend(frontend_paths: set[str]) -> None:
    """前端声明的每条路径都要能在 FastAPI 路由表里找到。"""
    backend = _backend_paths()
    missing = sorted(path for path in frontend_paths if path not in backend)
    assert not missing, f"前端引用了后端不存在的接口：{missing}"


def test_frontend_covers_the_critical_endpoints(frontend_paths: set[str]) -> None:
    """关键能力（抓取、SSE、级联删除、配置读写、探活）必须仍被前端声明。"""
    required = {
        "/api/health",
        "/api/stats",
        "/api/failed",
        "/api/jobs",
        "/api/jobs/{}/cancel",
        "/api/jobs/{}/events",
        "/api/articles",
        "/api/articles/{}",
        "/api/articles/{}/open",
        "/api/articles/retry",
        "/api/articles/bulk-delete",
        "/api/config",
        "/api/config/test",
        "/api/source/health",
        "/api/source/account",
        "/api/source/login-url",
        "/api/source/refresh",
        "/api/feeds",
        "/api/feeds/{}",
        "/api/scheduler/jobs",
        "/api/scheduler/jobs/{}/run-now",
        "/api/logs",
        "/api/logs/events",
        "/api/fs/list",
        "/api/fs/pick",
        "/api/shutdown",
    }
    missing = sorted(path for path in required if path not in frontend_paths)
    assert not missing, f"前端不再声明这些接口（回归？）：{missing}"
