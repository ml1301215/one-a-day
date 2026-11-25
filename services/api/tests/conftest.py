import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _tmp_db():
    """所有测试用临时库，不污染开发数据。"""
    d = tempfile.mkdtemp(prefix="oad-test-")
    os.environ["OAD_DB_PATH"] = str(Path(d) / "test.db")
    os.environ["OAD_PROVIDER"] = "mock"

    from app.config import settings

    settings.db_path = os.environ["OAD_DB_PATH"]
    settings.provider = "mock"

    from app import db

    db.init_db()
    yield
