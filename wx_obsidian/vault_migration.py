"""Vault 迁移：换 vault 路径时同步修正 processed.json 里的绝对路径。

**为什么需要**：`processed.json` 的 `file` 字段是绝对路径。用户搬动 vault 后：

- 增量去重**不受影响**（`orchestrator.py:733` 明确「去重以文章 ID 为准，不检查文件是否存在」）
- 但**级联删除**（`output/cleanup.py`）按 `file` 找文件 → 静默失效
- 「文件已存在」防御检查（`orchestrator.py:828`）失效 → 失败结果可能覆盖 done 记录

因此搬完 vault 应执行一次本迁移：改配置 + 重写路径前缀，两步都原子落盘。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from wx_obsidian.config import load_processed, save_processed
from wx_obsidian.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_SAMPLE_LIMIT = 3


@dataclass
class VaultMigrationReport:
    """迁移结果。"""

    old_vault: str
    new_vault: str
    articles_dir: str
    rewritten: int = 0
    already_ok: int = 0
    outside: int = 0
    missing_after: int = 0
    samples: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        """人类可读摘要。"""
        head = "（dry-run，未写入）" if self.dry_run else ""
        lines = [
            f"vault 路径 : {self.old_vault} → {self.new_vault}{head}",
            f"文章目录   : {self.articles_dir}",
            f"重写记录   : {self.rewritten}",
            f"本就正确   : {self.already_ok}",
            f"不在旧路径下（未改动）: {self.outside}",
        ]
        if self.missing_after:
            lines.append(f"⚠️ 重写后仍找不到文件的记录: {self.missing_after}")
        if self.samples:
            lines.append("样例：")
            lines.extend(f"  - {s}" for s in self.samples)
        return "\n".join(lines)


def migrate_vault_path(
    new_vault: Path,
    *,
    config_manager: ConfigManager | None = None,
    processed_file: Path | None = None,
    dry_run: bool = False,
) -> VaultMigrationReport:
    """把 vault 从旧路径迁到新路径。

    Args:
        new_vault: vault 新根目录（文章目录是其下的 `articles_dir` 子目录）。
        config_manager: 可注入的配置管理器（测试用）。
        processed_file: 可注入的 processed.json 路径（测试用）。
        dry_run: 只统计不落盘。

    Returns:
        迁移报告。

    Raises:
        ValueError: 未配置旧 vault 路径，或新路径不是绝对路径。
        FileNotFoundError: 新路径下找不到文章目录（防止把配置改到错误位置）。
    """
    cm = config_manager or ConfigManager()
    cm.load()

    old_vault = str(cm.get("obsidian.vault_path", "") or "")
    if not old_vault:
        raise ValueError("配置里没有 obsidian.vault_path，无法判断迁移起点")

    articles_dir = str(cm.get("obsidian.articles_dir", "公众号文章") or "公众号文章")
    new_vault_path = Path(new_vault).expanduser()
    if not new_vault_path.is_absolute():
        raise ValueError(f"新 vault 路径必须是绝对路径: {new_vault}")

    new_root = new_vault_path / articles_dir
    if not new_root.is_dir():
        raise FileNotFoundError(
            f"新路径下找不到文章目录: {new_root}\n请确认 vault 根目录是否正确（应存在 {new_root}）"
        )

    old_root = Path(old_vault).expanduser() / articles_dir
    prefix = str(old_root) + os.sep

    processed = load_processed(processed_file)
    report = VaultMigrationReport(
        old_vault=str(Path(old_vault).expanduser()),
        new_vault=str(new_vault_path),
        articles_dir=articles_dir,
        dry_run=dry_run,
    )

    for record in processed.values():
        if not isinstance(record, dict):
            continue  # 历史遗留的非 dict 记录（如字符串），原样保留
        raw = str(record.get("file") or "")
        if not raw:
            continue
        if not raw.startswith(prefix):
            report.outside += 1
            continue

        relative = raw[len(prefix) :]
        relocated = str(new_root / relative)
        if relocated == raw:
            report.already_ok += 1
            continue

        record["file"] = relocated
        report.rewritten += 1
        if not Path(relocated).exists():
            report.missing_after += 1
        if len(report.samples) < _SAMPLE_LIMIT:
            report.samples.append(relative)

    if not dry_run:
        save_processed(processed, processed_file)
        cm.set("obsidian.vault_path", str(new_vault_path))
        cm.save()
        logger.info("vault 迁移完成: %s → %s", report.old_vault, report.new_vault)

    return report
