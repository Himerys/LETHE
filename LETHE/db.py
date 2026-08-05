"""SQLite storage: domains, senders, outbound contacts, sent messages.

Schema v2. A v1 database (from the old footprint.py) is migrated in place:
triage decisions, JustDeleteMe enrichment and send status are all preserved,
old action SKIP becomes KEEP.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from LETHE.util import now_iso

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY,
    display_names TEXT,
    own_addrs TEXT,
    msg_count INTEGER DEFAULT 0,
    signal_count INTEGER DEFAULT 0,
    noreply_ratio REAL DEFAULT 0,
    list_unsub_ratio REAL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    sample_subjects TEXT,
    jdm_name TEXT, jdm_url TEXT, jdm_difficulty TEXT, jdm_email TEXT, jdm_notes TEXT,
    action TEXT DEFAULT '',              -- KEEP | DELETE | MANUAL | '' (undecided)
    status TEXT DEFAULT 'new',           -- new|drafted|sent|bounced|responded|deleted|refused|escalated|noaccount
    sent_date TEXT, deadline TEXT, note TEXT
);

CREATE TABLE IF NOT EXISTS senders (
    domain TEXT NOT NULL,
    address TEXT NOT NULL,
    msg_count INTEGER DEFAULT 0,
    last_seen TEXT,
    PRIMARY KEY (domain, address)
);

-- outbound privacy/support contact addresses per domain, with liveness tracking
CREATE TABLE IF NOT EXISTS contacts (
    domain TEXT NOT NULL,
    email TEXT NOT NULL,
    source TEXT DEFAULT '',              -- jdm | sniffer | manual | reply
    score INTEGER DEFAULT 0,
    status TEXT DEFAULT 'unverified',    -- unverified | alive | dead
    evidence TEXT,                       -- where it was found / why it died
    checked_at TEXT,
    PRIMARY KEY (domain, email)
);

-- every outbound erasure request, identified by its Message-ID
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    own_addr TEXT NOT NULL,
    to_addr TEXT,
    message_id TEXT UNIQUE,
    subject TEXT,
    status TEXT DEFAULT 'draft',         -- draft | sent | bounced | replied
    draft_path TEXT,
    sent_at TEXT,
    replied_at TEXT,
    bounce_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_domain ON messages(domain);
CREATE INDEX IF NOT EXISTS idx_contacts_domain ON contacts(domain);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def open_db(workdir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(workdir / "footprint.db")
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    is_v1 = _table_exists(conn, "domains") and not _table_exists(conn, "messages")
    conn.executescript(SCHEMA)

    if is_v1:
        cols = _columns(conn, "domains")
        # old databases carried sender addresses inline; split them into `senders`
        if "sender_addrs" in cols:
            for domain, addrs, last in conn.execute(
                "SELECT domain, sender_addrs, last_seen FROM domains"
            ).fetchall():
                for addr in (addrs or "").split("; "):
                    addr = addr.strip().lower()
                    if addr:
                        conn.execute(
                            "INSERT OR IGNORE INTO senders (domain, address, last_seen)"
                            " VALUES (?,?,?)",
                            (domain, addr, last),
                        )
        conn.execute("UPDATE domains SET action='KEEP' WHERE action='SKIP'")
        # known JustDeleteMe contact addresses seed the contacts table
        for domain, jdm_email in conn.execute(
            "SELECT domain, jdm_email FROM domains WHERE jdm_email IS NOT NULL AND jdm_email!=''"
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO contacts (domain, email, source, score, evidence)"
                " VALUES (?,?,'jdm',90,'listed on JustDeleteMe')",
                (domain, jdm_email.lower()),
            )

    conn.execute(
        "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# small shared queries
# ---------------------------------------------------------------------------

def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def upsert_contact(
    conn: sqlite3.Connection,
    domain: str,
    email_addr: str,
    source: str,
    score: int,
    evidence: str = "",
) -> None:
    """Insert or improve a contact; never resurrect one already proven dead."""
    email_addr = email_addr.strip().lower()
    row = conn.execute(
        "SELECT score, status FROM contacts WHERE domain=? AND email=?",
        (domain, email_addr),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO contacts (domain, email, source, score, evidence) VALUES (?,?,?,?,?)",
            (domain, email_addr, source, score, evidence[:300]),
        )
    elif score > row[0]:
        conn.execute(
            "UPDATE contacts SET score=?, source=?, evidence=? WHERE domain=? AND email=?",
            (score, source, evidence[:300], domain, email_addr),
        )


def best_contact(conn: sqlite3.Connection, domain: str) -> str | None:
    """Highest-scored contact address that is not known-dead."""
    row = conn.execute(
        "SELECT email FROM contacts WHERE domain=? AND status!='dead'"
        " ORDER BY score DESC, email LIMIT 1",
        (domain,),
    ).fetchone()
    return row[0] if row else None


def mark_contact(
    conn: sqlite3.Connection, domain: str, email_addr: str, status: str, evidence: str = ""
) -> None:
    conn.execute(
        "INSERT INTO contacts (domain, email, status, evidence, checked_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(domain, email) DO UPDATE SET status=excluded.status,"
        "   evidence=excluded.evidence, checked_at=excluded.checked_at",
        (domain, email_addr.lower(), status, evidence[:300], now_iso()),
    )
