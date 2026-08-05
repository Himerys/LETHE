import sqlite3

from LETHE.db import best_contact, mark_contact, open_db, upsert_contact

V1_SCHEMA = """
CREATE TABLE domains (
    domain TEXT PRIMARY KEY,
    display_names TEXT, sender_addrs TEXT, own_addrs TEXT,
    msg_count INTEGER DEFAULT 0, signal_count INTEGER DEFAULT 0,
    noreply_ratio REAL DEFAULT 0, list_unsub_ratio REAL DEFAULT 0,
    first_seen TEXT, last_seen TEXT, sample_subjects TEXT,
    jdm_name TEXT, jdm_url TEXT, jdm_difficulty TEXT, jdm_email TEXT, jdm_notes TEXT,
    action TEXT DEFAULT '', status TEXT DEFAULT 'new',
    sent_date TEXT, deadline TEXT, note TEXT
);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _make_v1(path):
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute(
        "INSERT INTO domains (domain, sender_addrs, action, status, jdm_email) VALUES "
        "('zalando.de', 'news@zalando.de; info@zalando.de', 'SKIP', 'new', NULL)"
    )
    conn.execute(
        "INSERT INTO domains (domain, sender_addrs, action, status, jdm_email) VALUES "
        "('shady.com', 'noreply@shady.com', 'DELETE', 'drafted', 'privacy@shady.com')"
    )
    conn.commit()
    conn.close()


def test_v1_migration(tmp_path):
    _make_v1(tmp_path / "footprint.db")
    conn = open_db(tmp_path)

    # SKIP -> KEEP
    assert conn.execute(
        "SELECT action FROM domains WHERE domain='zalando.de'"
    ).fetchone()[0] == "KEEP"
    # DELETE decisions survive
    assert conn.execute(
        "SELECT action, status FROM domains WHERE domain='shady.com'"
    ).fetchone() == ("DELETE", "drafted")
    # sender_addrs split into senders table
    senders = {
        r[0] for r in conn.execute("SELECT address FROM senders WHERE domain='zalando.de'")
    }
    assert senders == {"news@zalando.de", "info@zalando.de"}
    # jdm_email seeded into contacts
    assert best_contact(conn, "shady.com") == "privacy@shady.com"
    conn.close()

    # re-opening must be idempotent
    conn = open_db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM senders WHERE domain='zalando.de'").fetchone()[0] == 2
    conn.close()


def test_contact_lifecycle(tmp_path):
    conn = open_db(tmp_path)
    conn.execute("INSERT INTO domains (domain) VALUES ('corp.de')")
    upsert_contact(conn, "corp.de", "info@corp.de", "sniffer", 40)
    upsert_contact(conn, "corp.de", "privacy@corp.de", "sniffer", 120)
    assert best_contact(conn, "corp.de") == "privacy@corp.de"

    # a dead contact is never picked again
    mark_contact(conn, "corp.de", "privacy@corp.de", "dead", "bounced")
    assert best_contact(conn, "corp.de") == "info@corp.de"

    # upsert never downgrades score, never resurrects the dead
    upsert_contact(conn, "corp.de", "privacy@corp.de", "sniffer", 130)
    assert best_contact(conn, "corp.de") == "info@corp.de"
    conn.close()
