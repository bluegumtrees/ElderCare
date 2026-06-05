"""把心理咨询样例知识塞进 ChromaDB 的 'psych' collection。

后续可以把 PsyQA / D4 / SMILE 等开源数据集清洗后接入同一 collection。

用法：
    python scripts/ingest_psych.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.vector_store import add_documents, query  # noqa: E402

SAMPLE_DOCS = [
    {
        "id": "p001",
        "text": (
            "面对孤独感，可以尝试每天保持几件小的固定活动："
            "出门散步、和邻居聊几句、给老朋友打个电话。"
            "哪怕只是和小区里的人简单寒暄，也能减轻孤独感。"
            "如果家人在外地，约定每周固定时间视频通话，会让人感到被惦记、被需要。"
        ),
        "metadata": {"source": "sample", "topic": "孤独"},
    },
    {
        "id": "p002",
        "text": (
            "失眠时不要躺在床上反复看时间，越焦虑越难入睡。"
            "可以起身做些放松的事，听轻音乐、读几页书，等有困意再回床。"
            "睡前 1 小时减少看手机，可以喝杯温牛奶。"
            "如果长期失眠影响白天精神，建议看医生，必要时短期服用助眠药物。"
        ),
        "metadata": {"source": "sample", "topic": "失眠"},
    },
    {
        "id": "p003",
        "text": (
            "失去亲人后悲伤是正常的，需要时间慢慢消化，请允许自己流泪和怀念。"
            "可以做一些纪念性的小事：整理照片、写日记、把对方爱吃的菜做一份。"
            "如果悲伤持续超过半年且严重影响生活，建议寻求专业心理咨询师的帮助。"
        ),
        "metadata": {"source": "sample", "topic": "丧亲"},
    },
    {
        "id": "p004",
        "text": (
            "感到'自己没用了''是家人负担'，是老年抑郁常见的想法，"
            "但这其实是抑郁带来的认知偏差，不是事实。"
            "您一生的付出和经历本身就是有价值的。"
            "建议跟家人坦诚谈谈这些感受，也可以拨打老年心理援助热线，"
            "让专业的人陪您一起面对。"
        ),
        "metadata": {"source": "sample", "topic": "自我价值"},
    },
    {
        "id": "p005",
        "text": (
            "焦虑常常源于对未来不确定的担忧。"
            "可以试着把担心的事写下来，区分'可控'和'不可控'两部分，先处理可控的小事。"
            "深呼吸练习也有帮助：4 秒吸气、屏住 7 秒、8 秒呼气，"
            "每天重复几次能缓解紧张情绪。"
        ),
        "metadata": {"source": "sample", "topic": "焦虑"},
    },
    {
        "id": "p006",
        "text": (
            "退休后失去价值感是很常见的，毕竟工作占据了几十年。"
            "可以重新发现兴趣：学一项新技能、参加社区活动、做志愿者、带孙辈。"
            "把生活重心慢慢转向新的领域，需要时间，不必着急。"
        ),
        "metadata": {"source": "sample", "topic": "退休适应"},
    },
    {
        "id": "p007",
        "text": (
            "当出现伤害自己或了结生命的念头时，请记住：这是危机信号，需要立即求助。"
            "请马上联系家人，或拨打全国 24 小时心理援助热线 400-161-9995，"
            "也可拨打北京心理危机研究与干预中心 010-82951332。"
            "这些感受是可以被帮助的，您并不孤单。"
        ),
        "metadata": {"source": "sample", "topic": "危机干预"},
    },
    {
        "id": "p008",
        "text": (
            "子女不在身边时，可以主动建立属于自己的社交圈："
            "加入社区舞蹈队、合唱团、棋牌活动，"
            "或者去老年大学学习感兴趣的课程。"
            "稳定的人际联结能有效缓解孤独和情绪低落。"
        ),
        "metadata": {"source": "sample", "topic": "社交支持"},
    },
]


def main() -> None:
    ids = [d["id"] for d in SAMPLE_DOCS]
    texts = [d["text"] for d in SAMPLE_DOCS]
    metadatas = [d["metadata"] for d in SAMPLE_DOCS]

    print(f"[ingest_psych] 入库 {len(ids)} 条心理样例到 collection='psych' ...")
    add_documents("psych", ids, texts, metadatas)
    print("[ingest_psych] 入库完成。\n")

    test_queries = [
        "老伴走了我一个人好孤单",
        "晚上总睡不着觉",
        "退休后感觉自己没用了",
        "我有时候想活着没意思",
    ]
    print("[ingest_psych] 检索测试：")
    for q in test_queries:
        print(f"\n  Q: {q}")
        hits = query("psych", q, k=2)
        for i, h in enumerate(hits, 1):
            preview = h["text"][:50].replace("\n", " ")
            print(f"    [{i}] (cos_dist={h['distance']:.3f}) {preview}...")


if __name__ == "__main__":
    main()
