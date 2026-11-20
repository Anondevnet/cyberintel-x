"""SQLite storage: append-only versioned migrations, automatic backup before migrating,
no destructive resets. Every content row carries provenance (source, retrieval time,
verification status)."""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# Verification vocabulary used across the app (Command Center distinguishes these).
VERIFIED = "verified"        # fetched directly from the named authoritative source
UNVERIFIED = "unverified"    # third-party / not cross-checked
HISTORICAL = "historical"    # was true at retrieval time; may be stale
SIMULATED = "simulated"      # demo/educational content; never live intelligence
VERIFICATION_STATES = (VERIFIED, UNVERIFIED, HISTORICAL, SIMULATED)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# Append-only. Never edit a released migration; add a new one.
MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "phase1 foundation", """
    CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        base_url TEXT,
        docs_url TEXT,
        auth_env TEXT,
        requires_auth INTEGER NOT NULL DEFAULT 0,
        phase INTEGER NOT NULL DEFAULT 1,
        notes TEXT
    );

    CREATE TABLE source_health (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL REFERENCES sources(id),
        checked_at TEXT NOT NULL,
        ok INTEGER NOT NULL,
        status_code INTEGER,
        latency_ms INTEGER,
        detail TEXT,
        error TEXT,
        rate_limit TEXT
    );
    CREATE INDEX ix_health_source_time ON source_health(source_id, checked_at DESC);

    CREATE TABLE sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL REFERENCES sources(id),
        job TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        items_seen INTEGER DEFAULT 0,
        items_new INTEGER DEFAULT 0,
        items_updated INTEGER DEFAULT 0,
        content_sha256 TEXT,
        error TEXT
    );
    CREATE INDEX ix_sync_source_time ON sync_runs(source_id, started_at DESC);

    CREATE TABLE categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        description TEXT,
        source_id TEXT NOT NULL REFERENCES sources(id),
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );

    CREATE TABLE resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        description TEXT,
        section TEXT,                      -- README section it was found in
        kind TEXT NOT NULL,                -- awesome_list | repository | tool
        category_id INTEGER REFERENCES categories(id),
        parent_resource_id INTEGER REFERENCES resources(id),
        gh_owner TEXT,
        gh_repo TEXT,
        license TEXT,                      -- per-project; NOT inherited from aggregator
        stars INTEGER,
        forks INTEGER,
        pushed_at TEXT,
        archived INTEGER,
        metadata_fetched_at TEXT,
        link_status TEXT,                  -- null until checked
        link_checked_at TEXT,
        safety_class TEXT NOT NULL DEFAULT 'unreviewed',
        source_id TEXT NOT NULL REFERENCES sources(id),
        source_ref TEXT,                   -- e.g. README URL + sha256
        retrieved_at TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        present_in_source INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX ix_resources_category ON resources(category_id);
    CREATE INDEX ix_resources_kind ON resources(kind);
    CREATE INDEX ix_resources_gh ON resources(gh_owner, gh_repo);

    CREATE VIRTUAL TABLE resources_fts USING fts5(
        name, description, section, content='resources', content_rowid='id'
    );
    CREATE TRIGGER resources_ai AFTER INSERT ON resources BEGIN
        INSERT INTO resources_fts(rowid, name, description, section)
        VALUES (new.id, new.name, new.description, new.section);
    END;
    CREATE TRIGGER resources_ad AFTER DELETE ON resources BEGIN
        INSERT INTO resources_fts(resources_fts, rowid, name, description, section)
        VALUES ('delete', old.id, old.name, old.description, old.section);
    END;
    CREATE TRIGGER resources_au AFTER UPDATE ON resources BEGIN
        INSERT INTO resources_fts(resources_fts, rowid, name, description, section)
        VALUES ('delete', old.id, old.name, old.description, old.section);
        INSERT INTO resources_fts(rowid, name, description, section)
        VALUES (new.id, new.name, new.description, new.section);
    END;

    CREATE TABLE favorites (
        resource_id INTEGER PRIMARY KEY REFERENCES resources(id),
        created_at TEXT NOT NULL
    );

    CREATE TABLE dataset_meta (
        dataset_id TEXT PRIMARY KEY,
        access_status TEXT NOT NULL,       -- accessible | access_required | not_found | error | unknown
        gated TEXT,
        private INTEGER,
        license TEXT,
        last_modified TEXT,
        sha TEXT,
        downloads INTEGER,
        card_json TEXT,
        file_count INTEGER,
        total_bytes INTEGER,
        retrieved_at TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        last_error TEXT
    );

    CREATE TABLE dataset_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        path TEXT NOT NULL,
        size INTEGER,
        oid TEXT,
        is_lfs INTEGER,
        revision_sha TEXT,
        retrieved_at TEXT NOT NULL,
        download_status TEXT NOT NULL DEFAULT 'not_downloaded',
        local_path TEXT,
        UNIQUE(dataset_id, path)
    );

    CREATE TABLE dataset_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id TEXT NOT NULL,
        file_path TEXT NOT NULL,
        record_index INTEGER NOT NULL,
        schema_kind TEXT,
        title TEXT,
        body TEXT,
        raw_json TEXT,
        content_hash TEXT NOT NULL UNIQUE,
        revision_sha TEXT,
        ingested_at TEXT NOT NULL,
        verification_status TEXT NOT NULL
    );
    CREATE INDEX ix_records_file ON dataset_records(dataset_id, file_path);
    CREATE VIRTUAL TABLE dataset_records_fts USING fts5(
        title, body, content='dataset_records', content_rowid='id'
    );
    CREATE TRIGGER records_ai AFTER INSERT ON dataset_records BEGIN
        INSERT INTO dataset_records_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
    END;

    CREATE TABLE activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        source_id TEXT,
        message TEXT NOT NULL,
        verification_status TEXT
    );
    CREATE INDEX ix_activity_ts ON activity(ts DESC);
    """),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def backup(db_path: Path, backups_dir: Path, reason: str) -> Path | None:
    """Online-consistent copy via the SQLite backup API. Never deletes old backups."""
    if not db_path.exists():
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backups_dir / f"cyberintel-{stamp}-{reason}.sqlite3"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    return dest


def migrate(db_path: Path, backups_dir: Path) -> list[int]:
    """Apply pending migrations. Backs up an existing DB first. Returns applied versions."""
    conn = connect(db_path)
    try:
        version = current_version(conn)
        pending = [m for m in MIGRATIONS if m[0] > version]
        if not pending:
            return []
        if version > 0:
            conn.close()
            backup(db_path, backups_dir, f"pre-migrate-v{version}")
            conn = connect(db_path)
        applied = []
        for num, name, sql in pending:
            with conn:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_version(version, name, applied_at) VALUES (?,?,?)",
                    (num, name, utcnow()),
                )
            applied.append(num)
        return applied
    finally:
        conn.close()


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_activity(conn: sqlite3.Connection, kind: str, message: str,
                 source_id: str | None = None, verification_status: str | None = None) -> None:
    conn.execute(
        "INSERT INTO activity(ts, kind, source_id, message, verification_status) VALUES (?,?,?,?,?)",
        (utcnow(), kind, source_id, message, verification_status),
    )
