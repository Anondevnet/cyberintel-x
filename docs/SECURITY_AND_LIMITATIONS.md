# Security model & limitations

## Guarantees enforced in code
- **Local only.** Binds to `127.0.0.1` by default and warns if changed. State-changing actions are POST-only.
- **No execution of anything fetched.** Tools from Awesome-Hacking are indexed, never installed or run.
  Dataset files (from Phase 2) go to `data/artifacts_untrusted/` and are only ever parsed as data.
- **No fabricated data.** Counts come from the database. Planned modules show "not implemented" pages
  and no sample content. Tests use the real README snapshot (`tests/fixtures/*.snapshot.json` records
  its provenance). Mock transports return bare status codes only, to exercise error handling.
- **Provenance everywhere.** Each row records its source, a source reference (URL + content sha256),
  retrieval time and a verification status: `verified` / `unverified` / `historical` / `simulated`.
  "Verified" means *read directly from the named source*, not that the tool is safe or endorsed.
- **No silent data loss.** Migrations are append-only and back up the DB first. Entries removed
  upstream are flagged `present_in_source=0`, never deleted. A README that parses to zero entries is
  rejected. Failed syncs are recorded in `sync_runs` and the activity feed.
- **Secrets.** Read from env / `.env` (git-ignored). The UI and API expose only "set / not set". A test
  asserts that tokens never appear in any rendered page.
- **Licensing.** The aggregator's CC0 is never copied onto linked projects. Their license fields stay
  empty until fetched per project.

## Out of scope by design (all phases)
Active scanning without a stored authorization scope; exploitation or PoC execution; running malware
or dataset artifacts; wallet access, signing or contract deployment; credential collection or
bypassing authentication; labelling an indicator malicious based on one unverified source.

## Known limitations (Phase 1)
- HF dataset access is **untested against the real service** (gated; no token yet). The Hub page's
  size figures conflict (16 GB vs ~95 GB). The tree index will settle this.
- Ollama / OpenAI-compatible checks have not yet run against a real service.
- Only level 1 of Awesome-Hacking (81 entries) is indexed; stars/licenses/link health arrive in Phase 2.
- Single-user, no authentication on the local UI.
- Syncs run inline in the request. That's fine for level 1, but level 2 will move to a background job with progress.
