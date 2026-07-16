"""RRF 融合的纯函数行为。"""
from app.retrieval import rrf_fuse


def _hits(prefix: str, n: int, score_key: str):
    return [
        {"id": f"{prefix}{i}", "text": f"doc-{prefix}{i}", score_key: 1.0 - i * 0.1}
        for i in range(n)
    ]


def test_both_lists_top_ranked_doc_wins():
    vec = _hits("a", 3, "distance")
    bm = [dict(vec[1]), *_hits("b", 2, "bm25_score")]  # a1 同时出现在两路的第一名
    fused = rrf_fuse(vec, bm, top_n=10)
    assert fused[0]["id"] == "a1"  # 双路都召回的文档 RRF 分最高


def test_dedup_and_field_merge():
    vec = [{"id": "x", "text": "t", "distance": 0.2}]
    bm = [{"id": "x", "text": "t", "bm25_score": 7.5}]
    fused = rrf_fuse(vec, bm, top_n=10)
    assert len(fused) == 1
    # 两路的分数字段合并到同一条
    assert fused[0]["distance"] == 0.2
    assert fused[0]["bm25_score"] == 7.5
    assert fused[0]["rrf_score"] > 0


def test_top_n_truncation():
    fused = rrf_fuse(_hits("a", 5, "distance"), _hits("b", 5, "bm25_score"), top_n=3)
    assert len(fused) == 3


def test_missing_id_falls_back_to_text_prefix():
    a = [{"text": "同一段文本" * 20, "distance": 0.1}]
    b = [{"text": "同一段文本" * 20, "bm25_score": 3.0}]
    fused = rrf_fuse(a, b, top_n=10)
    assert len(fused) == 1
