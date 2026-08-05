"""End-to-end pipeline test: scan -> triage -> generate -> send -> check."""
import re

from LETHE.config import Config
from LETHE.db import best_contact, open_db, upsert_contact
from LETHE.drafts import PLACEHOLDER_PREFIX, run_generate
from LETHE.scan import run_scan
from LETHE.sender import run_send
from LETHE.tracker import run_check

PREFS_JS = """
user_pref("mail.account.account1.identities", "id1");
user_pref("mail.account.account1.server", "server1");
user_pref("mail.identity.id1.useremail", "me@testmail.local");
user_pref("mail.server.server1.hostname", "localhost");
user_pref("mail.server.server1.type", "pop3");
"""

MAILS = """From - Tue Aug 04 10:00:00 2026
From: Shady Corp <welcome@shady.com>
To: me@testmail.local
Subject: Welcome to Shady - confirm your account
Date: Tue, 04 Aug 2026 10:00:00 +0200
Message-ID: <w1@shady.com>

Thanks for registering!

From - Tue Aug 04 11:00:00 2026
From: Corp News <newsletter@news.corp.de>
To: me@testmail.local
Subject: Ihre Rechnung und Bestellung
Date: Tue, 04 Aug 2026 11:00:00 +0200
List-Unsubscribe: <mailto:unsub@corp.de>
Message-ID: <n1@corp.de>

Neues aus dem Shop.

From - Tue Aug 04 12:00:00 2026
From: Good Org <hello@keepme.org>
To: me@testmail.local
Subject: hi there
Date: Tue, 04 Aug 2026 12:00:00 +0200
Message-ID: <k1@keepme.org>

Just saying hi.

"""


def make_profile(tmp_path):
    profile = tmp_path / "profile"
    (profile / "Mail" / "localhost").mkdir(parents=True)
    (profile / "prefs.js").write_text(PREFS_JS, encoding="utf-8")
    inbox = profile / "Mail" / "localhost" / "Inbox"
    inbox.write_text(MAILS, encoding="utf-8")
    return profile


def make_config(tmp_path, smtp_port):
    return Config(
        tmp_path / "config.json",
        {
            "sender_name": "Test Person",
            "aliases": ["T. Person"],
            "language": "auto",
            "accounts": [
                {
                    "email": "me@testmail.local",
                    "smtp_host": "127.0.0.1",
                    "smtp_port": smtp_port,
                    "security": "none",
                    "username": "",
                }
            ],
            "send_delay_seconds": 0,
        },
    )


