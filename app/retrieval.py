"""检索编排：向量 + BM25 → RRF 融合 → CrossEncoder 重排。

三阶段流水线（工业 RAG 标配）：
  Stage 1: 双路并行召回（dense + sparse）—— 互补，避免单一方法的盲区
  Stage 2: RRF（Reciprocal Rank Fusion）—— 不用分数对齐，按排名融合
  Stage 3: Cross-Encoder 重排 —— 精排，把真正最相关的送给 LLM
"""
from .bm25_store import get_bm25_index
from .config import get_settings
from .reranker import rerank as rerank_items
from .vector_store import query as vector_query


def rrf_fuse(
    *result_lists: list[dict],
    k: int = 60,
    top_n: int = 20,
) -> list[dict]:
    """Reciprocal Rank Fusion。

    对每个文档：score = sum_i 1 / (k + rank_i)
    - k=60 是 RRF 论文里的经典默认值
    - 优点：不需要不同检索器的分数对齐（向量距离和 BM25 score 量纲完全不同）
    - 只看排名位置，越靠前贡献越大
    """
    score_map: dict[str, float] = {}
    item_map: dict[str, dict] = {}

    for results in result_lists:
        for rank, item in enumerate(results):
            iid = item.get("id") or item["text"][:80]
            score_map[iid] = score_map.get(iid, 0.0) + 1.0 / (k + rank)
            if iid not in item_map:
                item_map[iid] = dict(item)
            else:
                # 把不同检索器带来的额外字段合并（比如向量给 distance，BM25 给 bm25_score）
                for key, val in item.items():
                    if key not in item_map[iid]:
                        item_map[iid][key] = val

    top_ids = sorted(score_map.keys(), key=lambda i: -score_map[i])[:top_n]
    out = []
    for tid in top_ids:
        merged = item_map[tid]
        merged["rrf_score"] = score_map[tid]
        out.append(merged)
    return out


def retrieve(collection: str, q: str, top_n: int | None = None) -> list[dict]:
    """三阶段检索：dense + sparse → RRF → rerank。

    返回 list[dict]，每项含 id / text / metadata / distance? / bm25_score? / rrf_score / rerank_score
    （?表示该项是否存在取决于这条 doc 被哪路检索器召回）
    """
    s = get_settings()
    final_n = top_n or s.retrieve_top_n

    # Stage 1: 双路召回
    vec_hits = vector_query(collection, q, k=s.retrieve_candidate_k)
    bm25_hits = get_bm25_index(collection).search(q, k=s.retrieve_candidate_k)

    if not vec_hits and not bm25_hits:
        return []

    # Stage 2: RRF 融合
    fused = rrf_fuse(vec_hits, bm25_hits, top_n=s.retrieve_candidate_k)
    if not fused:
        return []

    # Stage 3: CrossEncoder 重排
    return rerank_items(q, fused, top_n=final_n)
