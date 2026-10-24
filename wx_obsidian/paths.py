from __future__ import annotations

from pathlib import Path

# 用户级配置目录。单独成模块是为了让宿主侧的目录选择器只用标准库 import：
# 它跑在裸 Python 上（不装项目依赖），而 config_manager 会拉进 requests / yaml。
CONFIG_DIR = Path.home() / ".wx-obsidian"
