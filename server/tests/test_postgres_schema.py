"""Startup schema code against a real Postgres.

_init_db runs on every deploy and restart. It uses Postgres-only SQL (JSONB,
SERIAL, TIMESTAMPTZ) that SQLite can't run, and swallows every migration
error, so a broken migration fails silently and surfaces later as missing
data. These tests check the resulting schema, not just "it didn't crash".

Opt-in: set TEST_DATABASE_URL to a Postgres server URL (CI provides one).
Each test creates and drops its own database on that server.
"""
from __future__ import annotations

import inspect as pyinspect
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("TEST_DATABASE_URL", "")

# Skip locally without a server, but never in CI: a missing URL there must fail
# loudly instead of quietly turning these tests green.
pytestmark = pytest.mark.skipif(
    not BASE_URL and not os.environ.get("CI"),
    reason="set TEST_DATABASE_URL to run Postgres schema tests",
)


def _with_db(url: str, dbname: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + dbname


def _sqlalchemy_url(url: str) -> str:
    return "postgresql+psycopg2://" + url.split("://", 1)[1]


@pytest.fixture
def fresh_db():
    """A brand-new empty database; yields its URL in Railway's postgres:// form."""
    import psycopg2

    name = f"schema_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(BASE_URL)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    url = _with_db(BASE_URL, name)
    yield "postgres://" + url.split("://", 1)[1]
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def _boot(db_url: str) -> str:
    """Start the app's DB layer exactly as production does: a fresh process
    with DATABASE_URL set, importing main and running _init_db."""
    env = {k: v for k, v in os.environ.items() if k != "FIREBASE_SERVICE_ACCOUNT_JSON"}
    env["DATABASE_URL"] = db_url
    script = ("import main\n"
              "print('DRIVER', main.engine.dialect.driver)\n"
              "main._init_db()\n"
              "print('INIT_DONE')\n")
    proc = subprocess.run([sys.executable, "-W", "ignore", "-c", script],
                          cwd=SERVER_DIR, env=env, capture_output=True,
                          text=True, timeout=180)
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output[-3000:]
    assert "INIT_DONE" in output, output[-3000:]
    return output


def _missing_model_columns(db_url: str) -> list[str]:
    import main
    from sqlalchemy import create_engine, inspect

    engine = create_engine(_sqlalchemy_url(db_url))
    try:
        insp = inspect(engine)
        live_tables = set(insp.get_table_names())
        missing = []
        for table in main._Base.metadata.sorted_tables:
            if table.name not in live_tables:
                missing.append(f"table {table.name}")
                continue
            live = {c["name"] for c in insp.get_columns(table.name)}
            missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in live]
        return missing
    finally:
        engine.dispose()


def _add_column_migrations() -> list[tuple[str, str]]:
    """(table, column) for every `ALTER TABLE ... ADD COLUMN` in _init_db."""
    import main

    source = pyinspect.getsource(main._init_db)
    return re.findall(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)", source)


def _migration_warnings(output: str) -> list[str]:
    return [line for line in output.splitlines()
            if "migration skipped" in line or "failed to create tables" in line]


def test_first_boot_on_empty_database_builds_full_schema(fresh_db):
    output = _boot(fresh_db)
    assert "DRIVER psycopg2" in output          # postgres:// resolves to the installed driver
    assert _migration_warnings(output) == []
    assert _missing_model_columns(fresh_db) == []


def test_reboot_on_existing_database_is_safe(fresh_db):
    # Every deploy after the first runs _init_db against a populated schema.
    _boot(fresh_db)
    output = _boot(fresh_db)
    assert _migration_warnings(output) == []
    assert _missing_model_columns(fresh_db) == []


def test_migrations_upgrade_an_older_database(fresh_db):
    # Production's database predates most columns. Recreate that by dropping
    # every column an ADD COLUMN migration is responsible for, then boot:
    # the migrations must bring each one back. A typo or Postgres-incompatible
    # migration would otherwise be swallowed silently.
    from sqlalchemy import create_engine, text

    _boot(fresh_db)
    migrated = _add_column_migrations()
    assert len(migrated) >= 30, "migration list not found in _init_db source"

    engine = create_engine(_sqlalchemy_url(fresh_db))
    with engine.begin() as conn:
        for table, column in migrated:
            conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN IF EXISTS "{column}" CASCADE'))
    engine.dispose()
    dropped = _missing_model_columns(fresh_db)
    assert dropped, "dropping migrated columns had no effect"

    _boot(fresh_db)

    assert _missing_model_columns(fresh_db) == []


def test_soccer_alarm_json_config_round_trips_through_the_api(fresh_db, monkeypatch):
    # soccer_alarms.config is JSONB in Postgres; SQLite has no such type.
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    import main

    _boot(fresh_db)
    engine = create_engine(_sqlalchemy_url(fresh_db))
    monkeypatch.setattr(main, "engine", engine)
    client = TestClient(main.app)

    config = {"max_diff": 2, "last_minutes": 20}
    created = client.post("/soccer-alarm", json={
        "user_token": "pg-test-token", "alarm_type": "late_drama",
        "match_id": "bsd-1", "match_name": "Home vs Away", "config": config,
    })
    assert created.status_code == 200, created.text

    listed = client.get("/soccer-alarms", params={"user_token": "pg-test-token"})
    assert listed.status_code == 200, listed.text
    alarms = listed.json().get("alarms") or listed.json()
    match = [a for a in alarms if a.get("match_id") == "bsd-1"]
    assert match and match[0]["config"] == config
    engine.dispose()
