"""管理端 endpoint：导出对话日志为 Excel。

后续可加 Spring Security 风格的权限控制（admin only），现在为 MVP 不做。
"""
from io import BytesIO

from fastapi import APIRouter, Query
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ..db import get_conn

router = APIRouter(prefix="/admin", tags=["admin"])


_HEADERS = ["id", "时间", "会话", "角色", "意图", "风险", "日志级别", "内容"]
_COL_WIDTHS = [6, 20, 14, 10, 12, 8, 12, 80]

_LEVEL_FILL = {
    "ALERT": PatternFill("solid", fgColor="FFC7CE"),
    "WARN": PatternFill("solid", fgColor="FFEB9C"),
    "INFO": PatternFill("solid", fgColor="FFFFFF"),
}


@router.get("/export_excel")
def export_excel(
    session_id: str | None = Query(default=None, description="只导某个 session"),
    log_level: str | None = Query(default=None, description="过滤 INFO/WARN/ALERT"),
    limit: int = Query(default=1000, ge=1, le=10000),
):
    sql = "SELECT id, created_at, session_id, role, intent, risk_level, log_level, content FROM messages"
    conds: list[str] = []
    args: list = []
    if session_id:
        conds.append("session_id = ?")
        args.append(session_id)
    if log_level:
        conds.append("log_level = ?")
        args.append(log_level.upper())
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "对话日志"

    # 表头
    ws.append(_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.fill = PatternFill("solid", fgColor="305496")

    # 列宽
    for i, w in enumerate(_COL_WIDTHS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    # 数据行
    for r in rows:
        ws.append(
            [
                r["id"],
                r["created_at"],
                r["session_id"],
                r["role"],
                r["intent"] or "",
                r["risk_level"] or "",
                r["log_level"] or "",
                r["content"],
            ]
        )
        # 按日志级别给整行上色
        level = (r["log_level"] or "").upper()
        fill = _LEVEL_FILL.get(level)
        if fill is not None and level != "INFO":
            row_idx = ws.max_row
            for col in range(1, len(_HEADERS) + 1):
                ws.cell(row=row_idx, column=col).fill = fill

    # 内容列自动换行
    for row in ws.iter_rows(min_row=2):
        row[-1].alignment = Alignment(wrap_text=True, vertical="top")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = "eldercare_log.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats")
def stats():
    """快速看一眼日志分布，便于面试 demo 时演示数据。"""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        by_intent = conn.execute(
            "SELECT intent, COUNT(*) AS c FROM messages "
            "WHERE role='user' AND intent IS NOT NULL GROUP BY intent"
        ).fetchall()
        by_level = conn.execute(
            "SELECT log_level, COUNT(*) AS c FROM messages "
            "WHERE role='user' AND log_level IS NOT NULL GROUP BY log_level"
        ).fetchall()

    return {
        "total_messages": total,
        "by_intent": {r["intent"]: r["c"] for r in by_intent},
        "by_log_level": {r["log_level"]: r["c"] for r in by_level},
    }
