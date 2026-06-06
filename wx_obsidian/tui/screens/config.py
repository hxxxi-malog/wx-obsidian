"""配置管理屏幕：API 密钥、路径、抓取天数等配置。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Static

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

            yield Static("\n  LLM API (DeepSeek)", id="llm-label")
            yield Static("  API Key:", id="llm-key-label")
            yield Input(placeholder="sk-...", id="llm-key-input", password=True)
            yield Static("  Base URL:", id="llm-url-label")
            yield Input(placeholder="https://api.deepseek.com", id="llm-url-input")
            yield Static("  模型:", id="llm-model-label")
            yield Input(placeholder="deepseek-chat", id="llm-model-input")
            yield StatusIndicator("DeepSeek", id="llm-status")
            yield Button("测试连通性", id="test-llm-btn", variant="primary")

            yield Static("\n  Vision API (可选)", id="vision-label")
            yield Static("  API Key:", id="vision-key-label")
            yield Input(placeholder="留空则禁用多模态", id="vision-key-input", password=True)
            yield Static("  Base URL:", id="vision-url-label")
            yield Input(
                placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1",
                id="vision-url-input",
            )
            yield Static("  模型:", id="vision-model-label")
            yield Input(placeholder="qwen-vl-plus", id="vision-model-input")
            yield StatusIndicator("Vision", id="vision-status")
            yield Button("测试连通性", id="test-vision-btn", variant="primary")

            yield Static("\n  WeWe RSS", id="wewe-label")
            yield Static("  服务地址:", id="wewe-url-label")
            yield Input(placeholder="http://localhost:4000", id="wewe-url-input")
            yield Static("  授权码:", id="wewe-auth-label")
            yield Input(placeholder="auth code", id="wewe-auth-input")
            yield StatusIndicator("WeWe RSS", id="wewe-status")
            yield Button("测试连通性", id="test-wewe-btn", variant="primary")

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

        # LLM
        self.query_one("#llm-key-input", Input).value = cm.get_env("DEEPSEEK_API_KEY")
        self.query_one("#llm-url-input", Input).value = cm.get("llm.base_url", "")
        self.query_one("#llm-model-input", Input).value = cm.get("llm.model", "")

        # Vision
        self.query_one("#vision-key-input", Input).value = cm.get_env("VISION_API_KEY")
        self.query_one("#vision-url-input", Input).value = cm.get("vision.base_url", "")
        self.query_one("#vision-model-input", Input).value = cm.get("vision.model", "")

        # WeWe RSS
        self.query_one("#wewe-url-input", Input).value = cm.get("wewe_rss.base_url", "")
        self.query_one("#wewe-auth-input", Input).value = cm.get_env("AUTH_CODE")

        # 其他
        self.query_one("#vault-input", Input).value = cm.get("obsidian.vault_path", "")
        self.query_one("#max-days-input", Input).value = str(cm.get("fetch.max_days", 7))
        self.query_one("#max-workers-input", Input).value = str(cm.get("fetch.max_workers", 5))

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
        elif btn_id == "test-wewe-btn":
            asyncio.create_task(
                self._test_connection(
                    app.config_manager.test_wewe_rss_connection, "#wewe-status", "WeWe RSS"
                )
            )
        elif btn_id == "save-btn":
            cm = app.config_manager

            # LLM
            llm_key = self.query_one("#llm-key-input", Input).value.strip()
            if llm_key:
                cm.set_env("DEEPSEEK_API_KEY", llm_key)
            llm_url = self.query_one("#llm-url-input", Input).value.strip()
            if llm_url:
                cm.set("llm.base_url", llm_url)
            llm_model = self.query_one("#llm-model-input", Input).value.strip()
            if llm_model:
                cm.set("llm.model", llm_model)

            # Vision
            vision_key = self.query_one("#vision-key-input", Input).value.strip()
            cm.set_env("VISION_API_KEY", vision_key)
            vision_url = self.query_one("#vision-url-input", Input).value.strip()
            if vision_url:
                cm.set("vision.base_url", vision_url)
            vision_model = self.query_one("#vision-model-input", Input).value.strip()
            if vision_model:
                cm.set("vision.model", vision_model)

            # WeWe RSS
            wewe_url = self.query_one("#wewe-url-input", Input).value.strip()
            if wewe_url:
                cm.set("wewe_rss.base_url", wewe_url)
            wewe_auth = self.query_one("#wewe-auth-input", Input).value.strip()
            if wewe_auth:
                cm.set_env("AUTH_CODE", wewe_auth)

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

    async def _test_connection(self, test_func: object, status_id: str, label: str) -> None:
        """异步测试连通性。"""
        result: ConnectionTestResult = await asyncio.to_thread(test_func)  # type: ignore[arg-type]
        if not self.is_current:
            return
        self.query_one(status_id, StatusIndicator).set_result(label, result.success, result.message)
