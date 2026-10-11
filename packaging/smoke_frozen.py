#!/usr/bin/env python3
"""冻结包端到端冒烟测试（设计文档 §5.6）。

验证只有真 `.app` 才能证明的三件事：

1. 冻结包能读到 `skills/` 与 `prompts/`——**真的生成一篇笔记**，不是只看能启动
2. 冻结包能读写 `~/.wx-obsidian/`（config.json / .env / processed.json）
3. `.app` 不依赖源码目录：把它拷到临时目录、用**独立 HOME**、`cwd` 也换掉再运行

做法：在本地起一个 stub（假装 we-mp-rss + DeepSeek 的 OpenAI 兼容接口），
用临时 HOME 启动冻结包 → 提交一次抓取 → 断言笔记落盘、processed.json 更新、
日志落盘且 token 被掩码 → 最后 `POST /api/shutdown` 验证进程能干净退出。

**不花一分钱、不碰真实 vault、不碰真实 `~/.wx-obsidian`。**

用法::

    .venv/bin/python packaging/smoke_frozen.py             # 用 dist/wx-obsidian.app
    .venv/bin/python packaging/smoke_frozen.py --app /Applications/wx-obsidian.app
    .venv/bin/python packaging/smoke_frozen.py --keep      # 保留临时目录便于排查
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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

NOTE_MARKER = "冒烟测试占位正文"
ARTICLE_TITLE = "冒烟测试文章：冻结包能否生成笔记"
ACCOUNT_NAME = "冒烟测试号"
FIX_PROMPT_MARKER = "请修正上述问题"


# ---------------------------------------------------------------------------
# stub 上游：we-mp-rss 的 /feed/*.json + DeepSeek 的 /chat/completions
# ---------------------------------------------------------------------------


def _feed_payload() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    body = "".join(
        f"<p>第 {i} 段：{NOTE_MARKER}，用于验证冻结包能走完取文 → 生成 → 落盘全链路。</p>"
        for i in range(1, 13)
    )
    return {
        "title": "冒烟测试订阅源",
        "name": ACCOUNT_NAME,
        "items": [
            {
                "id": "smoke-article-1",
                "title": ARTICLE_TITLE,
                "link": "https://mp.weixin.qq.com/s/smoke-test-article",
                "content": body,
                "updated": now,
                "channel_name": ACCOUNT_NAME,
            }
        ],
    }


def _note_payload() -> dict[str, Any]:
    section = f"{NOTE_MARKER}。" * 12
    return {
        "category": "LLM",
        "sub_topic": "",
        "summary": f"本文是打包自检用的结构化摘要，{NOTE_MARKER}。" * 4,
        "key_points": [
            f"**冻结包**：{NOTE_MARKER}，验证资源是否随包分发。",
            f"**端到端**：{NOTE_MARKER}，验证 skills 与 prompts 能被读到。",
            f"**落盘**：{NOTE_MARKER}，验证 vault 与 processed.json 可写。",
        ],
        "concepts": [
            {
                "name": "冻结包",
                "description": "冻结包是把解释器与依赖一起打进 .app 的产物。",
            }
        ],
        "tags": ["冒烟测试", "打包"],
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


# ---------------------------------------------------------------------------
# 驱动冻结包
# ---------------------------------------------------------------------------


def _wait_ready(binary: Path, port: int, token_file: Path, proc: subprocess.Popen[str]) -> str:
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"冻结包提前退出（exit={proc.returncode}）")
        try:
            payload = requests.get(f"{base}/api/ping", timeout=1.0).json()
        except (requests.RequestException, ValueError):
            time.sleep(0.3)
            continue
        if payload.get("app") == "wx-obsidian" and token_file.is_file():
            return token_file.read_text(encoding="utf-8").strip()
        time.sleep(0.3)
    raise RuntimeError("冻结包 30 秒内未就绪")


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
        snapshot = requests.get(f"{base}/api/jobs/{job_id}", headers=headers, timeout=10.0).json()
        if snapshot.get("state") in ("finished", "cancelled", "error"):
            return snapshot
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
    ok = NOTE_MARKER in content and "一、背景" in content
    detail = f"{note.relative_to(vault)} ({len(content)} 字符，同目录共 {len(notes)} 个 md)"
    return ok, detail


def main() -> int:  # noqa: C901 - 冒烟脚本按步骤线性展开，拆函数反而更难读
    args = _parse_args()
    repo = Path(__file__).resolve().parent.parent
    source_app = Path(args.app).expanduser()
    if not source_app.is_absolute():
        source_app = repo / source_app

    binary = source_app / "Contents" / "MacOS" / "wx-obsidian"
    if not binary.is_file():
        print(f"找不到冻结包可执行文件：{binary}", file=sys.stderr)
        print("先构建：./packaging/build_app.sh", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="wx-smoke-"))
    home = tmp / "home"
    home.mkdir(parents=True)
    vault = tmp / "Vault"
    vault.mkdir()
    # 拷出仓库再跑：证明 .app 不依赖源码目录
    app = tmp / "wx-obsidian.app"
    shutil.copytree(source_app, app, symlinks=True)

    stub, stub_port = _start_stub()
    web_port = _free_port()
    config_file = _write_config(home, vault, stub_port, web_port)
    config_before = config_file.read_text(encoding="utf-8")
    token_file = home / ".wx-obsidian" / "web.token"
    log_file = home / "Library" / "Logs" / "wx-obsidian" / "app.log"
    proc: subprocess.Popen[str] | None = None
    token = ""
    failures = 0

    print(f"临时目录   : {tmp}")
    print(f"冻结包     : {app}")
    print(f"stub 上游  : http://127.0.0.1:{stub_port}（we-mp-rss + DeepSeek）")
    print(f"服务端口   : {web_port}")

    try:
        print("\n1) 资源自检 --check-paths")
        check = subprocess.run(
            [str(app / "Contents" / "MacOS" / "wx-obsidian"), "--check-paths"],
            capture_output=True,
            text=True,
            cwd=tmp,
            timeout=120,
            check=False,
        )
        failures += 0 if _check(check.returncode == 0, "退出码 0") else 1
        failures += 0 if _check("MISSING" not in check.stdout, "资源路径全部就位") else 1
        failures += 0 if _check("jieba" in check.stdout, "依赖导入清单包含 jieba") else 1

        print("\n2) 独立 HOME 启动冻结包")
        proc = subprocess.Popen(
            [str(app / "Contents" / "MacOS" / "wx-obsidian"), "--port", str(web_port), "--no-open"],
            env={**os.environ, "HOME": str(home)},
            cwd=tmp,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        token = _wait_ready(app / "Contents" / "MacOS" / "wx-obsidian", web_port, token_file, proc)
        failures += 0 if _check(bool(token), "服务就绪并写出 web.token") else 1

        print("\n3) 提交一次抓取（stub 上游，零 API 费用）")
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

        print("\n4) 冻结包真的生成了笔记（skills/prompts 被读到）")
        ok, detail = _report_notes(vault)
        failures += 0 if _check(ok, "笔记内容正确", detail) else 1

        print("\n5) 能读写 ~/.wx-obsidian/")
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

        print("\n6) 日志落盘、掩码正确、没有假的 ERROR")
        log_text = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
        failures += 0 if _check(bool(log_text), "日志文件非空", str(log_file)) else 1
        failures += 0 if _check("已启动" in log_text, "日志含启动记录") else 1
        failures += 0 if _check(token not in log_text, "日志不含明文 token") else 1
        failures += 0 if _check("token=****" in log_text, "日志里的 token 已掩码") else 1
        failures += 0 if _check("uvicorn.error" in log_text, "uvicorn 日志经 root 落盘") else 1
        # uvicorn/jieba 的 INFO 若被打成 ERROR，真错误会被一排假 ERROR 淹没
        failures += (
            0 if _check("wx_obsidian.stderr" not in log_text, "无 stderr 被误标为 ERROR") else 1
        )

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
                print("--- 冻结包输出尾部 ---\n" + tail, file=sys.stderr)
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
    print("冒烟通过：冻结包能独立启动、生成笔记、落盘日志并干净退出")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="冻结包端到端冒烟测试")
    parser.add_argument("--app", default="dist/wx-obsidian.app", help=".app 路径")
    parser.add_argument("--timeout", type=float, default=180.0, help="抓取任务超时（秒）")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
