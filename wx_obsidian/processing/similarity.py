"""文章相似度计算：基于概念重叠、标签 Jaccard、TF-IDF 余弦的混合关联。"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

# 中文 2-6 字 + 英文 3+ 字
RE_CN_TOKEN = re.compile(r"[一-鿿]{2,6}")
RE_EN_TOKEN = re.compile(r"[a-zA-Z]{3,}")

# 默认权重
W_CONCEPT = 0.4
W_TAG = 0.3
W_TFIDF = 0.3


# ---------------------------------------------------------------------------
# 分词
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """从文本中提取关键词集合（中文 2-6 字，英文 3+ 字小写）。"""
    cn = set(RE_CN_TOKEN.findall(text))
    en = {w.lower() for w in RE_EN_TOKEN.findall(text)}
    return cn | en


# ---------------------------------------------------------------------------
# 相似度计算
# ---------------------------------------------------------------------------


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard 相似度。"""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _tfidf_cosine(texts: list[str]) -> list[list[float]]:
    """批量计算 TF-IDF 余弦相似度矩阵。

    Args:
        texts: 每篇文章的文本（摘要 + 核心观点拼接）。

    Returns:
        N×N 相似度矩阵，对角线为 0。
    """
    n = len(texts)
    if n == 0:
        return []

    tokenized = [_tokenize(t) for t in texts]

    # 构建 IDF
    doc_freq: dict[str, int] = {}
    for tokens in tokenized:
        for t in tokens:
            doc_freq[t] = doc_freq.get(t, 0) + 1

    # TF-IDF 向量
    vectors: list[dict[str, float]] = []
    for tokens in tokenized:
        tf: dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        total = len(tokens) if tokens else 1
        tfidf = {}
        for t, count in tf.items():
            idf = math.log(n / doc_freq[t]) + 1.0
            tfidf[t] = (count / total) * idf
        vectors.append(tfidf)

    # 余弦相似度矩阵
    result = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine_similarity(vectors[i], vectors[j])
            result[i][j] = sim
            result[j][i] = sim

    return result


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """两个稀疏向量的余弦相似度。"""
    common_keys = set(vec_a) & set(vec_b)
    if not common_keys:
        return 0.0

    dot = sum(vec_a[k] * vec_b[k] for k in common_keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_related(
    articles: dict[str, Any],
    new_ids: set[str] | None = None,
    top_n: int = 3,
    threshold: float = 0.1,
) -> dict[str, list[str]]:
    """计算文章间的关联，返回 {article_id: [related_titles]}。

    Args:
        articles: processed.json 的完整数据。
        new_ids: 仅计算这些文章 ID 的关联（None = 全部）。
        top_n: 每篇文章最多关联几篇。
        threshold: 低于此相似度不关联。

    Returns:
        {article_id: [related_article_title, ...]}
    """
    # 收集候选文章（有 summary 且 status=done）
    candidates: list[tuple[str, str, set[str], set[str], str]] = []
    for aid, rec in articles.items():
        if not isinstance(rec, dict):
            continue
        if rec.get("status") != "done":
            continue
        title = rec.get("title", "")
        if not title:
            continue
        concepts = {c.get("name", "") for c in (rec.get("concepts") or []) if isinstance(c, dict)}
        tags = set(rec.get("tags") or [])
        summary = rec.get("summary", "")
        candidates.append((aid, title, concepts, tags, summary))

    if len(candidates) < 2:
        return {}

    # 提取文本用于 TF-IDF
    texts = [c[4] for c in candidates]
    tfidf_matrix = _tfidf_cosine(texts)

    # 计算每对文章的综合得分
    target_ids = new_ids or {c[0] for c in candidates}
    result: dict[str, list[str]] = {}

    for i, (aid_i, _title_i, concepts_i, tags_i, _) in enumerate(candidates):
        if aid_i not in target_ids:
            continue

        scores: list[tuple[float, str]] = []
        for j, (_aid_j, title_j, concepts_j, tags_j, _) in enumerate(candidates):
            if i == j:
                continue

            concept_sim = _jaccard(concepts_i, concepts_j)
            tag_sim = _jaccard(tags_i, tags_j)
            tfidf_sim = tfidf_matrix[i][j]

            score = W_CONCEPT * concept_sim + W_TAG * tag_sim + W_TFIDF * tfidf_sim
            if score >= threshold:
                scores.append((score, title_j))

        scores.sort(reverse=True)
        result[aid_i] = [title for _, title in scores[:top_n]]

    related_count = sum(1 for v in result.values() if v)
    if related_count > 0:
        logger.info("关联计算完成：%d 篇文章建立了关联", related_count)

    return result
