"""Hugging Face dataset: metadata-only probing and file-tree indexing.

Phase 1 never downloads dataset content. It records:
  * whether the dataset is gated / accessible with the configured token,
  * card metadata (license etc.) as returned by the Hub API,
  * the file tree (paths + sizes) when the token grants access.
Selective, disk-checked downloads come in Phase 2.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import quote

import httpx

from ..db import VERIFIED, log_activity, utcnow

SOURCE_ID = "huggingface"
API = "https://huggingface.co/api"


def _headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def disk_status(path: Path, min_free_gb: float) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_gb = usage.free / 1e9
    return {"free_gb": round(free_gb, 1), "total_gb": round(usage.total / 1e9, 1),
            "min_free_gb": min_free_gb, "ok_for_downloads": free_gb >= min_free_gb}


def probe(client: httpx.Client, dataset_id: str, token: str | None) -> dict:
    """GET /api/datasets/{id}. Returns a normalized dict; never raises for HTTP errors."""
    url = f"{API}/datasets/{dataset_id}"
    try:
        r = client.get(url, headers=_headers(token))
    except httpx.HTTPError as exc:
        return {"access_status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if r.status_code == 404:
        return {"access_status": "not_found", "status_code": 404,
                "error": "Not found (or private and token lacks access)"}
    if r.status_code in (401, 403):
        return {"access_status": "access_required", "status_code": r.status_code,
                "error": "Authentication / gate acceptance required"}
    if r.status_code >= 400:
        return {"access_status": "error", "status_code": r.status_code, "error": r.text[:300]}
    info = r.json()
    card = info.get("cardData") or {}
    return {
        "access_status": "metadata_ok",
        "status_code": r.status_code,
        "gated": str(info.get("gated")) if info.get("gated") is not None else None,
        "private": int(bool(info.get("private"))),
        "license": card.get("license") if isinstance(card.get("license"), str) else json.dumps(card.get("license")),
        "last_modified": info.get("lastModified"),
        "sha": info.get("sha"),
        "downloads": info.get("downloads"),
        "card": card,
    }


def list_tree(client: httpx.Client, dataset_id: str, token: str | None, revision: str = "main") -> list[dict]:
    """Paginated recursive tree listing. Raises httpx.HTTPStatusError on 401/403."""
    url = f"{API}/datasets/{dataset_id}/tree/{quote(revision, safe='')}?recursive=true&expand=false"
    files: list[dict] = []
    while url:
        r = client.get(url, headers=_headers(token))
        r.raise_for_status()
        for item in r.json():
            if item.get("type") == "file":
                files.append({"path": item["path"], "size": item.get("size"),
                              "oid": item.get("oid"), "is_lfs": int(bool(item.get("lfs")))})
        url = r.links.get("next", {}).get("url")
    return files


def index_metadata(conn: sqlite3.Connection, client: httpx.Client, dataset_id: str, token: str | None) -> dict:
    now = utcnow()
    run_id = conn.execute("INSERT INTO sync_runs(source_id, job, started_at) VALUES (?,?,?)",
                          (SOURCE_ID, "metadata_index", now)).lastrowid
    conn.commit()
    p = probe(client, dataset_id, token)
    files: list[dict] = []
    tree_error = None
    access = p["access_status"]
    if access == "metadata_ok":
        try:
            files = list_tree(client, dataset_id, token, p.get("sha") or "main")
            access = "accessible"
        except httpx.HTTPStatusError as exc:
            access = "access_required" if exc.response.status_code in (401, 403) else "error"
            tree_error = f"tree listing HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            access, tree_error = "error", f"{type(exc).__name__}: {exc}"
    error = p.get("error") or tree_error
    total = sum(f["size"] or 0 for f in files) if files else None
    conn.execute(
        """INSERT INTO dataset_meta(dataset_id, access_status, gated, private, license, last_modified, sha,
               downloads, card_json, file_count, total_bytes, retrieved_at, verification_status, last_error)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(dataset_id) DO UPDATE SET access_status=excluded.access_status,
               gated=COALESCE(excluded.gated, dataset_meta.gated),
               private=COALESCE(excluded.private, dataset_meta.private),
               license=COALESCE(excluded.license, dataset_meta.license),
               last_modified=COALESCE(excluded.last_modified, dataset_meta.last_modified),
               sha=COALESCE(excluded.sha, dataset_meta.sha),
               downloads=COALESCE(excluded.downloads, dataset_meta.downloads),
               card_json=COALESCE(excluded.card_json, dataset_meta.card_json),
               file_count=COALESCE(excluded.file_count, dataset_meta.file_count),
               total_bytes=COALESCE(excluded.total_bytes, dataset_meta.total_bytes),
               retrieved_at=excluded.retrieved_at, verification_status=excluded.verification_status,
               last_error=excluded.last_error""",
        (dataset_id, access, p.get("gated"), p.get("private"), p.get("license"), p.get("last_modified"),
         p.get("sha"), p.get("downloads"), json.dumps(p["card"]) if p.get("card") else None,
         len(files) if files else None, total, now, VERIFIED if access in ("accessible", "metadata_ok") else "unverified",
         error),
    )
    new = 0
    for f in files:
        cur = conn.execute(
            """INSERT INTO dataset_files(dataset_id, path, size, oid, is_lfs, revision_sha, retrieved_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(dataset_id, path) DO UPDATE SET size=excluded.size, oid=excluded.oid,
                   is_lfs=excluded.is_lfs, revision_sha=excluded.revision_sha, retrieved_at=excluded.retrieved_at""",
            (dataset_id, f["path"], f["size"], f["oid"], f["is_lfs"], p.get("sha"), now))
        new += cur.rowcount
    status = "ok" if access == "accessible" else "partial" if access == "metadata_ok" else "blocked" if access == "access_required" else "error"
    conn.execute("""UPDATE sync_runs SET finished_at=?, status=?, items_seen=?, error=? WHERE id=?""",
                 (utcnow(), status, len(files), error, run_id))
    msg = {
        "accessible": f"HF dataset {dataset_id}: indexed {len(files)} file entries (metadata only, nothing downloaded)",
        "metadata_ok": f"HF dataset {dataset_id}: card metadata read; file tree not accessible ({tree_error})",
        "access_required": f"HF dataset {dataset_id}: gated. Accept conditions on huggingface.co and set HF_TOKEN",
        "not_found": f"HF dataset {dataset_id}: not found",
    }.get(access, f"HF dataset {dataset_id}: error: {error}")
    log_activity(conn, "sync" if access in ("accessible", "metadata_ok") else "warning", msg, SOURCE_ID,
                 VERIFIED if access == "accessible" else None)
    return {"access_status": access, "files": len(files), "total_bytes": total, "error": error}
