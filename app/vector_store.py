"""ChromaDB 包装。chromadb 懒导入：模块导入即拉起 onnxruntime 等重依赖，
放到首次真正用到向量库时再加载，保证服务启动秒开。"""
from .config import get_settings
from .embeddings import embed

_client = None


def get_chroma():
    global _client
    if _client is None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        s = get_settings()
        _client = chromadb.PersistentClient(
            path=s.chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    return _client


def get_or_create_collection(name: str):
    return get_chroma().get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def add_documents(
    collection_name: str,
    ids: list[str],
    texts: list[str],
    metadatas: list[dict] | None = None,
) -> None:
    col = get_or_create_collection(collection_name)
    vecs = embed(texts)
    col.add(ids=ids, documents=texts, embeddings=vecs, metadatas=metadatas)


def query(collection_name: str, query_text: str, k: int = 4) -> list[dict]:
    col = get_or_create_collection(collection_name)
    qv = embed([query_text])[0]
    res = col.query(query_embeddings=[qv], n_results=k)
    ids = res.get("ids", [[]])[0]
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0] or [{}] * len(docs)
    dists = res.get("distances", [[]])[0] or [0.0] * len(docs)
    return [
        {"id": i, "text": d, "metadata": m or {}, "distance": float(dist)}
        for i, d, m, dist in zip(ids, docs, metas, dists)
    ]
