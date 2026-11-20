"""Real connection checks against each Phase-1 integration. Results are stored with
timestamps; nothing is reported as working unless a request actually succeeded."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Callable

import httpx

from ..config import Settings
from ..db import log_activity, utcnow
from .awesome_hacking import README_URL


def _timed(fn: Callable[[], httpx.Response]) -> tuple[httpx.Response | None, int, str | None]:
    t0 = time.monotonic()
    try:
        r = fn()
        return r, int((time.monotonic() - t0) * 1000), None
    except httpx.HTTPError as exc:
        return None, int((time.monotonic() - t0) * 1000), f"{type(exc).__name__}: {exc}"


def check_awesome_hacking(client: httpx.Client, s: Settings) -> dict:
    r, ms, err = _timed(lambda: client.head(README_URL))
    return {"ok": bool(r is not None and r.status_code == 200), "status_code": r.status_code if r else None,
            "latency_ms": ms, "error": err, "detail": "README reachable" if r is not None and r.status_code == 200 else None}


def check_github_api(client: httpx.Client, s: Settings) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if s.github_token:
        headers["Authorization"] = f"Bearer {s.github_token}"
    # /rate_limit does not consume quota.
    r, ms, err = _timed(lambda: client.get("https://api.github.com/rate_limit", headers=headers))
    if r is None:
        return {"ok": False, "latency_ms": ms, "error": err}
    if r.status_code != 200:
        return {"ok": False, "status_code": r.status_code, "latency_ms": ms, "error": r.text[:200]}
    core = r.json().get("resources", {}).get("core", {})
    return {"ok": True, "status_code": 200, "latency_ms": ms,
            "detail": "authenticated" if s.github_token else "unauthenticated (60 req/h)",
            "rate_limit": json.dumps({"limit": core.get("limit"), "remaining": core.get("remaining"),
                                      "reset_epoch": core.get("reset")})}


def check_huggingface(client: httpx.Client, s: Settings) -> dict:
    headers = {"Authorization": f"Bearer {s.hf_token}"} if s.hf_token else {}
    r, ms, err = _timed(lambda: client.get(f"https://huggingface.co/api/datasets/{s.hf_dataset_id}", headers=headers))
    if r is None:
        return {"ok": False, "latency_ms": ms, "error": err}
    detail = None
    if r.status_code == 200:
        info = r.json()
        detail = f"reachable; gated={info.get('gated')}; token={'set' if s.hf_token else 'not set'}"
    if s.hf_token:
        w, _, werr = _timed(lambda: client.get("https://huggingface.co/api/whoami-v2", headers=headers))
        if w is not None and w.status_code == 200:
            detail = (detail or "") + f"; token valid for user '{w.json().get('name')}'"
        else:
            detail = (detail or "") + "; token INVALID or expired"
    return {"ok": r.status_code == 200, "status_code": r.status_code, "latency_ms": ms,
            "detail": detail, "error": None if r.status_code == 200 else r.text[:200]}


def check_ollama(client: httpx.Client, s: Settings) -> dict:
    r, ms, err = _timed(lambda: client.get(f"{s.ollama_host}/api/tags"))
    if r is None:
        return {"ok": False, "latency_ms": ms, "error": f"Ollama not reachable at {s.ollama_host} ({err})"}
    if r.status_code != 200:
        return {"ok": False, "status_code": r.status_code, "latency_ms": ms, "error": r.text[:200]}
    models = r.json().get("models", [])
    summary = [{"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 2),
                "params": (m.get("details") or {}).get("parameter_size"),
                "quant": (m.get("details") or {}).get("quantization_level")} for m in models]
    return {"ok": True, "status_code": 200, "latency_ms": ms,
            "detail": json.dumps({"model_count": len(models), "models": summary})}


def check_openai_compat(client: httpx.Client, s: Settings) -> dict:
    if not s.openai_base_url:
        return {"ok": False, "error": "not configured (OPENAI_BASE_URL unset)", "skipped": True}
    headers = {"Authorization": f"Bearer {s.openai_api_key}"} if s.openai_api_key else {}
    r, ms, err = _timed(lambda: client.get(f"{s.openai_base_url}/models", headers=headers))
    if r is None:
        return {"ok": False, "latency_ms": ms, "error": err}
    ok = r.status_code == 200
    detail = None
    if ok:
        ids = [m.get("id") for m in r.json().get("data", [])][:50]
        detail = json.dumps({"model_count": len(ids), "models": ids})
    return {"ok": ok, "status_code": r.status_code, "latency_ms": ms, "detail": detail,
            "error": None if ok else r.text[:200]}


CHECKS: dict[str, Callable[[httpx.Client, Settings], dict]] = {
    "awesome_hacking": check_awesome_hacking,
    "github_api": check_github_api,
    "huggingface": check_huggingface,
    "ollama": check_ollama,
    "openai_compat": check_openai_compat,
}


def run_all(conn: sqlite3.Connection, client: httpx.Client, s: Settings) -> dict[str, dict]:
    results = {}
    for source_id, fn in CHECKS.items():
        res = fn(client, s)
        results[source_id] = res
        if res.get("skipped"):
            continue
        conn.execute(
            """INSERT INTO source_health(source_id, checked_at, ok, status_code, latency_ms, detail, error, rate_limit)
               VALUES (?,?,?,?,?,?,?,?)""",
            (source_id, utcnow(), int(res["ok"]), res.get("status_code"), res.get("latency_ms"),
             res.get("detail"), res.get("error"), res.get("rate_limit")),
        )
    ok = [k for k, v in results.items() if v["ok"]]
    bad = [k for k, v in results.items() if not v["ok"] and not v.get("skipped")]
    log_activity(conn, "health", f"Health check: OK={', '.join(ok) or 'none'}; failing={', '.join(bad) or 'none'}")
    return results
