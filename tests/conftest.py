"""Shared pytest fixtures.

The critical requirement: every test runs against an ISOLATED temp SQLite DB.
`config` reads DB_PATH once at import, but `db.get_conn()` re-reads `config.DB_PATH`
on every call. So we monkeypatch `config.DB_PATH` to a fresh file per test and call
`db.init_db()`. `config.LOCAL_TZ` stays "Europe/Istanbul" (tzutil binds it at import).
"""
import pytest


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point the DB layer at a clean, throwaway SQLite file for each test.

    Autouse -> every test is isolated. Tests that need a custom on-disk schema
    (e.g. the migration test) can re-`monkeypatch.setattr(config, "DB_PATH", ...)`
    to their own file and call `db.init_db()` themselves.
    """
    import config
    import db

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    yield
