"""RAG 评测脚本（RAGAS-style）。

对比三条检索流水线在同一份评测集上的表现：
  baseline       : 纯向量 Top-3
  +rerank        : 向量 Top-20 → CrossEncoder rerank → Top-3
  +hybrid+rerank : (向量 + BM25) → RRF → rerank → Top-3 （即线上配置）

三个 RAGAS 风格指标（LLM-as-judge）：
  Context Precision : 召回的 chunk 与 query 相关比例
  Faithfulness      : 答案是否完全由 context 支持
  Answer Relevance  : 答案是否切题（1-5）

用法:
    python scripts/eval_rag.py                 # 全量评测（30 条）
    python scripts/eval_rag.py --max-queries 5 # 小样本试跑
    python scripts/eval_rag.py --concurrency 3 # 调整并发

输出:
    控制台打印对比表
    reports/eval_<时间戳>.json 存每条 query 的详细结果
    reports/eval_<时间戳>.md   生成 markdown 报告
"""
import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm.asyncio import tqdm as tqdm_async  # noqa: E402

from app.bm25_store import get_bm25_index, init_all_indexes  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm import get_llm  # noqa: E402
from app.reranker import rerank as cross_encoder_rerank  # noqa: E402
from app.retrieval import rrf_fuse  # noqa: E402
from app.templates import HEALTH_PROMPT, PSYCH_PROMPT_LOW_MID  # noqa: E402
from app.vector_store import query as vector_query  # noqa: E402


# ============================================================================
# 评测集（30 条，覆盖 HEALTH + PSYCH + 难例）
# ============================================================================

EVAL_DATASET: list[dict] = [
    # ---- HEALTH：常见健康咨询 ----
    {"query": "我血压有点高吃什么好", "collection": "health"},
    {"query": "高血压能吃西瓜吗", "collection": "health"},
    {"query": "糖尿病人能吃水果吗", "collection": "health"},
    {"query": "最近睡眠不好怎么办", "collection": "health"},
    {"query": "膝盖疼痛是什么原因", "collection": "health"},
    {"query": "感冒发烧了吃什么药", "collection": "health"},
    {"query": "便秘有什么办法", "collection": "health"},
    {"query": "胃胀气吃什么", "collection": "health"},
    {"query": "腰酸背痛怎么缓解", "collection": "health"},
    {"query": "老咳嗽不停怎么办", "collection": "health"},
    # ---- HEALTH 难例：模糊或专业 ----
    {"query": "二甲双胍能停吗", "collection": "health"},
    {"query": "心慌心悸是什么病", "collection": "health"},
    {"query": "脚肿是怎么回事", "collection": "health"},
    {"query": "突然头晕怎么回事", "collection": "health"},
    {"query": "白内障要不要做手术", "collection": "health"},

    # ---- PSYCH：常见心理倾诉 ----
    {"query": "老伴走了我一个人好孤单", "collection": "psych"},
    {"query": "退休以后觉得自己没用了", "collection": "psych"},
    {"query": "最近总是失眠想很多", "collection": "psych"},
    {"query": "孩子在外地我很想念", "collection": "psych"},
    {"query": "感觉很压抑提不起精神", "collection": "psych"},
    {"query": "怕给孩子添麻烦不敢说自己难受", "collection": "psych"},
    {"query": "看到老朋友走了心里难受", "collection": "psych"},
    {"query": "总是担心自己的身体", "collection": "psych"},
    {"query": "心情低落怎么调节", "collection": "psych"},
    {"query": "对什么都提不起兴趣", "collection": "psych"},
    # ---- PSYCH 难例：情绪边界/隐晦 ----
    {"query": "总是想哭", "collection": "psych"},
    {"query": "感觉日子没意思", "collection": "psych"},
    {"query": "晚上一个人害怕", "collection": "psych"},
    {"query": "孤单的时候怎么办", "collection": "psych"},
    {"query": "邻居总是吵架我心情不好", "collection": "psych"},
]


