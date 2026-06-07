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

# 权重
W_BM25 = 0.50
W_CONCEPT = 0.20
W_TAG = 0.20
BONUS_SUB_TOPIC = 0.10

# BM25 查询参数
TOP_K_KEYWORDS = 20

# 标点符号集合（用于过滤 BM25 关键词）
_PUNCTUATION = set("、，。！？；：''【】（）《》…—·,.!?;:\"'()[]{}<> \t\n")

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

    jieba 可用时使用 jieba，否则降级到正则分词。
    """
    jb = _get_jieba()
    if jb is not None:
        tokens = jb.lcut(text)
        return " ".join(t for t in tokens if t.strip())
    # 正则分词 fallback
    cn = RE_CN_TOKEN.findall(text)
    en = [w.lower() for w in RE_EN_TOKEN.findall(text)]
    return " ".join(cn + en)


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


def _score_pair(
    meta_a: dict[str, Any],
    meta_b: dict[str, Any],
    bm25_score: float,
) -> float:
    """计算两篇文章的融合相似度分数。"""
    concept_sim = _fuzzy_concept_score(meta_a["concepts"], meta_b["concepts"])
    tag_sim = _token_jaccard(meta_a["tags"], meta_b["tags"])
    bonus = (
        BONUS_SUB_TOPIC
        if meta_a["sub_topic"] and meta_a["sub_topic"] == meta_b["sub_topic"]
        else 0.0
    )
    return W_BM25 * bm25_score + W_CONCEPT * concept_sim + W_TAG * tag_sim + bonus


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
        """将一篇文章插入 articles 表和 articles_fts 表。"""
        concepts = [c.get("name", "") for c in (rec.get("concepts") or []) if isinstance(c, dict)]
        tags = rec.get("tags") or []
        conn.execute(
            "INSERT OR IGNORE INTO articles "
            "(article_id, title, category, sub_topic, concepts, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                aid,
                rec.get("title", ""),
                rec.get("category", ""),
                rec.get("sub_topic", ""),
                json.dumps(concepts),
                json.dumps(tags),
            ),
        )

        text = " ".join(
            filter(
                None,
                [
                    rec.get("summary", ""),
                    " ".join(rec.get("key_points") or []),
                    " ".join(concepts),
                    " ".join(tags),
                ],
            )
        )
        tokenized = _tokenize(text)
        conn.execute(
            "INSERT INTO articles_fts (article_id, content) VALUES (?, ?)",
            (aid, tokenized),
        )

    def _build_fts_index(self, conn: sqlite3.Connection, articles: dict[str, Any]) -> None:
        """构建 SQLite 表和 FTS5 全文索引。"""
        conn.execute("DROP TABLE IF EXISTS articles")
        conn.execute("DROP TABLE IF EXISTS articles_fts")
        conn.execute("""
            CREATE TABLE articles (
                article_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT,
                sub_topic TEXT,
                concepts TEXT,
                tags TEXT
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE articles_fts USING fts5(
                article_id UNINDEXED,
                content,
                tokenize='unicode61'
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
        """FTS5 BM25 查询，返回 [(article_id, normalized_score), ...]。"""
        row = conn.execute(
            "SELECT content FROM articles_fts WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        if not row:
            return []

        tokens = row[0].split()
        freq: dict[str, int] = {}
        for t in tokens:
            if len(t) >= 2 and not all(c in _PUNCTUATION for c in t):
                freq[t] = freq.get(t, 0) + 1

        sorted_tokens = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        top_tokens = [t for t, _ in sorted_tokens[:TOP_K_KEYWORDS]]
        if not top_tokens:
            return []

        # 用双引号包裹每个 token 作为短语查询，避免 FTS5 运算符解释
        query = " OR ".join(f'"{t}"' for t in top_tokens)
        try:
            rows = conn.execute(
                "SELECT article_id, bm25(articles_fts) AS score "
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
            normalized = 1.0 / (1.0 + math.exp(score))
            results.append((aid, normalized))
        return results

    def _load_all_meta(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        """批量加载所有文章元数据到内存。"""
        rows = conn.execute(
            "SELECT article_id, title, category, sub_topic, concepts, tags FROM articles"
        ).fetchall()
        meta: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                concepts = json.loads(row[4]) if row[4] else []
            except (json.JSONDecodeError, TypeError):
                concepts = []
            try:
                tags = json.loads(row[5]) if row[5] else []
            except (json.JSONDecodeError, TypeError):
                tags = []
            meta[row[0]] = {
                "title": row[1],
                "category": row[2],
                "sub_topic": row[3],
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
        """对 BM25 候选做融合评分，返回 top_n 篇关联文章标题。"""
        scores: list[tuple[float, str]] = []
        for cand_id, bm25_score in bm25_hits:
            meta_b = all_meta.get(cand_id)
            if not meta_b:
                continue
            score = _score_pair(source_meta, meta_b, bm25_score)
            if score >= threshold:
                scores.append((score, meta_b["title"]))

        scores.sort(reverse=True)
        return [title for _, title in scores[:top_n]]

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
