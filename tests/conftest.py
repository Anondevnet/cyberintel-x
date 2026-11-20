import dataclasses
from pathlib import Path

import httpx
import pytest

from cyberintel.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def real_readme() -> str:
    """Unmodified Awesome-Hacking README snapshot (see .snapshot.json for provenance)."""
    return (FIXTURES / "awesome_hacking_README.snapshot.md").read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path):
    s = get_settings()
    return dataclasses.replace(
        s, data_dir=tmp_path, db_path=tmp_path / "t.sqlite3", artifacts_dir=tmp_path / "artifacts",
        backups_dir=tmp_path / "backups", hf_token=None, github_token=None, openai_base_url=None,
        ollama_host="http://127.0.0.1:9",
    )


def offline_client(status: int = 503) -> httpx.Client:
    """Transport that returns only an HTTP status (no fabricated payloads) or a connect error."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 0:
            raise httpx.ConnectError("offline (test)", request=request)
        return httpx.Response(status, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))
