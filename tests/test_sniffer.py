from LETHE.sniffer import (
    _valid_email,
    extract_page_emails,
    find_contact_links,
    score_email,
)


def test_valid_email_rejects_asset_filenames():
    assert not _valid_email("logo@2x.png")
    assert not _valid_email("icon@3x.webp")
    assert not _valid_email("main@abc123.js")


def test_valid_email_rejects_junk_domains():
    assert not _valid_email("user@example.com")
    assert not _valid_email("abc@o12345.ingest.sentry.io")
    assert not _valid_email("x@sentry-next.wixpress.com")


def test_valid_email_rejects_hashes_and_artifacts():
    assert not _valid_email("6f1ed002ab5595859014ebf0951522d9@corp.de")
    assert not _valid_email("u003efoo@corp.de")


def test_valid_email_accepts_real_addresses():
    assert _valid_email("privacy@zalando.de")
    assert _valid_email("kunden-service@my-shop.co.uk")


def test_scoring_priorities():
    target = "corp.de"
    privacy = score_email("privacy@corp.de", target, "mailto", "https://corp.de/datenschutz")
    support = score_email("support@corp.de", target, "mailto", "https://corp.de/kontakt")
    info = score_email("info@corp.de", target, "text", "https://corp.de/")
    offsite = score_email("privacy@otherhost.com", target, "text", "https://corp.de/")
    assert privacy > support > info
    assert privacy > offsite


def test_extract_page_emails_mailto_and_obfuscated():
    html = """
    <html><body>
      <a href="mailto:privacy@corp.de?subject=hi">write us</a>
      <p>Support: support [at] corp [dot] de</p>
      <img src="logo@2x.png">
    </body></html>
    """
    found = extract_page_emails(html, "https://corp.de/kontakt", "corp.de")
    emails = {c["email"] for c in found}
    assert "privacy@corp.de" in emails
    assert "support@corp.de" in emails
    assert "logo@2x.png" not in emails


def test_find_contact_links_priorities():
    html = """
    <a href="/about">About</a>
    <a href="/datenschutz">Datenschutz</a>
    <a href="/kontakt">Kontakt</a>
    <a href="javascript:void(0)">skip</a>
    """
    links = find_contact_links(html, "https://corp.de")
    urls = {u for _, u in links}
    assert "https://corp.de/datenschutz" in urls
    assert "https://corp.de/kontakt" in urls
    prio = {u: p for p, u in links}
    assert prio["https://corp.de/datenschutz"] < prio["https://corp.de/kontakt"]
