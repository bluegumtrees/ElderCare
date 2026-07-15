"""Chat endpoints。

- /chat       : 裸 LLM 流式（对比模式的"无 RAG"一路）
- /chat_rag   : 直连 health 知识库的纯向量 RAG，演示对比用
- /agent      : 完整动态路由（意图分类 → 5 路分支 → 流式回复 → ALERT 触发邮件）

/agent 的 SSE 事件（前端轨迹面板按这些事件逐步点亮）：
  stage     {stage: classify|dense|sparse|rrf|rerank|generate, ms, ...}
  intent    {intent, risk_level, log_level}
  alert     {reason, action}
  retrieved {hits: [...]}
  message   {delta}
  error     {message}
  done      {answer, intent, risk_level, log_level, total_ms}
"""
import asyncio
import json
import time

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from ..db import get_recent_messages, save_message, touch_conversation
from ..intent import classify_intent, derive_log_level
from ..llm import stream_chat
from ..notifier import send_alert
from ..retrieval import retrieve_events
from ..schemas import ChatRequest
from ..security import get_optional_user
from ..templates import (
    CHAT_SYSTEM,
    EMERGENCY_TEMPLATE,
    FRAUD_TEMPLATE,
    HEALTH_PROMPT,
    PSYCH_PROMPT_HIGH,
    PSYCH_PROMPT_LOW_MID,
    stream_template,
)
from ..vector_store import query  # 保留：/chat_rag 端点用纯向量做对比演示

router = APIRouter()

FALLBACK_REPLY = "哎呀，我这边网络有点不顺，您稍等一下再跟我说一遍好吗？"


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


def _format_context(hits: list[dict]) -> str:
    if not hits:
        return "（无相关资料）"
    return "\n\n".join(f"[{i + 1}] {h['text']}" for i, h in enumerate(hits))


def _hits_preview(hits: list[dict]) -> list[dict]:
    """把检索结果序列化给前端，包含所有可用分数：
    distance(向量) / bm25_score / rrf_score / rerank_score

    text 不截断，长 chunk 在前端用 CSS max-height + 滚动呈现。
    """
    score_keys = ("distance", "bm25_score", "rrf_score", "rerank_score")
    out = []
    for h in hits:
        item = {"text": h["text"], "metadata": h.get("metadata", {})}
        for key in score_keys:
            if key in h and h[key] is not None:
                item[key] = round(float(h[key]), 4)
        out.append(item)
    return out


# ============ /chat：裸 LLM ============

@router.post("/chat")
async def chat(req: ChatRequest, user: dict | None = Depends(get_optional_user)):
    history = get_recent_messages(req.session_id, n_turns=6)
    messages = (
        [{"role": "system", "content": CHAT_SYSTEM}]
        + history
        + [{"role": "user", "content": req.message}]
    )

    if req.save_history:
        touch_conversation(req.session_id, user["id"] if user else None, req.message)
        save_message(req.session_id, "user", req.message)

    async def event_gen():
        parts: list[str] = []
        try:
            async for delta in stream_chat(messages):
                parts.append(delta)
                yield _sse("message", {"delta": delta})
        except Exception as e:
            print(f"[chat] LLM 流式失败：{e!r}", flush=True)
            yield _sse("error", {"message": "模型服务暂时不可用"})
            if not parts:
                parts.append(FALLBACK_REPLY)
                yield _sse("message", {"delta": FALLBACK_REPLY})
        full = "".join(parts)
        if req.save_history:
            save_message(req.session_id, "assistant", full)
        yield _sse("done", {"answer": full})

    return EventSourceResponse(event_gen())


# ============ /chat_rag：直接走 health 集合（纯向量，对比演示） ============

@router.post("/chat_rag")
async def chat_rag(req: ChatRequest, user: dict | None = Depends(get_optional_user)):
    history = get_recent_messages(req.session_id, n_turns=6)
    hits = await asyncio.to_thread(query, "health", req.message, 3)
    user_prompt = HEALTH_PROMPT.format(
        context=_format_context(hits), question=req.message
    )
    messages = history + [{"role": "user", "content": user_prompt}]

    if req.save_history:
        touch_conversation(req.session_id, user["id"] if user else None, req.message)
        save_message(req.session_id, "user", req.message)

    async def event_gen():
        yield _sse("retrieved", {"hits": _hits_preview(hits)})
        parts: list[str] = []
        try:
            async for delta in stream_chat(messages):
                parts.append(delta)
                yield _sse("message", {"delta": delta})
        except Exception as e:
            print(f"[chat_rag] LLM 流式失败：{e!r}", flush=True)
            yield _sse("error", {"message": "模型服务暂时不可用"})
            if not parts:
                parts.append(FALLBACK_REPLY)
                yield _sse("message", {"delta": FALLBACK_REPLY})
        full = "".join(parts)
        if req.save_history:
            save_message(req.session_id, "assistant", full)
        yield _sse("done", {"answer": full})

    return EventSourceResponse(event_gen())


