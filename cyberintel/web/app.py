"""FastAPI application. Binds to 127.0.0.1 by default. Sync/health actions are POST-only."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..config import Settings, get_settings
from ..db import migrate, session, utcnow
from ..sources import awesome_hacking, health, huggingface
from ..sources.registry import CURRENT_PHASE, sync_registry

HERE = Path(__file__).parent

# Navigation: every module is listed; unbuilt ones render an honest "planned" page.
MODULES = [
    ("command-center", "Command Center", 1),
    ("ai", "AI Cyber Intelligence", 3),
    ("dataset", "Cybersecurity Dataset Explorer", 1),
    ("arsenal", "Cyber Arsenal", 1),
    ("vulnerabilities", "Vulnerability Intelligence", 4),
    ("osint", "OSINT Intelligence", 4),
    ("threat-intel", "Threat Intelligence", 4),
    ("malware", "Malware Research", 4),
    ("network", "Network Security Research", 4),
    ("web3", "Web3 Security", 4),
    ("forensics", "Digital Forensics", 4),
    ("learning", "Learning Center", 4),
    ("reports", "Reports & Investigations", 6),
    ("settings", "Settings & Data Sources", 1),
]
MODULE_PLANS = {
    "ai": "Provider abstraction (Ollama / OpenAI-compatible), FTS-based retrieval over Arsenal + dataset, mandatory record citations.",
    "vulnerabilities": "NVD CVE 2.0, CISA KEV, OSV, GitHub Advisories; CVSS/CWE; confirmed vs unverified separation; no PoC execution.",
    "osint": "DNS, certificate transparency, email-domain security (SPF/DMARC), repo/developer research; public sources only.",
    "threat-intel": "MITRE ATT&CK library, IOC lookups (keyed feeds), confidence + source attribution; no verdicts from single unverified sources.",
    "malware": "Hash/metadata/strings/PE-ELF static inspection and report ingestion only. Samples are never executed.",
    "network": "TLS/header/DNS analysis; any active check requires an explicit, stored authorization scope.",
    "web3": "Read-only Slither/Mythril/Foundry report ingestion, SWC/OWASP SC Top 10 mapping. No wallets, signing or deployment.",
    "forensics": "Hashing, metadata, timelines and log/IOC extraction on copies; chain-of-custody records.",
    "learning": "Learning paths built from Arsenal categories and dataset topics; progress and bookmarks.",
    "reports": "Investigation IDs, scope, evidence, confidence, limitations; JSON/CSV/PDF export.",
}


def create_app(settings: Settings | None = None, http_client: httpx.Client | None = None) -> FastAPI:
    s = settings or get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.artifacts_dir.mkdir(parents=True, exist_ok=True)
    migrate(s.db_path, s.backups_dir)
    with session(s.db_path) as c:
        sync_registry(c, s.ollama_host, s.openai_base_url)

    app = FastAPI(title="CyberIntel X", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["fromjson"] = lambda v: json.loads(v) if v else None

    def client() -> httpx.Client:
        if http_client is not None:
            return http_client
        return httpx.Client(timeout=s.http_timeout, follow_redirects=True,
                            headers={"User-Agent": f"CyberIntelX/{__version__} (local research tool)"})

    def db():
        return session(s.db_path)  # commits on success, rolls back on error, always closes

    def render(request: Request, name: str, ctx: dict, active: str):
        ctx.update({"request": request, "modules": MODULES, "active": active, "phase": CURRENT_PHASE,
                    "version": __version__})
        return templates.TemplateResponse(request, name, ctx)

    # ---------- data helpers ----------
    def stats(c: sqlite3.Connection) -> dict:
        q = lambda sql, *a: c.execute(sql, a).fetchone()[0]
        meta = c.execute("SELECT * FROM dataset_meta WHERE dataset_id=?", (s.hf_dataset_id,)).fetchone()
        return {
            "resources": q("SELECT COUNT(*) FROM resources WHERE present_in_source=1"),
            "awesome_lists": q("SELECT COUNT(*) FROM resources WHERE kind='awesome_list' AND present_in_source=1"),
            "repositories": q("SELECT COUNT(*) FROM resources WHERE kind='repository' AND present_in_source=1"),
            "tools": q("SELECT COUNT(*) FROM resources WHERE kind='tool' AND present_in_source=1"),
            "categories": q("SELECT COUNT(*) FROM categories"),
            "missing_upstream": q("SELECT COUNT(*) FROM resources WHERE present_in_source=0"),
            "new_24h": q("SELECT COUNT(*) FROM resources WHERE first_seen >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')"),
            "dataset_files": q("SELECT COUNT(*) FROM dataset_files WHERE dataset_id=?", s.hf_dataset_id),
            "dataset_records": q("SELECT COUNT(*) FROM dataset_records WHERE dataset_id=?", s.hf_dataset_id),
            "dataset_access": meta["access_status"] if meta else "not_checked",
            "favorites": q("SELECT COUNT(*) FROM favorites"),
        }

    def latest_health(c: sqlite3.Connection) -> list[sqlite3.Row]:
        return c.execute("""
            SELECT src.*, h.checked_at, h.ok, h.status_code, h.latency_ms, h.detail, h.error, h.rate_limit,
                   (SELECT MAX(checked_at) FROM source_health WHERE source_id=src.id AND ok=1) AS last_success,
                   (SELECT finished_at FROM sync_runs WHERE source_id=src.id AND status='ok'
                       ORDER BY finished_at DESC LIMIT 1) AS last_sync
            FROM sources src
            LEFT JOIN source_health h ON h.id = (SELECT id FROM source_health WHERE source_id=src.id
                                                 ORDER BY checked_at DESC, id DESC LIMIT 1)
            ORDER BY src.phase, src.name""").fetchall()

    def fts_query(text: str) -> str | None:
        terms = re.findall(r"[\w\-\.]+", text)
        return " ".join(f'"{t}"*' for t in terms) if terms else None

    def search_resources(c, q: str | None, kind: str | None, category: str | None, favorites_only: bool, limit=500):
        sql = """SELECT r.*, cat.name AS category_name, (f.resource_id IS NOT NULL) AS is_favorite
                 FROM resources r LEFT JOIN categories cat ON cat.id=r.category_id
                 LEFT JOIN favorites f ON f.resource_id=r.id"""
        where, args = ["r.present_in_source=1"], []
        fq = fts_query(q) if q else None
        if fq:
            sql += " JOIN resources_fts ON resources_fts.rowid = r.id"
            where.append("resources_fts MATCH ?")
            args.append(fq)
        if kind:
            where.append("r.kind=?"); args.append(kind)
        if category:
            where.append("cat.slug=?"); args.append(category)
        if favorites_only:
            where.append("f.resource_id IS NOT NULL")
        sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY bm25(resources_fts)" if fq else " ORDER BY r.section, r.name COLLATE NOCASE"
        sql += f" LIMIT {int(limit)}"
        return c.execute(sql, args).fetchall()

    # ---------- pages ----------
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/command-center")

    @app.get("/command-center", response_class=HTMLResponse)
    def command_center(request: Request):
        with db() as c:
            ctx = {"stats": stats(c), "health": latest_health(c),
                   "activity": c.execute("SELECT * FROM activity ORDER BY ts DESC, id DESC LIMIT 40").fetchall(),
                   "syncs": c.execute("SELECT * FROM sync_runs ORDER BY started_at DESC, id DESC LIMIT 10").fetchall(),
                   "disk": huggingface.disk_status(s.data_dir, s.min_free_disk_gb)}
        return render(request, "command_center.html", ctx, "command-center")

    @app.get("/arsenal", response_class=HTMLResponse)
    def arsenal(request: Request, q: str = "", kind: str = "", category: str = "", fav: int = 0):
        with db() as c:
            rows = search_resources(c, q or None, kind or None, category or None, bool(fav))
            cats = c.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE").fetchall()
            last = c.execute("SELECT * FROM sync_runs WHERE source_id='awesome_hacking' ORDER BY started_at DESC LIMIT 1").fetchone()
        return render(request, "arsenal.html", {"rows": rows, "cats": cats, "q": q, "kind": kind,
                                                 "category": category, "fav": fav, "last_sync": last}, "arsenal")

    @app.get("/arsenal/{rid}", response_class=HTMLResponse)
    def resource_detail(request: Request, rid: int):
        with db() as c:
            r = c.execute("""SELECT r.*, cat.name AS category_name, (f.resource_id IS NOT NULL) AS is_favorite
                             FROM resources r LEFT JOIN categories cat ON cat.id=r.category_id
                             LEFT JOIN favorites f ON f.resource_id=r.id WHERE r.id=?""", (rid,)).fetchone()
            if r is None:
                raise HTTPException(404)
            children = c.execute("SELECT COUNT(*) FROM resources WHERE parent_resource_id=?", (rid,)).fetchone()[0]
        return render(request, "resource.html", {"r": r, "children": children}, "arsenal")

    @app.post("/arsenal/{rid}/favorite")
    def toggle_favorite(rid: int):
        with db() as c:
            if c.execute("SELECT 1 FROM resources WHERE id=?", (rid,)).fetchone() is None:
                raise HTTPException(404)
            if c.execute("SELECT 1 FROM favorites WHERE resource_id=?", (rid,)).fetchone():
                c.execute("DELETE FROM favorites WHERE resource_id=?", (rid,))  # user preference row only
            else:
                c.execute("INSERT INTO favorites(resource_id, created_at) VALUES (?,?)", (rid, utcnow()))
        return RedirectResponse(f"/arsenal/{rid}", status_code=303)

    @app.get("/dataset", response_class=HTMLResponse)
    def dataset(request: Request):
        with db() as c:
            meta = c.execute("SELECT * FROM dataset_meta WHERE dataset_id=?", (s.hf_dataset_id,)).fetchone()
            files = c.execute("SELECT * FROM dataset_files WHERE dataset_id=? ORDER BY path LIMIT 1000",
                              (s.hf_dataset_id,)).fetchall()
            runs = c.execute("SELECT * FROM sync_runs WHERE source_id='huggingface' ORDER BY started_at DESC LIMIT 5").fetchall()
        return render(request, "dataset.html", {"meta": meta, "files": files, "runs": runs,
                                                 "dataset_id": s.hf_dataset_id, "token_set": bool(s.hf_token),
                                                 "disk": huggingface.disk_status(s.data_dir, s.min_free_disk_gb)}, "dataset")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        with db() as c:
            ctx = {"health": latest_health(c), "secrets": s.secrets_status,
                   "cfg": {"data_dir": str(s.data_dir), "ollama_host": s.ollama_host,
                           "openai_base_url": s.openai_base_url or "(unset)", "hf_dataset_id": s.hf_dataset_id,
                           "bind": f"{s.host}:{s.port}"},
                   "schema": c.execute("SELECT * FROM schema_version ORDER BY version").fetchall(),
                   "disk": huggingface.disk_status(s.data_dir, s.min_free_disk_gb)}
        return render(request, "settings.html", ctx, "settings")

    @app.get("/{slug}", response_class=HTMLResponse)
    def planned(request: Request, slug: str):
        mod = next((m for m in MODULES if m[0] == slug), None)
        if mod is None:
            raise HTTPException(404)
        return render(request, "planned.html", {"mod": mod, "plan": MODULE_PLANS.get(slug, "")}, slug)

    # ---------- actions (POST) ----------
    @app.post("/actions/health")
    def action_health():
        with db() as c, client() as hc:
            health.run_all(c, hc, s)
        return RedirectResponse("/settings", status_code=303)

    @app.post("/actions/sync/awesome-hacking")
    def action_sync_ah():
        with db() as c, client() as hc:
            try:
                awesome_hacking.sync(c, hc)
            except Exception:
                pass  # recorded in sync_runs + activity; surfaced on the page
        return RedirectResponse("/arsenal", status_code=303)

    @app.post("/actions/sync/huggingface-metadata")
    def action_sync_hf():
        with db() as c, client() as hc:
            huggingface.index_metadata(c, hc, s.hf_dataset_id, s.hf_token)
        return RedirectResponse("/dataset", status_code=303)

    # ---------- JSON API ----------
    @app.get("/api/health")
    def api_health():
        with db() as c:
            v = c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        return {"status": "ok", "version": __version__, "schema_version": v, "time": utcnow()}

    @app.get("/api/stats")
    def api_stats():
        with db() as c:
            return stats(c)

    @app.get("/api/sources")
    def api_sources():
        with db() as c:
            return [dict(r) for r in latest_health(c)]

    @app.get("/api/resources")
    def api_resources(q: str = "", kind: str = "", category: str = "", limit: int = 100):
        with db() as c:
            rows = search_resources(c, q or None, kind or None, category or None, False, min(limit, 500))
        return JSONResponse([{k: r[k] for k in ("id", "name", "url", "description", "section", "kind",
                                                 "category_name", "license", "stars", "source_id", "source_ref",
                                                 "retrieved_at", "verification_status", "safety_class")} for r in rows])

    return app
