from fastapi.testclient import TestClient

from cyberintel import db
from cyberintel.sources import awesome_hacking as ah
from cyberintel.web.app import MODULES, create_app

from conftest import offline_client


def _client(settings, readme=None):
    app = create_app(settings, http_client=offline_client(0))
    if readme:
        with db.session(settings.db_path) as c:
            ah.ingest(c, readme, "fixture")
    return TestClient(app)


def test_api_health(settings):
    r = _client(settings).get("/api/health")
    assert r.status_code == 200 and r.json()["schema_version"] == db.LATEST_VERSION


def test_every_nav_entry_resolves(settings):
    c = _client(settings)
    for slug, label, _ in MODULES:
        r = c.get(f"/{slug}")
        assert r.status_code == 200, slug
        assert label.split()[0] in r.text


def test_planned_modules_say_so(settings):
    r = _client(settings).get("/vulnerabilities")
    assert "Not implemented yet" in r.text


def test_arsenal_search_filter_and_detail(settings, real_readme):
    c = _client(settings, real_readme)
    r = c.get("/arsenal", params={"q": "android"})
    assert r.status_code == 200 and "Android Security" in r.text
    data = c.get("/api/resources", params={"q": "yara"}).json()
    assert any(d["name"] == "YARA" for d in data)
    assert all(d["verification_status"] == "verified" and d["source_ref"] for d in data)
    only_lists = c.get("/api/resources", params={"kind": "awesome_list", "limit": 500}).json()
    assert only_lists and all(d["kind"] == "awesome_list" for d in only_lists)
    rid = data[0]["id"]
    assert c.get(f"/arsenal/{rid}").status_code == 200
    c.post(f"/arsenal/{rid}/favorite", follow_redirects=False)
    assert c.get("/api/stats").json()["favorites"] == 1


def test_command_center_counts_match_db(settings, real_readme):
    c = _client(settings, real_readme)
    stats = c.get("/api/stats").json()
    assert stats["resources"] == len(ah.parse_readme(real_readme))
    assert stats["dataset_access"] == "not_checked"
    assert c.get("/command-center").status_code == 200


def test_sync_action_offline_surfaces_error(settings):
    c = _client(settings)
    c.post("/actions/sync/awesome-hacking", follow_redirects=False)
    page = c.get("/arsenal").text
    assert "error" in page and "ConnectError" in page


def test_secrets_never_rendered(settings):
    import dataclasses
    s = dataclasses.replace(settings, hf_token="hf_SECRET_SHOULD_NOT_LEAK", github_token="ghp_SECRET_X")
    c = TestClient(create_app(s, http_client=offline_client(0)))
    for path in ("/settings", "/dataset", "/command-center", "/api/sources", "/api/stats"):
        body = c.get(path).text
        assert "SECRET" not in body, path
