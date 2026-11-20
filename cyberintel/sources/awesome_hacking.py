"""Awesome-Hacking ingestion.

Level 1 (Phase 1): the aggregator README -> sections, categories and the linked
awesome-lists / repositories. Level 2 (Phase 2): each linked awesome-list's README ->
individual tools. Nothing is ever installed or executed.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..db import VERIFIED, log_activity, utcnow

SOURCE_ID = "awesome_hacking"
README_URL = "https://raw.githubusercontent.com/Hack-with-Github/Awesome-Hacking/master/README.md"

# Sections whose rows are themselves curated awesome-lists (each one becomes a category).
LIST_SECTIONS = {"Awesome Repositories"}

_ROW = re.compile(r"^\s*\|?\s*\[(?P<name>[^\]]+)\]\((?P<url>[^)\s]+)\)\s*\|\s*(?P<desc>.*?)\s*\|?\s*$")
_HEADING = re.compile(r"^(#{2,6})\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class ParsedEntry:
    section: str
    name: str
    url: str
    description: str
    kind: str  # awesome_list | repository

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.section}|{self.name}|{self.url}|{self.description}".encode()).hexdigest()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def github_owner_repo(url: str) -> tuple[str | None, str | None]:
    p = urlparse(url)
    if p.netloc.lower() not in ("github.com", "www.github.com"):
        return None, None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) >= 2:
        return parts[0], parts[1].removesuffix(".git")
    return None, None


def parse_readme(markdown: str) -> list[ParsedEntry]:
    """Parse markdown tables of `[Name](url) | Description` under `##` headings.
    Discovers sections dynamically; nothing is hardcoded except which sections are lists."""
    entries: list[ParsedEntry] = []
    section: str | None = None
    for line in markdown.splitlines():
        h = _HEADING.match(line)
        if h:
            section = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", h.group("title")).strip()
            continue
        if section is None:
            continue
        m = _ROW.match(line)
        if not m:
            continue
        url = m.group("url").strip()
        if not url.startswith(("http://", "https://")):
            continue
        entries.append(ParsedEntry(
            section=section,
            name=m.group("name").strip(),
            url=url,
            description=m.group("desc").strip(),
            kind="awesome_list" if section in LIST_SECTIONS else "repository",
        ))
    return entries


def fetch_readme(client: httpx.Client) -> tuple[str, str]:
    r = client.get(README_URL)
    r.raise_for_status()
    text = r.text
    return text, hashlib.sha256(text.encode()).hexdigest()


def ingest(conn: sqlite3.Connection, markdown: str, sha256: str, retrieved_at: str | None = None) -> dict:
    """Idempotent upsert. Entries that disappear upstream are flagged, never deleted."""
    now = retrieved_at or utcnow()
    entries = parse_readme(markdown)
    if not entries:
        raise ValueError("README parsed to zero entries; refusing to update (format may have changed)")
    source_ref = f"{README_URL}#sha256={sha256}"
    stats = {"seen": len(entries), "new": 0, "updated": 0, "unchanged": 0, "categories": 0, "missing_upstream": 0}
    seen_urls = set()
    for e in entries:
        seen_urls.add(e.url)
        category_id = None
        if e.kind == "awesome_list":
            conn.execute(
                """INSERT INTO categories(name, slug, description, source_id, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET description=excluded.description, last_seen=excluded.last_seen""",
                (e.name, slugify(e.name), e.description, SOURCE_ID, now, now),
            )
            category_id = conn.execute("SELECT id FROM categories WHERE name=?", (e.name,)).fetchone()["id"]
        owner, repo = github_owner_repo(e.url)
        existing = conn.execute("SELECT id, content_hash FROM resources WHERE url=?", (e.url,)).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO resources(name,url,description,section,kind,category_id,gh_owner,gh_repo,
                       source_id,source_ref,retrieved_at,verification_status,content_hash,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (e.name, e.url, e.description, e.section, e.kind, category_id, owner, repo,
                 SOURCE_ID, source_ref, now, VERIFIED, e.content_hash, now, now),
            )
            stats["new"] += 1
        elif existing["content_hash"] != e.content_hash:
            conn.execute(
                """UPDATE resources SET name=?, description=?, section=?, kind=?, category_id=?,
                       source_ref=?, retrieved_at=?, content_hash=?, last_seen=?, present_in_source=1
                   WHERE id=?""",
                (e.name, e.description, e.section, e.kind, category_id, source_ref, now,
                 e.content_hash, now, existing["id"]),
            )
            stats["updated"] += 1
        else:
            conn.execute("UPDATE resources SET last_seen=?, retrieved_at=?, source_ref=?, present_in_source=1 WHERE id=?",
                         (now, now, source_ref, existing["id"]))
            stats["unchanged"] += 1
    # Flag (do not delete) level-1 entries no longer present upstream.
    rows = conn.execute(
        "SELECT id, url FROM resources WHERE source_id=? AND parent_resource_id IS NULL AND present_in_source=1",
        (SOURCE_ID,),
    ).fetchall()
    for r in rows:
        if r["url"] not in seen_urls:
            conn.execute("UPDATE resources SET present_in_source=0 WHERE id=?", (r["id"],))
            stats["missing_upstream"] += 1
    stats["categories"] = conn.execute("SELECT COUNT(*) FROM categories WHERE source_id=?", (SOURCE_ID,)).fetchone()[0]
    return stats


def sync(conn: sqlite3.Connection, client: httpx.Client) -> dict:
    started = utcnow()
    run_id = conn.execute(
        "INSERT INTO sync_runs(source_id, job, started_at) VALUES (?,?,?)",
        (SOURCE_ID, "readme_level1", started),
    ).lastrowid
    conn.commit()
    try:
        text, sha = fetch_readme(client)
        stats = ingest(conn, text, sha)
        conn.execute(
            """UPDATE sync_runs SET finished_at=?, status='ok', items_seen=?, items_new=?, items_updated=?,
                   content_sha256=? WHERE id=?""",
            (utcnow(), stats["seen"], stats["new"], stats["updated"], sha, run_id),
        )
        log_activity(conn, "sync", (
            f"Awesome-Hacking README synced: {stats['seen']} entries ({stats['new']} new, "
            f"{stats['updated']} updated), {stats['categories']} categories, "
            f"{stats['missing_upstream']} no longer upstream. sha256={sha[:12]}"),
            SOURCE_ID, VERIFIED)
        return stats
    except Exception as exc:  # recorded, surfaced in UI, never swallowed silently
        conn.rollback()
        conn.execute("UPDATE sync_runs SET finished_at=?, status='error', error=? WHERE id=?",
                     (utcnow(), f"{type(exc).__name__}: {exc}", run_id))
        log_activity(conn, "error", f"Awesome-Hacking sync failed: {type(exc).__name__}: {exc}", SOURCE_ID)
        conn.commit()
        raise
