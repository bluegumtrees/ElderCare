"""BM25 稀疏检索索引。

为什么需要 BM25：
- 向量检索（dense）擅长「语义相似」，但对「精确词匹配」弱
- 例："二甲双胍能吃吗" 向量可能召回"糖尿病饮食"却漏掉真的提到"二甲双胍"的片段
- BM25（sparse）按词频/IDF 打分，精确词匹配能力强，正好和向量互补

实现要点：
- 中文用 jieba 分词
- 启动时一次性从 Chroma 全量拉文档，构建索引（per collection）
- 索引常驻内存，查询毫秒级
- 注意：ingest 新数据后需要重启 uvicorn 才能让 BM25 看到新数据
"""
import numpy as np
import jieba
from rank_bm25 import BM25Okapi

from .vector_store import get_or_create_collection

_indexes: dict[str, "BM25Index"] = {}


def _tokenize(text: str) -> list[str]:
    """中文分词 + 过滤空白/单字符。"""
    tokens = jieba.lcut(text)
    return [t for t in tokens if t.strip() and len(t.strip()) >= 1]


class BM25Index:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self.ids: list[str] = []
        self.docs: list[str] = []
        self.metadatas: list[dict] = []
        self.bm25: BM25Okapi | None = None

    def build(self) -> None:
        """从 Chroma 全量拉文档，分词构建 BM25 索引。"""
        col = get_or_create_collection(self.collection_name)
        data = col.get()  # 默认拉全量
        self.ids = data.get("ids") or []
        self.docs = data.get("documents") or []
        metas = data.get("metadatas")
        self.metadatas = metas if metas else [{} for _ in self.ids]

        if not self.docs:
            self.bm25 = None
            print(f"[bm25] collection='{self.collection_name}' 为空，跳过构建", flush=True)
            return

        tokenized = [_tokenize(d) for d in self.docs]
        self.bm25 = BM25Okapi(tokenized)
        print(
            f"[bm25] collection='{self.collection_name}' 构建完成，{len(self.docs)} 个文档",
            flush=True,
        )

    def search(self, query: str, k: int = 20) -> list[dict]:
        if self.bm25 is None or not self.docs:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        if scores.max() <= 0:
            return []
        top_idx = np.argsort(scores)[::-1][:k]
        return [
            {
                "id": self.ids[i],
                "text": self.docs[i],
                "metadata": self.metadatas[i] or {},
                "bm25_score": float(scores[i]),
            }
            for i in top_idx
            if scores[i] > 0
        ]


def get_bm25_index(collection_name: str) -> BM25Index:
    if collection_name not in _indexes:
        idx = BM25Index(collection_name)
        idx.build()
        _indexes[collection_name] = idx
    return _indexes[collection_name]


def init_all_indexes(collections: list[str]) -> None:
    """启动时一次性构建所有 collection 的 BM25 索引。"""
    print("[bm25] 开始构建索引（首次会触发 jieba 词典加载）...", flush=True)
    for c in collections:
        get_bm25_index(c)
