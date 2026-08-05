"""JustDeleteMe enrichment: match inventory domains against the JDM dataset.

The dataset (data/sites.json) comes from https://justdeleteme.xyz — it maps
services to their account-deletion page, difficulty and known contact address.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from LETHE import ui
from LETHE.db import upsert_contact
from LETHE.util import registrable_domain


def _sites_path() -> Path | None:
    candidates = [Path(__file__).resolve().parent / "data" / "sites.json"]
    if getattr(sys, "_MEIPASS", None):  # PyInstaller bundle
        candidates.insert(0, Path(sys._MEIPASS) / "footprint_decimator" / "data" / "sites.json")
    for p in candidates:
        if p.exists():
            return p
    return None


def load_sites() -> list[dict]:
    path = _sites_path()
    if path is None:
        ui.warn("data/sites.json missing — JustDeleteMe enrichment skipped")
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        ui.warn(f"could not read {path}: {e} — enrichment skipped")
        return []


def build_index(sites: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for s in sites:
        for dom in s.get("domains", []):
            index[registrable_domain(dom)] = s
    return index


def run_enrich(conn: sqlite3.Connection) -> int:
    index = build_index(load_sites())
    if not index:
        return 0
    hits = 0
    for (domain,) in conn.execute("SELECT domain FROM domains").fetchall():
        s = index.get(domain)
        if not s:
            continue
        hits += 1
        notes = s.get("notes_de") or s.get("notes") or ""
        conn.execute(
            """UPDATE domains SET jdm_name=?, jdm_url=?, jdm_difficulty=?,
               jdm_email=?, jdm_notes=? WHERE domain=?""",
            (s.get("name"), s.get("url"), s.get("difficulty"),
             s.get("email"), notes[:400], domain),
        )
        if s.get("email"):
            upsert_contact(conn, domain, s["email"], "jdm", 90, "listed on JustDeleteMe")
    conn.commit()
    ui.ok(f"JustDeleteMe enrichment: {hits} services matched")
    return hits
