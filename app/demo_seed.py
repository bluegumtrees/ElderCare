"""demo 账号 + 预置演示对话。

启动时调用 ensure_demo_account()：
- demo 用户不存在则创建（用户名/密码来自 settings，默认 demo / demo2026）
- 并灌入覆盖五类意图的演示对话（含 WARN/ALERT 记录），让历史列表、
  管理仪表盘一打开就有东西可看。
- HF Spaces 的 SQLite 在 /tmp，每次重启清零 —— 所以这里做成幂等的启动种子，
  而不是一次性脚本。

时间戳刻意铺在过去 7 天，让仪表盘的按天趋势图有形状。
"""
from .config import get_settings
from .db import get_conn, get_user_by_username, create_user
from .security import hash_password

# (days_ago, title, messages)
# message: (role, content, intent, risk_level, log_level)
_DEMO_CONVERSATIONS: list[tuple[int, str, list[tuple]]] = [
    (
        6,
        "和孙子视频聊天",
        [
            ("user", "今天孙子给我打视频电话了，可高兴了", "CHAT", "low", "INFO"),
            (
                "assistant",
                "那真是太好了！孙子惦记着您呢。他跟您聊了些什么呀？",
                "CHAT",
                "low",
                "INFO",
            ),
            ("user", "他说放暑假要回来看我，我得准备点他爱吃的", "CHAT", "low", "INFO"),
            (
                "assistant",
                "听着就热闹！您慢慢准备，别累着。他爱吃您做的什么菜呀？说不定我还能帮您参谋参谋。",
                "CHAT",
                "low",
                "INFO",
            ),
        ],
    ),
    (
        5,
        "血压有点高怎么办",
        [
            ("user", "我这两天量血压有点高，150 了", "HEALTH", "low", "INFO"),
            (
                "assistant",
                "别太担心，先记下每天的数值。平时吃饭清淡一点，每天盐不超过一小勺 [1]，"
                "按时吃降压药别漏服 [2]。要是持续超过 160，建议去社区医院让医生看看。",
                "HEALTH",
                "low",
                "INFO",
            ),
            ("user", "那平时吃什么对血压好？", "HEALTH", "low", "INFO"),
            (
                "assistant",
                "多吃新鲜蔬菜水果，像芹菜、香蕉都不错 [1]。少吃咸菜、腊肉这些腌制品 [2]。"
                "饭后散散步，对血压也有好处 [3]。具体用药还是要听医生的哦。",
                "HEALTH",
                "low",
                "INFO",
            ),
        ],
    ),
    (
        3,
        "一个人在家闷得慌",
        [
            (
                "user",
                "孩子们都在外地，我一个人在家闷得慌，晚上也睡不好",
                "PSYCH",
                "mid",
                "WARN",
            ),
            (
                "assistant",
                "一个人在家，冷清的感觉我懂。您愿意跟我说说，心里就已经迈出一步了。"
                "白天可以去楼下小区走走，跟老邻居打个招呼 [1]；睡前少看手机，"
                "泡泡脚会好入睡一些 [2]。要是这种闷得慌的感觉一直有，"
                "跟孩子们说说，或者找社区的心理服务聊聊，都会有帮助的。",
                "PSYCH",
                "mid",
                "WARN",
            ),
        ],
    ),
    (
        2,
        "接到中奖电话",
        [
            (
                "user",
                "刚才有人打电话说我中了 20 万，让我先交 8000 块保证金",
                "FRAUD",
                "high",
                "ALERT",
            ),
            (
                "assistant",
                "⚠️ 您说的这个情况，我担心是诈骗。请您一定：\n\n"
                "1. 立刻停止任何转账、汇款\n"
                "2. 不要把银行卡号、密码、短信验证码告诉任何人\n"
                "3. 挂断电话，不要回拨陌生号码\n\n"
                "我已经把这个情况告诉您的家属了。\n"
                "如果已经转账或泄露了信息，请马上拨打反诈专线 96110。",
                "FRAUD",
                "high",
                "ALERT",
            ),
            ("user", "好的我没转，已经挂了", "CHAT", "low", "INFO"),
            (
                "assistant",
                "太好了，您做得非常对！记住：凡是先交钱才能领奖的，都是骗子。"
                "以后再接到这种电话直接挂断就行。",
                "CHAT",
                "low",
                "INFO",
            ),
        ],
    ),
    (
        1,
        "头晕胸闷",
        [
            ("user", "我突然觉得胸口发闷，有点喘不上气", "EMERGENCY", "high", "ALERT"),
            (
                "assistant",
                "⚠️ 您说的情况可能是紧急医疗状况，请马上按这几步做：\n\n"
                "1. 立刻拨打 120 急救电话\n"
                "2. 保持安静，不要走动，尽量平躺或半坐\n"
                "3. 通知身边的家人、邻居陪您\n\n"
                "我已经把这条消息发给您的家属了，他们会尽快联系您。\n"
                "请告诉我您现在的具体感觉，我陪您等到救援到来。",
                "EMERGENCY",
                "high",
                "ALERT",
            ),
        ],
    ),
    (
        0,
        "睡眠不好吃什么",
        [
            ("user", "最近老是半夜醒，睡眠不好有什么办法", "HEALTH", "low", "INFO"),
            (
                "assistant",
                "半夜容易醒，可以试试这几个办法：晚饭别吃太饱，睡前两小时不喝浓茶 [1]；"
                "白天晒晒太阳、适当活动，晚上会睡得沉一些 [2]。"
                "要是长期睡不好影响精神，建议去医院看看，别自己随便吃安眠药。",
                "HEALTH",
                "low",
                "INFO",
            ),
        ],
    ),
]


def ensure_demo_account() -> None:
    """幂等：demo 用户已存在则什么都不做。"""
    s = get_settings()
    if get_user_by_username(s.demo_username):
        return

    user_id = create_user(
        s.demo_username, hash_password(s.demo_password), display_name="演示账号"
    )

    with get_conn() as conn:
        for idx, (days_ago, title, messages) in enumerate(_DEMO_CONVERSATIONS):
            session_id = f"demo_{idx}_{days_ago}d"
            ts = f"-{days_ago} days"
            conn.execute(
                "INSERT OR IGNORE INTO conversations "
                "(session_id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, datetime('now', ?), datetime('now', ?))",
                (session_id, user_id, title, ts, ts),
            )
            for i, (role, content, intent, risk, level) in enumerate(messages):
                # 每条消息错开几分钟，保证 ORDER BY id/时间都稳定
                msg_ts = f"-{days_ago} days +{i * 2} minutes"
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, role, content, intent, risk_level, log_level, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, datetime('now', ?))",
                    (session_id, role, content, intent, risk, level, msg_ts),
                )

    print(
        f"[demo] 已创建演示账号 {s.demo_username}（{len(_DEMO_CONVERSATIONS)} 段预置对话）",
        flush=True,
    )
