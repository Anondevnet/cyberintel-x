"""Configuration. Reads environment variables and an optional local .env file.

Secrets are never logged or sent to the frontend; only "configured: yes/no" is exposed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    artifacts_dir: Path  # downloaded, untrusted research artifacts (never executed)
    backups_dir: Path
    host: str
    port: int
    hf_dataset_id: str
    hf_token: str | None
    github_token: str | None
    ollama_host: str
    openai_base_url: str | None
    openai_api_key: str | None
    http_timeout: float
    min_free_disk_gb: float

    @property
    def secrets_status(self) -> dict[str, bool]:
        return {
            "HF_TOKEN": bool(self.hf_token),
            "GITHUB_TOKEN": bool(self.github_token),
            "OPENAI_API_KEY": bool(self.openai_api_key),
        }


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("CYBERINTEL_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
    return Settings(
        data_dir=data_dir,
        db_path=data_dir / "cyberintel.sqlite3",
        artifacts_dir=data_dir / "artifacts_untrusted",
        backups_dir=data_dir / "backups",
        host=os.environ.get("CYBERINTEL_HOST", "127.0.0.1"),
        port=int(os.environ.get("CYBERINTEL_PORT", "8787")),
        hf_dataset_id=os.environ.get("HF_DATASET_ID", "Vyber07/cyber-security"),
        hf_token=os.environ.get("HF_TOKEN") or None,
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        openai_base_url=(os.environ.get("OPENAI_BASE_URL") or "").rstrip("/") or None,
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        http_timeout=float(os.environ.get("CYBERINTEL_HTTP_TIMEOUT", "15")),
        min_free_disk_gb=float(os.environ.get("CYBERINTEL_MIN_FREE_DISK_GB", "20")),
    )
