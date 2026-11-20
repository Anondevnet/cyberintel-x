"""Entry point: `python -m cyberintel` (serve) | `python -m cyberintel sync` | `python -m cyberintel health`."""
from __future__ import annotations

import json
import sys

import httpx

from .config import get_settings
from .db import migrate, session
from .sources import awesome_hacking, health, huggingface
from .sources.registry import sync_registry


def _cli(cmd: str) -> int:
    s = get_settings()
    migrate(s.db_path, s.backups_dir)
    with session(s.db_path) as c, httpx.Client(timeout=s.http_timeout, follow_redirects=True) as hc:
        sync_registry(c, s.ollama_host, s.openai_base_url)
        if cmd == "health":
            print(json.dumps(health.run_all(c, hc, s), indent=2))
        elif cmd == "sync":
            print("awesome_hacking:", awesome_hacking.sync(c, hc))
            print("huggingface:", huggingface.index_metadata(c, hc, s.hf_dataset_id, s.hf_token))
        else:
            print(f"unknown command {cmd!r}; use: serve | sync | health")
            return 2
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd != "serve":
        return _cli(cmd)
    import uvicorn
    from .web.app import create_app
    s = get_settings()
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {s.host} exposes CyberIntel X beyond this machine.")
    uvicorn.run(create_app(s), host=s.host, port=s.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
