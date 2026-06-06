#!/usr/bin/env python3
"""公众号文章 → Obsidian 知识库处理器入口。

用法:
    python process_articles.py              # CLI 模式（默认）
    python process_articles.py --limit 5    # CLI 模式，限制处理数量
    python process_articles.py tui          # TUI 模式
"""

import argparse
import sys


def _run_delete(query: str) -> None:
    """执行文章级联删除。"""
    from pathlib import Path

    from wx_obsidian.config import load_processed
    from wx_obsidian.config_manager import ConfigManager
    from wx_obsidian.output.cleanup import cascade_delete, find_article

    config_manager = ConfigManager()
    config_manager.load()

    vault_path_str = config_manager.get("obsidian.vault_path", "")
    if not vault_path_str:
        print("错误: 未配置 obsidian.vault_path")
        return

    vault_path = Path(vault_path_str)
    articles_dir_name = config_manager.get("obsidian.articles_dir", "公众号文章")
    articles_dir = vault_path / articles_dir_name

    processed = load_processed()
    if not processed:
        print("processed.json 为空，无需清理")
        return

    article_id = find_article(processed, query)
    if not article_id:
        print(f"未找到匹配的文章: {query}")
        return

    record = processed.get(article_id)
    title = record.get("title", "未知") if isinstance(record, dict) else "未知"
    print(f"找到文章: {title}  (ID: {article_id})")
    print()

    actions = cascade_delete(vault_path, articles_dir, processed, article_id)
    for action in actions:
        print(f"  {action}")

    print(f"\n已清理 {len(actions)} 项，可以重新生成该文章了")


def main() -> None:
    parser = argparse.ArgumentParser(description="公众号文章 → Obsidian 知识库处理器")
    subparsers = parser.add_subparsers(dest="command")

    # CLI 子命令（默认）
    cli_parser = subparsers.add_parser("cli", help="CLI 模式（默认）")
    cli_parser.add_argument("--limit", type=int, default=0, help="最多处理 N 篇文章（0=不限制）")

    # TUI 子命令
    subparsers.add_parser("tui", help="TUI 模式（终端图形界面）")

    # delete 子命令
    delete_parser = subparsers.add_parser("delete", help="级联删除文章及其关联数据")
    delete_parser.add_argument("query", help="文章标题或 ID")

    # 无子命令时默认 CLI 模式，兼容旧用法
    if len(sys.argv) > 1 and sys.argv[1] not in ("cli", "tui", "delete", "-h", "--help"):
        # 旧用法：python process_articles.py --limit 5
        # 插入 "cli" 子命令
        sys.argv.insert(1, "cli")

    args = parser.parse_args()

    if args.command == "delete":
        _run_delete(args.query)
    elif args.command == "tui":
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
