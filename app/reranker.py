"""Cross-Encoder 重排序。

为什么需要 rerank：
- 向量检索（双塔）快但不精——query 和 doc 各自编码，靠余弦相似度近似
- Cross-Encoder（单塔）慢但精——query+doc 一起进 Transformer，能学到细粒度匹配
- 工业最佳实践：用向量做"宽召回 Top-K"，用 Rerank 做"精排 Top-N"

模型选择：BAAI/bge-reranker-base ≈ 280M 参数，中文效果稳，首次下载约 1.1GB。
sentence_transformers 懒加载（同 embeddings.py），不拖慢服务启动。
"""
from .config import get_settings

_model = None


def get_reranker():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        s = get_settings()
        _model = CrossEncoder(s.reranker_model)
    return _model


def rerank(query: str, items: list[dict], top_n: int = 3) -> list[dict]:
    """对 items 用 CrossEncoder 二次打分，返回 top_n 个最相关的。

    每个 item 会被注入 `rerank_score` 字段，并按该字段从高到低排序。
    items 至少要含 'text' 字段。
    """
    if not items:
        return []

    model = get_reranker()
    pairs = [[query, item["text"]] for item in items]
    scores = model.predict(pairs)

    for item, score in zip(items, scores):
        item["rerank_score"] = float(score)

    return sorted(items, key=lambda x: -x["rerank_score"])[:top_n]
