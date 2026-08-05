"""GDPR Art. 17 erasure request letters (German + English)."""
from __future__ import annotations

import re

SUBJ_DE = "Antrag auf Löschung personenbezogener Daten gemäß Art. 17 DSGVO"
SUBJ_EN = "Request for erasure of personal data under Article 17 GDPR"

BODY_DE = """Sehr geehrte Damen und Herren,

hiermit fordere ich Sie gemäß Art. 17 Abs. 1 DSGVO auf, sämtliche zu meiner Person gespeicherten personenbezogenen Daten unverzüglich zu löschen. Dies umfasst insbesondere mein Nutzerkonto sowie alle damit verknüpften Daten, Profile, Inhalte, Log- und Backup-Daten, soweit keine gesetzliche Aufbewahrungspflicht entgegensteht.

Zur Identifikation: Mein Konto bei Ihnen ist unter der E-Mail-Adresse {own_addr} registriert.{names_line}

Zugleich widerrufe ich gemäß Art. 7 Abs. 3 DSGVO sämtliche erteilten Einwilligungen und widerspreche gemäß Art. 21 DSGVO der Verarbeitung meiner Daten zu Werbezwecken einschließlich Profiling.

Bitte bestätigen Sie mir die vollständige Löschung schriftlich an diese E-Mail-Adresse. Ich weise darauf hin, dass Sie gemäß Art. 12 Abs. 3 DSGVO verpflichtet sind, innerhalb eines Monats nach Eingang dieses Antrags zu reagieren. Ferner bitte ich um Mitteilung gemäß Art. 19 DSGVO, an welche Empfänger meine Daten weitergegeben wurden.

Sollte ich innerhalb der Frist keine Rückmeldung erhalten, behalte ich mir eine Beschwerde bei {authority} sowie weitere rechtliche Schritte vor.

Mit freundlichen Grüßen
{sender_name}"""

BODY_EN = """Dear Sir or Madam,

Pursuant to Article 17(1) GDPR, I hereby request the immediate erasure of all personal data you hold about me. This includes my user account and all associated data, profiles, content, logs and backups, unless retention is required by law.

For identification: my account with your service is registered under the email address {own_addr}.{names_line}

I furthermore withdraw all consents given (Article 7(3) GDPR) and object to any processing of my data for direct marketing purposes, including profiling (Article 21 GDPR).

Please confirm the complete erasure in writing to this email address. I remind you that under Article 12(3) GDPR you are obliged to respond within one month of receipt of this request. In accordance with Article 19 GDPR, please also inform me of any recipients to whom my data has been disclosed.

If I do not receive a response within this period, I reserve the right to lodge a complaint with {authority} and to pursue further legal remedies.

Kind regards
{sender_name}"""

GERMAN_TLDS = (".de", ".at", ".ch")
GERMAN_WORDS_RE = re.compile(
    r"best[aä]tig|willkommen|konto|ihre?\b|deine?\b|rechnung|anmeld|registrierung"
    r"|bestellung|kennwort|passwort\b|sicherheitscode",
    re.IGNORECASE,
)


def is_german_service(domain: str, sample_subjects: str) -> bool:
    """TLD heuristic + language sniffing on real subject lines (catches German
    services on .com/.net, e.g. gmx.com)."""
    if domain.endswith(GERMAN_TLDS):
        return True
    return bool(GERMAN_WORDS_RE.search(sample_subjects or ""))


def pick_language(cfg_language: str, domain: str, sample_subjects: str) -> str:
    if cfg_language in ("de", "en"):
        return cfg_language
    return "de" if is_german_service(domain, sample_subjects) else "en"


def render(
    lang: str,
    own_addr: str,
    sender_name: str,
    aliases: list[str],
    authority: str = "",
) -> tuple[str, str]:
    """Returns (subject, body)."""
    if lang == "de":
        subj, body_t = SUBJ_DE, BODY_DE
        names_line = (
            "\nDas Konto kann auch unter folgenden Namen geführt werden: "
            + ", ".join(aliases) + "."
            if aliases
            else ""
        )
        auth = (
            f"der zuständigen Aufsichtsbehörde ({authority})"
            if authority
            else "der zuständigen Datenschutz-Aufsichtsbehörde"
        )
    else:
        subj, body_t = SUBJ_EN, BODY_EN
        names_line = (
            "\nThe account may also be held under the following names: "
            + ", ".join(aliases) + "."
            if aliases
            else ""
        )
        auth = (
            f"the competent supervisory authority ({authority})"
            if authority
            else "the competent supervisory authority"
        )
    body = body_t.format(
        own_addr=own_addr, names_line=names_line, sender_name=sender_name, authority=auth
    )
    return subj, body
