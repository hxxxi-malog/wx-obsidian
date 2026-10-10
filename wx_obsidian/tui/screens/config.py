"""配置管理屏幕：API 密钥、路径、抓取天数等配置。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Static

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.models import ConnectionTestResult
from wx_obsidian.tui.widgets.status import StatusIndicator


class DirectoryPickerScreen(Screen[Path]):
    """目录选择器弹窗。"""

    CSS = """
    DirectoryPickerScreen {
        align: center middle;
    }
    #picker-container {
        width: 80%;
        height: 80%;
        border: solid $primary;
        background: $surface;
    }
    #picker-title {
        padding: 1 2;
        text-style: bold;
    }
    #picker-tree {
        height: 1fr;
        border: solid $secondary;
        margin: 0 1 1 1;
    }
    #picker-hint {
        padding: 0 2 1 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("backspace", "go_parent", "上级目录"),
    ]

    def __init__(self, start_path: str = "") -> None:
        super().__init__()
        self._current_path = Path(start_path) if start_path else Path.home()

    def compose(self) -> ComposeResult:
        with Static(id="picker-container"):
            yield Static("选择知识库文件夹", id="picker-title")
            yield Static(str(self._current_path), id="picker-current-path")
            yield DirectoryTree(str(self._current_path), id="picker-tree")
            yield Static("Backspace 上级目录 | Enter 选择 | Escape 取消", id="picker-hint")

    def action_go_parent(self) -> None:
        parent = self._current_path.parent
        if parent == self._current_path:
            return
        self._current_path = parent
        tree = self.query_one("#picker-tree", DirectoryTree)
        tree.path = str(parent)
        self.query_one("#picker-current-path", Static).update(str(parent))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.dismiss(Path(str(event.path)))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfigScreen(Screen[None]):
    """配置管理。"""

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("\n  配置管理\n", id="page-title")

            yield Static("\n  LLM API（语言模型渠道）", id="llm-label")
            yield Static("  生效渠道:", id="llm-active-label")
            yield Input(placeholder="deepseek", id="llm-active-input")
            yield Static("  API Key:", id="llm-key-label")
            yield Input(placeholder="sk-...", id="llm-key-input", password=True)
            yield Static("  Base URL (当前渠道):", id="llm-url-label")
            yield Input(placeholder="https://api.deepseek.com", id="llm-url-input")
            yield Static("  模型 (当前渠道):", id="llm-model-label")
            yield Input(placeholder="deepseek-v4-pro", id="llm-model-input")
            yield StatusIndicator("LLM", id="llm-status")
            yield Button("测试连通性", id="test-llm-btn", variant="primary")

            yield Static("\n  Vision API（视觉模型渠道）", id="vision-label")
            yield Static("  生效渠道:", id="vision-active-label")
            yield Input(placeholder="deepseek", id="vision-active-input")
            yield Static("  API Key (留空则用渠道声明的环境变量):", id="vision-key-label")
            yield Input(
                placeholder="留空则用 DEEPSEEK_API_KEY", id="vision-key-input", password=True
            )
            yield Static("  Base URL (当前渠道):", id="vision-url-label")
            yield Input(placeholder="https://api.deepseek.com", id="vision-url-input")
            yield Static("  模型 (当前渠道，deepseek-flash 支持图像):", id="vision-model-label")
            yield Input(placeholder="deepseek-flash", id="vision-model-input")
            yield StatusIndicator("Vision", id="vision-status")
            yield Button("测试连通性", id="test-vision-btn", variant="primary")

            yield Static("\n  文章数据源", id="source-label")
            yield Static("  类型 (we_mp_rss / wewe):", id="source-type-label")
            yield Input(placeholder="we_mp_rss", id="source-type-input")
            yield Static("  服务地址:", id="source-url-label")
            yield Input(placeholder="http://localhost:8001", id="source-url-input")
            yield Static("  访问令牌 (we-mp-rss 管理接口, 可选):", id="source-token-label")
            yield Input(placeholder="Bearer token", id="source-token-input")
            yield Static("  旧源授权码 (wewe 回退用):", id="wewe-auth-label")
            yield Input(placeholder="auth code", id="wewe-auth-input")
            yield StatusIndicator("数据源", id="source-status")
            yield Button("测试连通性", id="test-source-btn", variant="primary")

            yield Static("\n  知识库路径", id="vault-label")
            yield Input(placeholder="/path/to/obsidian/vault", id="vault-input")
            yield Button("选择文件夹", id="pick-vault-btn", variant="primary")

            yield Static("\n  抓取设置", id="fetch-label")
            yield Static("  最大抓取天数:", id="max-days-label")
            yield Input(placeholder="7", id="max-days-input")
            yield Static("  最大并行数:", id="max-workers-label")
            yield Input(placeholder="5", id="max-workers-input")

            yield Button("保存配置", id="save-btn", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        cm = app.config_manager

        # LLM（url/model 展示的是当前生效渠道的值）
        llm = cm.resolve_llm_channel()
        self.query_one("#llm-active-input", Input).value = str(cm.get("llm.active", ""))
        self.query_one("#llm-key-input", Input).value = cm.get_env("DEEPSEEK_API_KEY")
        self.query_one("#llm-url-input", Input).value = llm["base_url"]
        self.query_one("#llm-model-input", Input).value = llm["model"]

        # Vision
        vis = cm.resolve_vision_channel()
        self.query_one("#vision-active-input", Input).value = str(cm.get("vision.active", ""))
        self.query_one("#vision-key-input", Input).value = cm.get_env("VISION_API_KEY")
        self.query_one("#vision-url-input", Input).value = vis["base_url"]
        self.query_one("#vision-model-input", Input).value = vis["model"]

        # 文章数据源
        self.query_one("#source-type-input", Input).value = cm.get("source.type", "")
        self.query_one("#source-url-input", Input).value = cm.get("source.base_url", "")
        self.query_one("#source-token-input", Input).value = cm.get("source.token", "")
        self.query_one("#wewe-auth-input", Input).value = cm.get("wewe_rss.auth_code", "")

        # 其他
        self.query_one("#vault-input", Input).value = cm.get("obsidian.vault_path", "")
        self.query_one("#max-days-input", Input).value = str(cm.get("fetch.max_days", 7))
        self.query_one("#max-workers-input", Input).value = str(cm.get("fetch.max_workers", 5))

    def _apply_channel(
        self,
        cm: ConfigManager,
        section: str,
        active_selector: str,
        url_selector: str,
        model_selector: str,
    ) -> str | None:
        """切换某段落的生效渠道，并把 url/model 写回该渠道。

        Returns:
            生效渠道名；渠道不存在时返回 None（调用方应中止保存）。
        """
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        name = self.query_one(active_selector, Input).value.strip()
        if not name:
            return ""

        if not cm.set_active_channel(section, name):
            available = ", ".join(cm.channel_names(section)) or "无"
            app.show_notification(
                f"{section} 渠道「{name}」不存在（可用：{available}）", severity="error"
            )
            return None

        url = self.query_one(url_selector, Input).value.strip()
        if url:
            cm.set(f"{section}.providers.{name}.base_url", url)
        model = self.query_one(model_selector, Input).value.strip()
        if model:
            cm.set(f"{section}.providers.{name}.model", model)
        return name

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        btn_id = event.button.id

        if btn_id == "pick-vault-btn":
            current = self.query_one("#vault-input", Input).value.strip()
            self.app.push_screen(
                DirectoryPickerScreen(current),
                callback=self._on_vault_picked,
            )
        elif btn_id == "test-llm-btn":
            asyncio.create_task(
                self._test_connection(
                    app.config_manager.test_llm_connection, "#llm-status", "DeepSeek"
                )
            )
        elif btn_id == "test-vision-btn":
            asyncio.create_task(
                self._test_connection(
                    app.config_manager.test_vision_connection, "#vision-status", "Vision"
                )
            )
        elif btn_id == "test-source-btn":
            asyncio.create_task(
                self._test_connection(
                    app.config_manager.test_source_connection, "#source-status", "数据源"
                )
            )
        elif btn_id == "save-btn":
            cm = app.config_manager

            # LLM：切换生效渠道；url/model 写回「该渠道」的配置
            llm_key = self.query_one("#llm-key-input", Input).value.strip()
            if llm_key:
                cm.set_env("DEEPSEEK_API_KEY", llm_key)
            llm_active = self._apply_channel(
                cm, "llm", "#llm-active-input", "#llm-url-input", "#llm-model-input"
            )
            if llm_active is None:
                return

            # Vision
            vision_key = self.query_one("#vision-key-input", Input).value.strip()
            cm.set_env("VISION_API_KEY", vision_key)
            if (
                self._apply_channel(
                    cm, "vision", "#vision-active-input", "#vision-url-input", "#vision-model-input"
                )
                is None
            ):
                return

            # 文章数据源
            source_type = self.query_one("#source-type-input", Input).value.strip()
            if source_type:
                cm.set("source.type", source_type)
            source_url = self.query_one("#source-url-input", Input).value.strip()
            if source_url:
                cm.set("source.base_url", source_url)
            source_token = self.query_one("#source-token-input", Input).value.strip()
            if source_token:
                cm.set("source.token", source_token)
            wewe_auth = self.query_one("#wewe-auth-input", Input).value.strip()
            if wewe_auth:
                cm.set("wewe_rss.auth_code", wewe_auth)

            # 其他
            path = self.query_one("#vault-input", Input).value.strip()
            if path:
                cm.set("obsidian.vault_path", path)
            max_days = self.query_one("#max-days-input", Input).value.strip()
            if max_days.isdigit():
                cm.set("fetch.max_days", int(max_days))
            max_workers = self.query_one("#max-workers-input", Input).value.strip()
            if max_workers.isdigit():
                cm.set("fetch.max_workers", int(max_workers))

            cm.save()
            app.show_notification("配置已保存")

    def _on_vault_picked(self, path: Path | None) -> None:
        if path:
            self.query_one("#vault-input", Input).value = str(path)

    async def _test_connection(
        self, test_func: Callable[[], ConnectionTestResult], status_id: str, label: str
    ) -> None:
        """异步测试连通性。"""
        result: ConnectionTestResult = await asyncio.to_thread(test_func)
        if not self.is_current:
            return
        self.query_one(status_id, StatusIndicator).set_result(label, result.success, result.message)
