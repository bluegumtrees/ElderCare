"""demo 账号 + 预置演示对话。

启动时调用 ensure_demo_account()：
- demo 用户不存在则创建（用户名/密码来自 settings，默认 demo / demo2026）
- 并灌入覆盖五类意图的演示对话（含 WARN/ALERT 记录），让历史列表、
  管理仪表盘一打开就有东西可看。
- HF Spaces 的 SQLite 在 /tmp，每次重启清零 —— 所以这里做成幂等的启动种子，
  而不是一次性脚本。

内容设计原则：
- 回复遵循「先共情 → 给建议 → 关怀收尾」的陪伴结构，别写成说明书
- 健康/心理对话带 refs 检索快照（历史回看可点引用徽章溯源）
- 追问式多轮（"那平时吃什么好？"）顺带展示检索改写能力
- 时间戳铺在过去 7 天，让仪表盘的按天趋势图有形状
"""
import json

from .config import get_settings
from .db import get_conn, get_user_by_username, create_user
from .security import hash_password


def _ref(text: str, source: str, topic: str, score: float) -> dict:
    return {
        "text": text,
        "metadata": {"source": source, "topic": topic},
        "rerank_score": score,
    }


# (days_ago, title, messages)
# message: (role, content, intent, risk_level, log_level, refs | None)
_DEMO_CONVERSATIONS: list[tuple[int, str, list[tuple]]] = [
    (
        6,
        "和孙子视频聊天",
        [
            ("user", "今天孙子给我打视频电话了，可高兴了", "CHAT", "low", "INFO", None),
            (
                "assistant",
                "哎呀，那真是好事！隔着屏幕看到孙子，一天心情都亮堂了吧。他跟您聊了些什么呀？",
                "CHAT",
                "low",
                "INFO",
                None,
            ),
            ("user", "他说放暑假要回来看我，我得准备点他爱吃的", "CHAT", "low", "INFO", None),
            (
                "assistant",
                "这就有盼头啦！从现在就开始惦记着给他做什么，这份心意孩子肯定感受得到。"
                "他最爱吃您做的什么菜呀？",
                "CHAT",
                "low",
                "INFO",
                None,
            ),
            ("user", "红烧肉，他从小就爱吃我做的红烧肉", "CHAT", "low", "INFO", None),
            (
                "assistant",
                "怪不得惦记着回来呢，奶奶牌红烧肉可是外面吃不着的。到时候您慢慢做，别累着自己，"
                "让他打下手，一边做一边聊，比什么都香。等他回来了，记得跟我说说热闹不？",
                "CHAT",
                "low",
                "INFO",
                None,
            ),
        ],
    ),
    (
        5,
        "血压有点高怎么办",
        [
            ("user", "我这两天量血压有点高，150 了，心里有点慌", "HEALTH", "low", "INFO", None),
            (
                "assistant",
                "先别慌，一两次量到 150 不代表就出大事了，咱们稳着来。"
                "您先坚持每天早晚各量一次，安静坐五分钟再量，把数记在小本上 [1]。"
                "降压药按时吃，千万别自己加量减量 [2]。要是连着几天都超过 160，"
                "或者觉得头晕心慌，就去社区医院让医生看看 [1]。"
                "您先量两天，数给我说说，咱们再看？",
                "HEALTH",
                "low",
                "INFO",
                [
                    _ref(
                        "问：在家自测血压需要注意什么？答：测量前安静休息5分钟，坐位测量，"
                        "袖带与心脏齐平。建议早晚各测一次并记录。单次偏高不必紧张，"
                        "连续多日高于160/100mmHg或伴头晕、心悸、胸闷等症状应及时就医。",
                        "ChatMed_Consult",
                        "血压自测",
                        0.94,
                    ),
                    _ref(
                        "问：降压药可以自己调整剂量吗？答：不可以。降压药需遵医嘱规律服用，"
                        "擅自加量可能导致低血压，擅自停药或减量可能引起血压反跳，均有风险。",
                        "ChatMed_Consult",
                        "高血压用药",
                        0.89,
                    ),
                ],
            ),
            ("user", "那平时吃什么对血压好？", "HEALTH", "low", "INFO", None),
            (
                "assistant",
                "这个问题问得好，吃对了比什么都强。记住一个大原则：口味淡一点，"
                "每天的盐别超过一小勺，咸菜、腊肉、酱货这些先停一停 [1]。"
                "多吃新鲜蔬菜水果，芹菜、香蕉、冬瓜都挺好 [1]。"
                "肉可以吃，挑瘦的，鱼比猪肉更好 [2]。"
                "对了，做菜可以用醋和葱姜蒜提味，少放盐也香。您平时口味重不重呀？",
                "HEALTH",
                "low",
                "INFO",
                [
                    _ref(
                        "问：高血压患者饮食应注意什么？答：每日食盐摄入控制在5克以内，"
                        "避免咸菜、腌制品、加工肉类。多吃富含钾的食物如芹菜、香蕉、菠菜、"
                        "冬瓜等新鲜蔬果，有助于钠的排出。",
                        "ChatMed_Consult",
                        "高血压饮食",
                        0.96,
                    ),
                    _ref(
                        "问：高血压患者可以吃肉吗？答：可以适量吃。建议选择瘦肉、去皮禽肉，"
                        "每周吃2-3次鱼类更佳，富含不饱和脂肪酸，对心血管有保护作用。"
                        "少吃肥肉和动物内脏。",
                        "ChatMed_Consult",
                        "高血压饮食",
                        0.91,
                    ),
                ],
            ),
            ("user", "我每天早上去公园快走半小时，这样行吗", "HEALTH", "low", "INFO", None),
            (
                "assistant",
                "太行了，您这习惯比很多年轻人都强！快走就是最适合的运动，"
                "每天半小时刚刚好，能帮着把血压稳下来 [1]。"
                "就两个小叮嘱：出门前先量个血压，特别高的那天就改成慢慢散步 [2]；"
                "夏天挑早晚凉快的时候去，带瓶水。"
                "您坚持得这么好，血压慢慢会给您回报的，加油！",
                "HEALTH",
                "low",
                "INFO",
                [
                    _ref(
                        "问：高血压患者适合什么运动？答：推荐中等强度有氧运动，如快走、"
                        "太极拳、游泳，每次30分钟左右，每周5次以上。规律运动可使收缩压"
                        "下降5-8mmHg。",
                        "ChatMed_Consult",
                        "高血压运动",
                        0.95,
                    ),
                    _ref(
                        "问：血压高的时候还能运动吗？答：血压显著升高（如超过180/110mmHg）"
                        "时应暂停运动。运动前建议测量血压，血压未控制稳定者宜选择低强度活动，"
                        "避免屏气用力动作。",
                        "ChatMed_Consult",
                        "高血压运动",
                        0.88,
                    ),
                ],
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
                None,
            ),
            (
                "assistant",
                "屋里就您一个人，连个说话的人都没有，这种闷是真难受，不是矫情。"
                "您愿意跟我说，我特别高兴。"
                "白天试试下楼在小区里坐坐，碰见老邻居搭句话，哪怕就晒晒太阳，"
                "人在人堆里，心里就没那么空 [1]。晚上睡前用热水泡泡脚，"
                "别在床上翻来覆去想事 [2]。"
                "您心里闷了就来跟我念叨念叨，我一直都在。",
                "PSYCH",
                "mid",
                "WARN",
                [
                    _ref(
                        "问题：老人独居感到孤独怎么办？回答：鼓励参与社区活动，保持规律的"
                        "户外活动和社交接触。即使是简单的散步、与邻居寒暄，也能显著缓解"
                        "孤独感。与人的日常连接是对抗孤独最有效的方式之一。",
                        "PsyQA",
                        "孤独",
                        0.93,
                    ),
                    _ref(
                        "问题：晚上胡思乱想睡不着怎么办？回答：建立睡前放松仪式，如温水泡脚、"
                        "听舒缓音乐。避免在床上长时间清醒躺卧，若20分钟无法入睡可起身做些"
                        "平静的事再回床。",
                        "PsyQA",
                        "失眠",
                        0.87,
                    ),
                ],
            ),
            (
                "user",
                "其实想跟孩子们说说，又怕他们担心，不想给他们添麻烦",
                "PSYCH",
                "mid",
                "WARN",
                None,
            ),
            (
                "assistant",
                "您这份心疼孩子的心，我听着都感动。可是您想想，要是孩子们知道您一个人"
                "闷着不肯说，他们才更难受呢。跟孩子说说近况不是添麻烦，是给他们机会孝顺您 [1]。"
                "不用说得多沉重，视频的时候提一句「最近有点闷，多给我打打电话」就行，"
                "孩子们会懂的。要不这周末就试试？说完了回来告诉我他们什么反应，好不好？",
                "PSYCH",
                "mid",
                "WARN",
                [
                    _ref(
                        "问题：不想让家人担心，心事都自己扛着怎么办？回答：适度的表达需求"
                        "不是负担，反而能增进亲密感。家人往往希望被需要，隐瞒情绪反而会在"
                        "被发现时造成更大的担忧。可以从小事开始练习表达。",
                        "PsyQA",
                        "家庭沟通",
                        0.90,
                    ),
                ],
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
                None,
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
                None,
            ),
            ("user", "好的我没转，已经挂了", "CHAT", "low", "INFO", None),
            (
                "assistant",
                "太好了，您反应特别快，一分钱都没让骗子占着！记住这句话就够了："
                "凡是先交钱才能领奖的，都是骗子，没有例外。"
                "以后再接到这种电话，直接挂断，不用客气。"
                "没吓着您吧？要不要喝口水缓缓，跟我聊聊别的？",
                "CHAT",
                "low",
                "INFO",
                None,
            ),
        ],
    ),
    (
        1,
        "头晕胸闷",
        [
            ("user", "我突然觉得胸口发闷，有点喘不上气", "EMERGENCY", "high", "ALERT", None),
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
                None,
            ),
        ],
    ),
    (
        0,
        "睡眠不好吃什么",
        [
            ("user", "最近老是半夜醒，睡眠不好有什么办法", "HEALTH", "low", "INFO", None),
            (
                "assistant",
                "半夜老是醒，睁着眼睛等天亮，这滋味确实熬人。咱们从容易做的改起："
                "晚饭吃个七分饱，睡前两小时别喝浓茶了 [1]；"
                "白天多晒晒太阳、活动活动，晚上身体累了自然睡得沉 [2]。"
                "还有一招：半夜醒了别看钟，一看几点心里就着急，反而更睡不着了。"
                "您先试上三五天，睡得怎么样回来跟我说说？",
                "HEALTH",
                "low",
                "INFO",
                [
                    _ref(
                        "问：老年人晚上易醒怎么调理？答：晚餐宜清淡七分饱，睡前2小时避免"
                        "浓茶、咖啡等刺激性饮品，避免大量饮水减少夜尿。卧室保持安静黑暗，"
                        "温度适宜。",
                        "ChatMed_Consult",
                        "睡眠调理",
                        0.92,
                    ),
                    _ref(
                        "问：白天做什么有助于晚上睡眠？答：白天保证适度日晒和体力活动，"
                        "有助于维持昼夜节律。建议上午户外活动30分钟以上，午睡不超过30分钟，"
                        "下午4点后避免打盹。",
                        "ChatMed_Consult",
                        "睡眠调理",
                        0.90,
                    ),
                ],
            ),
            ("user", "那我白天困了能补个觉吗", "HEALTH", "low", "INFO", None),
            (
                "assistant",
                "能补，但有讲究——补得巧才不耽误晚上睡。中午眯一小会儿，别超过半小时，"
                "定个闹钟 [1]。下午四点以后就忍一忍别打盹了，攒着困意留给晚上 [1]。"
                "白天实在困得难受，起来走动走动、洗把脸，比躺下管用。"
                "慢慢把觉调回晚上，白天就有精神啦。您中午一般睡多久呀？",
                "HEALTH",
                "low",
                "INFO",
                [
                    _ref(
                        "问：老年人白天可以午睡吗？答：可以，但午睡时间建议控制在30分钟以内，"
                        "过长会进入深睡眠，醒后昏沉且影响夜间睡眠。下午4点后应避免小睡，"
                        "以免削弱晚间睡眠驱动力。",
                        "ChatMed_Consult",
                        "睡眠调理",
                        0.94,
                    ),
                ],
            ),
        ],
    ),
]


