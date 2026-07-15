"""Embedding 封装。

sentence_transformers（连带 torch，导入要 2-5 秒）放在函数内懒加载：
- 服务启动不被拖慢，/healthz 立即可用
- 第一次真正需要向量检索时才加载模型
"""
from .config import get_settings

_model = None


def get_embedder():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        s = get_settings()
        _model = SentenceTransformer(s.embedding_model)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    vecs = model.encode(texts, normalize_embeddings=True)
    return vecs.tolist()
