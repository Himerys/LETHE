"""Generate Art. 17 erasure-request drafts (.eml) for every DELETE domain.

Contact resolution order per domain:
  1. best address from the contacts table (JustDeleteMe / sniffer / manual)
  2. if nothing found -> the built-in sniffer crawls the service's website
  3. still nothing -> the domain lands in out/needs-contact.csv for manual research
"""
from __future__ import annotations

import csv
import email.message
import email.policy
import sqlite3
from pathlib import Path

from rich.progress import Progress, BarColumn, TextColumn

from LETHE import templates, ui
from LETHE.config import Config
from LETHE.db import best_contact, upsert_contact
from LETHE.sniffer import run_sniff
from LETHE.util import safe_filename

PLACEHOLDER_PREFIX = "FIND-CONTACT@"


def _sniff_missing(conn: sqlite3.Connection, cfg: Config, domains: list[str]) -> None:
    if not domains:
        return
    ui.info(f"sniffing websites of {len(domains)} domains without a known contact ...")
    opts = cfg.sniffer
    with Progress(
        TextColumn("[accent]sniffing[/accent]"),
        BarColumn(style="dim", complete_style="accent"),
        TextColumn("[muted]{task.completed}/{task.total}[/muted]"),
        console=ui.console,
    ) as progress:
        task = progress.add_task("sniff", total=len(domains))
        found_count = 0

        def on_result(res: dict):
            nonlocal found_count
            progress.advance(task)
            for c in res["candidates"][:5]:
                upsert_contact(
                    conn, res["domain"], c["email"], "sniffer", c["score"],
                    f"{c['method']} on {c['source_url']}",
                )
            if res["candidates"]:
                found_count += 1

        run_sniff(domains, opts, on_result=on_result)
        conn.commit()
    ui.ok(f"sniffer found contact candidates for {found_count}/{len(domains)} domains")


def run_generate(
    conn: sqlite3.Connection,
    cfg: Config,
    out_dir: Path,
    sniff: bool = True,
    prefer_self_service: bool = False,
) -> None:
    sender_name = cfg.sender_name
    if not sender_name:
        ui.err("no sender_name configured — run: fpd configure")
        raise SystemExit(1)

    rows = conn.execute(
        """SELECT domain, own_addrs, jdm_url, jdm_difficulty, jdm_notes, sample_subjects
           FROM domains WHERE action='DELETE' AND status IN ('new','drafted','bounced')"""
    ).fetchall()
    if not rows:
        ui.warn("no DELETE domains in status new/drafted/bounced — run scan + review first")
        return

    # 1) find contacts for domains that have none yet
    missing = [r[0] for r in rows if not best_contact(conn, r[0])]
    if sniff and missing:
        _sniff_missing(conn, cfg, missing)
    elif missing:
        ui.warn(f"{len(missing)} domains have no contact address (sniffer disabled)")

    drafts_dir = out_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    manual_rows: list[tuple] = []
    needs_contact: list[tuple] = []
    generated = 0

    for domain, own_addrs, jdm_url, jdm_diff, jdm_notes, subjects in rows:
        own_list = [a for a in (own_addrs or "").split("; ") if a] or ["UNKNOWN"]

        # optional: route easy self-service deletions to the manual queue instead
        if prefer_self_service and jdm_url and jdm_diff in ("easy", "medium"):
            manual_rows.append((domain, jdm_diff, jdm_url, jdm_notes or "self-service"))
            conn.execute(
                "UPDATE domains SET status='drafted' WHERE domain=?", (domain,)
            )
            continue

        to_addr = best_contact(conn, domain)
        if not to_addr:
            needs_contact.append((domain, jdm_url or f"https://{domain}"))
        lang = templates.pick_language(cfg.language, domain, subjects or "")
        subj, _ = templates.render(lang, "x", sender_name, cfg.aliases, cfg.supervisory_authority)

        for own_addr in own_list:
            _, body = templates.render(
                lang, own_addr, sender_name, cfg.aliases, cfg.supervisory_authority
            )
            msg = email.message.EmailMessage(policy=email.policy.SMTP)
            msg["From"] = f"{sender_name} <{own_addr}>"
            msg["To"] = to_addr or f"{PLACEHOLDER_PREFIX}{domain}"
            msg["Subject"] = subj
            msg["X-FP-Domain"] = domain
            msg.set_content(body, charset="utf-8")

            fn = drafts_dir / safe_filename(own_addr) / f"{safe_filename(domain)}.eml"
            fn.parent.mkdir(parents=True, exist_ok=True)
            fn.write_bytes(bytes(msg))
            generated += 1

            conn.execute(
                """INSERT INTO messages (domain, own_addr, to_addr, subject, status, draft_path)
                   SELECT ?,?,?,?,'draft',?
                   WHERE NOT EXISTS (SELECT 1 FROM messages
                                     WHERE domain=? AND own_addr=? AND status='draft')""",
                (domain, own_addr, to_addr or "", subj, str(fn), domain, own_addr),
            )
            conn.execute(
                """UPDATE messages SET to_addr=?, draft_path=?, subject=?
                   WHERE domain=? AND own_addr=? AND status='draft'""",
                (to_addr or "", str(fn), subj, domain, own_addr),
            )
        conn.execute("UPDATE domains SET status='drafted' WHERE domain=?", (domain,))
    conn.commit()

    # user-marked MANUAL domains -> manual queue
    for domain, jdm_url2, jdm_diff2, jdm_notes2 in conn.execute(
        "SELECT domain, jdm_url, jdm_difficulty, jdm_notes FROM domains WHERE action='MANUAL'"
    ):
        manual_rows.append(
            (domain, jdm_diff2 or "", jdm_url2 or f"https://{domain}", jdm_notes2 or "marked MANUAL")
        )

    if manual_rows:
        mpath = out_dir / "manual-queue.csv"
        with open(mpath, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["domain", "difficulty", "deletion_url", "notes"])
            w.writerows(manual_rows)
        ui.info(f"manual queue ({len(manual_rows)} services): {mpath}")

    if needs_contact:
        cpath = out_dir / "needs-contact.csv"
        with open(cpath, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["domain", "hint"])
            for domain, hint in needs_contact:
                w.writerow([domain, f"check privacy policy / imprint: {hint}"])
        ui.warn(
            f"{len(needs_contact)} drafts still have NO contact address "
            f"(placeholder To). List: {cpath}"
        )
        ui.info("add one manually with: fpd contact <domain> <email>")

    ui.ok(f"{generated} erasure-request drafts written to {drafts_dir}")
    ui.info("review them if you like, then: fpd send   (or fpd send --yes to autosend)")