def test_full_pipeline(tmp_path, smtp_sink):
    profile = make_profile(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    conn = open_db(workdir)
    cfg = make_config(workdir, smtp_sink.port)
    out_dir = workdir / "out"

    # --- scan ---------------------------------------------------------------
    n = run_scan(conn, str(profile))
    assert n == 3
    domains = {r[0] for r in conn.execute("SELECT domain FROM domains")}
    assert domains == {"shady.com", "corp.de", "keepme.org"}
    shady = conn.execute(
        "SELECT msg_count, signal_count, own_addrs FROM domains WHERE domain='shady.com'"
    ).fetchone()
    assert shady[0] == 1 and shady[1] >= 1 and shady[2] == "me@testmail.local"
    assert conn.execute(
        "SELECT list_unsub_ratio FROM domains WHERE domain='corp.de'"
    ).fetchone()[0] == 1.0
    senders = {r[0] for r in conn.execute("SELECT address FROM senders")}
    assert "welcome@shady.com" in senders and "newsletter@news.corp.de" in senders

    # --- triage -------------------------------------------------------------
    conn.execute("UPDATE domains SET action='DELETE' WHERE domain IN ('shady.com','corp.de')")
    conn.execute("UPDATE domains SET action='KEEP' WHERE domain='keepme.org'")
    upsert_contact(conn, "shady.com", "privacy@shady.com", "manual", 95)
    upsert_contact(conn, "corp.de", "privacy@corp.de", "manual", 95)
    conn.commit()

    # --- generate -----------------------------------------------------------
    run_generate(conn, cfg, out_dir, sniff=False)
    drafts = conn.execute(
        "SELECT domain, to_addr, draft_path FROM messages WHERE status='draft'"
    ).fetchall()
    assert {d[0] for d in drafts} == {"shady.com", "corp.de"}
    assert all(d[1].startswith("privacy@") for d in drafts)
    corp_draft = [d for d in drafts if d[0] == "corp.de"][0]
    body = open(corp_draft[2], encoding="utf-8").read()
    assert "DSGVO" in body            # German service -> German letter
    assert "me@testmail.local" in body
    assert "Test Person" in body
    shady_draft = [d for d in drafts if d[0] == "shady.com"][0]
    assert "GDPR" in open(shady_draft[2], encoding="utf-8").read()  # English letter

    # keepme.org must never get a draft
    assert conn.execute(
        "SELECT COUNT(*) FROM messages WHERE domain='keepme.org'"
    ).fetchone()[0] == 0

    # --- send ---------------------------------------------------------------
    run_send(conn, cfg, yes=True)
    assert len(smtp_sink.messages) == 2
    rcpts = {m["to"][0] for m in smtp_sink.messages}
    assert rcpts == {"privacy@shady.com", "privacy@corp.de"}
    sent_rows = conn.execute(
        "SELECT domain, message_id, status FROM messages WHERE status='sent'"
    ).fetchall()
    assert len(sent_rows) == 2
    assert all(re.match(r"<.+@testmail\.local>", r[1]) for r in sent_rows)
    assert conn.execute(
        "SELECT status, deadline FROM domains WHERE domain='shady.com'"
    ).fetchone()[0] == "sent"

    # the actual mail on the wire carries our tracking headers
    wire = [m for m in smtp_sink.messages if "privacy@shady.com" in m["to"]][0]["data"]
    assert "X-FP-Domain: shady.com" in wire
    assert "Message-ID:" in wire

    # --- responses arrive ---------------------------------------------------
    corp_msgid = conn.execute(
        "SELECT message_id FROM messages WHERE domain='corp.de'"
    ).fetchone()[0]
    shady_msgid = conn.execute(
        "SELECT message_id FROM messages WHERE domain='shady.com'"
    ).fetchone()[0]
    inbox = profile / "Mail" / "localhost" / "Inbox"
    with open(inbox, "a", encoding="utf-8") as fh:
        fh.write(
            f"""From - Wed Aug 05 09:00:00 2026
From: Mail Delivery System <MAILER-DAEMON@testmail.local>
To: me@testmail.local
Subject: Mail delivery failed: returning message to sender
Date: Wed, 05 Aug 2026 09:00:00 +0200
In-Reply-To: {corp_msgid}
Message-ID: <bounce1@testmail.local>

The address privacy@corp.de was not found.

From - Wed Aug 05 10:00:00 2026
From: Shady Support <support@shady.com>
To: me@testmail.local
Subject: RE: Request for erasure of personal data under Article 17 GDPR
Date: Wed, 05 Aug 2026 10:00:00 +0200
In-Reply-To: {shady_msgid}
References: {shady_msgid}
Message-ID: <reply1@shady.com>

We have deleted your account.

"""
        )

    # --- check --------------------------------------------------------------
    run_check(conn, str(profile))
    assert conn.execute(
        "SELECT status FROM messages WHERE domain='corp.de'"
    ).fetchone()[0] == "bounced"
    assert conn.execute(
        "SELECT status FROM messages WHERE domain='shady.com'"
    ).fetchone()[0] == "replied"
    assert conn.execute(
        "SELECT status FROM domains WHERE domain='corp.de'"
    ).fetchone()[0] == "bounced"
    assert conn.execute(
        "SELECT status FROM domains WHERE domain='shady.com'"
    ).fetchone()[0] == "responded"
    # bounce killed the contact, reply proved the other one alive
    assert conn.execute(
        "SELECT status FROM contacts WHERE domain='corp.de' AND email='privacy@corp.de'"
    ).fetchone()[0] == "dead"
    assert conn.execute(
        "SELECT status FROM contacts WHERE domain='shady.com' AND email='privacy@shady.com'"
    ).fetchone()[0] == "alive"
    assert best_contact(conn, "corp.de") is None

    # a second check run must not re-report or flip anything
    run_check(conn, str(profile))
    assert conn.execute(
        "SELECT status FROM messages WHERE domain='shady.com'"
    ).fetchone()[0] == "replied"

    # --- re-generate after bounce ------------------------------------------
    run_generate(conn, cfg, out_dir, sniff=False)
    redraft = conn.execute(
        "SELECT to_addr FROM messages WHERE domain='corp.de' AND status='draft'"
    ).fetchall()
    assert len(redraft) == 1
    assert redraft[0][0] == "" or redraft[0][0].startswith(PLACEHOLDER_PREFIX)
    # the bounced message row is preserved as history
    assert conn.execute(
        "SELECT COUNT(*) FROM messages WHERE domain='corp.de' AND status='bounced'"
    ).fetchone()[0] == 1
    # responded shady.com must NOT be re-drafted
    assert conn.execute(
        "SELECT COUNT(*) FROM messages WHERE domain='shady.com' AND status='draft'"
    ).fetchone()[0] == 0
    conn.close()
