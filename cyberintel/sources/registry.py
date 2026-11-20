"""Registry of every external integration. `phase` says when it is actually implemented;
the Settings page shows planned sources as planned, never as working."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

CURRENT_PHASE = 1


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    base_url: str
    docs_url: str
    auth_env: str | None = None
    requires_auth: bool = False
    phase: int = 1
    notes: str = ""


SOURCES: list[Source] = [
    Source("awesome_hacking", "Awesome-Hacking (GitHub README)", "resource_index",
           "https://raw.githubusercontent.com/Hack-with-Github/Awesome-Hacking/master/README.md",
           "https://github.com/Hack-with-Github/Awesome-Hacking",
           notes="CC0-1.0 aggregator. Linked projects have their own licenses."),
    Source("github_api", "GitHub REST API", "metadata",
           "https://api.github.com", "https://docs.github.com/en/rest",
           auth_env="GITHUB_TOKEN", requires_auth=False,
           notes="Unauthenticated limit is 60 req/h; token strongly recommended for stars/license enrichment."),
    Source("huggingface", "Hugging Face Hub (dataset)", "dataset",
           "https://huggingface.co/api", "https://huggingface.co/docs/hub/api",
           auth_env="HF_TOKEN", requires_auth=True,
           notes="Dataset is gated: accept its conditions on huggingface.co, then set HF_TOKEN."),
    Source("ollama", "Ollama (local models)", "ai_provider",
           "http://127.0.0.1:11434", "https://github.com/ollama/ollama/blob/main/docs/api.md",
           phase=1, notes="Detection only in Phase 1; assistant arrives in Phase 3."),
    Source("openai_compat", "OpenAI-compatible API", "ai_provider",
           "", "https://platform.openai.com/docs/api-reference/models",
           auth_env="OPENAI_API_KEY", requires_auth=True, phase=1,
           notes="Optional. Set OPENAI_BASE_URL + OPENAI_API_KEY."),
    Source("nvd", "NIST NVD CVE API 2.0", "vulnerability",
           "https://services.nvd.nist.gov/rest/json/cves/2.0", "https://nvd.nist.gov/developers/vulnerabilities",
           auth_env="NVD_API_KEY", phase=4),
    Source("cisa_kev", "CISA Known Exploited Vulnerabilities", "vulnerability",
           "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
           "https://www.cisa.gov/known-exploited-vulnerabilities-catalog", phase=4),
    Source("osv", "OSV.dev", "vulnerability", "https://api.osv.dev/v1", "https://google.github.io/osv.dev/api/", phase=4),
    Source("mitre_attack", "MITRE ATT&CK (STIX)", "threat_intel",
           "https://github.com/mitre-attack/attack-stix-data", "https://attack.mitre.org/resources/", phase=4),
]

SOURCES_BY_ID = {s.id: s for s in SOURCES}


def sync_registry(conn: sqlite3.Connection, ollama_host: str | None = None,
                  openai_base_url: str | None = None) -> None:
    for s in SOURCES:
        base = s.base_url
        if s.id == "ollama" and ollama_host:
            base = ollama_host
        if s.id == "openai_compat":
            base = openai_base_url or ""
        conn.execute(
            """INSERT INTO sources(id,name,kind,base_url,docs_url,auth_env,requires_auth,phase,notes)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,
                 base_url=excluded.base_url, docs_url=excluded.docs_url, auth_env=excluded.auth_env,
                 requires_auth=excluded.requires_auth, phase=excluded.phase, notes=excluded.notes""",
            (s.id, s.name, s.kind, base, s.docs_url, s.auth_env, int(s.requires_auth), s.phase, s.notes),
        )
