# CyberIntel X: Phase 1 foundation

A local-first cybersecurity research environment that will combine the
[Awesome-Hacking](https://github.com/Hack-with-Github/Awesome-Hacking) resource index with the
[Vyber07/cyber-security](https://huggingface.co/datasets/Vyber07/cyber-security) dataset through one
shared search, provenance and AI-retrieval layer.

**Status: Phase 1 of 6.** See `docs/IMPLEMENTATION_PLAN.md` for the plan and
`docs/SECURITY_AND_LIMITATIONS.md` for the limits.

## Quick start (macOS, Apple Silicon)

From the repository root:

```bash
./run.sh                 # creates .venv, installs 4 small deps, copies .env, serves
open http://127.0.0.1:8787
```

Other commands:

```bash
./run.sh health          # real connection checks (GitHub, HF, Ollama, OpenAI-compatible)
./run.sh sync            # Awesome-Hacking README import + HF metadata/access check
.venv/bin/python -m pytest                          # offline test suite
CYBERINTEL_LIVE_TESTS=1 .venv/bin/python -m pytest -m live   # hits real services
```

## What works in Phase 1 (tested)

| Area | State |
|---|---|
| SQLite schema v1, FTS5 search, append-only migrations, automatic pre-migration backup | ✅ tested |
| Provenance on every row (source, source_ref + sha256, retrieved_at, verification status) | ✅ tested |
| Awesome-Hacking level-1 import: sections discovered dynamically, 81 entries / 47 categories, idempotent, removed-upstream entries flagged rather than deleted | ✅ tested **live** (2026-11-17) |
| Cyber Arsenal: search, type/category filters, favorites, detail + provenance | ✅ tested |
| Command Center: live counts, source health, sync history, activity feed, disk space | ✅ tested |
| Settings: per-integration status, auth configured yes/no, last success/error, latency, GitHub rate limit, Ollama model list, schema versions | ✅ tested (offline paths) |
| HF dataset: access/gate detection, card metadata, paginated file-tree index (no download) | ⚠️ error paths tested; **real access untested** (gated, and no token yet) |
| Ollama / OpenAI-compatible detection | ⚠️ code paths tested offline; first real run will be on your Mac |
| All other modules | Shown in the nav as "planned · P#", each with an honest "not implemented yet" page |

## Layout

```
cyberintel/
  config.py            env/.env settings (secrets never rendered)
  db.py                schema, migrations, backups, verification vocabulary
  sources/
    registry.py        every integration + the phase it lands in
    awesome_hacking.py README parser + idempotent ingest
    huggingface.py     gate probe, metadata, file tree, disk check
    health.py          real connection checks
  web/                 FastAPI app, Jinja templates, CSS
tests/                 offline suite + opt-in live suite + real README fixture
data/                  (created at runtime) sqlite db, backups/, artifacts_untrusted/
```
