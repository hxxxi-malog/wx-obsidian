"""CLI 入口：解析参数，调用 orchestrator 编排完整流程。"""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="公众号文章 → Obsidian 知识库处理器")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 篇文章（0=不限制）")
    return parser.parse_args()


def main() -> None:
    """主流程：拉取文章 → 总结 → 写入 Obsidian。通过 orchestrator 编排。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = _parse_args()

    from wx_obsidian.config_manager import ConfigManager
    from wx_obsidian.orchestrator import Orchestrator
    from wx_obsidian.wewe_rss import WeWeRSSClient

    config_manager = ConfigManager()
    config_manager.load()

    wewe_url = config_manager.get("wewe_rss.base_url", "http://localhost:4000")
    auth_code = config_manager.get("wewe_rss.auth_code", "")
    wewe_rss = WeWeRSSClient(wewe_url, auth_code)
    orchestrator = Orchestrator(config_manager, wewe_rss)

    import asyncio

    results = asyncio.run(orchestrator.fetch_and_process(limit=args.limit))

    done_count = sum(1 for r in results if r.status == "done")
    failed = [r for r in results if r.status in ("error", "skipped", "failed")]
    print(f"\n处理完成！共处理 {done_count}/{len(results)} 篇文章")
    if failed:
        print(f"\n失败 {len(failed)} 篇:")
        for r in failed:
            err = r.error or "未知原因"
            print(f"  - {r.title[:50]}: {err}")
