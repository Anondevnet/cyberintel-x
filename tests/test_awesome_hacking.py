from cyberintel import db
from cyberintel.sources import awesome_hacking as ah
from cyberintel.sources.registry import sync_registry


def _conn(tmp_path):
    p = tmp_path / "a.sqlite3"
    db.migrate(p, tmp_path / "bk")
    c = db.connect(p)
    sync_registry(c)
    return c


def test_parse_real_readme_sections_discovered(real_readme):
    entries = ah.parse_readme(real_readme)
    sections = {e.section for e in entries}
    assert "Awesome Repositories" in sections
    assert "Other Useful Repositories" in sections
    lists = [e for e in entries if e.kind == "awesome_list"]
    repos = [e for e in entries if e.kind == "repository"]
    assert len(lists) >= 40 and len(repos) >= 25          # snapshot: ~47 / ~34
    names = {e.name for e in lists}
    for expected in ("Android Security", "AppSec", "Bug Bounty", "CTF", "OSINT", "YARA"):
        assert expected in names
    assert all(e.url.startswith("http") for e in entries)
    assert len({e.url for e in entries}) == len(entries), "duplicate URLs in parse"


def test_github_owner_repo():
    assert ah.github_owner_repo("https://github.com/ashishb/android-security-awesome") == ("ashishb", "android-security-awesome")
    assert ah.github_owner_repo("https://gchq.github.io/CyberChef/") == (None, None)


def test_ingest_idempotent_with_provenance_and_fts(tmp_path, real_readme):
    c = _conn(tmp_path)
    s1 = ah.ingest(c, real_readme, "sha-test")
    assert s1["new"] == s1["seen"] > 0
    s2 = ah.ingest(c, real_readme, "sha-test")
    assert s2["new"] == 0 and s2["updated"] == 0 and s2["unchanged"] == s1["seen"]
    row = c.execute("SELECT * FROM resources WHERE name='YARA'").fetchone()
    assert row["verification_status"] == db.VERIFIED
    assert row["source_id"] == "awesome_hacking" and "sha256=sha-test" in row["source_ref"]
    assert row["license"] is None, "aggregator license must not be copied onto linked projects"
    hits = c.execute("SELECT rowid FROM resources_fts WHERE resources_fts MATCH 'android'").fetchall()
    assert hits


def test_removed_upstream_is_flagged_not_deleted(tmp_path, real_readme):
    c = _conn(tmp_path)
    ah.ingest(c, real_readme, "a")
    total = c.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    trimmed = "\n".join(l for l in real_readme.splitlines() if "[YARA]" not in l)
    stats = ah.ingest(c, trimmed, "b")
    assert stats["missing_upstream"] == 1
    assert c.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == total
    assert c.execute("SELECT present_in_source FROM resources WHERE name='YARA'").fetchone()[0] == 0


def test_empty_parse_refuses_update(tmp_path):
    c = _conn(tmp_path)
    try:
        ah.ingest(c, "# nothing here", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
