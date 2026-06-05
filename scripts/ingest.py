"""把样例健康知识塞进 ChromaDB，验证入库 + 检索能通。

用法：
    python scripts/ingest.py
"""
import sys
from pathlib import Path

# 让脚本能从仓库根目录直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.vector_store import add_documents, query  # noqa: E402

SAMPLE_DOCS = [
    {
        "id": "h001",
        "text": (
            "高血压患者日常饮食应少盐，每日盐摄入控制在 5 克以内。"
            "建议多吃蔬菜水果，适量运动，定期监测血压。"
            "若血压持续高于 160/100 mmHg 或伴有头晕胸闷，应及时就医。"
        ),
        "metadata": {"source": "sample", "topic": "高血压"},
    },
    {
        "id": "h002",
        "text": (
            "糖尿病患者要控制主食量，避免高糖食物。"
            "可适量吃粗粮、杂豆，每餐七分饱。"
            "建议每天散步 30 分钟，定期监测血糖。"
            "出现头晕、出冷汗、心慌可能是低血糖，需立即吃含糖食物。"
        ),
        "metadata": {"source": "sample", "topic": "糖尿病"},
    },
    {
        "id": "h003",
        "text": (
            "老年人失眠常见原因包括焦虑、夜尿增多、白天小睡过多。"
            "建议固定作息，睡前避免饮浓茶咖啡，睡前 1 小时减少屏幕时间。"
            "若长期失眠影响生活，应到神经内科或睡眠门诊就诊。"
        ),
        "metadata": {"source": "sample", "topic": "失眠"},
    },
    {
        "id": "h004",
        "text": (
            "突发胸痛、胸闷、呼吸困难，特别是疼痛放射到左肩或下颌，"
            "可能是急性心肌梗死症状，请立即拨打 120，"
            "保持安静、不要走动，可以含服一片阿司匹林（如无禁忌）。"
        ),
        "metadata": {"source": "sample", "topic": "急症-心梗"},
    },
    {
        "id": "h005",
        "text": (
            "感冒发烧多由病毒引起，多喝温水、好好休息，一般几天可自行缓解。"
            "若高烧不退（超过 38.5 度持续 3 天）或伴有严重咳嗽、呼吸困难，"
            "应及时就医。老人和有基础病者要更早就医。"
        ),
        "metadata": {"source": "sample", "topic": "感冒"},
    },
    {
        "id": "h006",
        "text": (
            "老年人膝关节疼痛常因骨关节炎引起。"
            "建议控制体重、避免长时间下蹲和爬楼梯，可适度做股四头肌锻炼。"
            "热敷与适量活动有助缓解，疼痛严重时应到骨科就诊。"
        ),
        "metadata": {"source": "sample", "topic": "关节疼痛"},
    },
    {
        "id": "h007",
        "text": (
            "突发一侧肢体无力、口角歪斜、说话不清，可能是脑卒中（中风）前兆，"
            "请立即拨打 120，记下发病时间，不要给患者随意喂水喂药，"
            "黄金救治时间为 4.5 小时内。"
        ),
        "metadata": {"source": "sample", "topic": "急症-中风"},
    },
    {
        "id": "h008",
        "text": (
            "老年人便秘建议多喝水、多吃蔬菜水果和粗纤维食物，"
            "养成定时排便习惯，适当运动。"
            "若长期便秘伴有便血、体重下降，应警惕肠道疾病，及时就医检查。"
        ),
        "metadata": {"source": "sample", "topic": "便秘"},
    },
]


def main() -> None:
    ids = [d["id"] for d in SAMPLE_DOCS]
    texts = [d["text"] for d in SAMPLE_DOCS]
    metadatas = [d["metadata"] for d in SAMPLE_DOCS]

    print(f"[ingest] 入库 {len(ids)} 条样例数据到 collection='health' ...")
    add_documents("health", ids, texts, metadatas)
    print("[ingest] 入库完成。\n")

    test_queries = [
        "我血压有点高怎么办",
        "晚上总睡不着",
        "突然胸口很痛",
        "膝盖疼是怎么回事",
    ]
    print("[ingest] 检索测试：")
    for q in test_queries:
        print(f"\n  Q: {q}")
        hits = query("health", q, k=2)
        for i, h in enumerate(hits, 1):
            preview = h["text"][:50].replace("\n", " ")
            print(f"    [{i}] (cos_dist={h['distance']:.3f}) {preview}...")


if __name__ == "__main__":
    main()
