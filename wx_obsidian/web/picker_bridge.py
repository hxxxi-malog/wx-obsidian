# 宿主目录选择器的桥接层：端点文件的读写 + 容器侧转发。
#
# 只用标准库：宿主 helper 用裸 Python 运行（不装项目依赖），而容器侧也要 import 它。
# 端点文件放在用户配置目录，协议见 docs/design-docs/web-ui/docker.md。
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wx_obsidian.paths import CONFIG_DIR

ENDPOINT_FILE = CONFIG_DIR / "host-picker.json"
TOKEN_HEADER = "X-WX-Picker-Token"
# 面板是模态的，用户可能思考一会儿；比宿主侧 300 秒的上限略长，让宿主的超时先报
_PICK_TIMEOUT_SECONDS = 320.0
_CANCEL_TIMEOUT_SECONDS = 15.0
_HEALTH_TIMEOUT_SECONDS = 0.5
# Docker Desktop 与 OrbStack 都从容器里把宿主暴露成这个名字；Linux 容器没有
_CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


class BridgeError(RuntimeError):
    """宿主目录选择器调用失败。"""


class BridgeUnavailableError(BridgeError):
    """没有安装宿主目录选择器，或它没有在运行。"""


class BridgeBusyError(BridgeError):
    """宿主目录选择器已经打开了一个面板。"""


@dataclass(frozen=True)
class Endpoint:
    """宿主选择器写下的端点信息。"""

    url: str
    port: int
    token: str
    pid: int = 0
    started_at: str = ""


def read_endpoint(path: Path | None = None) -> Endpoint | None:
    """读端点文件；缺失、损坏、字段不齐全都当成「没有宿主选择器」。"""
    target = path or ENDPOINT_FILE
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    url = str(payload.get("url") or "").strip()
    token = str(payload.get("token") or "").strip()
    try:
        port = int(payload.get("port") or 0)
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if not url or not token or port <= 0:
        return None
    return Endpoint(
        url=url,
        port=port,
        token=token,
        pid=pid,
        started_at=str(payload.get("started_at") or ""),
    )


def write_endpoint(endpoint: Endpoint, path: Path | None = None) -> Path:
    """原子写端点文件并收紧到 0600（容器通过挂载目录读同一份）。"""
    target = path or ENDPOINT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": endpoint.url,
        "port": endpoint.port,
        "token": endpoint.token,
        "pid": endpoint.pid,
        "started_at": endpoint.started_at,
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle_fd, temp_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise
    return target


def remove_endpoint(path: Path | None = None) -> None:
    """删掉端点文件；不存在时什么都不做。"""
    target = path or ENDPOINT_FILE
    with contextlib.suppress(FileNotFoundError):
        target.unlink()


def runs_in_container() -> bool:
    """当前进程是否在容器里（容器里要把回环地址换成 host.docker.internal）。"""
    return any(Path(marker).exists() for marker in _CONTAINER_MARKERS)


def service_url(endpoint: Endpoint) -> str:
    """宿主选择器的可访问地址：容器里走 host.docker.internal，宿主上走回环。"""
    if runs_in_container():
        return f"http://host.docker.internal:{endpoint.port}"
    return endpoint.url


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _post(
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    """POST 一个 JSON；连接失败归成 BridgeUnavailableError，HTTP 错误码原样返回。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header(TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeUnavailableError(f"连不上宿主目录选择器：{exc}") from exc


def _endpoint_or_raise(path: Path | None) -> Endpoint:
    endpoint = read_endpoint(path)
    if endpoint is None:
        raise BridgeUnavailableError(
            "没有检测到宿主目录选择器：在 Mac 上执行 ./scripts/host-picker.sh install 后重试"
        )
    return endpoint


def pick_via_bridge(prompt: str, endpoint_path: Path | None = None) -> str:
    """请宿主选择器弹面板，返回绝对路径；取消返回空串。"""
    endpoint = _endpoint_or_raise(endpoint_path)
    status, payload = _post(
        f"{service_url(endpoint)}/pick",
        endpoint.token,
        {"prompt": prompt},
        timeout=_PICK_TIMEOUT_SECONDS,
    )
    if status == 409 or payload.get("error") == "picker_busy":
        raise BridgeBusyError(str(payload.get("detail") or "宿主选择器已经打开了一个目录面板"))
    if status == 501:
        raise BridgeUnavailableError(str(payload.get("detail") or "宿主目录选择器不可用"))
    if status != 200:
        raise BridgeError(str(payload.get("detail") or f"宿主选择器返回 HTTP {status}"))
    path = str(payload.get("path") or "")
    if payload.get("cancelled") or not path:
        return ""
    return path


def cancel_via_bridge(endpoint_path: Path | None = None) -> bool:
    """取消宿主选择器上正在打开的面板；没有面板时返回 False。"""
    endpoint = _endpoint_or_raise(endpoint_path)
    status, payload = _post(
        f"{service_url(endpoint)}/cancel",
        endpoint.token,
        {},
        timeout=_CANCEL_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise BridgeError(str(payload.get("detail") or f"宿主选择器返回 HTTP {status}"))
    return bool(payload.get("cancelled"))


def bridge_state(endpoint_path: Path | None = None) -> tuple[bool, str]:
    """探测宿主选择器是否可用；不可用时给一句能直接展示给用户的说明。"""
    endpoint = read_endpoint(endpoint_path)
    if endpoint is None:
        return False, "没有检测到宿主目录选择器：在仓库目录执行 ./scripts/host-picker.sh install"
    try:
        with urllib.request.urlopen(
            f"{service_url(endpoint)}/health", timeout=_HEALTH_TIMEOUT_SECONDS
        ) as response:
            if response.status == 200:
                return True, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"宿主目录选择器没有响应：{exc}"
    return False, "宿主目录选择器的健康检查没有返回 200"
