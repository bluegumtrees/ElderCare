"""意图分类器：让 LLM 输出 {intent, risk_level, query} 的 JSON。

设计要点：
- 一次 LLM 调用同时输出意图大类 + 风险等级 + 检索改写（standalone query），
  不为多轮追问多付一次调用成本。
- 带最近对话历史进分类：追问（"那平时吃什么好？"）能延续上一话题的意图，
  并被改写成不依赖上下文的完整检索问题。
- 用独立的小模型（settings.intent_model）跑分类，降低首字延迟。
- temperature=0 + JSON 模式 + few-shot 例子，最大化稳定性。
- 解析失败 fallback 到 CHAT/low/原句，绝不让分类器把整个请求搞挂。
- EMERGENCY / FRAUD 强制 risk_level=high（业务硬规则，不信任模型）。
"""
import json
import re
from typing import Literal, TypedDict

from .config import get_settings
from .llm import get_llm

Intent = Literal["CHAT", "HEALTH", "PSYCH", "EMERGENCY", "FRAUD"]
RiskLevel = Literal["low", "mid", "high"]
LogLevel = Literal["INFO", "WARN", "ALERT"]

VALID_INTENTS: set[str] = {"CHAT", "HEALTH", "PSYCH", "EMERGENCY", "FRAUD"}
VALID_RISKS: set[str] = {"low", "mid", "high"}


class IntentResult(TypedDict):
    intent: Intent
    risk_level: RiskLevel
    query: str


CLASSIFIER_SYSTEM = """你是老年陪伴助手的意图分类器，只输出 JSON，不输出任何解释。

输出格式严格为：
{"intent":"<标签>","risk_level":"<等级>","query":"<改写后的独立问题>"}

intent 五选一：
- CHAT：日常闲聊、问候、天气、家长里短，与健康/心理/紧急/诈骗无关
- HEALTH：健康咨询（症状、用药、饮食、就医建议），非紧急
- PSYCH：心理倾诉（孤独、低落、焦虑、失眠、抑郁、自我价值疑虑）
- EMERGENCY：突发医疗急症（胸痛、剧烈头痛、中风症状、跌倒后无法起身、呼吸困难、剧烈出血）
- FRAUD：疑似诈骗（陌生人要转账、中奖通知、冒充公检法、高回报投资、索要银行卡密码/验证码）

risk_level 三选一：
- low：一般描述、无明确危机信号
- mid：持续负面情绪、表达无助、生活受到明显影响
- high：明确危机（自杀/自残/伤人念头、严重急症、正在被诈骗）

query 改写规则：
- 把「用户最新的话」改写成一句不看对话历史也能独立理解的完整问题，供知识库检索用
- 最新的话里有指代或省略时（"那平时吃什么好？"），结合对话历史补全（"高血压患者平时吃什么好？"）
- 本来就完整时，原样照抄
- CHAT / EMERGENCY / FRAUD 直接照抄原话

规则：
- 意图判断要结合对话历史：追问延续上一个话题的意图
- EMERGENCY 和 FRAUD 默认 high
- CHAT 默认 low
- HEALTH 一般 low，明显严重但未到急症为 mid
- PSYCH 根据严重程度判断 low/mid/high

示例：
用户最新的话：今天天气真好
{"intent":"CHAT","risk_level":"low","query":"今天天气真好"}

对话历史：
老人：我最近血压有点高，该注意什么
助手：平时要清淡饮食、按时吃药，定期量血压……
用户最新的话：那平时吃什么好？
{"intent":"HEALTH","risk_level":"low","query":"高血压患者平时吃什么好？"}

用户最新的话：我胸口好痛喘不上气
{"intent":"EMERGENCY","risk_level":"high","query":"我胸口好痛喘不上气"}

用户最新的话：老伴走了以后我一个人好孤单，晚上总睡不着
{"intent":"PSYCH","risk_level":"mid","query":"老伴去世后一个人生活感到孤单、晚上失眠怎么办"}

用户最新的话：我觉得活着没意思，对谁都是负担
{"intent":"PSYCH","risk_level":"high","query":"觉得活着没意思、自己是家人的负担怎么办"}

用户最新的话：有人打电话说我中奖了，让我转 5000 块手续费
{"intent":"FRAUD","risk_level":"high","query":"有人打电话说我中奖了，让我转 5000 块手续费"}
"""


def _safe_parse(raw: str) -> dict:
    """尽最大努力解析 JSON：直接 parse 失败就抽取第一个 {...} 块。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _format_input(message: str, history: list[dict] | None) -> str:
    """把对话历史 + 最新输入拼成分类器的 user prompt。

    历史每条截断到 100 字：分类只需要话题线索，不需要全文。
    """
    parts: list[str] = []
    if history:
        lines = []
        for m in history[-6:]:
            role = "老人" if m.get("role") == "user" else "助手"
            content = (m.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{role}：{content[:100]}")
        if lines:
            parts.append("对话历史：\n" + "\n".join(lines))
    parts.append(f"用户最新的话：{message}")
    return "\n\n".join(parts)


async def classify_intent(message: str, history: list[dict] | None = None) -> IntentResult:
    """对用户输入做意图 + 风险分类，并输出改写后的独立检索问题。

    失败时 fallback 到 CHAT / low / 原句。
    """
    client = get_llm()
    s = get_settings()
    try:
        resp = await client.chat.completions.create(
            model=s.intent_model,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM},
                {"role": "user", "content": _format_input(message, history)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            # Qwen 混合推理模型默认会"思考"，分类这种模板任务会平白多几秒；
            # OpenRouter 统一参数关掉，不支持该参数的模型会自动忽略
            extra_body={"reasoning": {"enabled": False}},
        )
        raw = resp.choices[0].message.content or "{}"
    except Exception:
        # 网络/模型/JSON-mode 不支持等都兜底，绝不抛
        return {"intent": "CHAT", "risk_level": "low", "query": message}

    data = _safe_parse(raw)
    intent = data.get("intent", "CHAT")
    risk = data.get("risk_level", "low")
    query = str(data.get("query") or "").strip() or message

    if intent not in VALID_INTENTS:
        intent = "CHAT"
    if risk not in VALID_RISKS:
        risk = "low"
    # 业务硬规则：急症和诈骗永远是 high
    if intent in ("EMERGENCY", "FRAUD"):
        risk = "high"

    return {"intent": intent, "risk_level": risk, "query": query}  # type: ignore[return-value]


def derive_log_level(intent: str, risk_level: str) -> LogLevel:
    """根据意图 + 风险等级映射到 INFO / WARN / ALERT。

    INFO：默认存档
    WARN：进每日汇总
    ALERT：立即邮件
    """
    if intent in ("EMERGENCY", "FRAUD"):
        return "ALERT"
    if intent == "PSYCH":
        return {"low": "INFO", "mid": "WARN", "high": "ALERT"}.get(risk_level, "INFO")  # type: ignore[return-value]
    return "INFO"
