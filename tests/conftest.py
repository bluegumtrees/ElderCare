"""测试公共配置：隔离的 SQLite + 空 API key（不发真实请求）。"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须在任何 app 模块 import 前设好环境
os.environ.setdefault("OPENROUTER_API_KEY", "")
os.environ.setdefault("ADMIN_TOKEN", "")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """每个用例一个干净的 SQLite 库。"""
    from app.config import get_settings

    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()

    from app.db import init_db

    init_db()
    yield
    get_settings.cache_clear()
