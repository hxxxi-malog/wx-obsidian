#!/usr/bin/env python3
"""Web 服务端到端冒烟测试。

做法：在本地起一个 stub（假装 we-mp-rss + DeepSeek 的 OpenAI 兼容接口），
用**独立 HOME** 启动真实的 Web 服务（源码方式：`python -m wx_obsidian.web`）
→ 提交一次抓取 → 断言：

1. 服务能读到 `skills/` 与 `prompts/`——**真的生成一篇笔记**，不是只看能启动
2. 服务能读写 `~/.wx-obsidian/`（config.json / .env / processed.json）
3. 服务不依赖 `cwd`：换到临时目录、换掉 HOME 再跑
4. 日志落盘、token 被掩码，`POST /api/shutdown` 能让进程干净退出

**不花一分钱、不碰真实 vault、不碰真实 `~/.wx-obsidian`。**

用法::

    .venv/bin/python tests/e2e/smoke_web.py
    .venv/bin/python tests/e2e/smoke_web.py --keep      # 保留临时目录便于排查
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

NOTE_MARKER = "冒烟测试占位正文"
ARTICLE_TITLE = "冒烟测试文章：Web 服务能否生成笔记"
ACCOUNT_NAME = "冒烟测试号"
FIX_PROMPT_MARKER = "请修正上述问题"
# 上游短链里带 `~`（WeRead 对 `_` 的替换），落盘与接口都必须还原成 `_` 形式
ARTICLE_LINK = "https://mp.weixin.qq.com/s/smoke~test-article"
ARTICLE_LINK_OPENABLE = "https://mp.weixin.qq.com/s/smoke_test-article"


# ---------------------------------------------------------------------------
# stub 上游：we-mp-rss 的 /feed/*.json + DeepSeek 的 /chat/completions
# ---------------------------------------------------------------------------


def _feed_payload() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    body = "".join(
        f"<p>第 {i} 段：{NOTE_MARKER}，用于验证服务能走完取文 → 生成 → 落盘全链路。</p>"
        for i in range(1, 13)
    )
    return {
        "title": "冒烟测试订阅源",
        "name": ACCOUNT_NAME,
        "items": [
            {
                "id": "smoke-article-1",
                "title": ARTICLE_TITLE,
                "link": ARTICLE_LINK,
                "content": body,
                "updated": now,
                "channel_name": ACCOUNT_NAME,
            }
        ],
    }


def _article_list_payload() -> dict[str, Any]:
    """上游文章库列表（管理接口形态）：一行已抓取、一行带 `~` token 的新文章。"""
    return {
        "code": 0,
        "data": {
            "list": [
                {
                    "id": "smoke-article-1",
                    "title": ARTICLE_TITLE,
                    "url": ARTICLE_LINK,
                    "mp_name": ACCOUNT_NAME,
                    "mp_id": "MP_WXS_SMOKE",
                    "publish_time": int(datetime.now(UTC).timestamp()),
                    "pic_url": "",
                    "has_content": 1,
                },
                {
                    "id": "smoke-article-2",
                    "title": "带波浪号 token 的文章",
                    "url": "https://mp.weixin.qq.com/s/smoke~tilde",
                    "mp_name": ACCOUNT_NAME,
                    "mp_id": "MP_WXS_SMOKE",
                    "publish_time": int(datetime.now(UTC).timestamp()) - 3600,
                    "pic_url": "",
                    "has_content": 0,
                },
            ],
            "total": 2,
        },
    }


def _note_payload() -> dict[str, Any]:
    section = f"{NOTE_MARKER}。" * 12
    return {
        "category": "LLM",
        "sub_topic": "",
        "summary": f"本文是端到端自检用的结构化摘要，{NOTE_MARKER}。" * 4,
        "key_points": [
            f"**端到端**：{NOTE_MARKER}，验证服务能读到 skills 与 prompts。",
            f"**落盘**：{NOTE_MARKER}，验证 vault 与 processed.json 可写。",
            f"**退出**：{NOTE_MARKER}，验证 POST /api/shutdown 能干净收尾。",
        ],
        "concepts": [
            {
                "name": "端到端冒烟",
                "description": "用本地 stub 上游跑完整管线，验证资源加载与落盘。",
            }
        ],
        "tags": ["冒烟测试", "端到端"],
        "related_topics": [],
        "images": [],
        "body_sections": [
            {"heading": "一、背景", "content": section},
            {"heading": "二、验证方法", "content": section},
            {"heading": "三、结果与结论", "content": section},
        ],
    }


class _StubHandler(BaseHTTPRequestHandler):
    """同时冒充 we-mp-rss（读接口）与 DeepSeek（OpenAI 兼容写接口）。"""

    protocol_version = "HTTP/1.1"

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
        if self.path.startswith("/feed/"):
            self._send_json(_feed_payload())
            return
        if self.path.startswith("/api/v1/wx/articles"):
            self._send_json(_article_list_payload())
            return
        self._send_json({"error": "not_found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        if not self.path.endswith("/chat/completions"):
            self._send_json({"error": "not_found"}, 404)
            return

        try:
            request = json.loads(raw)
        except ValueError:
            request = {}
        prompt = " ".join(str(m.get("content", "")) for m in request.get("messages", []))
        if FIX_PROMPT_MARKER in prompt:
            # 「结构性格式修正」要的是纯 markdown，不是 JSON：原地返回当前版本
            content = _extract_current_markdown(prompt)
        else:
            content = json.dumps(_note_payload(), ensure_ascii=False)

        self._send_json(
            {
                "id": "smoke-completion",
                "object": "chat.completion",
                "model": "smoke-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 128, "completion_tokens": 256, "total_tokens": 384},
            }
        )

    def log_message(self, *args: Any) -> None:
        """静音访问日志。"""


def _extract_current_markdown(prompt: str) -> str:
    marker = "--- 当前 markdown ---"
    if marker not in prompt:
        return f"# {ARTICLE_TITLE}\n\n{NOTE_MARKER}。\n"
    tail = prompt.split(marker, 1)[1]
    return tail.split("--- 结束 ---", 1)[0].strip()


def _start_stub() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, int(server.server_address[1])


# ---------------------------------------------------------------------------
# 临时 HOME / 配置
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(home: Path, vault: Path, stub_port: int, web_port: int) -> Path:
    config_dir = home / ".wx-obsidian"
    config_dir.mkdir(parents=True, exist_ok=True)
    base_url = f"http://127.0.0.1:{stub_port}/v1"
    config = {
        "version": "1.0",
        "llm": {
            "active": "deepseek",
            "providers": {
                "deepseek": {
                    "base_url": base_url,
                    "model": "smoke-model",
                    "api_key": "smoke-key-not-a-secret",
                }
            },
            "max_tokens": 4096,
            "temperature": 0.7,
        },
        "vision": {"enabled": False, "active": "deepseek", "providers": {}},
        "obsidian": {"vault_path": str(vault), "articles_dir": "公众号文章"},
        "source": {
            "type": "we_mp_rss",
            "base_url": f"http://127.0.0.1:{stub_port}",
            "timeout": 15,
            "token": "",
        },
        "fetch": {"max_days": 7, "max_workers": 2, "feed_limit": 10},
        "web": {"port": web_port, "open_browser": False, "log_level": "INFO"},
    }
    path = config_dir / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    # .env 与 config 保持一致：两条取 Key 的路径都指向 stub
    (config_dir / ".env").write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=smoke-key-not-a-secret",
                f"DEEPSEEK_BASE_URL={base_url}",
                "MODEL_NAME=smoke-model",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _default_log_file(home: Path) -> Path:
    """复刻 `web.logging_setup.default_log_file()`：日志路径随平台不同。

    刻意在这里重算而不是把 `--log-file` 传进去——要验证的正是「默认路径」
    在独立 HOME 下确实落到了临时目录里。
    """
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / "wx-obsidian" / "app.log"
    return home / ".wx-obsidian" / "logs" / "app.log"


# ---------------------------------------------------------------------------
# 驱动 Web 服务
# ---------------------------------------------------------------------------


def _wait_ready(port: int, token_file: Path, proc: subprocess.Popen[str]) -> str:
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"服务提前退出（exit={proc.returncode}）")
        try:
            payload = requests.get(f"{base}/api/ping", timeout=1.0).json()
        except (requests.RequestException, ValueError):
            time.sleep(0.3)
            continue
        if payload.get("app") == "wx-obsidian" and token_file.is_file():
            return token_file.read_text(encoding="utf-8").strip()
        time.sleep(0.3)
    raise RuntimeError("服务 30 秒内未就绪")


def _fetch_once(port: int, token: str, timeout: float) -> dict[str, Any]:
    base = f"http://127.0.0.1:{port}"
    headers = {"X-WX-Token": token}
    response = requests.post(
        f"{base}/api/jobs", json={"kind": "fetch", "limit": 1}, headers=headers, timeout=10.0
    )
    response.raise_for_status()
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload: Any = requests.get(
            f"{base}/api/jobs/{job_id}", headers=headers, timeout=10.0
        ).json()
        if isinstance(payload, dict) and payload.get("state") in ("finished", "cancelled", "error"):
            return payload
        time.sleep(0.5)
    raise RuntimeError(f"任务 {job_id} 在 {timeout:.0f} 秒内未结束")


def _shutdown(port: int, token: str, proc: subprocess.Popen[str]) -> int:
    if proc.poll() is not None:
        return int(proc.returncode or 0)
    with contextlib.suppress(requests.RequestException):
        requests.post(
            f"http://127.0.0.1:{port}/api/shutdown",
            headers={"X-WX-Token": token},
            timeout=5.0,
        )
    try:
        return proc.wait(timeout=20.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------


def _check(condition: bool, label: str, detail: str = "") -> bool:
    print(f"  [{'OK ' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    return condition


def _report_notes(vault: Path) -> tuple[bool, str]:
    articles = vault / "公众号文章"
    notes = sorted(articles.rglob("*.md")) if articles.is_dir() else []
    real = [p for p in notes if not p.name.startswith("_") and "概念" not in p.parts]
    if not real:
        return False, f"未找到笔记（共 {len(notes)} 个 md）"

    note = real[0]
    content = note.read_text(encoding="utf-8")
    ok = (
        NOTE_MARKER in content
        and "一、背景" in content
        and f'url: "{ARTICLE_LINK_OPENABLE}"' in content
    )
    detail = f"{note.relative_to(vault)} ({len(content)} 字符，同目录共 {len(notes)} 个 md)"
    return ok, detail


def main() -> int:  # noqa: C901 - 冒烟脚本按步骤线性展开，拆函数反而更难读
    args = _parse_args()
    repo = Path(__file__).resolve().parent.parent.parent

    tmp = Path(tempfile.mkdtemp(prefix="wx-smoke-"))
    home = tmp / "home"
    home.mkdir(parents=True)
    vault = tmp / "Vault"
    vault.mkdir()

    stub, stub_port = _start_stub()
    web_port = _free_port()
    config_file = _write_config(home, vault, stub_port, web_port)
    config_before = config_file.read_text(encoding="utf-8")
    token_file = home / ".wx-obsidian" / "web.token"
    log_file = _default_log_file(home)
    cmd = [
        sys.executable,
        "-m",
        "wx_obsidian.web",
        "--port",
        str(web_port),
        "--no-open",
    ]
    proc: subprocess.Popen[str] | None = None
    token = ""
    failures = 0

    print(f"临时目录   : {tmp}")
    print(f"源码根     : {repo}")
    print(f"启动命令   : {' '.join(cmd)}")
    print(f"stub 上游  : http://127.0.0.1:{stub_port}（we-mp-rss + DeepSeek）")
    print(f"服务端口   : {web_port}")

    try:
        print("\n1) 独立 HOME + 临时 cwd 启动 Web 服务")
        # cwd 换到 tmp、HOME 换到临时目录：证明服务不依赖当前目录，也不碰真实配置
        proc = subprocess.Popen(
            cmd,
            env={**os.environ, "HOME": str(home), "PYTHONPATH": str(repo)},
            cwd=tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        token = _wait_ready(web_port, token_file, proc)
        failures += 0 if _check(bool(token), "服务就绪并写出 web.token") else 1

        print("\n2) 提交一次抓取（stub 上游，零 API 费用）")
        snapshot = _fetch_once(web_port, token, timeout=args.timeout)
        failures += 0 if _check(snapshot.get("state") == "finished", "任务终态 finished") else 1
        failures += (
            0
            if _check(
                int(snapshot.get("completed") or 0) >= 1,
                "至少处理 1 篇",
                f"completed={snapshot.get('completed')} total={snapshot.get('total')}",
            )
            else 1
        )

        print("\n3) 服务真的生成了笔记（skills/prompts 被读到）")
        ok, detail = _report_notes(vault)
        failures += 0 if _check(ok, "笔记内容正确", detail) else 1

        print("\n4) 能读写 ~/.wx-obsidian/")
        processed = json.loads((home / ".wx-obsidian" / "processed.json").read_text("utf-8"))
        done = [
            k for k, v in processed.items() if isinstance(v, dict) and v.get("status") == "done"
        ]
        failures += 0 if _check(bool(done), "processed.json 有 done 记录", f"{len(done)} 条") else 1
        # 管线发现新分类会写回 config.json —— 这是「能写」的直接证据，不只是「能读」
        config_after = config_file.read_text(encoding="utf-8")
        failures += (
            0
            if _check(
                config_after != config_before,
                "config.json 被写入",
                f"categories={json.loads(config_after).get('categories')}",
            )
            else 1
        )

        print("\n5) 最新文章接口读到数据源文章，并标出已入库")
        page = requests.get(
            f"http://127.0.0.1:{web_port}/api/source/articles",
            headers={"X-WX-Token": token},
            timeout=10.0,
        ).json()
        items = page.get("items") or []
        first = items[0] if items else {}
        failures += (
            0 if _check(page.get("total") == 2, "返回总数", f"total={page.get('total')}") else 1
        )
        failures += (
            0
            if _check(
                first.get("article_id") == "smoke_test-article"
                and first.get("url") == ARTICLE_LINK_OPENABLE,
                "文章 ID 归一化 + 原文链接",
                f"article_id={first.get('article_id')}",
            )
            else 1
        )
        failures += 0 if _check(first.get("processed") is True, "已抓取文章标记为已入库") else 1
        failures += (
            0
            if _check(
                first.get("account") == ACCOUNT_NAME and bool(first.get("published_at")),
                "公众号名与发布时间",
                f"account={first.get('account')}",
            )
            else 1
        )
        second = items[1] if len(items) > 1 else {}
        failures += (
            0
            if _check(
                second.get("article_id") == "smoke_tilde"
                and second.get("url") == "https://mp.weixin.qq.com/s/smoke_tilde",
                "上游 `~` token 还原成 `_`",
                f"article_id={second.get('article_id')}",
            )
            else 1
        )
        failures += (
            0
            if _check(
                page.get("token_configured") is False,
                "未配置访问令牌时如实返回 token_configured=false",
            )
            else 1
        )

        print("\n6) 日志落盘、掩码正确")
        log_text = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
        failures += 0 if _check(bool(log_text), "日志文件非空", str(log_file)) else 1
        failures += 0 if _check("已启动" in log_text, "日志含启动记录") else 1
        failures += 0 if _check(token not in log_text, "日志不含明文 token") else 1
        failures += 0 if _check("token=****" in log_text, "日志里的 token 已掩码") else 1
        failures += 0 if _check("uvicorn.error" in log_text, "uvicorn 日志经 root 落盘") else 1

        print("\n7) POST /api/shutdown 让进程干净退出")
        code = _shutdown(web_port, token, proc)
        failures += 0 if _check(code == 0, "退出码 0", f"exit={code}") else 1
        proc = None
    except (RuntimeError, requests.RequestException, OSError, ValueError) as exc:
        print(f"\n冒烟失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        if proc is not None and proc.stdout is not None:
            with proc.stdout:
                tail = proc.stdout.read()[-2000:]
            if tail.strip():
                print("--- 服务输出尾部 ---\n" + tail, file=sys.stderr)
        if log_file.is_file():
            print("--- 日志尾部 ---\n" + log_file.read_text("utf-8")[-2000:], file=sys.stderr)
        failures += 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        stub.shutdown()
        stub.server_close()
        if args.keep:
            print(f"\n临时目录已保留：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("=" * 60))
    if failures:
        print(f"冒烟失败：{failures} 项未通过")
        return 1
    print("冒烟通过：服务能独立启动、生成笔记、落盘日志并干净退出")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web 服务端到端冒烟测试")
    parser.add_argument("--timeout", type=float, default=180.0, help="抓取任务超时（秒）")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
