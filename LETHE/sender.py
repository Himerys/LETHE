"""Send generated drafts via SMTP.

Every sent mail gets a unique Message-ID which is stored in the messages
table — that ID is how `fpd check` later matches bounces and replies.

Default is one confirmation per mail; --yes sends everything unattended.
"""
from __future__ import annotations

import datetime as dt
import email
import email.policy
import email.utils
import smtplib
import sqlite3
import ssl
import time
from pathlib import Path

from rich.table import Table

from LETHE import ui
from LETHE.config import Config
from LETHE.db import mark_contact
from LETHE.drafts import PLACEHOLDER_PREFIX
from LETHE.util import add_month, now_iso


class _SmtpPool:
    """One live SMTP connection per account, reconnecting on failure."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.conns: dict[str, smtplib.SMTP] = {}
        self.passwords: dict[str, str] = {}

    def _connect(self, acc: dict) -> smtplib.SMTP:
        host, port = acc["smtp_host"], int(acc.get("smtp_port", 465))
        security = acc.get("security", "ssl")
        ctx = ssl.create_default_context()
        if security == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.ehlo()
            if security == "starttls":
                s.starttls(context=ctx)
                s.ehlo()
        # username defaults to the address; an explicit empty string disables AUTH
        username = acc.get("username")
        if username is None:
            username = acc["email"]
        if username:
            key = acc["email"]
            if key not in self.passwords:
                self.passwords[key] = self.cfg.smtp_password(acc)
            s.login(username, self.passwords[key])
        return s

    def get(self, acc: dict) -> smtplib.SMTP:
        key = acc["email"]
        conn = self.conns.get(key)
        if conn is not None:
            try:
                conn.noop()
                return conn
            except smtplib.SMTPException:
                pass
        conn = self._connect(acc)
        self.conns[key] = conn
        return conn

    def close(self) -> None:
        for c in self.conns.values():
            try:
                c.quit()
            except Exception:
                pass


def run_send(
    conn: sqlite3.Connection,
    cfg: Config,
    yes: bool = False,
    limit: int = 0,
    dry_run: bool = False,
) -> None:
    drafts = conn.execute(
        """SELECT id, domain, own_addr, to_addr, subject, draft_path
           FROM messages WHERE status='draft' ORDER BY domain"""
    ).fetchall()
    if not drafts:
        ui.warn("no drafts to send — run: fpd generate")
        return

    sendable, skipped = [], []
    for row in drafts:
        _id, domain, own_addr, to_addr, subject, draft_path = row
        if not to_addr or to_addr.startswith(PLACEHOLDER_PREFIX):
            skipped.append((domain, "no contact address"))
            continue
        if not cfg.account_for(own_addr):
            skipped.append((domain, f"no SMTP account for {own_addr}"))
            continue
        if not draft_path or not Path(draft_path).exists():
            skipped.append((domain, "draft file missing — re-run fpd generate"))
            continue
        sendable.append(row)

    if skipped:
        ui.warn(f"{len(skipped)} drafts skipped:")
        for domain, why in skipped[:15]:
            ui.console.print(f"    [muted]{domain}: {why}[/muted]")
        if len(skipped) > 15:
            ui.console.print(f"    [muted]... and {len(skipped) - 15} more[/muted]")

    if not sendable:
        ui.err("nothing sendable. Fix contacts (fpd sniff / fpd contact) or SMTP accounts (fpd configure).")
        return

    if limit:
        sendable = sendable[:limit]
    daily = cfg.send_daily_limit
    if len(sendable) > daily:
        ui.warn(
            f"{len(sendable)} mails exceed send_daily_limit ({daily}) — "
            f"sending the first {daily}, run fpd send again tomorrow"
        )
        sendable = sendable[:daily]

    t = Table(title=f"outgoing erasure requests ({len(sendable)})",
              border_style="dim", title_style="head")
    t.add_column("domain", style="accent")
    t.add_column("from", style="muted")
    t.add_column("to")
    for _id, domain, own_addr, to_addr, _subj, _p in sendable[:20]:
        t.add_row(domain, own_addr, to_addr)
    if len(sendable) > 20:
        t.add_row("...", f"({len(sendable) - 20} more)", "")
    ui.console.print(t)

    if dry_run:
        ui.info("dry run — nothing sent")
        return

    pool = _SmtpPool(cfg)
    sent = failed = 0
    send_all = yes
    try:
        for _id, domain, own_addr, to_addr, subject, draft_path in sendable:
            if not send_all:
                ui.console.print(
                    f"send to [accent]{domain}[/accent] <{to_addr}>? "
                    "[muted]y=yes n=skip a=all(autosend) v=view q=quit[/muted]"
                )
                while True:
                    key = ui.getkey()
                    if key == "v":
                        body = Path(draft_path).read_text(encoding="utf-8", errors="replace")
                        ui.console.print(f"[muted]{body}[/muted]")
                        continue
                    break
                if key == "q":
                    break
                if key == "a":
                    send_all = True
                elif key != "y":
                    continue

            acc = cfg.account_for(own_addr)
            msg = email.message_from_bytes(
                Path(draft_path).read_bytes(), policy=email.policy.SMTP
            )
            del msg["To"]
            msg["To"] = to_addr  # contact may have improved since generate
            message_id = email.utils.make_msgid(idstring="fpd", domain=own_addr.split("@")[1])
            msg["Message-ID"] = message_id
            msg["Date"] = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))

            try:
                smtp = pool.get(acc)
                smtp.send_message(msg)
            except smtplib.SMTPRecipientsRefused as e:
                failed += 1
                ui.err(f"{domain}: recipient refused ({e.recipients})")
                conn.execute(
                    "UPDATE messages SET status='bounced', bounce_reason=? WHERE id=?",
                    (f"SMTP refused: {e.recipients}", _id),
                )
                mark_contact(conn, domain, to_addr, "dead", "SMTP recipient refused")
                conn.commit()
                continue
            except (smtplib.SMTPException, OSError) as e:
                failed += 1
                ui.err(f"{domain}: send failed ({e}) — draft kept")
                pool.conns.pop(acc["email"], None)
                continue

            sent += 1
            today = dt.date.today()
            conn.execute(
                """UPDATE messages SET status='sent', message_id=?, sent_at=?, to_addr=?
                   WHERE id=?""",
                (message_id, now_iso(), to_addr, _id),
            )
            conn.execute(
                """UPDATE domains SET status='sent', sent_date=?, deadline=?
                   WHERE domain=?""",
                (today.isoformat(), add_month(today).isoformat(), domain),
            )
            conn.commit()
            ui.ok(f"{domain} <- request sent from {own_addr} (deadline {add_month(today)})")
            if cfg.send_delay > 0:
                time.sleep(cfg.send_delay)
    finally:
        pool.close()

    ui.console.print()
    ui.ok(f"done: {sent} sent, {failed} failed, {len(skipped)} skipped")
    if sent:
        ui.info("responses & bounces: run  fpd check  in a few days "
                "(after Betterbird has fetched new mail)")
