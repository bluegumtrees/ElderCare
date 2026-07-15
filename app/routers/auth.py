"""登录 / 注册 / demo 一键体验。

会话用服务端 token + httponly cookie：
- JS 摸不到 cookie（防 XSS 偷 token）
- token 存 SQLite，登出即删、到期失效，可随时撤销
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import Cookie

from ..config import get_settings
from ..db import (
    create_auth_token,
    create_user,
    delete_auth_token,
    get_user_by_username,
)
from ..demo_seed import ensure_demo_account
from ..schemas import LoginRequest, RegisterRequest
from ..security import (
    COOKIE_NAME,
    get_optional_user,
    hash_password,
    new_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _login_response(response: Response, user_id: int, username: str, display_name: str) -> dict:
    s = get_settings()
    token = new_token()
    create_auth_token(token, user_id, s.auth_token_ttl_days)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=s.auth_token_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return {"username": username, "display_name": display_name}


@router.post("/register")
def register(req: RegisterRequest, response: Response):
    if get_user_by_username(req.username):
        raise HTTPException(status_code=409, detail="用户名已被占用")
    display_name = (req.display_name or "").strip() or req.username
    user_id = create_user(req.username, hash_password(req.password), display_name)
    return _login_response(response, user_id, req.username, display_name)


@router.post("/login")
def login(req: LoginRequest, response: Response):
    user = get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码不对")
    return _login_response(
        response, user["id"], user["username"], user["display_name"] or user["username"]
    )


@router.post("/demo")
def demo_login(response: Response):
    """一键进入演示账号（不存在则现场创建并灌演示数据）。"""
    s = get_settings()
    ensure_demo_account()
    user = get_user_by_username(s.demo_username)
    if not user:
        raise HTTPException(status_code=500, detail="演示账号初始化失败")
    return _login_response(
        response, user["id"], user["username"], user["display_name"] or "演示账号"
    )


@router.post("/logout")
def logout(
    response: Response,
    eldercare_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
):
    if eldercare_token:
        delete_auth_token(eldercare_token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict | None = Depends(get_optional_user)):
    return {"user": user}