# ============================================================================
# 三条 Pipeline
# ============================================================================

def pipeline_baseline(collection: str, q: str, top_n: int = 3) -> list[dict]:
    """纯向量 Top-3，最朴素的 RAG。"""
    return vector_query(collection, q, k=top_n)


def pipeline_rerank(collection: str, q: str, top_n: int = 3, candidates: int = 20) -> list[dict]:
    """向量宽召回 → CrossEncoder 重排 → Top-3。"""
    cands = vector_query(collection, q, k=candidates)
    if not cands:
        return []
    return cross_encoder_rerank(q, cands, top_n=top_n)


def pipeline_hybrid_rerank(collection: str, q: str, top_n: int = 3, candidates: int = 20) -> list[dict]:
    """完整三阶段：dense + sparse → RRF → rerank → Top-3。"""
    vec = vector_query(collection, q, k=candidates)
    bm25 = get_bm25_index(collection).search(q, k=candidates)
    if not vec and not bm25:
        return []
    fused = rrf_fuse(vec, bm25, top_n=candidates)
    if not fused:
        return []
    return cross_encoder_rerank(q, fused, top_n=top_n)


PIPELINES: dict[str, callable] = {
    "baseline": pipeline_baseline,
    "+rerank": pipeline_rerank,
    "+hybrid+rerank": pipeline_hybrid_rerank,
}


# ============================================================================
# LLM 调用与 Judges
# ============================================================================

