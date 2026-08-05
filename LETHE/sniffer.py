"""Contact sniffer: crawl a service's website for a privacy/support e-mail address.

Rewritten from the old standalone extract_emails.py. Key behaviors:
 - visits the homepage, then contact-ish links found on it, then generated
   candidate paths (/contact, /impressum, /privacy, ... with locale prefixes)
   in priority order until something good is found or max_pages is reached
 - extracts mailto: links and plain-text addresses (incl. [at]/[dot] obfuscation)
 - filters out asset filenames (logo@2x.png) and tracking/CDN junk
 - scores every candidate: privacy@/dpo@ > legal@ > support@ > contact@/info@
 - respects robots.txt (cached per host) and a polite per-host delay
"""
from __future__ import annotations

import re
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from LETHE.util import registrable_domain

USER_AGENT = "FootprintDecimator/0.4 (GDPR Art.17 contact discovery)"

# path keywords, best first — order defines crawl priority
PRIORITY_KEYWORDS: list[tuple[int, tuple[str, ...]]] = [
    (0, ("privacy", "datenschutz", "impressum", "imprint", "legal", "dsgvo", "gdpr")),
    (1, ("contact", "contact-us", "contactus", "kontakt", "kontaktformular")),
    (2, ("support", "help", "customer-service", "customerservice", "kundenservice",
         "help-center", "helpcenter", "about", "about-us", "team")),
]
ALL_KEYWORDS = tuple(k for _, ks in PRIORITY_KEYWORDS for k in ks)

SUBDOMAIN_PREFIXES = ("support", "help", "contact", "service")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_OBF_AT = re.compile(r"\s*[\[\(\{]\s*(?:at|@)\s*[\]\)\}]\s*", re.I)
_OBF_DOT = re.compile(r"\s*[\[\(\{]\s*(?:dot|punkt)\s*[\]\)\}]\s*", re.I)

# things the regex catches that are not e-mail addresses
BAD_DOMAIN_EXT_RE = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|ico|css|js|json|woff2?|ttf|eot|mp4|webm|pdf|html?)$", re.I
)
BAD_DOMAIN_PARTS = (
    "example.", "sentry", "wixpress", "schema.org", "w3.org", "googleapis",
    "gstatic", "doubleclick", "cloudfront", "yourdomain", "domain.com",
    "email.com", "mysite", "sample.",
)
HEXHASH_RE = re.compile(r"^[0-9a-f]{24,}$", re.I)

LOCALPART_SCORES: list[tuple[tuple[str, ...], int]] = [
    (("privacy", "datenschutz", "dpo", "dsb", "gdpr", "dsgvo", "dataprotection",
      "data-protection", "datenschutzbeauftragte"), 100),
    (("legal", "recht", "compliance"), 80),
    (("support", "help", "service", "kundenservice", "kundendienst", "customercare",
      "customerservice", "customer-service", "care"), 60),
    (("contact", "kontakt", "hello", "hallo", "office", "info", "mail", "post"), 40),
    (("abuse", "postmaster", "webmaster", "hostmaster", "admin", "noc"), 10),
]


def _valid_email(addr: str) -> bool:
    addr = addr.strip().strip(".").lower()
    if addr.count("@") != 1 or len(addr) > 254:
        return False
    local, dom = addr.split("@")
    if not local or len(local) > 64 or local.startswith(".") or local.endswith("."):
        return False
    if HEXHASH_RE.match(local):
        return False
    if "u003e" in local or "%" in local:
        return False
    if BAD_DOMAIN_EXT_RE.search(dom):
        return False
    if any(part in dom for part in BAD_DOMAIN_PARTS):
        return False
    if "." not in dom:
        return False
    return True


def score_email(addr: str, target_domain: str, method: str, source_url: str) -> int:
    local, dom = addr.lower().split("@", 1)
    score = 25
    for keys, s in LOCALPART_SCORES:
        if any(local == k or local.startswith(k + ".") or local.startswith(k + "-") or k in local
               for k in keys):
            score = s
            break
    if registrable_domain(dom) == target_domain:
        score += 20
    else:
        score -= 25
    if method == "mailto":
        score += 10
    src = source_url.lower()
    if any(k in src for k in ("privacy", "datenschutz", "impressum", "imprint", "legal",
                              "contact", "kontakt")):
        score += 10
    return score


def extract_page_emails(html: str, page_url: str, target_domain: str) -> list[dict]:
    """All plausible addresses on one page, scored."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?")[0].strip().strip(".").lower()
            if addr and _valid_email(addr) and addr not in seen:
                seen.add(addr)
                out.append({
                    "email": addr, "source_url": page_url, "method": "mailto",
                    "score": score_email(addr, target_domain, "mailto", page_url),
                })

    text = soup.get_text(" ", strip=True)
    text = _OBF_DOT.sub(".", _OBF_AT.sub("@", text))
    for m in EMAIL_RE.finditer(text):
        addr = m.group(0).strip(".").lower()
        if _valid_email(addr) and addr not in seen:
            seen.add(addr)
            out.append({
                "email": addr, "source_url": page_url, "method": "text",
                "score": score_email(addr, target_domain, "text", page_url),
            })
    return out


def find_contact_links(html: str, base_url: str) -> list[tuple[int, str]]:
    """(priority, absolute_url) for links that look like contact/legal pages."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if not href or low.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        text = (a.get_text(" ", strip=True) or "").lower()
        for prio, keys in PRIORITY_KEYWORDS:
            if any(k in low or k in text for k in keys):
                found.append((prio, urljoin(base_url, href)))
                break
    return found


