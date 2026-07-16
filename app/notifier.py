"""高风险预警通知。

设计要点：
- SMTP 未配置时走 stdout dev 模式，方便本地开发不挂依赖。
- 异步发送：调用方用 asyncio.create_task() 派发，不阻塞流式响应。
- 不同 intent 用不同邮件标题，方便家属在邮箱里快速识别紧急程度。
"""
import sys
from datetime import datetime
from email.message import EmailMessage

import aiosmtplib

from .config import get_settings


def _safe_print(text: str) -> None:
    """预警链路的日志绝不能自己崩：Windows GBK 控制台打不出 emoji 时降级替换。"""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc), flush=True)

_SUBJECT_PREFIX = {
    "EMERGENCY": "🚨【紧急】",
    "FRAUD": "⚠️【疑似诈骗】",
    "PSYCH": "💛【心理高危】",
}

_INTENT_DESC = {
    "EMERGENCY": "疑似突发医疗急症",
    "FRAUD": "疑似遭遇电信诈骗",
    "PSYCH": "情绪危机（高风险心理状态）",
}


def _compose_body(intent: str, risk: str, session_id: str, user_message: str) -> str:
    desc = _INTENT_DESC.get(intent, "高风险事件")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"系统检测到一起{desc}。\n\n"
        f"时间      : {now}\n"
        f"会话 ID   : {session_id}\n"
        f"风险等级  : {risk}\n"
        f"用户原话  : 「{user_message}」\n\n"
        f"建议尽快联系老人确认情况。\n"
        f"—— ElderCare 智能体自动通知"
    )


def _compose_subject(intent: str) -> str:
    prefix = _SUBJECT_PREFIX.get(intent, "【预警】")
    return f"{prefix}ElderCare 智能体高风险通知"


async def _deliver(subject: str, body: str) -> None:
    """真发邮件或 dev 模式打印，send_alert / send_daily_digest 共用。"""
    s = get_settings()
    if not s.smtp_enabled:
        _safe_print(
            "\n========== [DEV-NOTIFIER] 未发送（SMTP 未配置）==========\n"
            f"收件人: {s.alert_to_email or '<未配置>'}\n"
            f"主题  : {subject}\n"
            f"正文  :\n{body}\n"
            "============================================================\n"
        )
        return

    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = s.alert_to_email
    msg["Subject"] = subject
    msg.set_content(body)

    # 端口约定：465 走 SSL，587 走 STARTTLS，其它默认无加密
    use_tls = s.smtp_port == 465
    start_tls = s.smtp_port == 587

    try:
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_user,
            password=s.smtp_password,
            use_tls=use_tls,
            start_tls=start_tls,
            timeout=10,
        )
        _safe_print(f"[notifier] 邮件已发送 → {s.alert_to_email}（{subject[:20]}…）")
    except Exception as e:
        # 真发送失败不抛异常，否则会污染调用方
        _safe_print(f"[notifier] 邮件发送失败：{e!r}")


async def send_alert(intent: str, risk: str, session_id: str, user_message: str) -> None:
    """触发一次高风险预警。SMTP 未配置时走 stdout 假发送。"""
    subject = _compose_subject(intent)
    body = _compose_body(intent, risk, session_id, user_message)
    await _deliver(subject, body)


async def send_daily_digest() -> None:
    """每日汇总：近 24 小时的 WARN/ALERT 用户消息打包成一封邮件。

    没有可汇总的内容就跳过——避免家属被空邮件骚扰（告警疲劳的反面）。
    """
    from .db import get_warn_messages_since_hours

    rows = get_warn_messages_since_hours(24)
    if not rows:
        _safe_print("[digest] 近 24 小时无 WARN/ALERT，跳过每日汇总")
        return

    alerts = [r for r in rows if r["log_level"] == "ALERT"]
    warns = [r for r in rows if r["log_level"] == "WARN"]
    desc = {"EMERGENCY": "急症", "FRAUD": "诈骗", "PSYCH": "心理", "HEALTH": "健康"}

    lines = [
        f"过去 24 小时共记录 {len(alerts)} 条紧急预警、{len(warns)} 条关注事项。",
        "",
    ]
    if alerts:
        lines.append("【紧急预警（已实时通知）】")
        for r in alerts:
            lines.append(
                f"- {r['created_at']} [{desc.get(r['intent'], r['intent'])}] 「{r['content']}」"
            )
        lines.append("")
    if warns:
        lines.append("【需要关注（建议近期联系老人聊聊）】")
        for r in warns:
            lines.append(
                f"- {r['created_at']} [{desc.get(r['intent'], r['intent'])}] 「{r['content']}」"
            )
        lines.append("")
    lines.append("—— ElderCare 智能体每日汇总")

    subject = f"【每日汇总】ElderCare：{len(alerts)} 条预警 / {len(warns)} 条关注"
    await _deliver(subject, "\n".join(lines))
