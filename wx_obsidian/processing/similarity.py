"""文章相似度计算：基于 SQLite FTS5 + jieba + 结构化信号的混合关联。"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# RRF 参数：k 随语料库大小线性插值，k = K_MIN + (K_MAX - K_MIN) * min(1, N / K_REF)
RRF_K_MIN = 15  # 语料库极小时的 k
RRF_K_MAX = 60  # 语料库 >= K_REF 时的 k
RRF_K_REF = 300  # 参考语料库大小（k 到达 K_MAX 的拐点）

# BM25F 字段权重
FIELD_WEIGHTS = {
    "title": 3.0,
    "tags": 2.5,
    "concepts": 2.0,
    "key_points": 1.5,
    "summary": 1.0,
}

# 结构化加成
BONUS_SUB_TOPIC = 0.03
BONUS_SAME_CATEGORY = 0.03
BONUS_SAME_SOURCE = 0.05

# BM25 查询参数
TOP_K_KEYWORDS = 20

# 标点符号集合（用于过滤 BM25 关键词）
_PUNCTUATION = set("、，。！？；：''【】（）《》…—·,.!?;:\"'()[]{}<> \t\n")

# 领域停用词：通用中文停用词 + AI/技术领域高频低信息词
_STOP_WORDS: set[str] = {
    # 通用中文停用词
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "他",
    "她",
    "它",
    "们",
    "那",
    "被",
    "从",
    "把",
    "对",
    "让",
    "用",
    "为",
    "以",
    "所",
    "之",
    "与",
    "及",
    "或",
    "但",
    "而",
    "如",
    "若",
    "因",
    "则",
    "其",
    "此",
    "等",
    "来",
    "后",
    "前",
    "时",
    "中",
    "下",
    "可",
    "能",
    "已",
    "于",
    "又",
    "更",
    "再",
    "将",
    "还",
    "没",
    "才",
    "只",
    "即",
    "使",
    "因为",
    "所以",
    "但是",
    "然而",
    "如果",
    "虽然",
    "由于",
    "进行",
    # AI/技术领域高频低信息词（几乎所有文章都出现，无区分度）
    "核心",
    "技术",
    "方案",
    "文章",
    "分析",
    "问题",
    "解决",
    "实现",
    "使用",
    "支持",
    "提供",
    "包括",
    "以及",
    "同时",
    "目前",
    "其中",
    "可以",
    "需要",
    "基于",
    "利用",
    "采用",
    "场景",
    "能力",
    "功能",
    "系统",
    "平台",
    "工具",
    "方法",
    "方式",
    "关键",
    "重要",
    "主要",
    "基础",
    "整体",
    "具体",
    "相关",
    "不同",
    "能够",
    "已经",
    "因此",
    "介绍",
    "内容",
    "部分",
    "方面",
    "特点",
    "优势",
    "价值",
    "目标",
    "通过",
    "显著",
    "有效",
    "全面",
    "深入",
    "快速",
    "简单",
    "直接",
    "进一步",
    "基本",
    "传统",
    "典型",
    "常见",
    "本质",
}

# 默认数据库路径
_DEFAULT_DB_PATH = Path.home() / ".wx-obsidian" / "similarity.sqlite"

# 正则分词 fallback 模式
RE_CN_TOKEN = re.compile(r"[一-鿿]{2,6}")
RE_EN_TOKEN = re.compile(r"[a-zA-Z]{3,}")

# jieba 延迟加载状态：None=未加载, module=已加载, False=加载失败（降级）
_jieba: Any = None
_jieba_checked = False


def _get_jieba() -> Any:
    """延迟加载 jieba，仅首次调用时导入。未安装时返回 None（降级到正则分词）。"""
    global _jieba, _jieba_checked
    if not _jieba_checked:
        _jieba_checked = True
        try:
            import jieba as _jb

            _jieba = _jb
        except ImportError:
            logger.warning("jieba 未安装，降级到正则分词（pip install jieba）")
            _jieba = None
    return _jieba


# ---------------------------------------------------------------------------
# 分词
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> str:
    """分词，返回空格分隔的 token 字符串（供 FTS5 索引）。

    jieba 可用时使用 jieba，否则降级到正则分词。自动过滤停用词。
    """
    jb = _get_jieba()
    if jb is not None:
        tokens = jb.lcut(text)
        return " ".join(t for t in tokens if t.strip() and t not in _STOP_WORDS)
    # 正则分词 fallback
    cn = RE_CN_TOKEN.findall(text)
    en = [w.lower() for w in RE_EN_TOKEN.findall(text)]
    return " ".join(t for t in cn + en if t not in _STOP_WORDS)


# ---------------------------------------------------------------------------
# 结构化信号
# ---------------------------------------------------------------------------


def _fuzzy_concept_score(concepts_a: list[str], concepts_b: list[str]) -> float:
    """概念模糊匹配（非对称 max-avg）。

    对 A 的每个概念，在 B 中找最佳匹配得分，取均值。
    子串包含 -> 1.0，编辑距离/max_len <= 0.3 -> 0.8，否则 -> 0.0。
    """
    if not concepts_a or not concepts_b:
        return 0.0

    scores: list[float] = []
    for a in concepts_a:
        best = 0.0
        for b in concepts_b:
            if a in b or b in a:
                best = max(best, 1.0)
            else:
                max_len = max(len(a), len(b))
                if max_len > 0:
                    dist = _edit_distance(a, b)
                    ratio = dist / max_len
                    if ratio <= 0.3:
                        best = max(best, 0.8)
        scores.append(best)

    return sum(scores) / len(scores)


def _edit_distance(s1: str, s2: str) -> int:
    """Levenshtein 编辑距离。"""
    m, n = len(s1), len(s2)
    if m == 0:
        return n
    if n == 0:
        return m

    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[n]


def _token_jaccard(tags_a: list[str], tags_b: list[str]) -> float:
    """标签 token 化 Jaccard：tags 按 '_' 分割为 token 集合后计算。"""
    tokens_a: set[str] = set()
    for tag in tags_a:
        tokens_a.update(t.lower() for t in tag.split("_") if t)

    tokens_b: set[str] = set()
    for tag in tags_b:
        tokens_b.update(t.lower() for t in tag.split("_") if t)

    if not tokens_a and not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# SimilarityEngine
# ---------------------------------------------------------------------------


class SimilarityEngine:
    """文章相似度计算引擎，基于 SQLite FTS5 + 结构化信号。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SimilarityEngine:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _ensure_db(self, articles: dict[str, Any]) -> sqlite3.Connection:
        """确保数据库连接可用，不存在则从 processed.json 构建。"""
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1 FROM articles LIMIT 1")
                self._insert_new_articles(self._conn, articles)
                return self._conn
            except sqlite3.DatabaseError:
                self._conn.close()
                self._conn = None

        if self._db_path.exists():
            try:
                conn = sqlite3.connect(str(self._db_path))
                conn.execute("SELECT 1 FROM articles LIMIT 1")
                self._conn = conn
                self._insert_new_articles(conn, articles)
                self._sync_deletions(conn, articles)
                return conn
            except sqlite3.DatabaseError:
                conn.close()
                self._db_path.unlink(missing_ok=True)

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            self._build_fts_index(conn, articles)
        except Exception:
            conn.close()
            raise
        self._conn = conn
        return conn

    def _insert_one_article(self, conn: sqlite3.Connection, aid: str, rec: dict[str, Any]) -> None:
        """将一篇文章插入 articles 表和 articles_fts 表（BM25F 分字段）。"""
        concepts = [c.get("name", "") for c in (rec.get("concepts") or []) if isinstance(c, dict)]
        tags = rec.get("tags") or []
        conn.execute(
            "INSERT OR IGNORE INTO articles "
            "(article_id, title, category, sub_topic, source, concepts, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                aid,
                rec.get("title", ""),
                rec.get("category", ""),
                rec.get("sub_topic", ""),
                rec.get("source", ""),
                json.dumps(concepts),
                json.dumps(tags),
            ),
        )

        # BM25F: 每个字段独立分词后插入
        conn.execute(
            "INSERT INTO articles_fts "
            "(article_id, title, tags, concepts, key_points, summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                aid,
                _tokenize(rec.get("title", "")),
                _tokenize(" ".join(tags)),
                _tokenize(" ".join(concepts)),
                _tokenize(" ".join(rec.get("key_points") or [])),
                _tokenize(rec.get("summary", "")),
            ),
        )

    def _build_fts_index(self, conn: sqlite3.Connection, articles: dict[str, Any]) -> None:
        """构建 SQLite 表和 FTS5 全文索引（BM25F 分字段）。"""
        conn.execute("DROP TABLE IF EXISTS articles")
        conn.execute("DROP TABLE IF EXISTS articles_fts")
        conn.execute("""
            CREATE TABLE articles (
                article_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT,
                sub_topic TEXT,
                source TEXT,
                concepts TEXT,
                tags TEXT
            )
        """)
        # BM25F: 每个字段独立索引，查询时按 FIELD_WEIGHTS 加权
        conn.execute("""
            CREATE VIRTUAL TABLE articles_fts USING fts5(
                article_id UNINDEXED,
                title,
                tags,
                concepts,
                key_points,
                summary,
                tokenize='ascii'
            )
        """)

        for aid, rec in articles.items():
            if not isinstance(rec, dict) or rec.get("status") != "done":
                continue
            if not rec.get("title"):
                continue
            self._insert_one_article(conn, aid, rec)

        conn.commit()
        logger.info(
            "FTS5 索引构建完成：%d 篇文章",
            conn.execute("SELECT count(*) FROM articles").fetchone()[0],
        )

    def _insert_new_articles(self, conn: sqlite3.Connection, articles: dict[str, Any]) -> None:
        """增量插入新文章到已有数据库。"""
        existing = {r[0] for r in conn.execute("SELECT article_id FROM articles").fetchall()}
        inserted = 0
        for aid, rec in articles.items():
            if aid in existing:
                continue
            if not isinstance(rec, dict) or rec.get("status") != "done":
                continue
            if not rec.get("title"):
                continue
            self._insert_one_article(conn, aid, rec)
            inserted += 1

        if inserted > 0:
            conn.commit()
            logger.info("增量插入 %d 篇新文章", inserted)

    def _sync_deletions(self, conn: sqlite3.Connection, articles: dict[str, Any]) -> None:
        """删除 SQLite 中已被 processed.json 移除的文章。"""
        article_keys = set(articles.keys())
        db_ids = {r[0] for r in conn.execute("SELECT article_id FROM articles").fetchall()}
        orphans = db_ids - article_keys
        if not orphans:
            return
        placeholders = ",".join("?" for _ in orphans)
        conn.execute(f"DELETE FROM articles WHERE article_id IN ({placeholders})", list(orphans))
        conn.execute(
            f"DELETE FROM articles_fts WHERE article_id IN ({placeholders})", list(orphans)
        )
        conn.commit()
        logger.info("清理 %d 篇已删除文章的索引", len(orphans))

    def _query_bm25(
        self, conn: sqlite3.Connection, article_id: str, limit: int
    ) -> list[tuple[str, float]]:
        """FTS5 BM25F 查询，返回 [(article_id, normalized_score), ...]。

        使用字段加权 BM25：title 权重 3.0, tags 2.5, concepts 2.0, key_points 1.5, summary 1.0。
        """
        # 从所有字段收集 token
        row = conn.execute(
            "SELECT title, tags, concepts, key_points, summary "
            "FROM articles_fts WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        if not row:
            return []

        # 合并所有字段的 token，按频率排序取 top-K
        all_tokens: list[str] = []
        for field_text in row:
            if field_text:
                all_tokens.extend(field_text.split())
        freq: dict[str, int] = {}
        for t in all_tokens:
            if len(t) >= 2 and not all(c in _PUNCTUATION for c in t):
                freq[t] = freq.get(t, 0) + 1

        sorted_tokens = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        top_tokens = [t for t, _ in sorted_tokens[:TOP_K_KEYWORDS]]
        if not top_tokens:
            return []

        # 用双引号包裹每个 token 作为短语查询，避免 FTS5 运算符解释
        query = " OR ".join(f'"{t}"' for t in top_tokens)

        # BM25F: bm25() 接受每列的权重参数，顺序对应 FTS5 表的列
        # 列顺序: title, tags, concepts, key_points, summary
        w = FIELD_WEIGHTS
        weights_sql = (
            f"{w['title']}, {w['tags']}, {w['concepts']}, {w['key_points']}, {w['summary']}"
        )
        try:
            rows = conn.execute(
                f"SELECT article_id, bm25(articles_fts, {weights_sql}) AS score "
                "FROM articles_fts WHERE articles_fts MATCH ? "
                "AND article_id != ? "
                "ORDER BY score LIMIT ?",
                (query, article_id, limit),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("BM25 查询失败 [%s]: %s", article_id, e)
            return []

        results: list[tuple[str, float]] = []
        for aid, score in rows:
            # FTS5 bm25 返回负数（越小越相关），sigmoid 归一化到 (0, 1)
            normalized = 1.0 / (1.0 + math.exp(score))
            results.append((aid, normalized))
        return results

    def _load_all_meta(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        """批量加载所有文章元数据到内存。"""
        rows = conn.execute(
            "SELECT article_id, title, category, sub_topic, source, concepts, tags FROM articles"
        ).fetchall()
        meta: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                concepts = json.loads(row[5]) if row[5] else []
            except (json.JSONDecodeError, TypeError):
                concepts = []
            try:
                tags = json.loads(row[6]) if row[6] else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            meta[row[0]] = {
                "title": row[1],
                "category": row[2],
                "sub_topic": row[3],
                "source": row[4] or "",
                "concepts": concepts,
                "tags": tags,
            }
        return meta

    def _find_reverse_candidates_fts(
        self,
        conn: sqlite3.Connection,
        new_ids: set[str],
        all_ids: set[str],
    ) -> set[str]:
        """用新文章的 BM25 查询结果发现需要反向更新的旧文章（spec 4.3 有界策略）。"""
        old_ids = all_ids - new_ids
        if not old_ids:
            return set()

        reverse_candidates: set[str] = set()
        for nid in new_ids:
            # 复用 BM25 查询，取更多候选以覆盖反向关联
            hits = self._query_bm25(conn, nid, TOP_K_KEYWORDS * 3)
            for cand_id, _score in hits:
                if cand_id in old_ids:
                    reverse_candidates.add(cand_id)
        return reverse_candidates

    def _score_and_rank(
        self,
        source_id: str,
        source_meta: dict[str, Any],
        bm25_hits: list[tuple[str, float]],
        all_meta: dict[str, dict[str, Any]],
        top_n: int,
        threshold: float,
    ) -> list[str]:
        """RRF 融合 BM25 + 概念 + 标签三个信号，返回 top_n 篇关联文章标题。

        RRF 公式: score = sum(w_i / (k + rank_i))。
        k 随语料库大小线性插值：k = 15 + 45 * min(1, N/300)。
        对每个信号独立排序，再按 RRF 融合排名，避免不同信号分数不可比的问题。
        """
        if not bm25_hits:
            return []

        # --- 信号 1: BM25 排名（已按分数排序） ---
        bm25_ranked = [aid for aid, _ in bm25_hits]

        # --- 信号 2: 概念相似度排名 ---
        concept_scores: list[tuple[float, str]] = []
        for cand_id, _ in bm25_hits:
            meta_b = all_meta.get(cand_id)
            if not meta_b:
                continue
            score = _fuzzy_concept_score(source_meta["concepts"], meta_b["concepts"])
            concept_scores.append((score, cand_id))
        concept_scores.sort(reverse=True)
        concept_ranked = [aid for _, aid in concept_scores]

        # --- 信号 3: 标签相似度排名 ---
        tag_scores: list[tuple[float, str]] = []
        for cand_id, _ in bm25_hits:
            meta_b = all_meta.get(cand_id)
            if not meta_b:
                continue
            score = _token_jaccard(source_meta["tags"], meta_b["tags"])
            tag_scores.append((score, cand_id))
        tag_scores.sort(reverse=True)
        tag_ranked = [aid for _, aid in tag_scores]

        # --- RRF 融合 ---
        rrf_scores: dict[str, float] = {}
        # k 平滑插值：语料库越大，排名差异越被拉平
        corpus_size = len(all_meta)
        k = RRF_K_MIN + (RRF_K_MAX - RRF_K_MIN) * min(1.0, corpus_size / RRF_K_REF)

        for rank, aid in enumerate(bm25_ranked, 1):
            rrf_scores[aid] = rrf_scores.get(aid, 0.0) + 1.0 / (k + rank)
        for rank, aid in enumerate(concept_ranked, 1):
            rrf_scores[aid] = rrf_scores.get(aid, 0.0) + 0.8 / (k + rank)
        for rank, aid in enumerate(tag_ranked, 1):
            rrf_scores[aid] = rrf_scores.get(aid, 0.0) + 0.6 / (k + rank)

        # --- 结构化加成 ---
        for aid in rrf_scores:
            meta_b = all_meta.get(aid)
            if not meta_b:
                continue
            if source_meta.get("source") and source_meta["source"] == meta_b.get("source"):
                rrf_scores[aid] += BONUS_SAME_SOURCE
            if source_meta["category"] == meta_b["category"]:
                rrf_scores[aid] += BONUS_SAME_CATEGORY
            if source_meta["sub_topic"] and source_meta["sub_topic"] == meta_b["sub_topic"]:
                rrf_scores[aid] += BONUS_SUB_TOPIC

        # --- 排序 + 阈值过滤 ---
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        return [
            all_meta[aid]["title"] for aid, score in sorted_results[:top_n] if score >= threshold
        ]

    def compute_related(
        self,
        articles: dict[str, Any],
        new_ids: set[str] | None = None,
        top_n: int = 3,
        threshold: float = 0.1,
    ) -> dict[str, list[str]]:
        """计算文章间的关联，返回 {article_id: [related_article_title, ...]}。

        仅返回文章关联，不包含概念页面。接口签名与原函数完全一致。
        """
        conn = self._ensure_db(articles)

        all_ids = {r[0] for r in conn.execute("SELECT article_id FROM articles").fetchall()}
        if len(all_ids) < 2:
            return {}

        target_ids = new_ids & all_ids if new_ids else all_ids
        if not target_ids:
            return {}

        # 批量加载所有元数据，避免 N+1 查询
        all_meta = self._load_all_meta(conn)

        result: dict[str, list[str]] = {}

        # 正向：为每篇目标文章计算关联
        for tid in target_ids:
            bm25_hits = self._query_bm25(conn, tid, top_n * 3)
            if not bm25_hits:
                result[tid] = []
                continue
            meta_a = all_meta.get(tid)
            if not meta_a:
                continue
            result[tid] = self._score_and_rank(tid, meta_a, bm25_hits, all_meta, top_n, threshold)

        # 反向：用 FTS5 发现与新文章相关的旧文章，更新旧文章的推荐
        if new_ids:
            reverse_candidates = self._find_reverse_candidates_fts(conn, new_ids, all_ids)
            for old_id in reverse_candidates:
                bm25_hits = self._query_bm25(conn, old_id, top_n * 3)
                if not bm25_hits:
                    continue
                old_meta = all_meta.get(old_id)
                if not old_meta:
                    continue
                result[old_id] = self._score_and_rank(
                    old_id, old_meta, bm25_hits, all_meta, top_n, threshold
                )

        related_count = sum(1 for v in result.values() if v)
        if related_count > 0:
            logger.info("关联计算完成：%d 篇文章建立了关联", related_count)

        return result


# ---------------------------------------------------------------------------
# 模块级包装函数（对外接口，保持向后兼容）
# ---------------------------------------------------------------------------

_engine: SimilarityEngine | None = None


def compute_related(
    articles: dict[str, Any],
    new_ids: set[str] | None = None,
    top_n: int = 3,
    threshold: float = 0.1,
    db_path: Path | None = None,
) -> dict[str, list[str]]:
    """计算文章间的关联。模块级函数，内部委托给 SimilarityEngine 实例。"""
    global _engine
    if _engine is None:
        _engine = SimilarityEngine(db_path=db_path)
    return _engine.compute_related(articles, new_ids, top_n, threshold)
