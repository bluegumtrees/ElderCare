"""密码哈希 + 登录态依赖。全部标准库实现，不引入新依赖。

- 密码：PBKDF2-HMAC-SHA256，20 万次迭代，随机 salt，格式
  `pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>`
- 登录态：服务端 token（secrets.token_urlsafe）存 SQLite，可撤销、可过期；
  客户端只拿一个 httponly cookie，前端 JS 摸不到。
"""
import hashlib
import hmac
import secrets

from fastapi import Cookie, Depends, Header, HTTPException

from .db import get_user_by_token

COOKIE_NAME = "eldercare_token"
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS
    )
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), expected)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def extract_token(
    authorization: str | None, cookie_token: str | None
) -> str | None:
    """优先 Authorization: Bearer（HF Space 的跨站 iframe 里 cookie 会被浏览器
    屏蔽，header 是主通道），cookie 作为直连访问时的备用。"""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return cookie_token


def get_optional_user(
    authorization: str | None = Header(default=None),
    eldercare_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict | None:
    """token 有效则返回 {id, username, display_name}，否则 None。匿名可用的端点用它。"""
    token = extract_token(authorization, eldercare_token)
    if not token:
        return None
    return get_user_by_token(token)


def require_user(user: dict | None = Depends(get_optional_user)) -> dict:
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