# ============ /agent：完整动态路由 ============

@router.post("/agent")
async def agent(req: ChatRequest, user: dict | None = Depends(get_optional_user)):
    """完整动态路由：意图分类（带历史，含检索改写）→ 按意图分流到 5 种 handler。

    分类放在 SSE 流内部执行：连接立刻建立，前端从第一毫秒就能开始画轨迹，
    而不是等分类做完才看到响应开头。
    """
    user_id = user["id"] if user else None

    async def event_gen():
        t_start = time.perf_counter()

        # 先取历史（不含本条），再落库本条 —— 否则 LLM 会把当前消息看到两遍
        history = get_recent_messages(req.session_id, n_turns=6)

        t0 = time.perf_counter()
        cls = await classify_intent(req.message, history)
        intent, risk, rag_query = cls["intent"], cls["risk_level"], cls["query"]
        log_level = derive_log_level(intent, risk)
        classify_ms = round((time.perf_counter() - t0) * 1000)

        yield _sse(
            "stage",
            {
                "stage": "classify",
                "ms": classify_ms,
                "intent": intent,
                "risk_level": risk,
                "log_level": log_level,
                "query": rag_query,
            },
        )
        yield _sse(
            "intent",
            {"intent": intent, "risk_level": risk, "log_level": log_level},
        )

        touch_conversation(req.session_id, user_id, req.message)
        save_message(
            req.session_id,
            "user",
            req.message,
            intent=intent,
            risk_level=risk,
            log_level=log_level,
        )

        # ALERT 级别派发后台任务发邮件，不阻塞流式响应
        if log_level == "ALERT":
            asyncio.create_task(
                send_alert(
                    intent=intent,
                    risk=risk,
                    session_id=req.session_id,
                    user_message=req.message,
                )
            )

        parts: list[str] = []
        t_gen = None
        first_token_ms = None

        try:
            messages: list[dict] | None = None

            if intent == "CHAT":
                messages = (
                    [{"role": "system", "content": CHAT_SYSTEM}]
                    + history
                    + [{"role": "user", "content": req.message}]
                )

            elif intent in ("HEALTH", "PSYCH"):
                collection = "health" if intent == "HEALTH" else "psych"
                hits: list[dict] = []
                async for kind, payload in retrieve_events(collection, rag_query):
                    if kind == "stage":
                        yield _sse("stage", payload)
                    else:
                        hits = payload["hits"]
                yield _sse("retrieved", {"hits": _hits_preview(hits)})

                if intent == "HEALTH":
                    template = HEALTH_PROMPT
                else:
                    template = PSYCH_PROMPT_HIGH if risk == "high" else PSYCH_PROMPT_LOW_MID
                user_prompt = template.format(
                    context=_format_context(hits), question=req.message
                )
                messages = (
                    [{"role": "system", "content": CHAT_SYSTEM}]
                    + history
                    + [{"role": "user", "content": user_prompt}]
                )

            elif intent == "EMERGENCY":
                yield _sse("alert", {"reason": "EMERGENCY", "action": "已派发家属邮件预警"})
                t_gen = time.perf_counter()
                async for delta in stream_template(EMERGENCY_TEMPLATE):
                    parts.append(delta)
                    yield _sse("message", {"delta": delta})

            elif intent == "FRAUD":
                yield _sse("alert", {"reason": "FRAUD", "action": "已派发家属邮件预警"})
                t_gen = time.perf_counter()
                async for delta in stream_template(FRAUD_TEMPLATE):
                    parts.append(delta)
                    yield _sse("message", {"delta": delta})

            if messages is not None:
                t_gen = time.perf_counter()
                async for delta in stream_chat(messages):
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - t_gen) * 1000)
                    parts.append(delta)
                    yield _sse("message", {"delta": delta})

        except Exception as e:
            print(f"[agent] 处理失败：{e!r}", flush=True)
            yield _sse("error", {"message": "模型服务暂时不可用"})
            if not parts:
                parts.append(FALLBACK_REPLY)
                yield _sse("message", {"delta": FALLBACK_REPLY})

        gen_payload = {"stage": "generate"}
        if t_gen is not None:
            gen_payload["ms"] = round((time.perf_counter() - t_gen) * 1000)
        if first_token_ms is not None:
            gen_payload["first_token_ms"] = first_token_ms
        yield _sse("stage", gen_payload)

        full = "".join(parts)
        save_message(
            req.session_id,
            "assistant",
            full,
            intent=intent,
            risk_level=risk,
            log_level=log_level,
        )
        yield _sse(
            "done",
            {
                "answer": full,
                "intent": intent,
                "risk_level": risk,
                "log_level": log_level,
                "total_ms": round((time.perf_counter() - t_start) * 1000),
            },
        )

    return EventSourceResponse(event_gen())