async def call_llm(prompt: str, max_tokens: int = 300, temperature: float = 0.0) -> str:
    """非流式调用一次 LLM，返回完整字符串。"""
    s = get_settings()
    client = get_llm()
    try:
        resp = await client.chat.completions.create(
            model=s.chat_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"[LLM_ERROR: {e!r}]"


def _format_context(chunks: list[dict]) -> str:
    return "\n\n".join(f"[{i + 1}] {c['text']}" for i, c in enumerate(chunks))


async def generate_answer(collection: str, q: str, chunks: list[dict]) -> str:
    """用对应领域的 prompt 生成 RAG 回答。"""
    context = _format_context(chunks) if chunks else "（无相关资料）"
    template = HEALTH_PROMPT if collection == "health" else PSYCH_PROMPT_LOW_MID
    prompt = template.format(context=context, question=q)
    return await call_llm(prompt, max_tokens=500)


# ---------- Judge 1: Context Precision（每个 chunk 是否相关） ----------

JUDGE_RELEVANCE_PROMPT = """你是相关性评判员。判断下面这段「资料」对于回答「问题」是否有帮助。

问题：{query}

资料：{chunk}

只输出 yes 或 no，不要任何解释。"""


async def judge_chunk_relevance(query: str, chunk: dict) -> bool:
    out = await call_llm(
        JUDGE_RELEVANCE_PROMPT.format(query=query, chunk=chunk["text"][:800]),
        max_tokens=10,
    )
    return "yes" in out.lower() or "是" in out[:3]


# ---------- Judge 2: Faithfulness（答案是否被资料支持） ----------

JUDGE_FAITHFULNESS_PROMPT = """你是事实核查员。判断下面「回答」中的具体建议是否能由「参考资料」支持。

参考资料：
{context}

回答：
{answer}

判断标准：
- 共情、问候、温和的鼓励、建议联系医生/家人 这类通用话术不算事实主张，忽略它们
- 只看具体的医学/心理建议（吃什么、怎么做、量多少等）
- 如果回答里所有具体建议都能在参考资料里找到依据，回 yes
- 如果有任何一处具体建议是参考资料里没提到的，回 no

只输出 yes 或 no，不要解释。"""


async def judge_faithfulness(answer: str, context: str) -> bool:
    out = await call_llm(
        JUDGE_FAITHFULNESS_PROMPT.format(context=context[:3000], answer=answer[:1500]),
        max_tokens=10,
    )
    return "yes" in out.lower() or "是" in out[:3]


# ---------- Judge 3: Answer Relevance（答案是否切题，1-5） ----------

JUDGE_ANSWER_RELEVANCE_PROMPT = """判断下面「回答」是否切题地回应了「问题」。

问题：{query}

回答：{answer}

打分标准：
5 = 完全切题、有具体可行建议、语气合适
4 = 切题且有建议，但略偏题或建议不够具体
3 = 沾边但回答较空泛
2 = 大部分跑题
1 = 完全不切题

只输出一个 1-5 之间的整数，不要解释。"""


def _parse_score(text: str) -> int:
    m = re.search(r"[1-5]", text)
    return int(m.group(0)) if m else 3


async def judge_answer_relevance(query: str, answer: str) -> int:
    out = await call_llm(
        JUDGE_ANSWER_RELEVANCE_PROMPT.format(query=query, answer=answer[:1500]),
        max_tokens=10,
    )
    return _parse_score(out)


# ============================================================================
# 评测一条 query × 一条 pipeline
# ============================================================================

async def eval_one(
    sem: asyncio.Semaphore,
    item: dict,
    pipe_name: str,
    pipe_fn,
) -> dict:
    async with sem:
        q = item["query"]
        col = item["collection"]

        # Step 1: 检索（同步函数，包到 thread 里以免阻塞 event loop）
        t0 = time.perf_counter()
        chunks = await asyncio.to_thread(pipe_fn, col, q)
        retrieval_latency = time.perf_counter() - t0

        # Step 2: 生成答案
        t1 = time.perf_counter()
        answer = await generate_answer(col, q, chunks)
        gen_latency = time.perf_counter() - t1

        # Step 3: 并发跑 judges
        ctx_str = _format_context(chunks)
        judge_tasks = [
            judge_chunk_relevance(q, c) for c in chunks
        ] + [
            judge_faithfulness(answer, ctx_str),
            judge_answer_relevance(q, answer),
        ]
        results = await asyncio.gather(*judge_tasks)

        chunk_rels = list(results[: len(chunks)])
        faithful = bool(results[len(chunks)])
        relevance_score = int(results[len(chunks) + 1])

        ctx_precision = sum(chunk_rels) / len(chunk_rels) if chunk_rels else 0.0

        return {
            "pipeline": pipe_name,
            "query": q,
            "collection": col,
            "chunks": [{"text": c["text"][:200], "id": c.get("id")} for c in chunks],
            "chunk_relevance": chunk_rels,
            "context_precision": ctx_precision,
            "answer": answer,
            "faithful": faithful,
            "answer_relevance": relevance_score,
            "retrieval_latency_s": round(retrieval_latency, 3),
            "gen_latency_s": round(gen_latency, 3),
        }


# ============================================================================
# 汇总 + 报告
# ============================================================================

def aggregate(results: list[dict]) -> dict:
    """按 pipeline 聚合指标。"""
    by_pipe: dict[str, list[dict]] = {}
    for r in results:
        by_pipe.setdefault(r["pipeline"], []).append(r)

    summary = {}
    for pipe, rs in by_pipe.items():
        n = len(rs)
        ctx_p = sum(r["context_precision"] for r in rs) / n
        faith = sum(1 for r in rs if r["faithful"]) / n
        ans_rel = sum(r["answer_relevance"] for r in rs) / n
        retr_lat = sum(r["retrieval_latency_s"] for r in rs) / n
        gen_lat = sum(r["gen_latency_s"] for r in rs) / n
        summary[pipe] = {
            "n": n,
            "context_precision": round(ctx_p, 3),
            "faithfulness": round(faith, 3),
            "answer_relevance": round(ans_rel, 2),
            "avg_retrieval_latency_s": round(retr_lat, 3),
            "avg_gen_latency_s": round(gen_lat, 3),
        }
    return summary


def print_comparison(summary: dict) -> None:
    pipes = list(summary.keys())
    print("\n" + "=" * 80)
    print(" RAG 评测对比")
    print("=" * 80)
    rows = [
        ("样本数 n",            "n"),
        ("Context Precision",   "context_precision"),
        ("Faithfulness",        "faithfulness"),
        ("Answer Relevance/5",  "answer_relevance"),
        ("Avg 检索延迟 (s)",     "avg_retrieval_latency_s"),
        ("Avg 生成延迟 (s)",     "avg_gen_latency_s"),
    ]
    header = f" {'指标':<22}" + "".join(f"| {p:^16}" for p in pipes)
    print(header)
    print(" " + "-" * (len(header) - 1))
    for label, key in rows:
        vals = "".join(f"| {summary[p][key]:^16}" for p in pipes)
        print(f" {label:<22}{vals}")
    print()


def write_reports(summary: dict, details: list[dict], stamp: str) -> Path:
    reports_dir = Path(__file__).resolve().parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    json_path = reports_dir / f"eval_{stamp}.json"
    md_path = reports_dir / f"eval_{stamp}.md"

    json_path.write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = ["# RAG 评测报告", f"生成时间：{stamp}\n", "## 汇总对比\n"]
    pipes = list(summary.keys())
    md.append("| 指标 | " + " | ".join(pipes) + " |")
    md.append("|---|" + "|".join("---" for _ in pipes) + "|")
    rows = [
        ("样本数 n", "n"),
        ("Context Precision", "context_precision"),
        ("Faithfulness", "faithfulness"),
        ("Answer Relevance/5", "answer_relevance"),
        ("Avg 检索延迟 (s)", "avg_retrieval_latency_s"),
        ("Avg 生成延迟 (s)", "avg_gen_latency_s"),
    ]
    for label, key in rows:
        md.append(f"| {label} | " + " | ".join(str(summary[p][key]) for p in pipes) + " |")
    md.append("\n详细每条结果见同名 `.json` 文件。\n")
    md_path.write_text("\n".join(md), encoding="utf-8")

    return json_path


# ============================================================================
# 主入口
# ============================================================================

async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--max-queries", type=int, default=None, help="最多评测多少条 query")
    ap.add_argument("--concurrency", type=int, default=4, help="LLM 调用并发上限")
    ap.add_argument(
        "--pipelines",
        nargs="+",
        default=list(PIPELINES.keys()),
        choices=list(PIPELINES.keys()),
        help="要跑哪几条 pipeline",
    )
    args = ap.parse_args()

    dataset = EVAL_DATASET
    if args.max_queries:
        dataset = dataset[: args.max_queries]

    # 启动时先把 BM25 索引建好（hybrid pipeline 需要）
    if "+hybrid+rerank" in args.pipelines:
        print("[setup] 构建 BM25 索引...")
        init_all_indexes(["health", "psych"])

    print(f"\n评测集: {len(dataset)} 条 × {len(args.pipelines)} 条 pipeline = {len(dataset) * len(args.pipelines)} 次评测")
    print(f"并发上限: {args.concurrency}")
    print(f"Pipelines: {', '.join(args.pipelines)}\n")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = []
    for item in dataset:
        for pipe_name in args.pipelines:
            tasks.append(eval_one(sem, item, pipe_name, PIPELINES[pipe_name]))

    results = []
    for fut in tqdm_async.as_completed(tasks, desc="评测中"):
        try:
            results.append(await fut)
        except Exception as e:
            print(f"  [warn] 单次评测失败: {e!r}")

    summary = aggregate(results)
    print_comparison(summary)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = write_reports(summary, results, stamp)
    print(f"\n详细报告已存：{json_path}")
    print(f"          markdown：{json_path.with_suffix('.md')}\n")


if __name__ == "__main__":
    asyncio.run(main())
