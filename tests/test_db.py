import sqlite3

from cyberintel import db


def test_migrate_creates_schema_and_is_idempotent(tmp_path):
    p, b = tmp_path / "x.sqlite3", tmp_path / "bk"
    assert db.migrate(p, b) == [db.LATEST_VERSION]
    assert db.migrate(p, b) == []
    c = db.connect(p)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table')")}
    for t in ("sources", "resources", "resources_fts", "categories", "dataset_meta", "dataset_files",
              "dataset_records", "activity", "sync_runs", "source_health", "favorites", "schema_version"):
        assert t in names
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == db.LATEST_VERSION


def test_pending_migration_backs_up_existing_db(tmp_path, monkeypatch):
    p, b = tmp_path / "x.sqlite3", tmp_path / "bk"
    db.migrate(p, b)
    c = db.connect(p)
    db.log_activity(c, "note", "keep me")
    c.commit(); c.close()
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + [(999, "test add", "CREATE TABLE zz(x);")])
    assert db.migrate(p, b) == [999]
    backups = list(b.glob("*.sqlite3"))
    assert len(backups) == 1
    bc = sqlite3.connect(backups[0])
    assert bc.execute("SELECT message FROM activity").fetchone()[0] == "keep me"  # no data loss
