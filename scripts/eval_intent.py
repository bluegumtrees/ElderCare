"""意图分类器准确率评测。

跑一组标注样本，统计 intent 准确率 + risk_level 准确率（仅在 intent 正确时计）。
输出每条结果 + 错例汇总，方便面试时拿数字说话。

用法：
    python scripts/eval_intent.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.intent import classify_intent  # noqa: E402


# (用户输入, 期望 intent, 期望 risk_level)
CASES: list[tuple[str, str, str]] = [
    # ---- CHAT ----
    ("今天天气真好啊", "CHAT", "low"),
    ("你叫什么名字", "CHAT", "low"),
    ("吃饭了吗", "CHAT", "low"),
    ("最近孙子来看我了好开心", "CHAT", "low"),

    # ---- HEALTH ----
    ("我血压有点高该吃什么", "HEALTH", "low"),
    ("最近老咳嗽是不是感冒了", "HEALTH", "low"),
    ("膝盖有点疼怎么办", "HEALTH", "low"),
    ("糖尿病人能吃水果吗", "HEALTH", "low"),

    # ---- EMERGENCY ----
    ("我胸口好痛喘不上气", "EMERGENCY", "high"),
    ("突然手脚动不了说话也不清楚", "EMERGENCY", "high"),
    ("摔倒了起不来感觉腰断了", "EMERGENCY", "high"),
    ("头突然剧痛眼前发黑", "EMERGENCY", "high"),

    # ---- PSYCH ----
    ("老伴走了我一个人好孤单", "PSYCH", "mid"),
    ("退休以后觉得自己没用了", "PSYCH", "mid"),
    ("最近总是睡不着心情不好", "PSYCH", "mid"),
    ("我觉得活着没意思", "PSYCH", "high"),
    ("我对家人是个负担不如离开", "PSYCH", "high"),

    # ---- FRAUD ----
    ("有人打电话说我中奖了要转手续费", "FRAUD", "high"),
    ("自称公检法的让我转账配合调查", "FRAUD", "high"),
    ("听说投资理财年化30% 让我打款", "FRAUD", "high"),
    ("陌生人加我微信要银行卡验证码", "FRAUD", "high"),
]


async def main():
    print(f"评测样本数: {len(CASES)}\n")
    total = len(CASES)
    intent_correct = 0
    risk_correct = 0
    errors: list[dict] = []

    for i, (msg, exp_intent, exp_risk) in enumerate(CASES, 1):
        result = await classify_intent(msg)
        got_intent = result["intent"]
        got_risk = result["risk_level"]

        intent_ok = got_intent == exp_intent
        risk_ok = intent_ok and got_risk == exp_risk

        if intent_ok:
            intent_correct += 1
        if risk_ok:
            risk_correct += 1

        mark = "✓" if intent_ok and risk_ok else ("△" if intent_ok else "✗")
        print(
            f"  {mark} [{i:02d}] {msg[:30]:<30s} | "
            f"期望 {exp_intent}/{exp_risk:<4s} → 实际 {got_intent}/{got_risk}"
        )
        if not (intent_ok and risk_ok):
            errors.append(
                {
                    "message": msg,
                    "expected": f"{exp_intent}/{exp_risk}",
                    "got": f"{got_intent}/{got_risk}",
                    "intent_ok": intent_ok,
                }
            )

    print("\n========== 评测结果 ==========")
    print(f"意图准确率 : {intent_correct}/{total} = {intent_correct / total:.1%}")
    print(f"风险准确率 : {risk_correct}/{total} = {risk_correct / total:.1%}")
    print(f"  (注：风险准确率仅在意图正确的情况下统计)")

    if errors:
        print(f"\n========== 错例 ({len(errors)} 条) ==========")
        for e in errors:
            tag = "意图错" if not e["intent_ok"] else "意图对但风险错"
            print(f"  [{tag}] {e['message']}")
            print(f"        期望: {e['expected']}  → 实际: {e['got']}")


if __name__ == "__main__":
    asyncio.run(main())
