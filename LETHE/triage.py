"""Triage: decide per domain — KEEP, DELETE (send erasure request) or MANUAL.

Interactive terminal review by default; CSV export/import still available for
people who prefer Excel.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from LETHE import ui

ACTIONS = {"d": "DELETE", "k": "KEEP", "m": "MANUAL"}
ALIAS_ACTIONS = {"SKIP": "KEEP", "STAY": "KEEP", "DEL": "DELETE"}


def _recommendation(row: dict) -> str:
    if row["jdm_name"]:
        return "known service (JustDeleteMe listed) — likely a real account"
    if row["signal_count"] and row["signal_count"] > 0:
        return "registration signals found — likely a real account"
    if (row["list_unsub_ratio"] or 0) >= 0.5:
        return "mostly newsletters — unsubscribe or DELETE"
    return "no strong signals — maybe just transactional mail"


def _domain_panel(row: dict, pos: int, total: int) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", no_wrap=True)
    t.add_column()
    t.add_row("messages", f"{row['msg_count']}  (signals: {row['signal_count']})")
    if row["display_names"]:
        t.add_row("names", row["display_names"][:120])
    if row["senders"]:
        t.add_row("senders", row["senders"][:160])
    if row["own_addrs"]:
        t.add_row("your addr", row["own_addrs"])
    if row["first_seen"]:
        t.add_row("period", f"{row['first_seen']} -> {row['last_seen']}")
    if row["sample_subjects"]:
        t.add_row("subjects", Text(row["sample_subjects"][:220], style="muted"))
    if row["jdm_name"]:
        jdm = f"{row['jdm_name']}  [difficulty: {row['jdm_difficulty'] or '?'}]"
        if row["jdm_email"]:
            jdm += f"  contact: {row['jdm_email']}"
        t.add_row("justdelete.me", Text(jdm, style="warn"))
        if row["jdm_url"]:
            t.add_row("", Text(row["jdm_url"], style="muted"))
    t.add_row("verdict", Text(_recommendation(row), style="accent"))
    title = f"[head]{row['domain']}[/head]"
    if row["action"]:
        title += f"  [warn]current: {row['action']}[/warn]"
    return Panel(t, title=title, subtitle=f"[muted]{pos}/{total}[/muted]", border_style="dim")


def _top_senders(conn: sqlite3.Connection, domain: str, limit: int = 4) -> str:
    addrs = [
        r[0]
        for r in conn.execute(
            "SELECT address FROM senders WHERE domain=? ORDER BY msg_count DESC LIMIT ?",
            (domain, limit),
        )
    ]
    return "; ".join(addrs)


def run_review(conn: sqlite3.Connection, include_decided: bool = False) -> None:
    where = "" if include_decided else "WHERE action=''"
    cur = conn.execute(
        f"""SELECT domain, display_names, own_addrs, msg_count, signal_count,
                   noreply_ratio, list_unsub_ratio, first_seen, last_seen,
                   sample_subjects, jdm_name, jdm_url, jdm_difficulty,
                   jdm_email, action
            FROM domains {where}
            ORDER BY signal_count DESC, msg_count DESC"""
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for row in rows:
        row["senders"] = _top_senders(conn, row["domain"])
    if not rows:
        ui.ok("nothing left to review — every domain has a decision")
        ui.info("next: fpd generate")
        return

    c = ui.console
    c.print(f"\n[head]═══ triage ═══[/head]  [muted]{len(rows)} domains to review[/muted]")
    c.print(
        "[muted]keys: [/muted][ok]d[/ok][muted]=delete-request  [/muted]"
        "[ok]k[/ok][muted]=keep  [/muted][ok]m[/ok][muted]=manual  [/muted]"
        "[ok]enter[/ok][muted]=decide later  [/muted][ok]u[/ok][muted]=undo  [/muted]"
        "[ok]q[/ok][muted]=quit (saves)[/muted]\n"
    )

    history: list[str] = []
    decided = 0
    i = 0
    while i < len(rows):
        row = rows[i]
        c.print(_domain_panel(row, i + 1, len(rows)))
        try:
            key = ui.getkey()
        except KeyboardInterrupt:
            key = "q"
        if key == "q":
            break
        if key == "u":
            if history:
                prev = history.pop()
                conn.execute("UPDATE domains SET action='' WHERE domain=?", (prev,))
                conn.commit()
                decided = max(0, decided - 1)
                i = next(idx for idx, r in enumerate(rows) if r["domain"] == prev)
                ui.warn(f"undo: {prev} is undecided again")
            else:
                ui.warn("nothing to undo")
            continue
        if key in ACTIONS:
            action = ACTIONS[key]
            conn.execute(
                "UPDATE domains SET action=? WHERE domain=?", (action, row["domain"])
            )
            conn.commit()
            history.append(row["domain"])
            decided += 1
            style = {"DELETE": "err", "KEEP": "ok", "MANUAL": "warn"}[action]
            c.print(f"    -> [{style}]{action}[/{style}]\n")
        elif key in ("\r", "\n", " "):
            c.print("    -> [muted]later[/muted]\n")
        else:
            ui.warn("unknown key (d/k/m/enter/u/q)")
            continue
        i += 1

    conn.commit()
    counts = dict(
        conn.execute(
            "SELECT action, COUNT(*) FROM domains WHERE action!='' GROUP BY action"
        ).fetchall()
    )
    undecided = conn.execute("SELECT COUNT(*) FROM domains WHERE action=''").fetchone()[0]
    ui.ok(f"session: {decided} decisions saved — totals: {counts}, undecided: {undecided}")
    if not undecided:
        ui.info("next: fpd generate")


# ---------------------------------------------------------------------------
# CSV round-trip (Excel workflow)
# ---------------------------------------------------------------------------

INV_COLS = [
    "domain", "action", "status", "msg_count", "signal_count",
    "jdm_difficulty", "jdm_email", "jdm_url", "jdm_notes",
    "display_names", "own_addrs", "first_seen", "last_seen",
    "noreply_ratio", "list_unsub_ratio", "sample_subjects", "note",
]


def export_csv(conn: sqlite3.Connection, path: Path) -> None:
    rows = conn.execute(
        f"SELECT {','.join(INV_COLS)} FROM domains ORDER BY signal_count DESC, msg_count DESC"
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig + semicolon: opens correctly in German Excel by double-click
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(INV_COLS)
        w.writerows(rows)
    ui.ok(f"inventory exported: {path} ({len(rows)} domains)")
    ui.info('set the "action" column to DELETE / KEEP / MANUAL, then: fpd review --csv <file>')


def import_csv(conn: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        ui.err(f"{path} not found")
        raise SystemExit(1)
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        for row in csv.DictReader(fh, delimiter=delim):
            action = (row.get("action") or "").strip().upper()
            action = ALIAS_ACTIONS.get(action, action)
            if action in ("DELETE", "KEEP", "MANUAL"):
                note = (row.get("note") or "").strip()
                conn.execute(
                    "UPDATE domains SET action=?, note=? WHERE domain=?",
                    (action, note, (row.get("domain") or "").strip().lower()),
                )
                n += 1
    conn.commit()
    counts = dict(
        conn.execute(
            "SELECT action, COUNT(*) FROM domains WHERE action!='' GROUP BY action"
        ).fetchall()
    )
    ui.ok(f"imported {n} decisions — totals: {counts}")
