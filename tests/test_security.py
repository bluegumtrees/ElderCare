"""密码哈希与 token 提取。"""
from app.security import extract_token, hash_password, verify_password


def test_hash_verify_roundtrip():
    stored = hash_password("正确密码123")
    assert verify_password("正确密码123", stored)
    assert not verify_password("错误密码", stored)


def test_hashes_are_salted():
    assert hash_password("同一个密码") != hash_password("同一个密码")


def test_verify_rejects_malformed_stored_value():
    assert not verify_password("x", "不是合法格式")
    assert not verify_password("x", "md5$1$00$00")


def test_extract_token_prefers_bearer_header():
    assert extract_token("Bearer abc123", "cookie-token") == "abc123"
    assert extract_token("bearer abc123", None) == "abc123"  # 大小写不敏感


def test_extract_token_falls_back_to_cookie():
    assert extract_token(None, "cookie-token") == "cookie-token"
    assert extract_token("Basic xxx", "cookie-token") == "cookie-token"
    assert extract_token("Bearer ", "cookie-token") == "cookie-token"  # 空 bearer 回退
    assert extract_token(None, None) is None
