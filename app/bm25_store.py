"""BM25 稀疏检索索引。

为什么需要 BM25：
- 向量检索（dense）擅长「语义相似」，但对「精确词匹配」弱
- 例："二甲双胍能吃吗" 向量可能召回"糖尿病饮食"却漏掉真的提到"二甲双胍"的片段
- BM25（sparse）按词频/IDF 打分，精确词匹配能力强，正好和向量互补

实现要点：
- 中文用 jieba 分词
- 索引常驻内存，查询毫秒级
- 磁盘 pickle 缓存：首次构建后落盘，之后启动直接 load（15k 文档的
  全量分词从 ~10s 降到 <1s）。用 collection 的文档数做失效判断，
  ingest 新数据后文档数变化会自动触发重建。
"""
import pickle
from pathlib import Path

import numpy as np
import jieba
from rank_bm25 import BM25Okapi

from .config import get_settings
from .vector_store import get_or_create_collection

_indexes: dict[str, "BM25Index"] = {}

_CACHE_VERSION = 1


def _cache_path(collection_name: str) -> Path:
    return Path(get_settings().chroma_dir) / f"bm25_{collection_name}.pkl"


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
        """优先从磁盘缓存加载；缓存缺失/过期时从 Chroma 全量重建并落盘。"""
        col = get_or_create_collection(self.collection_name)
        doc_count = col.count()

        if self._load_cache(doc_count):
            print(
                f"[bm25] collection='{self.collection_name}' 命中缓存，{doc_count} 个文档",
                flush=True,
            )
            return

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
        self._save_cache(doc_count)
        print(
            f"[bm25] collection='{self.collection_name}' 构建完成，{len(self.docs)} 个文档",
            flush=True,
        )

    def _load_cache(self, expected_count: int) -> bool:
        path = _cache_path(self.collection_name)
        if not path.exists() or expected_count == 0:
            return False
        try:
            with path.open("rb") as f:
                cached = pickle.load(f)
            if cached.get("version") != _CACHE_VERSION or cached.get("count") != expected_count:
                return False
            self.ids = cached["ids"]
            self.docs = cached["docs"]
            self.metadatas = cached["metadatas"]
            self.bm25 = cached["bm25"]
            return True
        except Exception as e:
            print(f"[bm25] 缓存读取失败，回退全量构建：{e!r}", flush=True)
            return False

    def _save_cache(self, doc_count: int) -> None:
        path = _cache_path(self.collection_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                pickle.dump(
                    {
                        "version": _CACHE_VERSION,
                        "count": doc_count,
                        "ids": self.ids,
                        "docs": self.docs,
                        "metadatas": self.metadatas,
                        "bm25": self.bm25,
                    },
                    f,
                )
        except OSError as e:
            # 只读文件系统等场景不致命，下次启动重建就是
            print(f"[bm25] 缓存写入失败（忽略）：{e!r}", flush=True)

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
