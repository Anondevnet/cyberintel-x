# Implementation plan

## Phase 1 findings (2026-11-17)

**Environment.** Mac mini (arm64, macOS). Existing related projects (`osint-dashboard*`,
`breachscope-intelligence`, `mac-cybersecurity-toolkit`, `~/.ollama`, `~/.mythril`, `~/.foundry`) are
left untouched; this project lives only in `~/projects/cyberintel-x`. **Free disk was too low** for
Claude's sandbox VM to start (it needs ~17 GB), so the app checks free space before any download.
Python version, FTS5 support and installed Ollama models are checked on the Mac at runtime (`run.sh`
and Settings → health checks); nothing assumes they exist.

**Awesome-Hacking.** Two Markdown tables, which makes it a *list of lists*:
- "Awesome Repositories": 47 curated awesome-lists. Each becomes a **category**.
- "Other Useful Repositories": 34 standalone repos (CyberChef, GTFOBins, PayloadsAllTheThings…).
- Aggregator license: CC0-1.0. Linked projects keep their **own** licenses.

Getting real tool cards (stars, license, last push) therefore needs **level 2**, which means parsing each
linked awesome-list README. That multiplies the GitHub API calls, so a `GITHUB_TOKEN` is effectively required.

**HF dataset `Vyber07/cyber-security`.** According to the Hub page: **gated**, Apache-2.0, JSON, ~507
files, ~95 GB total (the card also mentions 16 GB; these figures conflict and need verifying with the
tree API). Content is described as text: ShareGPT `conversations`, `Instruction/Input/Output/Metadata`
and `metadata{domain,level,task_type}` layouts. No binaries are described. **Schema not verified yet**
because the conditions haven't been accepted.

## Phases

1. **Foundation** ✅ Schema, provenance, registry, health checks, level-1 Awesome-Hacking import, HF access probe, Command Center / Arsenal / Dataset / Settings UI, tests.
2. **Source integration**
   - Level-2 parser: fetch each awesome-list README and extract individual tools. Links carry `parent_resource_id`, deduplicated by URL.
   - GitHub enrichment (stars, forks, license SPDX, pushed_at, archived) with ETag caching and rate-limit backoff.
   - Broken-link checker (HEAD with throttling), update tracking, comparisons, recently-viewed.
   - HF: verify real splits/columns on the actual files. Download only the files the user selects, after a disk check, streaming into `dataset_records` with content-hash dedup and the revision SHA recorded. Schema-kind detection runs on observed fields only.
3. **AI engine**
   - Provider abstraction (Ollama → OpenAI-compatible). Only one model is loaded at a time.
   - Retrieval uses FTS5 BM25 over resources and records. Embeddings are optional, and only added if a small local embed model is present.
   - Every factual sentence cites record IDs, and the reply states "no evidence found" when retrieval is empty.
4. **Intelligence modules**, one at a time: Vulnerability (NVD, KEV, OSV, GHSA) → Threat Intel (ATT&CK) → OSINT (DNS/CT/email-auth) → Web3 (report ingestion) → Malware (static metadata only) → Forensics → Network (authorization scope required) → Learning.
5. **Command Center & relationship engine**: evidence-backed edges (record→category→tool→docs, CVE→CWE→advisory).
6. **Reports & hardening**: report model, JSON/CSV/PDF export, perf, security review, docs.

## Requirements for you
- Hugging Face: accept the dataset conditions, create a read token, set `HF_TOKEN` in `.env`.
- GitHub: optional fine-grained token (`GITHUB_TOKEN`) before the Phase 2 enrichment run.
- Free disk: clear space before downloading any dataset files. 20 GB free is the default floor.
