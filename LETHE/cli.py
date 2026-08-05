"""fpd — command line interface and pipeline dispatcher."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from LETHE import __version__, ui
from LETHE.config import Config, wizard
from LETHE.db import open_db, upsert_contact
from LETHE.util import registrable_domain


def default_workdir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller exe -> portable next to the exe
        return Path(sys.executable).parent
    return Path.cwd()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fpd",
        description="Footprint Decimator — mailbox recon, GDPR Art. 17 requests, response tracking.",
    )
    ap.add_argument("--workdir", help="working directory (config.json, footprint.db, out/)")
    ap.add_argument("--no-banner", action="store_true", help="suppress the ASCII banner")
    ap.add_argument("--version", action="version", version=f"fpd {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("configure", help="interactive setup (name, SMTP accounts, ...)")

    p = sub.add_parser("scan", help="scan Betterbird/Thunderbird mailboxes, build the inventory")
    p.add_argument("--profile", help="explicit profile path (default: auto-discover)")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("review", help="triage domains: DELETE / KEEP / MANUAL")
    p.add_argument("--all", action="store_true", help="also revisit already-decided domains")
    p.add_argument("--csv-export", metavar="FILE", help="export inventory CSV for Excel instead")
    p.add_argument("--csv", metavar="FILE", help="import decisions from an edited CSV")

    p = sub.add_parser("generate", help="write erasure-request drafts (.eml) for DELETE domains")
    p.add_argument("--no-sniff", action="store_true",
                   help="do not crawl websites for missing contact addresses")
    p.add_argument("--prefer-self-service", action="store_true",
                   help="route JustDeleteMe easy/medium services to the manual queue instead of mail")

    p = sub.add_parser("sniff", help="crawl websites for privacy/support contact addresses")
    p.add_argument("targets", nargs="*", help="domains or URLs (default: DELETE domains without contact)")
    p.add_argument("--input", "-i", help="file with one domain per line")
    p.add_argument("--output", "-o", help="also write results to this CSV")

    p = sub.add_parser("send", help="send the drafts via SMTP")
    p.add_argument("--yes", "-y", action="store_true", help="autosend without per-mail confirmation")
    p.add_argument("--limit", type=int, default=0, help="send at most N mails")
    p.add_argument("--dry-run", action="store_true", help="show what would be sent, send nothing")

    p = sub.add_parser("check", help="scan mailbox for bounces & replies to our requests")
    p.add_argument("--profile", help="explicit profile path")

    sub.add_parser("status", help="progress dashboard, deadlines, next step")

    p = sub.add_parser("sent", help="manually mark domain(s) as sent (starts the 1-month deadline)")
    p.add_argument("domains", nargs="+")

    p = sub.add_parser("mark", help="set a domain's final status")
    p.add_argument("domain")
    p.add_argument("status", choices=["deleted", "responded", "refused", "escalated", "noaccount"])

    p = sub.add_parser("contact", help="manually set the contact address for a domain")
    p.add_argument("domain")
    p.add_argument("email")

    sub.add_parser("export-contacts", help="export the shareable contact knowledge base")

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else default_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    out_dir = workdir / "out"

    if not args.no_banner and args.cmd in (None, "status"):
        ui.banner(__version__)

    cfg = Config.load(workdir)

    if args.cmd == "configure":
        wizard(workdir)
        return

    conn = open_db(workdir)
    try:
        dispatch(args, conn, cfg, workdir, out_dir)
    finally:
        conn.close()


def dispatch(args, conn, cfg: Config, workdir: Path, out_dir: Path) -> None:
    from LETHE import status as status_mod

    if args.cmd is None:
        if not cfg.path.exists():
            ui.warn("no config.json yet — starting first-run setup")
            cfg = wizard(workdir)
        nxt = status_mod.run_status(conn)
        ui.console.print(f"\n[accent]>>[/accent] next step: [ok]{nxt}[/ok]\n")
        return

    if args.cmd == "scan":
        from LETHE.enrich import run_enrich
        from LETHE.scan import run_scan

        run_scan(conn, args.profile or cfg.profile_path, args.verbose)
        run_enrich(conn)
        undecided = conn.execute("SELECT COUNT(*) FROM domains WHERE action=''").fetchone()[0]
        if undecided:
            ui.info(f"next: fpd review   ({undecided} domains waiting for a decision)")

    elif args.cmd == "review":
        from LETHE import triage

        if args.csv_export:
            triage.export_csv(conn, Path(args.csv_export))
        elif args.csv:
            triage.import_csv(conn, Path(args.csv))
        else:
            triage.run_review(conn, include_decided=args.all)

    elif args.cmd == "generate":
        from LETHE.drafts import run_generate

        run_generate(conn, cfg, out_dir, sniff=not args.no_sniff,
                     prefer_self_service=args.prefer_self_service)

    elif args.cmd == "sniff":
        _cmd_sniff(args, conn, cfg, out_dir)

    elif args.cmd == "send":
        from LETHE.sender import run_send

        run_send(conn, cfg, yes=args.yes, limit=args.limit, dry_run=args.dry_run)

    elif args.cmd == "check":
        from LETHE.tracker import run_check

        run_check(conn, args.profile or cfg.profile_path)

    elif args.cmd == "status":
        nxt = status_mod.run_status(conn)
        ui.console.print(f"\n[accent]>>[/accent] next step: [ok]{nxt}[/ok]\n")

    elif args.cmd == "sent":
        _cmd_sent(args, conn)

    elif args.cmd == "mark":
        cur = conn.execute(
            "UPDATE domains SET status=? WHERE domain=?", (args.status, args.domain.lower())
        )
        conn.commit()
        if cur.rowcount:
            ui.ok(f"{args.domain} -> {args.status}")
        else:
            ui.err(f"{args.domain}: not in inventory")

    elif args.cmd == "contact":
        domain = args.domain.lower().strip()
        upsert_contact(conn, domain, args.email, "manual", 95, "set manually")
        conn.commit()
        ui.ok(f"contact for {domain}: {args.email}")
        ui.info("re-run  fpd generate  to update the draft")

    elif args.cmd == "export-contacts":
        from LETHE.exporter import run_export_contacts

        run_export_contacts(conn, out_dir)


def _cmd_sent(args, conn) -> None:
    import datetime as dt

    from LETHE.util import add_month

    today = dt.date.today()
    deadline = add_month(today)
    for domain in args.domains:
        cur = conn.execute(
            "UPDATE domains SET status='sent', sent_date=?, deadline=? WHERE domain=?",
            (today.isoformat(), deadline.isoformat(), domain.lower()),
        )
        if cur.rowcount:
            ui.ok(f"{domain}: marked sent, deadline (Art. 12(3) GDPR): {deadline}")
        else:
            ui.err(f"{domain}: not in inventory")
    conn.commit()


def _cmd_sniff(args, conn, cfg: Config, out_dir: Path) -> None:
    from rich.progress import BarColumn, Progress, TextColumn

    from LETHE.db import best_contact
    from LETHE.sniffer import run_sniff

    targets: list[str] = list(args.targets or [])
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            targets += [ln.strip() for ln in fh if ln.strip()]
    if not targets:
        targets = [
            r[0]
            for r in conn.execute("SELECT domain FROM domains WHERE action='DELETE'").fetchall()
            if not best_contact(conn, r[0])
        ]
        if not targets:
            ui.ok("every DELETE domain already has a contact address")
            return
        ui.info(f"sniffing {len(targets)} DELETE domains without a known contact")
    targets = list(dict.fromkeys(targets))

    known_domains = {r[0] for r in conn.execute("SELECT domain FROM domains").fetchall()}
    all_rows: list[dict] = []
    errors: list[tuple[str, str]] = []
    with Progress(
        TextColumn("[accent]sniffing[/accent]"),
        BarColumn(style="dim", complete_style="accent"),
        TextColumn("[muted]{task.completed}/{task.total} {task.fields[current]}[/muted]"),
        console=ui.console,
    ) as progress:
        task = progress.add_task("sniff", total=len(targets), current="")

        def on_result(res: dict):
            progress.update(task, advance=1, current=res["domain"][:40])
            if res.get("error"):
                errors.append((res["domain"], res["error"]))
            dbdomain = registrable_domain(res["domain"])
            for c in res["candidates"][:5]:
                all_rows.append({"domain": res["domain"], **c})
                if dbdomain in known_domains:
                    upsert_contact(conn, dbdomain, c["email"], "sniffer", c["score"],
                                   f"{c['method']} on {c['source_url']}")
            conn.commit()

        run_sniff(targets, cfg.sniffer, on_result=on_result)

    found_domains = {r["domain"] for r in all_rows}
    ui.ok(f"found addresses on {len(found_domains)}/{len(targets)} domains "
          f"({len(all_rows)} candidates)")
    if errors:
        ui.warn(f"{len(errors)} domains had problems:")
        for domain, error in errors[:10]:
            ui.console.print(f"    [muted]{domain}: {error}[/muted]")
        if len(errors) > 10:
            ui.console.print(f"    [muted]... and {len(errors) - 10} more[/muted]")
    for row in sorted(all_rows, key=lambda r: (r["domain"], -r["score"]))[:25]:
        ui.console.print(
            f"    [accent]{row['domain']:<28}[/accent] {row['email']:<40} "
            f"[muted]score {row['score']} ({row['method']})[/muted]"
        )
    if len(all_rows) > 25:
        ui.console.print(f"    [muted]... and {len(all_rows) - 25} more[/muted]")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["domain", "email", "score", "method", "source_url"])
            w.writeheader()
            for r in sorted(all_rows, key=lambda r: (r["domain"], -r["score"])):
                w.writerow({k: r[k] for k in ("domain", "email", "score", "method", "source_url")})
        ui.ok(f"results written to {out}")


if __name__ == "__main__":
    main()
