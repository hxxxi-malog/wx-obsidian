"""安全测试：回环来源限制 + token 鉴权。

Web 服务能写用户硬盘、能读 API Key，这两道防线必须有测试兜住。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_web.helpers import make_fake_runtime
from wx_obsidian.web.api import create_app, is_loopback_host

TOKEN = "test-token-123"
AUTH_HEADERS = {"X-WX-Token": TOKEN}


def _client(*, allow_test_client: bool = True, healthy: bool = True) -> TestClient:
    """构造测试用客户端。

    TestClient 的 client host 是 "testclient"，故默认额外放行；
    `allow_test_client=False` 用于验证非回环来源确实被拒。
    """
    extra = frozenset({"testclient"}) if allow_test_client else frozenset()
    app = create_app(make_fake_runtime(healthy=healthy), token=TOKEN, extra_allowed_hosts=extra)
    return TestClient(app)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.5", True),
        ("::1", True),
        ("localhost", True),
        ("192.168.1.5", False),
        ("10.0.0.2", False),
        ("", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool) -> None:
    assert is_loopback_host(host) is expected


def test_ping_needs_no_token() -> None:
    with _client() as client:
        response = client.get("/api/ping")
    assert response.status_code == 200
    assert response.json()["app"] == "wx-obsidian"


def test_api_rejects_missing_token() -> None:
    with _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_api_rejects_wrong_token() -> None:
    with _client() as client:
        response = client.get("/api/health", headers={"X-WX-Token": "nope"})
    assert response.status_code == 401


def test_api_accepts_header_token() -> None:
    with _client() as client:
        response = client.get("/api/health", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_api_accepts_query_token() -> None:
    # SSE 的 EventSource 无法设自定义头，故 query token 必须受支持
    with _client() as client:
        response = client.get(f"/api/health?token={TOKEN}")
    assert response.status_code == 200


def test_non_loopback_client_is_rejected() -> None:
    with _client(allow_test_client=False) as client:
        response = client.get("/api/ping")
    assert response.status_code == 403
    assert response.json() == {"error": "forbidden"}
