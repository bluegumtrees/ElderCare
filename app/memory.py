"""长期记忆：从对话中提取用户画像事实，注入后续对话。

设计：
- 每轮 /agent 回复完成后，asyncio.create_task 异步提取，不阻塞流式响应
- 提取器输入「已有事实 + 本轮对话」，输出**合并后的完整列表**（≤12 条）——
  全量替换写库，天然去重、自动淘汰过期信息（"感冒了"下周就该没了）
- 用小模型（settings.intent_model）+ JSON 模式，一次调用几厘钱
- owner_key：登录用户 u{id}，匿名会话 s{session_id}，互不串号
- 注入：作为 system 提示附加，要求"自然运用，不要生硬复述"
"""
import json

from .config import get_settings
from .db import list_facts, replace_facts
from .llm import get_llm

MEMORY_SYSTEM = """你是陪伴助手的记忆管理器。根据「已有记忆」和「本轮对话」，输出更新后的完整记忆列表。

只记对长期陪伴有用的事实：
- 健康状况（慢性病、用药、睡眠等）
- 家庭与社会关系（子女、老伴、独居情况）
- 生活习惯与爱好（锻炼、饮食偏好）
- 重要日程或事件（孙子暑假回来、下周复诊）

规则：
- 每条 ≤ 40 字，客观白描，不带评价
- 保留仍然有效的旧记忆；同一件事有新进展就合并成一条
- 一次性寒暄、天气、无长期价值的内容不记
- 最多 12 条；没有可记的就返回已有记忆原样
- 只输出 JSON：{"facts": ["...", "..."]}
"""


def owner_key_for(user: dict | None, session_id: str) -> str:
    return f"u{user['id']}" if user else f"s{session_id}"


def get_memory(owner_key: str) -> list[str]:
    return list_facts(owner_key)


def format_memory_prompt(facts: list[str]) -> str:
    """把事实列表拼成注入 system 的提示段。"""
    lines = "\n".join(f"- {f}" for f in facts)
    return (
        "你之前了解到这位老人的情况（供参考，自然地运用在对话里，"
        f"不要生硬复述）：\n{lines}"
    )


async def update_memory(owner_key: str, user_msg: str, assistant_msg: str) -> None:
    """异步提取并更新记忆。任何失败都静默吞掉——记忆是增强项，不能影响主流程。"""
    try:
        existing = list_facts(owner_key)
        payload = (
            "已有记忆：\n"
            + ("\n".join(f"- {f}" for f in existing) if existing else "（空）")
            + f"\n\n本轮对话：\n老人：{user_msg[:200]}\n助手：{assistant_msg[:300]}"
        )
        s = get_settings()
        resp = await get_llm().chat.completions.create(
            model=s.intent_model,
            messages=[
                {"role": "system", "content": MEMORY_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            extra_body={"reasoning": {"enabled": False}},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        facts = data.get("facts")
        if not isinstance(facts, list):
            return
        cleaned = [str(f).strip()[:60] for f in facts if str(f).strip()][:12]
        # 内容没变就不写库，避免无谓的删插
        if cleaned and cleaned != existing:
            replace_facts(owner_key, cleaned)
            print(f"[memory] {owner_key} 记忆更新为 {len(cleaned)} 条", flush=True)
    except Exception as e:
        print(f"[memory] 提取失败（忽略）：{e!r}", flush=True)
