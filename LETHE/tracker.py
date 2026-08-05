"""Track responses: scan the mailbox for bounces and replies to our requests.

Matching strategy, most reliable first:
  1. In-Reply-To / References headers containing one of our Message-IDs
  2. bounce reports (mailer-daemon / multipart-report / bounce subjects) whose
     body contains one of our Message-IDs, our X-FP-Domain marker, or a known
     contact address

A bounce marks the contact address 'dead' (feeding the shareable contact DB),
a real reply marks it 'alive' and the domain 'responded'.
"""
from __future__ import annotations

import email.utils
import mailbox
import re
import sqlite3

from LETHE import ui
from LETHE.db import mark_contact, set_meta
from LETHE.profiles import find_profiles, iter_mbox_files
from LETHE.util import decode_hdr, now_iso

BOUNCE_FROM_RE = re.compile(r"mailer-daemon|postmaster|mail delivery (subsystem|system)", re.I)
BOUNCE_SUBJ_RE = re.compile(
    r"undeliver|delivery status|delivery has failed|failure notice|returned mail"
    r"|mail delivery failed|could not be delivered|not delivered|delivery incomplete"
    r"|unzustellbar|zustellung fehlgeschlagen|adresse nicht gefunden|address not found",
    re.I,
)


def _is_bouncey(msg, frm: str, subj: str) -> bool:
    if BOUNCE_FROM_RE.search(frm):
        return True
    if BOUNCE_SUBJ_RE.search(subj):
        return True
    ctype = msg.get("Content-Type", "")
    return "multipart/report" in ctype.lower()


def _raw_text(msg, limit: int = 200_000) -> str:
    """Whole message as text — bounces are small, so this is cheap."""
    try:
        return msg.as_string()[:limit]
    except Exception:
        try:
            return str(msg)[:limit]
        except Exception:
            return ""


def run_check(conn: sqlite3.Connection, profile_path: str | None) -> None:
    raw_rows = conn.execute(
        """SELECT id, domain, own_addr, to_addr, message_id, sent_at, status
           FROM messages WHERE status IN ('sent','replied','bounced') AND message_id IS NOT NULL"""
    ).fetchall()
    if not raw_rows:
        ui.warn("no sent requests to check — send some first (fpd send)")
        return
    sent = [r[:6] for r in raw_rows]
    prior_status = {r[0]: r[6] for r in raw_rows}
    handled: set[tuple[int, str]] = set()

    # strip <> for substring matching in References/bodies
    id_cores = {row[4].strip("<>"): row for row in sent}
    by_domain_marker = {f"X-FP-Domain: {row[1]}": row for row in sent}
    contact_to_row = {}
    for row in sent:
        if row[3]:
            contact_to_row.setdefault(row[3].lower(), row)

    own_addrs = {row[2].lower() for row in sent}

    profiles = find_profiles(profile_path or None)
    if not profiles:
        ui.err("no mail profile found")
        raise SystemExit(1)

    bounces, replies = [], []
    n_msgs = 0
    with ui.console.status("[accent]checking mailbox for responses...[/accent]", spinner="dots"):
        for profile in profiles:
            for path, _server_dir, _kind in iter_mbox_files(profile):
                try:
                    mb = mailbox.mbox(str(path), create=False)
                except Exception:
                    continue
                for msg in mb:
                    n_msgs += 1
                    frm = decode_hdr(msg.get("From")).lower()
                    _, frm_addr = email.utils.parseaddr(frm)
                    if frm_addr in own_addrs:
                        continue  # our own copies
                    subj = decode_hdr(msg.get("Subject"))
                    refs = f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}"

                    hit = None
                    for core, row in id_cores.items():
                        if core in refs:
                            hit = row
                            break
                    bouncey = _is_bouncey(msg, frm, subj)

                    if hit is None and bouncey:
                        raw = _raw_text(msg)
                        for core, row in id_cores.items():
                            if core in raw:
                                hit = row
                                break
                        if hit is None:
                            for marker, row in by_domain_marker.items():
                                if marker in raw:
                                    hit = row
                                    break
                        if hit is None:
                            for contact, row in contact_to_row.items():
                                if contact in raw.lower():
                                    hit = row
                                    break
                    if hit is None:
                        continue

                    mid, domain, own_addr, to_addr, message_id, sent_at = hit
                    kind = "bounce" if bouncey else "reply"
                    if (mid, kind) in handled:
                        continue
                    handled.add((mid, kind))
                    try:
                        reply_date = email.utils.parsedate_to_datetime(
                            msg.get("Date")
                        ).isoformat(timespec="seconds")
                    except Exception:
                        reply_date = now_iso()

                    if bouncey:
                        if prior_status.get(mid) == "replied":
                            continue  # a real reply outranks a stray bounce
                        if prior_status.get(mid) != "bounced":
                            bounces.append((domain, to_addr, subj))
                        conn.execute(
                            "UPDATE messages SET status='bounced', bounce_reason=? WHERE id=?",
                            (subj[:200], mid),
                        )
                        if to_addr:
                            mark_contact(conn, domain, to_addr, "dead", f"bounced: {subj[:120]}")
                        conn.execute(
                            """UPDATE domains SET status='bounced'
                               WHERE domain=? AND status IN ('sent','drafted')""",
                            (domain,),
                        )
                        prior_status[mid] = "bounced"
                    else:
                        if prior_status.get(mid) != "replied":
                            replies.append((domain, frm_addr, subj))
                        conn.execute(
                            "UPDATE messages SET status='replied', replied_at=? WHERE id=?",
                            (reply_date, mid),
                        )
                        if to_addr:
                            mark_contact(conn, domain, to_addr, "alive", "got a reply")
                        conn.execute(
                            """UPDATE domains SET status='responded'
                               WHERE domain=? AND status IN ('sent','bounced','drafted')""",
                            (domain,),
                        )
                        prior_status[mid] = "replied"
    set_meta(conn, "last_check", now_iso())
    conn.commit()

    ui.ok(f"checked {n_msgs} messages")
    if bounces:
        ui.console.print(f"\n[err]═══ bounces ({len(bounces)}) — contact marked dead ═══[/err]")
        for domain, to_addr, subj in bounces:
            ui.console.print(f"  [err]x[/err] {domain} <{to_addr}> [muted]{subj[:60]}[/muted]")
        ui.info("re-run  fpd generate  to draft these again with the next-best contact")
    if replies:
        ui.console.print(f"\n[ok]═══ replies ({len(replies)}) ═══[/ok]")
        for domain, frm_addr, subj in replies:
            ui.console.print(f"  [ok]+[/ok] {domain} [muted]{frm_addr}: {subj[:60]}[/muted]")
        ui.info("read them, then:  fpd mark <domain> deleted|refused|noaccount")
    if not bounces and not replies:
        ui.info("no new bounces or replies found")
