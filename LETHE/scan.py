"""Mailbox scan: read every mbox, aggregate per sender domain, store inventory."""
from __future__ import annotations

import email.utils
import mailbox
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from LETHE import ui
from LETHE.db import set_meta
from LETHE.profiles import (
    find_profiles,
    iter_mbox_files,
    own_addr_for_server,
    parse_identities,
)
from LETHE.util import decode_hdr, now_iso, registrable_domain

# Registration signals (DE/EN/FR/PL — matched against subject + sender display name)
SIGNAL_PATTERNS = [
    r"welcome", r"willkommen", r"bienvenue", r"witamy",
    r"verif", r"verifiz", r"confirm", r"best[aä]tig", r"potwierd",
    r"registr", r"anmeld", r"sign[- ]?up", r"inscription",
    r"activat", r"aktivier", r"freischalt",
    r"account", r"konto", r"compte",
    r"passwor[dt]", r"kennwort", r"mot de passe", r"has[lł]o",
    r"login", r"log[- ]?in", r"einlog",
    r"security code", r"sicherheitscode", r"one[- ]?time", r"einmal", r"\b2fa\b", r"otp\b",
    r"your order", r"ihre bestellung", r"deine bestellung",
    r"invoice", r"rechnung",
    r"terms of service", r"nutzungsbedingung", r"agb[- ]",
    r"privacy policy (update|change)", r"datenschutz",
    r"subscription", r"abonnement", r"mitgliedschaft", r"membership",
]
SIGNAL_RE = re.compile("|".join(SIGNAL_PATTERNS), re.IGNORECASE)

NOREPLY_RE = re.compile(r"no[-_.]?reply|do[-_.]?not[-_.]?reply|newsletter|mailing", re.I)


def run_scan(conn: sqlite3.Connection, profile_path: str | None, verbose: bool = False) -> int:
    profiles = find_profiles(profile_path or None)
    if not profiles:
        ui.err(
            "no Thunderbird/Betterbird profile found. "
            "Set profile_path in config.json or pass --profile <path>."
        )
        raise SystemExit(1)

    ui.info(f"profiles found: {len(profiles)}")
    for p in profiles:
        ui.console.print(f"    [muted]{p}[/muted]")

    ident_map: dict[str, str] = {}
    for p in profiles:
        ident_map.update(parse_identities(p))
    if ident_map:
        ui.info(f"own identities from prefs.js: {len(ident_map)}")
        for host, addr in sorted(ident_map.items()):
            ui.console.print(f"    [muted]{host} -> {addr}[/muted]")
    else:
        ui.warn("no identities found in prefs.js — falling back to To/Delivered-To headers")

    own_addrs_known = set(ident_map.values())
    agg: dict[str, dict] = defaultdict(
        lambda: {
            "names": set(), "senders": defaultdict(int), "own": set(),
            "n": 0, "sig": 0, "noreply": 0, "unsub": 0,
            "first": None, "last": None, "subjects": [],
        }
    )

    n_files = n_msgs = 0
    with ui.console.status("[accent]scanning mailboxes...[/accent]", spinner="dots"):
        for profile in profiles:
            for path, server_dir, kind in iter_mbox_files(profile):
                n_files += 1
                own_addr_for_file = own_addr_for_server(ident_map, server_dir)
                try:
                    mb = mailbox.mbox(str(path), create=False)
                except Exception as e:
                    ui.warn(f"skipping {path.name}: {e}")
                    continue
                for msg in mb:
                    n_msgs += 1
                    _ingest(msg, agg, own_addrs_known, own_addr_for_file)
                if verbose:
                    ui.console.print(
                        f"    [muted]{kind}/{server_dir}/{path.name}: {n_msgs} messages total[/muted]"
                    )

    ui.ok(f"scanned {n_files} mbox files, {n_msgs} messages, {len(agg)} sender domains")

    for domain, d in agg.items():
        n = d["n"] or 1
        conn.execute(
            """
            INSERT INTO domains (domain, display_names, own_addrs, msg_count,
                signal_count, noreply_ratio, list_unsub_ratio,
                first_seen, last_seen, sample_subjects)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(domain) DO UPDATE SET
                display_names=excluded.display_names,
                own_addrs=excluded.own_addrs,
                msg_count=excluded.msg_count,
                signal_count=excluded.signal_count,
                noreply_ratio=excluded.noreply_ratio,
                list_unsub_ratio=excluded.list_unsub_ratio,
                first_seen=excluded.first_seen,
                last_seen=excluded.last_seen,
                sample_subjects=excluded.sample_subjects
            """,
            (
                domain,
                "; ".join(sorted(d["names"]))[:400],
                "; ".join(sorted(d["own"]))[:400],
                d["n"], d["sig"],
                round(d["noreply"] / n, 2), round(d["unsub"] / n, 2),
                d["first"], d["last"],
                " | ".join(d["subjects"])[:500],
            ),
        )
        for addr, cnt in d["senders"].items():
            conn.execute(
                """
                INSERT INTO senders (domain, address, msg_count, last_seen)
                VALUES (?,?,?,?)
                ON CONFLICT(domain, address) DO UPDATE SET
                    msg_count=excluded.msg_count, last_seen=excluded.last_seen
                """,
                (domain, addr, cnt, d["last"]),
            )
    set_meta(conn, "last_scan", now_iso())
    conn.commit()
    return len(agg)


def _ingest(msg, agg, own_addrs_known: set[str], own_addr_for_file: str) -> None:
    frm = decode_hdr(msg.get("From"))
    name, addr = email.utils.parseaddr(frm)
    addr = addr.lower()
    if not addr or "@" not in addr:
        return
    if addr in own_addrs_known:  # own outgoing mail is not a service
        return
    domain = registrable_domain(addr.split("@", 1)[1])
    subj = decode_hdr(msg.get("Subject"))
    d = agg[domain]
    d["n"] += 1
    if name:
        d["names"].add(name[:60])
    d["senders"][addr] += 1
    if own_addr_for_file:
        d["own"].add(own_addr_for_file)
    else:
        for h in ("Delivered-To", "X-Original-To", "To"):
            _, own = email.utils.parseaddr(decode_hdr(msg.get(h)))
            if own and "@" in own:
                d["own"].add(own.lower())
                break
    blob = f"{subj} {name} {addr}"
    if SIGNAL_RE.search(blob):
        d["sig"] += 1
        if len(d["subjects"]) < 5 and subj:
            d["subjects"].append(subj[:90])
    elif len(d["subjects"]) < 2 and subj:
        d["subjects"].append(subj[:90])
    if NOREPLY_RE.search(addr):
        d["noreply"] += 1
    if msg.get("List-Unsubscribe"):
        d["unsub"] += 1
    try:
        ts = email.utils.parsedate_to_datetime(msg.get("Date"))
        iso = ts.date().isoformat()
        d["first"] = min(filter(None, [d["first"], iso]))
        d["last"] = max(filter(None, [d["last"], iso]))
    except Exception:
        pass
