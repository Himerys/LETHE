"""Status dashboard: pipeline progress, deadlines, next-step hint."""
from __future__ import annotations

import datetime as dt
import sqlite3

from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from LETHE import ui
from LETHE.db import get_meta

STATUS_STYLES = {
    "new": "muted", "drafted": "accent", "sent": "warn", "bounced": "err",
    "responded": "ok", "deleted": "ok", "refused": "err",
    "escalated": "warn", "noaccount": "muted",
}


def run_status(conn: sqlite3.Connection) -> str:
    """Prints the dashboard, returns a suggested next command."""
    c = ui.console
    total = conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
    if not total:
        ui.warn("inventory is empty")
        return "fpd scan"

    undecided = conn.execute("SELECT COUNT(*) FROM domains WHERE action=''").fetchone()[0]
    actions = dict(
        conn.execute(
            "SELECT action, COUNT(*) FROM domains WHERE action!='' GROUP BY action"
        ).fetchall()
    )
    last_scan = get_meta(conn, "last_scan", "never")
    c.print(
        f"\n[head]═══ inventory ═══[/head]  [muted]{total} domains, last scan {last_scan}[/muted]"
    )
    with Progress(
        TextColumn("[accent]triaged[/accent]"),
        BarColumn(style="dim", complete_style="accent", finished_style="ok"),
        TextColumn("[muted]{task.completed}/{task.total}[/muted]"),
        console=c,
    ) as p:
        p.add_task("triaged", total=total, completed=total - undecided)
    c.print(
        f"  [err]DELETE {actions.get('DELETE', 0)}[/err]   "
        f"[ok]KEEP {actions.get('KEEP', 0)}[/ok]   "
        f"[warn]MANUAL {actions.get('MANUAL', 0)}[/warn]   "
        f"[muted]undecided {undecided}[/muted]"
    )

    del_statuses = dict(
        conn.execute(
            "SELECT status, COUNT(*) FROM domains WHERE action='DELETE' GROUP BY status"
        ).fetchall()
    )
    if del_statuses:
        t = Table(title="erasure pipeline", border_style="dim", title_style="head")
        t.add_column("status")
        t.add_column("domains", justify="right")
        for st in ("new", "drafted", "sent", "bounced", "responded",
                   "deleted", "refused", "escalated", "noaccount"):
            if st in del_statuses:
                style = STATUS_STYLES.get(st, "")
                t.add_row(f"[{style}]{st}[/{style}]", str(del_statuses[st]))
        c.print(t)

    msg_stats = dict(
        conn.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall()
    )
    if msg_stats:
        c.print(
            "  [muted]mails:[/muted] "
            + "  ".join(f"[{STATUS_STYLES.get(k, 'muted')}]{k} {v}[/]" for k, v in sorted(msg_stats.items()))
        )
    dead = conn.execute("SELECT COUNT(*) FROM contacts WHERE status='dead'").fetchone()[0]
    alive = conn.execute("SELECT COUNT(*) FROM contacts WHERE status='alive'").fetchone()[0]
    if dead or alive:
        c.print(f"  [muted]contact intel:[/muted] [ok]{alive} alive[/ok], [err]{dead} dead[/err]"
                f"  [muted](fpd export-contacts to share)[/muted]")

    today = dt.date.today().isoformat()
    overdue = conn.execute(
        """SELECT domain, sent_date, deadline FROM domains
           WHERE status='sent' AND deadline < ? ORDER BY deadline""",
        (today,),
    ).fetchall()
    if overdue:
        c.print(f"\n[err]═══ OVERDUE — no answer within the one-month deadline ({len(overdue)}) ═══[/err]")
        for domain, sent, dl in overdue[:20]:
            c.print(f"  [err]![/err] {domain:<35} [muted]sent {sent}, deadline {dl} expired[/muted]")
        c.print(
            "  [warn]escalation:[/warn] [muted]file a complaint with your supervisory authority "
            "(free, online), then: fpd mark <domain> escalated[/muted]"
        )
    pending = conn.execute(
        """SELECT domain, deadline FROM domains
           WHERE status='sent' AND deadline >= ? ORDER BY deadline""",
        (today,),
    ).fetchall()
    if pending:
        c.print(f"\n[head]═══ running deadlines ({len(pending)}) ═══[/head]")
        for domain, dl in pending[:15]:
            c.print(f"  [warn]•[/warn] {domain:<35} [muted]deadline {dl}[/muted]")
        if len(pending) > 15:
            c.print(f"  [muted]... and {len(pending) - 15} more[/muted]")

    # suggest the next pipeline step
    if undecided:
        return "fpd review"
    if msg_stats.get("draft"):
        return "fpd send"
    # DELETE domains that should have a draft/sent request but don't
    need_generate = conn.execute(
        """SELECT COUNT(*) FROM domains d
           WHERE d.action='DELETE' AND d.status IN ('new','drafted','bounced')
           AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.domain=d.domain
                           AND m.status IN ('draft','sent','replied'))"""
    ).fetchone()[0]
    if need_generate:
        return "fpd generate"
    if del_statuses.get("sent"):
        return "fpd check"
    return "fpd scan"
