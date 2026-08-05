"""Thunderbird/Betterbird profile discovery and identity mapping.

Betterbird is a Thunderbird fork and uses the same profile layout, so both
apps are supported by looking in both config locations.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def candidate_profile_roots() -> list[Path]:
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots += [Path(appdata) / "Thunderbird", Path(appdata) / "Betterbird"]
    home = Path.home()
    roots += [
        home / ".thunderbird",
        home / ".betterbird",
        home / "Library" / "Thunderbird",  # macOS
        home / ".var" / "app" / "org.mozilla.Thunderbird" / ".thunderbird",  # flatpak
        home / "snap" / "thunderbird" / "common" / ".thunderbird",  # snap
    ]
    return [r for r in roots if r.exists()]


def find_profiles(explicit: str | None) -> list[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            sys.exit(f"profile path does not exist: {p}")
        return [p]
    profiles = []
    for root in candidate_profile_roots():
        ini = root / "profiles.ini"
        if ini.exists():
            for m in re.finditer(r"^Path=(.+)$", ini.read_text(errors="replace"), re.M):
                rel = m.group(1).strip()
                p = Path(rel) if Path(rel).is_absolute() else root / rel
                if p.exists():
                    profiles.append(p)
        else:
            profiles += [p for p in root.glob("Profiles/*") if p.is_dir()]
    seen, out = set(), []
    for p in profiles:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def parse_identities(profile: Path) -> dict[str, str]:
    """prefs.js -> {server_hostname: own_email_address}

    Maps the ImapMail/<hostname> (or Mail/<hostname>) folder names onto the
    e-mail identity configured for that server.
    """
    prefs = profile / "prefs.js"
    mapping: dict[str, str] = {}
    if not prefs.exists():
        return mapping
    txt = prefs.read_text(errors="replace")

    identities = {
        m.group(1): m.group(2).lower()
        for m in re.finditer(
            r'user_pref\("mail\.identity\.(id\d+)\.useremail",\s*"([^"]+)"\);', txt
        )
    }
    acct_server = {
        m.group(1): m.group(2)
        for m in re.finditer(
            r'user_pref\("mail\.account\.(account\d+)\.server",\s*"([^"]+)"\);', txt
        )
    }
    acct_ids = {
        m.group(1): m.group(2).split(",")[0]
        for m in re.finditer(
            r'user_pref\("mail\.account\.(account\d+)\.identities",\s*"([^"]+)"\);', txt
        )
    }
    srv_host = {
        m.group(1): m.group(2)
        for m in re.finditer(
            r'user_pref\("mail\.server\.(server\d+)\.hostname",\s*"([^"]+)"\);', txt
        )
    }

    for acct, srv in acct_server.items():
        mail_addr = identities.get(acct_ids.get(acct, ""), "")
        host = srv_host.get(srv, "")
        if host and mail_addr:
            mapping[host.lower()] = mail_addr
    return mapping


# Folders that contain outgoing/own mail — their senders are not services.
SKIP_FOLDER_NAMES = {
    "sent", "sent mail", "sent-mail", "sent messages", "sent items",
    "gesendet", "gesendete objekte", "gesendete elemente",
    "drafts", "entwürfe", "entwuerfe", "templates", "vorlagen",
    "outbox", "postausgang", "unsent messages",
}


def _is_own_mail_folder(rel: Path) -> bool:
    for part in rel.parts[:-1]:
        if part.lower().removesuffix(".sbd") in SKIP_FOLDER_NAMES:
            return True
    return rel.parts[-1].lower() in SKIP_FOLDER_NAMES


def iter_mbox_files(profile: Path, include_own_folders: bool = False):
    """Yield (path, server_dir, kind) for every mbox file under Mail/ and ImapMail/.

    Mbox files have no extension and start with the 'From ' magic. Sent/Drafts
    folders are skipped by default so your own outgoing mail is not inventoried
    as a 'service'.
    """
    for kind in ("ImapMail", "Mail"):
        root = profile / kind
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                p = Path(dirpath) / fn
                if p.suffix:  # .msf, .dat, .json, .sqlite ...
                    continue
                rel = p.relative_to(root)
                if not include_own_folders and _is_own_mail_folder(rel):
                    continue
                try:
                    with open(p, "rb") as fh:
                        if fh.read(5) != b"From ":
                            continue
                except OSError:
                    continue
                server_dir = rel.parts[0].lower() if rel.parts else ""
                yield p, server_dir, kind


def own_addr_for_server(ident_map: dict[str, str], server_dir: str) -> str:
    """Match a folder name like 'imap.gmail.com' or 'imap.gmail.com-1' to an identity."""
    if server_dir in ident_map:
        return ident_map[server_dir]
    stripped = re.sub(r"-\d+$", "", server_dir)
    return ident_map.get(stripped, "")
