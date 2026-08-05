"""Live sniffer test against a local HTTP server."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from LETHE.sniffer import sniff_domain

PAGES = {
    "/": """<html><body>
        <h1>Corp Test</h1>
        <a href="/kontakt">Kontakt</a>
        <a href="/products">Products</a>
        </body></html>""",
    "/kontakt": """<html><body>
        <p>Fragen? <a href="mailto:privacy@corp-test.de">privacy@corp-test.de</a></p>
        <p>Oder: support [at] corp-test [dot] de</p>
        <img src="logo@2x.png">
        </body></html>""",
    "/products": "<html><body>nothing here</body></html>",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = PAGES.get(self.path.rstrip("/") or "/")
        if page is None:
            self.send_response(404)
            self.end_headers()
            return
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_sniff_domain_finds_contact_page_addresses(http_site):
    res = sniff_domain(
        http_site,
        {"timeout": 5, "max_pages": 6, "polite_delay": 0, "locales": ["de"],
         "respect_robots": True},
    )
    assert res["error"] is None
    emails = {c["email"] for c in res["candidates"]}
    assert "privacy@corp-test.de" in emails
    assert "support@corp-test.de" in emails
    assert not any(e.endswith(".png") for e in emails)
    # privacy@ must rank first
    assert res["candidates"][0]["email"] == "privacy@corp-test.de"


def test_sniff_unreachable_domain():
    res = sniff_domain(
        "http://127.0.0.1:9",  # discard port — nothing listens
        {"timeout": 1, "max_pages": 3, "polite_delay": 0, "locales": [],
         "respect_robots": False},
    )
    assert res["error"] == "homepage unreachable"
    assert res["candidates"] == []
