"""Live tests against the real services. Opt-in: CYBERINTEL_LIVE_TESTS=1 pytest -m live
They make real network requests; HF tests need HF_TOKEN + accepted gate to fully pass."""
import os

import httpx
import pytest

from cyberintel import db
from cyberintel.sources import awesome_hacking as ah, health, huggingface as hf
from cyberintel.sources.registry import sync_registry

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("CYBERINTEL_LIVE_TESTS") != "1", reason="set CYBERINTEL_LIVE_TESTS=1")]


@pytest.fixture
def live():
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        yield c


def _conn(settings):
    db.migrate(settings.db_path, settings.backups_dir)
    c = db.connect(settings.db_path)
    sync_registry(c)
    return c


def test_live_awesome_hacking_sync(settings, live):
    c = _conn(settings)
    stats = ah.sync(c, live)
    assert stats["seen"] >= 60 and stats["categories"] >= 30


def test_live_hf_metadata(settings, live):
    import dataclasses
    s = dataclasses.replace(settings, hf_token=os.environ.get("HF_TOKEN"))
    c = _conn(s)
    res = hf.index_metadata(c, live, s.hf_dataset_id, s.hf_token)
    if s.hf_token:
        assert res["access_status"] in ("accessible", "access_required")
    else:
        assert res["access_status"] in ("access_required", "metadata_ok", "accessible")


def test_live_health(settings, live):
    import dataclasses
    s = dataclasses.replace(settings, ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    c = _conn(s)
    res = health.run_all(c, live, s)
    assert res["awesome_hacking"]["ok"]
