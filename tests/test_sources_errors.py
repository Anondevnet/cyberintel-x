"""Error-handling paths. The mock transport returns bare status codes / connection errors,
never invented payloads, so no fabricated data is used to claim integration success."""
from cyberintel import db
from cyberintel.sources import awesome_hacking as ah, health, huggingface as hf
from cyberintel.sources.registry import sync_registry

from conftest import offline_client


def _conn(settings):
    db.migrate(settings.db_path, settings.backups_dir)
    c = db.connect(settings.db_path)
    sync_registry(c)
    return c


def test_hf_gated_is_reported_as_access_required(settings):
    c = _conn(settings)
    res = hf.index_metadata(c, offline_client(401), settings.hf_dataset_id, None)
    assert res["access_status"] == "access_required" and res["files"] == 0
    row = c.execute("SELECT access_status, verification_status FROM dataset_meta").fetchone()
    assert row["access_status"] == "access_required" and row["verification_status"] == "unverified"
    assert c.execute("SELECT status FROM sync_runs WHERE source_id='huggingface'").fetchone()[0] == "blocked"


def test_hf_network_error_recorded(settings):
    c = _conn(settings)
    res = hf.index_metadata(c, offline_client(0), settings.hf_dataset_id, None)
    assert res["access_status"] == "error" and "ConnectError" in res["error"]


def test_awesome_sync_failure_is_recorded_not_silent(settings):
    c = _conn(settings)
    try:
        ah.sync(c, offline_client(503))
    except Exception:
        pass
    run = c.execute("SELECT status, error FROM sync_runs WHERE source_id='awesome_hacking'").fetchone()
    assert run["status"] == "error" and "503" in run["error"]
    assert c.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0


def test_health_checks_never_report_ok_when_offline(settings):
    c = _conn(settings)
    results = health.run_all(c, offline_client(0), settings)
    assert not any(r["ok"] for r in results.values())
    assert results["openai_compat"].get("skipped")  # unconfigured -> skipped, not "failing"
    assert c.execute("SELECT COUNT(*) FROM source_health WHERE ok=1").fetchone()[0] == 0


def test_disk_status(tmp_path):
    d = hf.disk_status(tmp_path, 1e9)
    assert d["ok_for_downloads"] is False and d["free_gb"] >= 0
