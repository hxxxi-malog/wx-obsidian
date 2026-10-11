#!/usr/bin/env python3
"""冻结包入口：只暴露 Web 模式。

刻意不复用 process_articles.py —— 那个入口还会拉进 TUI/delete 分支，
而 TUI 与 textual 都不进包。
"""

import sys

from wx_obsidian.web.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
