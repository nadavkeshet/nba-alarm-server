#!/usr/bin/env python3
"""
One-shot, idempotent sync of iOS users' data from the dev Railway Postgres
into the prod Railway Postgres.

• Never deletes anything — from either database.
• Uses ON CONFLICT DO NOTHING / pre-INSERT existence checks, so re-running
  is safe and will not duplicate rows.
• Schemas are identical between dev and prod (verified against main.py).

Usage:
    export DEV_DATABASE_URL='postgres://...'   # Railway → dev service → Variables
    export PROD_DATABASE_URL='postgres://...'  # Railway → prod service → Variables
    python3 sync_dev_to_prod.py                # dry-run: prints what WOULD happen
    python3 sync_dev_to_prod.py --apply        # actually writes to prod
"""
from __future__ import annotations
import os
import sys
from typing import Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


# ── Config ────────────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"ERROR: {name} env var is required.", file=sys.stderr)
        sys.exit(2)
    return v


def _normalize(url: str) -> str:
    """Railway hands out `postgres://` URLs; SQLAlchemy wants `postgresql://`."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


DEV_URL = _normalize(_require_env("DEV_DATABASE_URL"))
PROD_URL = _normalize(_require_env("PROD_DATABASE_URL"))
APPLY = "--apply" in sys.argv

dev = create_engine(DEV_URL, pool_pre_ping=True)
prod = create_engine(PROD_URL, pool_pre_ping=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dev_rows(table: str) -> list[dict]:
    with dev.connect() as c:
        return [dict(r) for r in c.execute(text(f"SELECT * FROM {table}")).mappings().all()]


def _prod_exists(conn, table: str, dedup_cols: Iterable[str], row: dict) -> bool:
    where = " AND ".join(f"{c} = :{c}" for c in dedup_cols)
    params = {c: row[c] for c in dedup_cols}
    return conn.execute(
        text(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1"), params
    ).first() is not None


def _insert_row(conn, table: str, row: dict) -> None:
    cols = list(row.keys())
    collist = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    conn.execute(text(f"INSERT INTO {table} ({collist}) VALUES ({placeholders})"), row)


def sync_natural_pk(table: str, pk_col: str) -> tuple[int, int]:
    """Copy rows keyed by a natural PK using ON CONFLICT DO NOTHING."""
    rows = _dev_rows(table)
    inserted = 0
    skipped = 0
    if not APPLY:
        # Dry-run: count how many would be new
        with prod.connect() as pc:
            for r in rows:
                if _prod_exists(pc, table, [pk_col], r):
                    skipped += 1
                else:
                    inserted += 1
        return inserted, skipped
    with prod.begin() as pc:
        for r in rows:
            cols = list(r.keys())
            collist = ", ".join(cols)
            placeholders = ", ".join(f":{c}" for c in cols)
            result = pc.execute(
                text(
                    f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({pk_col}) DO NOTHING"
                ),
                r,
            )
            if result.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped


def sync_autoinc(table: str, dedup_cols: list[str]) -> tuple[int, int]:
    """Copy rows with auto-increment PK. Drops `id` before insert; dedups on
    the semantic key (dedup_cols) so re-runs don't duplicate rows."""
    rows = _dev_rows(table)
    inserted = 0
    skipped = 0
    conn_mgr = prod.begin() if APPLY else prod.connect()
    with conn_mgr as pc:
        for r in rows:
            r_no_id = dict(r)
            r_no_id.pop("id", None)
            if _prod_exists(pc, table, dedup_cols, r_no_id):
                skipped += 1
                continue
            if APPLY:
                _insert_row(pc, table, r_no_id)
            inserted += 1
    return inserted, skipped


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"{'APPLY' if APPLY else 'DRY-RUN'} — dev → prod sync")
    print(f"  dev:  {DEV_URL.split('@')[-1]}")
    print(f"  prod: {PROD_URL.split('@')[-1]}")
    print()

    # Pre-flight: ensure both DBs reachable and same tables exist
    for label, eng in [("dev", dev), ("prod", prod)]:
        with eng.connect() as c:
            tables = {r[0] for r in c.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            ))}
        required = {"alarms", "favorites", "favorite_teams", "user_preferences", "fired_alerts"}
        missing = required - tables
        if missing:
            print(f"ERROR: {label} missing tables: {missing}", file=sys.stderr)
            return 3

    # 1. user_preferences — natural PK = user_token. Preserves prod's Android
    #    settings; only adds iOS user rows that don't already exist in prod.
    ins, skip = sync_natural_pk("user_preferences", "user_token")
    print(f"user_preferences : +{ins} new, {skip} existing preserved")

    # 2. fired_alerts — natural PK = key. Dedup prevents the same alert
    #    firing a second time on prod after the switch.
    ins, skip = sync_natural_pk("fired_alerts", "key")
    print(f"fired_alerts     : +{ins} new, {skip} existing preserved")

    # 3. favorite_teams — auto-inc id. Dedup on (user_token, team_id).
    ins, skip = sync_autoinc("favorite_teams", ["user_token", "team_id"])
    print(f"favorite_teams   : +{ins} new, {skip} existing preserved")

    # 4. favorites (players) — auto-inc id. Dedup on (user_token, target_id).
    ins, skip = sync_autoinc("favorites", ["user_token", "target_id"])
    print(f"favorites        : +{ins} new, {skip} existing preserved")

    # 5. alarms — auto-inc id. Dedup on (user_token, target_id, game_id) so
    #    a re-run doesn't re-insert the same active alarm. Inactive alarms
    #    with matching keys are also treated as duplicates (we're not here to
    #    revive them on prod).
    ins, skip = sync_autoinc("alarms", ["user_token", "target_id", "game_id"])
    print(f"alarms           : +{ins} new, {skip} existing preserved")

    # notification_history is intentionally skipped: append-only log, moving
    # it provides no user-facing value and risks PK collisions.

    if not APPLY:
        print()
        print("Dry-run complete. Nothing was written to prod.")
        print("Re-run with --apply to actually copy the rows.")
    else:
        print()
        print("Sync applied. Dev DB untouched. Prod DB now contains iOS + Android data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
