"""意图分类器：让 LLM 输出 {intent, risk_level} 的 JSON。

设计要点：
- 一次 LLM 调用同时输出意图大类 + 风险等级，避免两次调用。
- temperature=0 + JSON 模式 + few-shot 例子，最大化稳定性。
- 解析失败 fallback 到 CHAT/low，绝不让分类器把整个请求搞挂。
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


CLASSIFIER_SYSTEM = """你是老年陪伴助手的意图分类器，只输出 JSON，不输出任何解释。

输出格式严格为：
{"intent":"<标签>","risk_level":"<等级>"}

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

规则：
- EMERGENCY 和 FRAUD 默认 high
- CHAT 默认 low
- HEALTH 一般 low，明显严重但未到急症为 mid
- PSYCH 根据严重程度判断 low/mid/high

示例：
用户：今天天气真好
{"intent":"CHAT","risk_level":"low"}

用户：我最近血压有点高，该吃什么
{"intent":"HEALTH","risk_level":"low"}

用户：我胸口好痛喘不上气
{"intent":"EMERGENCY","risk_level":"high"}

用户：老伴走了以后我一个人好孤单，晚上总睡不着
{"intent":"PSYCH","risk_level":"mid"}

用户：我觉得活着没意思，对谁都是负担
{"intent":"PSYCH","risk_level":"high"}

用户：有人打电话说我中奖了，让我转 5000 块手续费
{"intent":"FRAUD","risk_level":"high"}
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


async def classify_intent(message: str) -> IntentResult:
    """对单条用户输入做意图 + 风险分类。失败时 fallback 到 CHAT/low。"""
    client = get_llm()
    s = get_settings()
    try:
        resp = await client.chat.completions.create(
            model=s.chat_model,
            messages=[
                {"role": "system", "content": CLASSIFIER_SYSTEM},
                {"role": "user", "content": f"用户：{message}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
    except Exception:
        # 网络/模型/JSON-mode 不支持等都兜底，绝不抛
        return {"intent": "CHAT", "risk_level": "low"}

    data = _safe_parse(raw)
    intent = data.get("intent", "CHAT")
    risk = data.get("risk_level", "low")

    if intent not in VALID_INTENTS:
        intent = "CHAT"
    if risk not in VALID_RISKS:
        risk = "low"
    # 业务硬规则：急症和诈骗永远是 high
    if intent in ("EMERGENCY", "FRAUD"):
        risk = "high"

    return {"intent": intent, "risk_level": risk}  # type: ignore[return-value]


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
