"""历史会话：列表 + 回看 + 删除。仅登录用户可用，且只能操作自己的会话。"""
from fastapi import APIRouter, Depends, HTTPException

from ..db import (
    delete_conversation,
    get_conversation,
    get_session_messages,
    list_conversations,
)
from ..security import require_user

router = APIRouter(prefix="/conversations", tags=["history"])


def _owned_conversation(session_id: str, user: dict) -> dict:
    conv = get_conversation(session_id)
    if not conv or conv["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


@router.get("")
def my_conversations(user: dict = Depends(require_user)):
    return {"conversations": list_conversations(user["id"])}


@router.get("/{session_id}/messages")
def conversation_messages(session_id: str, user: dict = Depends(require_user)):
    conv = _owned_conversation(session_id, user)
    return {
        "session_id": session_id,
        "title": conv["title"],
        "messages": get_session_messages(session_id),
    }


@router.delete("/{session_id}")
def remove_conversation(session_id: str, user: dict = Depends(require_user)):
    _owned_conversation(session_id, user)
    delete_conversation(session_id)
    return {"ok": True}
