"""高风险预警通知。

设计要点：
- SMTP 未配置时走 stdout dev 模式，方便本地开发不挂依赖。
- 异步发送：调用方用 asyncio.create_task() 派发，不阻塞流式响应。
- 不同 intent 用不同邮件标题，方便家属在邮箱里快速识别紧急程度。
"""
from datetime import datetime
from email.message import EmailMessage

import aiosmtplib

from .config import get_settings

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


async def send_alert(intent: str, risk: str, session_id: str, user_message: str) -> None:
    """触发一次高风险预警。SMTP 未配置时走 stdout 假发送。"""
    s = get_settings()
    subject = _compose_subject(intent)
    body = _compose_body(intent, risk, session_id, user_message)

    if not s.smtp_enabled:
        print(
            "\n========== [DEV-NOTIFIER] 预警未发送（SMTP 未配置）==========\n"
            f"收件人: {s.alert_to_email or '<未配置>'}\n"
            f"主题  : {subject}\n"
            f"正文  :\n{body}\n"
            "============================================================\n",
            flush=True,
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
        print(f"[notifier] 预警邮件已发送 → {s.alert_to_email} ({intent})", flush=True)
    except Exception as e:
        # 真发送失败不抛异常，否则会污染 SSE 流
        print(f"[notifier] 邮件发送失败：{e!r}", flush=True)
