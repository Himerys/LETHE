"""Export the verified-contacts knowledge base — shareable with justdelete.me.

Contains ONLY service data (domain, contact address, liveness, where it was
found) — never your own addresses, names or mail contents.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from LETHE import ui

COLS = ["domain", "email", "status", "source", "score", "evidence", "checked_at"]


def run_export_contacts(conn: sqlite3.Connection, out_dir: Path) -> None:
    rows = conn.execute(
        f"""SELECT {','.join(COLS)} FROM contacts
            WHERE status IN ('alive','dead') OR source IN ('sniffer','manual')
            ORDER BY domain, score DESC"""
    ).fetchall()
    if not rows:
        ui.warn("no contact knowledge to export yet — send requests and run fpd check first")
        return
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "contacts-export.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        w.writerows(rows)

    json_path = out_dir / "contacts-export.json"
    json_path.write_text(
        json.dumps([dict(zip(COLS, r)) for r in rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    alive = sum(1 for r in rows if r[2] == "alive")
    dead = sum(1 for r in rows if r[2] == "dead")
    ui.ok(f"exported {len(rows)} contacts ({alive} verified alive, {dead} dead): {csv_path}")
    ui.info("this file contains no personal data — safe to share (e.g. with justdelete.me)")
