"""状态指示器组件。"""

from __future__ import annotations

from textual.widgets import Static


class StatusIndicator(Static):
    """状态指示器：显示连接测试结果。"""

    def set_result(self, label: str, success: bool, message: str) -> None:
        """设置状态指示器内容。"""
        icon = "●" if success else "○"
        self.update(f"{label}: {icon} {message}")
