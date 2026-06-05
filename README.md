# ElderCare 银发陪伴智能体

面向独居老人的 AI 陪伴助手。基于 **FastAPI + 动态路由 RAG** 构建，将闲聊 / 健康咨询 / 心理倾诉 / 医疗急症 / 电信诈骗五类意图自动分诊；**三阶段检索**（dense + sparse → RRF → CrossEncoder rerank）保证 RAG 质量，**答案附带可点击引用源**使回答完全可解释；高风险场景实时邮件通知家属。

**在线 Demo**：https://huggingface.co/spaces/bluegum/eldercare-rag
（首次访问会有约 30 秒冷启动，建议演示前先打开预热）

## 核心特性

| 模块 | 能力 |
|---|---|
| 动态路由 RAG | 5 类意图分诊，意图 × 风险二维分类，一次 LLM 调用同时输出 |
| 三阶段检索 | 向量 + BM25 双路召回 → RRF 融合 → BGE-reranker 精排 |
| 引用溯源 | LLM 输出 `[1] [2]` 标号，前端点击徽章跳转到底层 chunk 并高亮 |
| 异步预警 | 高危场景 `asyncio.create_task` 派发邮件，不阻塞流式响应 |
| 三级日志 | INFO / WARN / ALERT，避免家属告警疲劳 |
| 浏览器无障碍 | Web Speech API 实现中文语音输入输出，零依赖、零成本 |
| 量化评测 | LLM-as-judge 在 30 条评测集上对比三条检索流水线 |

## 评测结果

30 条评测集 × 3 条流水线 × 3 个 RAGAS 风格指标（LLM-as-judge）：

| 指标 | 纯向量 | +rerank | +hybrid+rerank |
|---|---|---|---|
| **Context Precision** | 75.6% | **78.9%** | 77.8% |
| Faithfulness | 83.3% | 80.0% | 76.7% |
| Answer Relevance | 4.63/5 | 4.60/5 | 4.70/5 |

Rerank 取得 **+3.3 pts** 的实证收益；Hybrid 在问答用词高度对齐的语料上无显著增益（分析见 `scripts/eval_rag.py` 报告）。

## 架构

```
用户输入
   |
意图分类 LLM  -->  {intent, risk_level} JSON
   |
+- CHAT       -> 裸 LLM 流式
+- HEALTH     -> 三阶段检索（health 库）-> RAG prompt 带 [1][2][3]
+- PSYCH      -> 三阶段检索（psych 库） -> 按 risk 选 prompt
+- EMERGENCY  -> 跳过 RAG -> 固定模板 + 异步邮件
+- FRAUD      -> 跳过 RAG -> 固定模板 + 异步邮件

三阶段检索：
  Dense (BGE-small-zh)  -> Top-20
  Sparse (jieba + BM25) -> Top-20
  RRF (k=60)            -> Top-20
  CrossEncoder Rerank   -> Top-3 -> LLM
```

## 技术栈

- **后端**：Python 3.11 / FastAPI / Pydantic / SSE 流式
- **向量库**：ChromaDB（HNSW + cosine）
- **Embedding**：BAAI/bge-small-zh-v1.5（本地）
- **Reranker**：BAAI/bge-reranker-base（本地 CrossEncoder）
- **稀疏检索**：rank-bm25 + jieba
- **LLM**：DeepSeek-Chat via OpenRouter
- **数据**：SQLite + aiosmtplib + openpyxl
- **前端**：原生 HTML / CSS / JS + Web Speech API

## 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 在 .env 中填入 OPENROUTER_API_KEY

# 3. 构建知识库（首次必做）
python scripts/ingest.py
python scripts/ingest_psych.py

# 可选：接入真实开源数据集
python scripts/ingest_dataset.py \
    --hf-dataset michaelwzhu/ChatMed_Consult_Dataset \
    --collection health --question-field query --answer-field response \
    --max-rows 10000

# 4. 启动服务
uvicorn app.main:app --reload --port 8000
```

访问 http://localhost:8000

## 知识库

| Collection | 数据集 | 条数 |
|---|---|---|
| `health` | [michaelwzhu/ChatMed_Consult_Dataset](https://huggingface.co/datasets/michaelwzhu/ChatMed_Consult_Dataset) | 10000 |
| `psych` | [liuzj288/PsyQA](https://huggingface.co/datasets/liuzj288/PsyQA) | 5000 |

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENROUTER_API_KEY` | 是 | OpenRouter API key（HF Spaces 在 Secrets 配置） |
| `CHAT_MODEL` | 否 | 默认 `deepseek/deepseek-chat` |
| `SMTP_HOST/USER/PASSWORD/...` | 否 | 邮件预警，未配置时打印到 stdout |

## 项目结构

```
app/                # 后端
├── main.py         # FastAPI 入口
├── intent.py       # 意图分类
├── retrieval.py    # 三阶段检索编排
├── bm25_store.py   # BM25 稀疏索引
├── reranker.py     # CrossEncoder 精排
├── vector_store.py # Chroma 包装
├── notifier.py     # 异步邮件预警
├── templates.py    # Prompt 与模板
└── routers/        # /agent /chat_rag /admin

static/             # 前端
scripts/            # 数据接入与评测
```

## 评测

```bash
python scripts/eval_intent.py    # 意图分类准确率
python scripts/eval_rag.py       # 三条流水线 RAGAS 风格对比
```

报告输出至 `reports/eval_<时间戳>.{json,md}`。

## 免责声明

本项目仅用于学习与技术演示，**不能替代医生诊断或心理咨询师**。模型输出仅供参考，紧急情况请拨打 120 或心理援助热线 **400-161-9995**。

## 数据致谢

- [ChatMed_Consult_Dataset](https://huggingface.co/datasets/michaelwzhu/ChatMed_Consult_Dataset)（michaelwzhu）
- [PsyQA](https://huggingface.co/datasets/liuzj288/PsyQA)（liuzj288 社区镜像 / 原 thu-coai/PsyQA）
- [BGE 系列模型](https://huggingface.co/BAAI)（BAAI）

## License

[MIT](LICENSE)
