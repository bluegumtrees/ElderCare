# ElderCare 银发陪伴智能体 — HuggingFace Spaces Docker 镜像
#
# 策略：
#   1. 模型（BGE-small-zh + BGE-reranker-base）build 时预下载，烤进镜像
#   2. Chroma 数据（15000 条向量）build 时通过 COPY 烤进镜像
#   3. 启动只需加载常驻内存的模型 + BM25 索引重建（~10-15 秒）
#   4. SQLite 写到 /tmp（HF Spaces 容器内可写）
#
# 总镜像大小约 2.5-3 GB（torch + 两个模型 + chroma 数据）

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    CHROMA_DIR=/app/chroma_data \
    SQLITE_PATH=/tmp/app.db

# 极简系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖（独立 layer，便于缓存）
COPY requirements.txt .
RUN pip install -U pip && pip install -r requirements.txt

# 预下载 embedding + reranker 模型，把首次冷启动延迟移到 build 阶段
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-small-zh-v1.5')" \
 && python -c "from sentence_transformers import CrossEncoder; \
CrossEncoder('BAAI/bge-reranker-base')"

# 拷贝应用代码
COPY app/ ./app/
COPY static/ ./static/
COPY scripts/ ./scripts/

# 构建时现场 ingest 知识库，使镜像自包含、代码仓库无需提交向量数据。
# 先灌样例数据（无网络依赖，保证一定有数据），再尝试真实开源数据集
# （网络/字段异常时用 `|| echo` 兜底，不让整个 build 失败）。
RUN python scripts/ingest.py && python scripts/ingest_psych.py
RUN python scripts/ingest_dataset.py \
        --hf-dataset michaelwzhu/ChatMed_Consult_Dataset \
        --collection health --question-field query --answer-field response \
        --max-rows 10000 \
    || echo "[build] health 数据集 ingest 跳过，回退到样例数据"
RUN python scripts/ingest_dataset.py \
        --hf-dataset liuzj288/PsyQA \
        --collection psych --question-field description --answer-field answers \
        --max-rows 5000 \
    || echo "[build] psych 数据集 ingest 跳过，回退到样例数据"

# HF Spaces 默认端口
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
