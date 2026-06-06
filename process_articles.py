#!/usr/bin/env python3
"""公众号文章 → Obsidian 知识库处理器入口。

用法:
    python process_articles.py              # CLI 模式（默认）
    python process_articles.py --limit 5    # CLI 模式，限制处理数量
    python process_articles.py tui          # TUI 模式
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="公众号文章 → Obsidian 知识库处理器")
    subparsers = parser.add_subparsers(dest="command")

    # CLI 子命令（默认）
    cli_parser = subparsers.add_parser("cli", help="CLI 模式（默认）")
    cli_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 篇文章（0=不限制）")

    # TUI 子命令
    subparsers.add_parser("tui", help="TUI 模式（终端图形界面）")

    # 无子命令时默认 CLI 模式，兼容旧用法
    if len(sys.argv) > 1 and sys.argv[1] not in ("cli", "tui", "-h", "--help"):
        # 旧用法：python process_articles.py --limit 5
        # 插入 "cli" 子命令
        sys.argv.insert(1, "cli")

    args = parser.parse_args()

    if args.command == "tui":
        from wx_obsidian.tui.app import main as tui_main

        tui_main()
    else:
        # CLI 模式（默认）
        # 将 --limit 参数传递给 cli.main
        sys.argv = [sys.argv[0]]
        if hasattr(args, "limit") and args.limit:
            sys.argv.extend(["--limit", str(args.limit)])
        from wx_obsidian.cli import main as cli_main

        cli_main()


if __name__ == "__main__":
    main()