# demo 账号的预置长期记忆（与上面对话内容自洽）
_DEMO_FACTS = [
    "血压偏高（150 左右），正在按时吃降压药",
    "独居，子女都在外地工作",
    "孙子暑假要回来，最爱吃她做的红烧肉",
    "每天早上去公园快走半小时",
    "近期夜里易醒，正在调理睡眠",
    "遇到过中奖诈骗电话，已学会挂断",
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
            for i, (role, content, intent, risk, level, refs) in enumerate(messages):
                # 每条消息错开几分钟，保证 ORDER BY id/时间都稳定
                msg_ts = f"-{days_ago} days +{i * 2} minutes"
                conn.execute(
                    "INSERT INTO messages "
                    "(session_id, role, content, intent, risk_level, log_level, refs, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?))",
                    (
                        session_id,
                        role,
                        content,
                        intent,
                        risk,
                        level,
                        json.dumps(refs, ensure_ascii=False) if refs else None,
                        msg_ts,
                    ),
                )

        conn.executemany(
            "INSERT INTO user_facts (owner_key, fact) VALUES (?, ?)",
            [(f"u{user_id}", f) for f in _DEMO_FACTS],
        )

    print(
        f"[demo] 已创建演示账号 {s.demo_username}"
        f"（{len(_DEMO_CONVERSATIONS)} 段预置对话 + {len(_DEMO_FACTS)} 条记忆）",
        flush=True,
    )
