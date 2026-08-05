"""Shared helpers: domain normalization, header decoding, date math."""
from __future__ import annotations

import datetime as dt
import email.header
import re

# Two-level public suffixes that matter for typical EU/US mail traffic.
# Only used as fallback when tldextract is unavailable.
TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "net.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "com.br", "com.mx", "com.ar", "com.tr", "com.cn",
    "co.jp", "ne.jp", "or.jp", "co.kr", "co.in", "co.nz", "co.za", "com.sg",
    "com.hk", "com.tw", "com.pl", "net.pl", "org.pl", "com.ua", "in.ua",
    "co.at", "or.at", "ac.at", "com.de",
}

try:
    import tldextract

    _TLDX = tldextract.TLDExtract(suffix_list_urls=())  # offline snapshot only
except ImportError:  # pragma: no cover
    _TLDX = None


def registrable_domain(host: str) -> str:
    """mail.shop.example.co.uk -> example.co.uk ; mail.zalando.de -> zalando.de"""
    host = host.strip().lower().rstrip(".")
    if not host or "." not in host:
        return host
    # strip port if present (e.g. localhost:8000 stays untouched above, but a.b:80 -> a.b)
    if ":" in host:
        host = host.split(":", 1)[0]
    if _TLDX:
        ext = _TLDX(host)
        joined = ".".join(p for p in (ext.domain, ext.suffix) if p)
        return joined or host
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def decode_hdr(raw) -> str:
    """Decode an RFC 2047 header value into a plain string."""
    if raw is None:
        return ""
    try:
        out = []
        for val, enc in email.header.decode_header(str(raw)):
            if isinstance(val, bytes):
                out.append(val.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(val)
        return " ".join(out).strip()
    except Exception:
        return str(raw)


def add_month(d: dt.date) -> dt.date:
    """One calendar month later (Art. 12(3) GDPR response deadline)."""
    y, m = d.year + (d.month // 12), (d.month % 12) + 1
    try:
        return d.replace(year=y, month=m)
    except ValueError:  # e.g. Jan 31 -> Feb 28
        return (d.replace(year=y, month=m, day=1) + dt.timedelta(days=31)).replace(
            day=1
        ) - dt.timedelta(days=1)


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


_SAFE_FILE_RE = re.compile(r"[^\w.@-]")


def safe_filename(s: str) -> str:
    return _SAFE_FILE_RE.sub("_", s)
