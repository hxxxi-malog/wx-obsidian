"""一次性修复：折叠分类目录里的「连续重复段」（历史嵌套 bug 的存量数据）。

背景
----
`output/vault.py:maybe_create_subcategory` 曾在迁移后再次被触发：迁移会把
`record["category"]` 改成「顶层/子主题」，下一轮又用这个 category + 原 sub_topic
调用，于是长出 `Agent/上下文工程/上下文工程/…`，实测最深 5 层。

代码侧已在 `vault.py` 加了层级 guard（category 含 `/` 即返回），不会再长新的；
但磁盘目录、`processed.json`、`config.json` 里还留着存量。

规范化规则
----------
**折叠连续重复的路径段**：`Agent/RAG/RAG/RAG` → `Agent/RAG`。

该 bug 只会在末尾追加「上一段的副本」，所以折叠连续重复段恰好还原正确形态，
且不会误伤合法的单层子目录（`Agent/MCP`、`LLM基础/推理优化` 无连续重复，原样保留）。

修复同时按**路径**与**分类名**两条线进行，缺一不可：
- 路径线：文件物理位于嵌套目录 → 搬到折叠后的目录，`processed.json` 的 `file`
  与 `category` 都跟着落点走（有的记录 `category` 与所在目录并不一致）。
- 分类名线：`category` 是嵌套形但文件已在规范位置 → 只改 `category`。

用法
----
    # 默认 dry-run：只打印将要做什么，不写任何文件
    python repair_category_nesting.py

    # 真正执行（先自动备份 config.json 与 processed.json）
    python repair_category_nesting.py --apply

执行前请先停掉 Web 服务与 CLI 抓取：两边各自持有任务状态，会争抢 processed.json / MOC 写入。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from wx_obsidian.config import PROCESSED_FILE, atomic_write, load_processed, save_processed
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.output.vault import (
    _fix_concept_links,
    _fix_links_batch,
    _parse_moc_content,
    _rebuild_moc,
    _update_parent_moc,
    escape_display,
)
from wx_obsidian.taxonomy import canonical_sub_topic

CONCEPT_DIR_NAME = "概念"
_CATEGORY_LINE = re.compile(r'(?m)^category:\s*".*"$')
_SUB_TOPIC_LINE = re.compile(r'(?m)^sub_topic:\s*".*"$')

#: 声明式分类合并表（源分类 → 目标分类）。
#: 依据见 docs/design-docs/taxonomy/spec.md §4.3：同一主题因「中英混写 + 父分类抖动」
#: 被拆进多个目录，这里各收敛到一个落点。改这张表后 `--apply` 即可，无需改代码。
MERGE_CATEGORIES: tuple[tuple[str, str], ...] = (
    # Loop 主题 15 篇：3 个目录 → Harness Engineering/Loop Engineering
    ("Agent/Loop工程", "Harness Engineering/Loop Engineering"),
    ("Harness Engineering/Loop工程", "Harness Engineering/Loop Engineering"),
    # 多Agent协同：skill 已声明该二级目录属于 Agent，HE 侧的 4 篇并回去
    ("Harness Engineering/多Agent协同", "Agent/多Agent协同"),
)


def canonical_category(category: str) -> str:
    """折叠连续重复的路径段：`Agent/RAG/RAG/RAG` → `Agent/RAG`。"""
    collapsed: list[str] = []
    for part in category.split("/"):
        if not part:
            continue
        if not collapsed or collapsed[-1] != part:
            collapsed.append(part)
    return "/".join(collapsed)


def is_nested(category: str) -> bool:
    """是否是嵌套形（折叠后与原值不同）。"""
    return bool(category) and canonical_category(category) != category


def _has_repeated_segments(rel: Path) -> bool:
    """相对路径里是否存在连续重复的目录段。"""
    parts = rel.parts
    return any(parts[i] == parts[i - 1] for i in range(1, len(parts)))


#: wikilink：[[target]] 或 [[target|display]]；target 可含 `#锚点`（锚点挂在最后一段上，
#: 所以「最后一段 = 文件名(+锚点)」的切分依然成立），但不含 `|` 与 `]`
_LINK_RE = re.compile(r"\[\[([^\]|]+?)(\|[^\]]*)?\]\]")


def canonical_link_target(target: str) -> str | None:
    """折叠链接目标里的嵌套分类前缀；无需改动时返回 None。

    文件名经过 sanitize_path_segment，绝不含 `/`，所以「最后一段 = 文件名、
    其余 = 分类路径」是可靠的。分类路径出现连续重复段即为历史嵌套的指纹。
    """
    if "/" not in target:
        return None
    parts = target.split("/")
    category = "/".join(parts[:-1])
    if not _has_repeated_segments(Path(category)):
        return None
    return f"{canonical_category(category)}/{parts[-1]}"


def rewrite_text_links(text: str, prefix_map: dict[str, str] | None = None) -> tuple[str, int]:
    """改写正文里的链接，返回 (新文本, 改动条数)。

    两类改写：
    1. **分类前缀重定向**（`prefix_map`，来自分类合并）：`[[旧分类/…]]` → `[[新分类/…]]`。
       必须按「前缀」而不是「标题集合」来改——`_fix_links_batch` 会把标题重新 sanitize
       一次，遇到文件名里带 `：` 等历史字符的笔记就静默跳过（实测有 20 个文件的
       related_topics 因此断链）。
    2. **嵌套前缀折叠**：`[[A/B/B/title]]` → `[[A/B/title]]`。
    """
    rewritten = 0
    if prefix_map:
        for old_prefix, new_prefix in sorted(prefix_map.items(), key=lambda kv: -len(kv[0])):
            if old_prefix == new_prefix:
                continue
            needle = f"[[{old_prefix}/"
            if needle in text:
                rewritten += text.count(needle)
                text = text.replace(needle, f"[[{new_prefix}/")

    changed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        new_target = canonical_link_target(match.group(1).strip())
        if new_target is None:
            return match.group(0)
        changed += 1
        return f"[[{new_target}{match.group(2) or ''}]]"

    folded = _LINK_RE.sub(_replace, text)
    return folded, rewritten + changed


def dissolve_self_folders(content: str, own_category: str) -> tuple[str, int]:
    """把指向自己的文件夹组拆开（迁移残留），文章条目升级为独立条目。"""
    title, folder_groups, standalone = _parse_moc_content(content)
    self_link = f"[[{own_category}/_MOC"
    kept: list[tuple[str, list[str]]] = []
    dissolved = 0
    for folder_line, articles in folder_groups:
        if self_link in folder_line:
            for article in articles:
                if article not in standalone:
                    standalone.append(article)
            dissolved += 1
        else:
            kept.append((folder_line, articles))
    if not dissolved:
        return content, 0
    return _rebuild_moc(title, kept, standalone), dissolved


def _entry_target(line: str) -> str:
    """取出条目行里 wikilink 的目标（无链接返回空串）。"""
    match = _LINK_RE.search(line)
    return match.group(1).strip() if match else ""


def clean_moc_entries(content: str) -> tuple[str, int]:
    """同一 MOC 内同一目标只保留一条；已在文件夹组里的条目不再重复出现在独立区。

    合并嵌套目录的 MOC 时，同一个目标可能同时存在于两侧（日期行不同），
    按目标去重才能得到干净索引。
    """
    title, folder_groups, standalone = _parse_moc_content(content)
    removed = 0

    in_folders: set[str] = set()
    new_groups: list[tuple[str, list[str]]] = []
    for folder_line, articles in folder_groups:
        local: set[str] = set()
        kept_articles: list[str] = []
        for article in articles:
            target = _entry_target(article)
            if target and target in local:
                removed += 1
                continue
            if target:
                local.add(target)
            kept_articles.append(article)
        new_groups.append((folder_line, kept_articles))
        in_folders |= local

    new_standalone: list[str] = []
    seen: set[str] = set()
    for entry in standalone:
        target = _entry_target(entry)
        if target and (target in seen or target in in_folders):
            removed += 1
            continue
        if target:
            seen.add(target)
        new_standalone.append(entry)

    if not removed:
        return content, 0
    return _rebuild_moc(title, new_groups, new_standalone), removed


@dataclass
class FileFix:
    """单个文件需要的链接类修复。"""

    path: Path
    links: int = 0
    dissolved: int = 0
    deduped: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.links or self.dissolved or self.deduped)


def _process_markdown(
    path: Path, articles_dir: Path, prefix_map: dict[str, str] | None = None
) -> tuple[str, int, int, int]:
    """对单个 .md 做「链接重定向/折叠 → 拆自引用文件夹组 → MOC 去重」，返回 (新文本, 各计数)。"""
    content = path.read_text(encoding="utf-8")
    new_text, links = rewrite_text_links(content, prefix_map)
    dissolved = deduped = 0
    if path.name == "_MOC.md":
        own_category = path.parent.relative_to(articles_dir).as_posix()
        new_text, dissolved = dissolve_self_folders(new_text, own_category)
        new_text, deduped = clean_moc_entries(new_text)
    return new_text, links, dissolved, deduped


@dataclass
class LinkPass:
    """第二遍：清掉链接里残留的嵌套前缀（第一遍只改了最深的那一层）。"""

    files: list[FileFix] = field(default_factory=list)

    @property
    def link_total(self) -> int:
        return sum(f.links for f in self.files)

    @property
    def dissolved_total(self) -> int:
        return sum(f.dissolved for f in self.files)

    @property
    def deduped_total(self) -> int:
        return sum(f.deduped for f in self.files)

    @property
    def empty(self) -> bool:
        return not self.files


def scan_residual_links(articles_dir: Path, prefix_map: dict[str, str] | None = None) -> LinkPass:
    """扫描所有 .md（含概念页），找出需重定向/折叠的链接、自引用文件夹组与重复条目。"""
    result = LinkPass()
    for path in sorted(articles_dir.rglob("*.md")):
        try:
            _text, links, dissolved, deduped = _process_markdown(path, articles_dir, prefix_map)
        except (OSError, UnicodeDecodeError):
            continue
        fix = FileFix(path=path, links=links, dissolved=dissolved, deduped=deduped)
        if fix.changed:
            result.files.append(fix)
    return result


def _rewrite_frontmatter_line(path: Path, pattern: re.Pattern[str], new_line: str) -> bool:
    """只替换 frontmatter 块里的某一行；返回是否实际改写。"""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    head, rest = content[:end], content[end:]
    updated, count = pattern.subn(new_line, head, count=1)
    if not count or updated == head:
        return False
    atomic_write(path, updated + rest)
    return True


def set_frontmatter_category(path: Path, new_category: str) -> bool:
    """只替换 frontmatter 块里的 category 行；返回是否实际改写。"""
    return _rewrite_frontmatter_line(path, _CATEGORY_LINE, f'category: "{new_category}"')


def set_frontmatter_sub_topic(path: Path, new_sub_topic: str) -> bool:
    """只替换 frontmatter 块里的 sub_topic 行；返回是否实际改写。"""
    return _rewrite_frontmatter_line(path, _SUB_TOPIC_LINE, f'sub_topic: "{new_sub_topic}"')


def drop_folder_group(moc_path: Path, category: str) -> bool:
    """从父 MOC 里删掉指向 `category` 的文件夹组（该子目录已被合并走）。"""
    if not moc_path.exists():
        return False
    content = moc_path.read_text(encoding="utf-8")
    title, folder_groups, standalone = _parse_moc_content(content)
    kept = [(line, arts) for line, arts in folder_groups if f"[[{category}/_MOC" not in line]
    if len(kept) == len(folder_groups):
        return False
    atomic_write(moc_path, _rebuild_moc(title, kept, standalone))
    return True


def refresh_folder_group(moc_path: Path, category: str, sub_moc: Path) -> bool:
    """按子目录 MOC 的当前内容重建父 MOC 里对应的文件夹组（合并后条目会变多）。"""
    if not moc_path.exists() or not sub_moc.exists():
        return False
    content = moc_path.read_text(encoding="utf-8")
    title, folder_groups, standalone = _parse_moc_content(content)
    _, sub_groups, sub_standalone = _parse_moc_content(sub_moc.read_text(encoding="utf-8"))
    children = [article for _line, articles in sub_groups for article in articles]
    children.extend(sub_standalone)

    changed = False
    new_groups: list[tuple[str, list[str]]] = []
    for line, articles in folder_groups:
        if f"[[{category}/_MOC" in line:
            if articles != children:
                changed = True
            new_groups.append((line, children))
        else:
            new_groups.append((line, articles))
    if not changed:
        return False
    atomic_write(moc_path, _rebuild_moc(title, new_groups, standalone))
    return True


@dataclass
class FileMove:
    src: Path
    dst: Path
    old_category: str
    new_category: str


@dataclass
class MocMerge:
    src: Path
    dst: Path
    old_category: str
    new_category: str


@dataclass
class RecordFix:
    article_id: str
    old_label: str
    new_category: str
    #: 由路径驱动（文件会被搬走）还是由分类名驱动（文件已在规范位置）
    by_path: bool


@dataclass
class LegacyFix:
    """分类名与文件位置对不上的历史遗留记录。

    设计约定：category 里出现 "/" 就表示文件已经在子目录里。这类记录名字改了、
    文件没搬——目标子目录真实存在时把文件搬进去（补完当年的迁移）；目录从未
    存在时把分类名改回文件实际所在的那一层（不凭空造空壳目录）。
    """

    article_id: str
    src: Path
    #: None 表示只改分类名，不搬文件
    dst: Path | None
    old_category: str
    new_category: str
    title: str
    date: str


@dataclass
class MissingMoc:
    """有文章却没有 _MOC.md 的分类目录。"""

    category: str
    directory: Path
    entries: list[tuple[str, str, str]] = field(default_factory=list)  # (date, stem, title)


@dataclass
class Plan:
    file_moves: list[FileMove] = field(default_factory=list)
    moc_merges: list[MocMerge] = field(default_factory=list)
    collisions: list[tuple[Path, Path]] = field(default_factory=list)
    dirs_to_remove: list[Path] = field(default_factory=list)
    record_fixes: list[RecordFix] = field(default_factory=list)
    categories_to_fix: list[tuple[str, str]] = field(default_factory=list)
    legacy_fixes: list[LegacyFix] = field(default_factory=list)
    missing_mocs: list[MissingMoc] = field(default_factory=list)
    link_pass: LinkPass = field(default_factory=LinkPass)
    #: L0：分类合并的源 → 目标（用于打印与 config 同步）
    merges: list[tuple[str, str]] = field(default_factory=list)
    #: 需要从父 MOC 里删掉的文件夹组：(源分类,)
    merge_parent_cleanups: list[str] = field(default_factory=list)
    #: 需要按合并后子目录 MOC 重建的父 MOC 文件夹组：(目标分类,)
    merge_parent_refreshes: list[str] = field(default_factory=list)
    #: 存量 sub_topic 归一化：(article_id, 原值, 规范值)
    sub_topic_fixes: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.file_moves
            or self.moc_merges
            or self.record_fixes
            or self.categories_to_fix
            or self.legacy_fixes
            or self.missing_mocs
            or self.link_pass.files
            or self.sub_topic_fixes
            or self.merges
        )


def build_plan(
    articles_dir: Path,
    processed: dict[str, object],
    categories: list[str],
) -> Plan:
    """扫描磁盘 + processed.json + config categories，产出修复计划（不写盘）。"""
    plan = Plan()

    # 1) 磁盘：嵌套目录里的文件 → 规范化目录
    nested_dirs: list[Path] = []
    if articles_dir.exists():
        for path in articles_dir.rglob("*"):
            if not path.is_dir():
                continue
            rel = path.relative_to(articles_dir)
            if rel.parts and rel.parts[0] == CONCEPT_DIR_NAME:
                continue  # 概念目录不参与分类层级
            if _has_repeated_segments(rel):
                nested_dirs.append(path)

    for src_dir in sorted(nested_dirs):
        old_category = src_dir.relative_to(articles_dir).as_posix()
        new_category = canonical_category(old_category)
        dst_dir = articles_dir / new_category
        for entry in sorted(src_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name == "_MOC.md":
                plan.moc_merges.append(
                    MocMerge(
                        src=entry,
                        dst=dst_dir / "_MOC.md",
                        old_category=old_category,
                        new_category=new_category,
                    )
                )
                continue
            dst = dst_dir / entry.name
            if dst.exists():
                plan.collisions.append((entry, dst))
                continue
            plan.file_moves.append(
                FileMove(src=entry, dst=dst, old_category=old_category, new_category=new_category)
            )

    # 1.5) 声明式分类合并（L0）：把源目录的文件并入目标目录。
    # 复用与嵌套修复同一套搬迁 / frontmatter / processed / MOC / 链接机制，
    # 因此这里只负责「配对」，不引入第二套写盘逻辑。
    for src_category, dst_category in MERGE_CATEGORIES:
        src_dir = articles_dir / src_category
        if src_category == dst_category or not src_dir.is_dir():
            continue
        dst_dir = articles_dir / dst_category
        for entry in sorted(src_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name == "_MOC.md":
                plan.moc_merges.append(
                    MocMerge(
                        src=entry,
                        dst=dst_dir / "_MOC.md",
                        old_category=src_category,
                        new_category=dst_category,
                    )
                )
                continue
            dst = dst_dir / entry.name
            if dst.exists():
                plan.collisions.append((entry, dst))
                continue
            plan.file_moves.append(
                FileMove(src=entry, dst=dst, old_category=src_category, new_category=dst_category)
            )
        plan.merges.append((src_category, dst_category))
        plan.dirs_to_remove.append(src_dir)
        plan.merge_parent_cleanups.append(src_category)
        plan.merge_parent_refreshes.append(dst_category)

    # 2) processed.json：路径线 + 分类名线
    moved_by_src = {str(move.src): move for move in plan.file_moves}
    for article_id, record in processed.items():
        if not isinstance(record, dict):
            continue
        file_path = str(record.get("file") or "")
        category = str(record.get("category") or "")
        moved = moved_by_src.get(file_path)
        if moved is not None:
            plan.record_fixes.append(
                RecordFix(
                    article_id=str(article_id),
                    old_label=category or file_path,
                    new_category=moved.new_category,
                    by_path=True,
                )
            )
        elif is_nested(category):
            plan.record_fixes.append(
                RecordFix(
                    article_id=str(article_id),
                    old_label=category,
                    new_category=canonical_category(category),
                    by_path=False,
                )
            )

    # 3) config.json：categories 需要折叠 + 去重（合并的增删在 apply 阶段处理）
    for category in categories:
        if is_nested(category):
            plan.categories_to_fix.append((category, canonical_category(category)))

    # 4) 待删除的空目录（从深到浅，父目录才不会先被删）；合并后的空源目录一并清理
    plan.dirs_to_remove = sorted(
        {*nested_dirs, *plan.dirs_to_remove}, key=lambda p: len(p.parts), reverse=True
    )

    # 4.5) 存量 sub_topic 归一化（L0-3）：存了别名写法的记录统一到规范写法，
    # 这样 _count_sub_topic_articles 与迁移筛选才能命中（见 vault.py 的同一口径改动）
    for article_id, record in processed.items():
        if not isinstance(record, dict) or record.get("status") != "done":
            continue
        raw = str(record.get("sub_topic") or "")
        if not raw:
            continue
        canonical = canonical_sub_topic(raw)
        if canonical != raw:
            plan.sub_topic_fixes.append((str(article_id), raw, canonical))

    # 5) 分类名与位置不一致的历史遗留（嵌套形与已安排搬走的记录在前两阶段处理过）
    legacy_dst: dict[str, Path] = {}
    for article_id, record in processed.items():
        if not isinstance(record, dict) or record.get("status") != "done":
            continue
        category = str(record.get("category") or "")
        file_path = str(record.get("file") or "")
        if not category or is_nested(category) or file_path in moved_by_src:
            continue
        src = Path(file_path)
        if not src.exists():
            continue
        target_dir = articles_dir / category
        if src.parent == target_dir:
            continue  # 名字与位置一致
        parent = category.rsplit("/", 1)[0] if "/" in category else None
        parent_dir = articles_dir / parent if parent else None
        dest: Path | None = None
        if parent_dir is not None and src.parent == parent_dir and target_dir.is_dir():
            # 文件还在父目录、子目录已存在 → 补完当年没做成的迁移
            candidate = target_dir / src.name
            if candidate.exists():
                plan.collisions.append((src, candidate))
                continue
            dest = candidate
            new_category = category
        else:
            # 其余情形（子目录从未建过、文件在另一个同级目录里，或文件比 category
            # 更深一层）一律只把分类名对齐到文件实际所在目录，绝不跨目录搬文件——
            # 那属于分类合并，得人来定
            if src.parent == articles_dir:
                continue  # 文件在文章根目录，没有可对齐的分类名
            new_category = src.parent.relative_to(articles_dir).as_posix()
        if dest is not None:
            legacy_dst[file_path] = dest
        plan.legacy_fixes.append(
            LegacyFix(
                article_id=str(article_id),
                src=src,
                dst=dest,
                old_category=category,
                new_category=new_category,
                title=str(record.get("title") or src.stem),
                date=str(record.get("processed_at") or "")[:10],
            )
        )

    # 6) 有文章却没有 _MOC.md 的目录（按记录修完之后的落点来算）
    planned_dst = {str(move.src): move.dst for move in plan.file_moves}
    planned_dst.update(legacy_dst)
    by_dir: dict[Path, list[tuple[str, str, str]]] = {}
    for record in processed.values():
        if not isinstance(record, dict) or record.get("status") != "done":
            continue
        file_path = str(record.get("file") or "")
        src = Path(file_path)
        if not src.name:
            continue
        final = planned_dst.get(file_path, src)
        directory = final.parent
        if not directory.is_dir() and directory not in {m.dst.parent for m in plan.file_moves}:
            continue
        rel = directory.relative_to(articles_dir)
        if rel.parts and rel.parts[0] == CONCEPT_DIR_NAME:
            continue
        if rel.as_posix() in ("", "."):
            continue  # 文章根目录的 MOC 不属于分类 MOC
        by_dir.setdefault(directory, []).append(
            (
                str(record.get("processed_at") or "")[:10],
                final.stem,
                str(record.get("title") or final.stem),
            )
        )
    for directory, entries in sorted(by_dir.items()):
        if (directory / "_MOC.md").exists():
            continue
        plan.missing_mocs.append(
            MissingMoc(
                category=directory.relative_to(articles_dir).as_posix(),
                directory=directory,
                entries=sorted(entries),
            )
        )

    # 7) 第二遍：链接里残留的嵌套前缀（第一遍只改了最深的一层）
    # 链接重定向用完整的合并表而不是本次 plan.merges：合并跑过一次之后源目录就没了，
    # 但存量链接仍指向旧分类，重跑时也要能继续收敛（幂等）
    plan.link_pass = scan_residual_links(articles_dir, dict(MERGE_CATEGORIES))
    return plan


def _print_plan(plan: Plan, articles_dir: Path, config_file: Path, processed_file: Path) -> None:
    print("=" * 78)
    print("修复计划（dry-run，未写盘）")
    print("=" * 78)

    by_map: dict[tuple[str, str], list[FileMove]] = {}
    for move in plan.file_moves:
        by_map.setdefault((move.old_category, move.new_category), []).append(move)

    if plan.merges:
        print(f"\n[0] 分类合并（MERGE_CATEGORIES）：{len(plan.merges)} 组")
        for merged_from, merged_to in plan.merges:
            count = len(by_map.get((merged_from, merged_to), []))
            print(f"    {merged_from}  →  {merged_to}   （{count} 个文件）")

    print(f"\n[1] 移动文件：{len(plan.file_moves)} 个，涉及 {len(by_map)} 组目录")
    for (old, new), moves in sorted(by_map.items()):
        print(f"    {old}\n      → {new}   ({len(moves)} 个文件)")

    print(f"\n[2] 合并 _MOC.md：{len(plan.moc_merges)} 个")
    for merge in plan.moc_merges:
        print(f"    {merge.src.relative_to(articles_dir)}  →  {merge.new_category}/_MOC.md")

    if plan.merges:
        print(
            f"\n[2.5] 父 MOC 文件夹组维护：删 {len(plan.merge_parent_cleanups)} 组、"
            f"重建 {len(plan.merge_parent_refreshes)} 组"
        )
        for category in plan.merge_parent_cleanups:
            print(f"    删除  📁 [[{category}/_MOC]]（源目录已并入别处）")
        for category in plan.merge_parent_refreshes:
            print(f"    重建  📁 [[{category}/_MOC]]（按合并后的子目录 MOC）")

    if plan.sub_topic_fixes:
        print(f"\n[2.6] 存量 sub_topic 归一化：{len(plan.sub_topic_fixes)} 条")
        tally: dict[tuple[str, str], int] = {}
        for _aid, old, new in plan.sub_topic_fixes:
            tally[(old, new)] = tally.get((old, new), 0) + 1
        for (old, new), count in sorted(tally.items()):
            print(f"    {count:3d} 条  {old}  →  {new}")

    by_path = [f for f in plan.record_fixes if f.by_path]
    by_name = [f for f in plan.record_fixes if not f.by_path]
    print(f"\n[3] {processed_file} 记录修正：{len(plan.record_fixes)} 条")
    print(f"    (a) 路径线 {len(by_path)} 条：文件被搬走，file 与 category 一起改到落点")

    def _tally(items: list[RecordFix]) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for item in items:
            key = (item.old_label, item.new_category)
            counts[key] = counts.get(key, 0) + 1
        return counts

    for (old, new), count in sorted(_tally(by_path).items()):
        print(f"        {count:3d} 条  {old!r}\n               → {new!r}")
    print(f"    (b) 分类名线 {len(by_name)} 条：文件已在规范位置，只改 category")
    for (old, new), count in sorted(_tally(by_name).items()):
        print(f"        {count:3d} 条  {old}  →  {new}")

    print(f"\n[4] {config_file} 的 categories：折叠 {len(plan.categories_to_fix)} 项并去重")

    print(f"\n[5] 删除空目录：{len(plan.dirs_to_remove)} 个")
    for path in plan.dirs_to_remove[:10]:
        print(f"    {path.relative_to(articles_dir)}")
    if len(plan.dirs_to_remove) > 10:
        print(f"    … 其余 {len(plan.dirs_to_remove) - 10} 个")

    to_move = [f for f in plan.legacy_fixes if f.dst is not None]
    to_rename = [f for f in plan.legacy_fixes if f.dst is None]
    print(f"\n[6] 分类名与文件位置不一致的历史遗留：{len(plan.legacy_fixes)} 条")
    print(f"    (a) 文件还在父目录且子目录已存在 → 补完当年的迁移：{len(to_move)} 条")
    for fix in to_move:
        print(
            f"        {fix.src.name[:52]}\n"
            f"          {fix.old_category}  →  {fix.new_category}/（文件入子目录）"
        )
    print(f"    (b) 其余 → 分类名对齐到文件实际所在目录，文件不动：{len(to_rename)} 条")
    rename_tally: dict[tuple[str, str], int] = {}
    for fix in to_rename:
        key = (fix.old_category, fix.new_category)
        rename_tally[key] = rename_tally.get(key, 0) + 1
    for (old, new), count in sorted(rename_tally.items())[:12]:
        print(f"        {count:3d} 条  {old}  →  {new}")
    if len(rename_tally) > 12:
        print(f"        … 其余 {len(rename_tally) - 12} 个分类名")

    print(f"\n[7] 补建缺失的 _MOC.md：{len(plan.missing_mocs)} 个")
    for moc in plan.missing_mocs:
        print(f"    {moc.category}/_MOC.md  （写入 {len(moc.entries)} 条已有文章）")

    link_pass = plan.link_pass
    print(
        f"\n[8] 链接清理：{len(link_pass.files)} 个文件 / {link_pass.link_total} 条嵌套前缀链接"
        f" / {link_pass.dissolved_total} 个自引用文件夹组 / {link_pass.deduped_total} 行重复条目"
    )
    for link_fix in link_pass.files[:10]:
        detail = []
        if link_fix.links:
            detail.append(f"{link_fix.links} 条链接")
        if link_fix.dissolved:
            detail.append(f"拆 {link_fix.dissolved} 个文件夹组")
        if link_fix.deduped:
            detail.append(f"去重 {link_fix.deduped} 行")
        print(f"    {link_fix.path.relative_to(articles_dir)}  （{'，'.join(detail)}）")
    if len(link_pass.files) > 10:
        print(f"    … 其余 {len(link_pass.files) - 10} 个文件")

    if plan.collisions:
        print(f"\n[!] 文件名冲突 {len(plan.collisions)} 个（跳过，绝不覆盖）：")
        for src, dst in plan.collisions:
            print(f"    {src.name}\n      目标已存在：{dst.parent.relative_to(articles_dir)}/")


def _merge_moc(merge: MocMerge, dst_content: str | None) -> str:
    """把嵌套目录的 MOC 条目并入规范化目录的 MOC（保留文件夹分组，按行去重）。"""
    title = f"# {merge.new_category.rsplit('/', 1)[-1]}"
    folder_groups: list[tuple[str, list[str]]] = []
    standalone: list[str] = []
    if dst_content is not None:
        title, folder_groups, standalone = _parse_moc_content(dst_content)

    src_content = merge.src.read_text(encoding="utf-8") if merge.src.exists() else ""
    _, src_folders, src_standalone = _parse_moc_content(src_content)

    def retarget(line: str) -> str:
        return line.replace(f"[[{merge.old_category}/", f"[[{merge.new_category}/")

    seen_standalone = set(standalone)
    for entry in src_standalone:
        fixed = retarget(entry)
        if fixed not in seen_standalone:
            standalone.append(fixed)
            seen_standalone.add(fixed)

    for folder_line, articles in src_folders:
        fixed_line = retarget(folder_line)
        for existing_line, existing_articles in folder_groups:
            if existing_line == fixed_line:
                for article in articles:
                    fixed_article = retarget(article)
                    if fixed_article not in existing_articles:
                        existing_articles.append(fixed_article)
                break
        else:
            folder_groups.append((fixed_line, [retarget(a) for a in articles]))

    return _rebuild_moc(title, folder_groups, standalone)


def apply_plan(plan: Plan, articles_dir: Path, cm: ConfigManager, processed_file: Path) -> None:
    """执行计划：备份 → 移动 → 改 frontmatter → 修链接 → 合并 MOC → 删空目录 → 落盘。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    for src in (cm.config_file, processed_file):
        if src.exists():
            backup = src.with_name(f"{src.name}.bak.repair-{stamp}")
            shutil.copy2(src, backup)
            print(f"  备份 {src} → {backup.name}")

    concept_dir = articles_dir / CONCEPT_DIR_NAME
    moved_titles: dict[tuple[str, str], list[str]] = {}

    for move in plan.file_moves:
        move.dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(move.src), str(move.dst))
        moved_titles.setdefault((move.old_category, move.new_category), []).append(move.dst.stem)
        set_frontmatter_category(move.dst, move.new_category)
    print(f"  已移动 {len(plan.file_moves)} 个文件并同步 frontmatter")

    for (old, new), titles in moved_titles.items():
        if concept_dir.exists():
            _fix_concept_links(concept_dir, titles, old, new)
        _fix_links_batch(articles_dir, titles, old, new)
    print(f"  已修正 {sum(len(t) for t in moved_titles.values())} 个文件的双链（含概念页）")

    for merge in plan.moc_merges:
        existing = merge.dst.read_text(encoding="utf-8") if merge.dst.exists() else None
        merge.dst.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(merge.dst, _merge_moc(merge, existing))
        # 内容已并入目标 MOC；源文件必须删掉，否则嵌套目录永远清不空
        merge.src.unlink(missing_ok=True)
    print(f"  已合并 {len(plan.moc_merges)} 个 _MOC.md（源文件已并入目标）")

    removed = 0
    for path in plan.dirs_to_remove:
        if path.exists() and not any(path.iterdir()):
            path.rmdir()
            removed += 1
    print(f"  已清理空目录 {removed}/{len(plan.dirs_to_remove)} 个")

    # 分类合并的收尾：父 MOC 里指向已删源目录的文件夹组必须删掉，
    # 目标分类的组要按合并后的子目录 MOC 重建（否则条目数停在合并前）
    cleaned = 0
    for src_category in plan.merge_parent_cleanups:
        parent, _, _leaf = src_category.rpartition("/")
        if not parent:
            continue
        if drop_folder_group(articles_dir / parent / "_MOC.md", src_category):
            cleaned += 1
    refreshed = 0
    for dst_category in plan.merge_parent_refreshes:
        parent, _, leaf = dst_category.rpartition("/")
        if not parent:
            continue
        parent_moc = articles_dir / parent / "_MOC.md"
        sub_moc = articles_dir / dst_category / "_MOC.md"
        if refresh_folder_group(parent_moc, dst_category, sub_moc):
            refreshed += 1
        elif parent_moc.exists() and sub_moc.exists():
            # 父 MOC 里还没有这个分类的文件夹组 → 按 app 自己的方式补一个
            _update_parent_moc(articles_dir, parent, leaf)
            refreshed += 1
    if plan.merges:
        print(f"  父 MOC 文件夹组：删除 {cleaned} 个、重建 {refreshed} 个")

    # 历史遗留：目标子目录存在就把文件搬进去，否则只改分类名（文件不动）
    legacy_moved = 0
    legacy_renamed = 0
    for fix in plan.legacy_fixes:
        if fix.dst is not None:
            fix.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(fix.src), str(fix.dst))
            set_frontmatter_category(fix.dst, fix.new_category)
            legacy_moved += 1
        else:
            set_frontmatter_category(fix.src, fix.new_category)
            legacy_renamed += 1
    print(f"  历史遗留：搬入已有子目录 {legacy_moved} 条，分类名改回实际所在层 {legacy_renamed} 条")

    # 补建缺失的分类 MOC（沿用 app 自己的条目格式）
    for moc in plan.missing_mocs:
        lines = [f"# {moc.category.rsplit('/', 1)[-1]}", ""]
        for date, stem, title in moc.entries:
            lines.append(f"- {date} [[{moc.category}/{stem}|{escape_display(title)}]]")
        atomic_write(moc.directory / "_MOC.md", "\n".join(lines) + "\n")
        parent, _, name = moc.category.rpartition("/")
        if parent:
            _update_parent_moc(articles_dir, parent, name)
    print(f"  已补建 {len(plan.missing_mocs)} 个分类 _MOC.md")

    link_files = 0
    for link_fix in plan.link_pass.files:
        path = link_fix.path
        try:
            content = path.read_text(encoding="utf-8")
            new_text, _l, _d, _dd = _process_markdown(path, articles_dir, dict(MERGE_CATEGORIES))
        except (OSError, UnicodeDecodeError):
            continue
        if new_text != content:
            atomic_write(path, new_text)
            link_files += 1
    print(
        f"  已清理 {link_files} 个文件"
        f"（{plan.link_pass.link_total} 条残留链接，{plan.link_pass.dissolved_total} 个自引用文件夹组，"
        f"{plan.link_pass.deduped_total} 行重复条目）"
    )

    # processed.json：路径线（file + category 一起改）与分类名线（只改 category）
    processed = load_processed()
    moved_by_src = {str(move.src): move for move in plan.file_moves}
    legacy_by_src = {str(fix.src): fix for fix in plan.legacy_fixes}
    updated = 0
    subtopic_updated = 0
    warnings: list[str] = []
    for _article_id, record in processed.items():
        if not isinstance(record, dict):
            continue
        file_path = str(record.get("file") or "")
        moved = moved_by_src.get(file_path)
        if moved is not None:
            record["file"] = str(moved.dst)
            record["category"] = moved.new_category
            updated += 1
        else:
            legacy = legacy_by_src.get(file_path)
            if legacy is not None:
                record["category"] = legacy.new_category
                if legacy.dst is not None:
                    record["file"] = str(legacy.dst)
                updated += 1
            else:
                category = str(record.get("category") or "")
                if is_nested(category):
                    new_category = canonical_category(category)
                    record["category"] = new_category
                    old_file = Path(file_path)
                    if old_file.name:
                        new_path = articles_dir / new_category / old_file.name
                        if new_path.exists():
                            record["file"] = str(new_path)
                            set_frontmatter_category(new_path, new_category)
                        else:
                            warnings.append(
                                f"{old_file} 不在落点 {new_path}，file 字段保持原样待人工确认"
                            )
                    updated += 1

        # 存量 sub_topic 归一化（对已被搬动的记录用新落点改写 frontmatter）
        raw_sub_topic = str(record.get("sub_topic") or "")
        canonical_sub = canonical_sub_topic(raw_sub_topic)
        if raw_sub_topic and canonical_sub != raw_sub_topic:
            record["sub_topic"] = canonical_sub
            target = Path(str(record.get("file") or ""))
            if target.exists():
                set_frontmatter_sub_topic(target, canonical_sub)
            subtopic_updated += 1
    if updated or subtopic_updated:
        save_processed(processed)
    print(f"  已修正 processed.json 记录 {updated} 条，sub_topic 归一化 {subtopic_updated} 条")
    for message in warnings:
        print(f"  [!] {message}")

    # config.json：categories 折叠 + 去重 + 合并（源分类移除、目标分类保留；
    # ConfigManager.load 已剔除已移除的 scheduler 段）
    categories = [str(c) for c in (cm.get("categories", []) or [])]
    rebuilt: list[str] = []
    for category in categories:
        canonical = canonical_category(category)
        if canonical and canonical not in rebuilt:
            rebuilt.append(canonical)
    if plan.merges:
        merge_sources = {src for src, _dst in plan.merges}
        merge_targets = {dst for _src, dst in plan.merges}
        for merged_target in sorted(merge_targets):
            if merged_target not in rebuilt:
                rebuilt.append(merged_target)
        for merged_source in sorted(merge_sources):
            if merged_source not in merge_targets and merged_source in rebuilt:
                rebuilt.remove(merged_source)
    cm.set("categories", rebuilt)
    cm.save()
    print(f"  categories：{len(categories)} → {len(rebuilt)} 项")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="折叠分类目录里的连续重复段（历史嵌套 bug）")
    parser.add_argument("--apply", action="store_true", help="真正执行；省略时只 dry-run")
    args = parser.parse_args(argv)

    cm = ConfigManager()
    cm.load()

    vault_path = Path(str(cm.get("obsidian.vault_path", "") or ""))
    articles_dir = vault_path / str(cm.get("obsidian.articles_dir", "公众号文章") or "公众号文章")
    if not articles_dir.is_dir():
        print(f"文章目录不存在：{articles_dir}", file=sys.stderr)
        return 2

    processed = load_processed()
    categories = [str(c) for c in (cm.get("categories", []) or [])]
    plan = build_plan(articles_dir, processed, categories)

    print(f"Vault：{vault_path}")
    print(f"文章目录：{articles_dir}")
    _print_plan(plan, articles_dir, cm.config_file, PROCESSED_FILE)

    if plan.empty:
        print("\n没有需要修复的内容。")
        return 0

    if not args.apply:
        print("\n这是 dry-run。确认计划无误后加 --apply 执行。")
        return 0

    print("\n开始执行…")
    apply_plan(plan, articles_dir, cm, PROCESSED_FILE)
    print("完成。建议在 Obsidian 里重新打开 Vault 确认目录与链接。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