class _RobotsCache:
    """Thread-local per-host robots.txt cache with sane timeouts."""

    def __init__(self, session: requests.Session, respect: bool):
        self.session = session
        self.respect = respect
        self.parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        if not self.respect:
            return True
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self.parsers:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self.session.get(f"{host}/robots.txt", timeout=5)
                if resp.status_code >= 400:
                    self.parsers[host] = None  # no robots -> allowed
                else:
                    rp.parse(resp.text.splitlines())
                    self.parsers[host] = rp
            except requests.RequestException:
                self.parsers[host] = None
        rp = self.parsers[host]
        return True if rp is None else rp.can_fetch("*", url)


def _normalize_target(domain_or_url: str) -> str | None:
    s = domain_or_url.strip()
    if not s:
        return None
    parsed = urlparse(s if "://" in s else "https://" + s)
    if not parsed.netloc:
        return None
    return f"{parsed.scheme or 'https'}://{parsed.netloc}"


def sniff_domain(domain_or_url: str, opts: dict) -> dict:
    """Crawl one domain. Returns {'domain', 'candidates': [...], 'pages': n, 'error': str|None}."""
    base = _normalize_target(domain_or_url)
    result = {"domain": domain_or_url.strip().lower(), "candidates": [], "pages": 0, "error": None}
    if not base:
        result["error"] = "unparseable domain"
        return result

    parsed = urlparse(base)
    target_domain = registrable_domain(parsed.netloc)
    result["domain"] = target_domain if "://" not in domain_or_url else domain_or_url.strip().lower()

    timeout = float(opts.get("timeout", 10))
    max_pages = int(opts.get("max_pages", 15))
    polite = float(opts.get("polite_delay", 0.5))
    locales = list(opts.get("locales", ["en", "de"]))

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = _RobotsCache(session, bool(opts.get("respect_robots", True)))

    last_hit: dict[str, float] = {}

    def polite_get(url: str):
        netloc = urlparse(url).netloc
        wait = last_hit.get(netloc, 0) + polite - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        last_hit[netloc] = time.monotonic()
        try:
            return session.get(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            return None

    # base URLs to try: as given, www. variant, http fallback
    bases = [base]
    if parsed.netloc == target_domain:
        bases.append(f"{parsed.scheme}://www.{parsed.netloc}")
    if parsed.scheme == "https":
        bases.append(f"http://{parsed.netloc}")

    candidates: list[dict] = []
    seen_emails: set[str] = set()
    visited: set[str] = set()
    queue: list[tuple[int, str]] = []  # (priority, url) — lower priority first

    def harvest(html: str, page_url: str):
        for c in extract_page_emails(html, page_url, target_domain):
            if c["email"] not in seen_emails:
                seen_emails.add(c["email"])
                candidates.append(c)

    def norm(url: str) -> str:
        return url.rstrip("/")

    root_html = None
    for b in bases:
        if not robots.allowed(b):
            continue
        resp = polite_get(b)
        if resp is None or resp.status_code >= 400:
            continue
        visited.add(norm(b))
        visited.add(norm(resp.url))
        result["pages"] += 1
        root_html = resp.text
        base = f"{urlparse(resp.url).scheme}://{urlparse(resp.url).netloc}"
        harvest(root_html, resp.url)
        for prio, link in find_contact_links(root_html, resp.url):
            queue.append((prio, link))
        break

    if root_html is None:
        result["error"] = "homepage unreachable"
        result["candidates"] = sorted(candidates, key=lambda c: -c["score"])
        return result

    # generated fallback candidates, after real links
    for prio, keys in PRIORITY_KEYWORDS:
        for k in keys:
            queue.append((5 + prio, f"{base}/{k}"))
            for loc in locales:
                queue.append((6 + prio, f"{base}/{loc}/{k}"))
    if opts.get("guess_subdomains"):
        scheme = urlparse(base).scheme
        for pref in SUBDOMAIN_PREFIXES:
            queue.append((9, f"{scheme}://{pref}.{target_domain}"))

    queue.sort(key=lambda t: t[0])

    def good_enough() -> bool:
        return any(c["score"] >= 90 for c in candidates)

    for _prio, url in queue:
        if result["pages"] >= max_pages or good_enough():
            break
        if norm(url) in visited:
            continue
        visited.add(norm(url))
        if not robots.allowed(url):
            continue
        resp = polite_get(url)
        if resp is None or resp.status_code >= 400:
            continue
        if norm(resp.url) != norm(url):
            if norm(resp.url) in visited:
                continue
            visited.add(norm(resp.url))
        result["pages"] += 1
        harvest(resp.text, resp.url)

    result["candidates"] = sorted(candidates, key=lambda c: -c["score"])
    return result


def run_sniff(domains: list[str], opts: dict, on_result=None) -> list[dict]:
    """Sniff many domains in parallel. Calls on_result(result) in the main thread."""
    results = []
    workers = max(1, int(opts.get("workers", 8)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(sniff_domain, d, opts): d for d in domains}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as e:  # defensive: a worker must never kill the run
                res = {"domain": futures[fut], "candidates": [], "pages": 0, "error": str(e)}
            results.append(res)
            if on_result:
                on_result(res)
    return results
