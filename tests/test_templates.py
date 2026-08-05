from LETHE.templates import is_german_service, pick_language, render


def test_german_detection_by_tld():
    assert is_german_service("zalando.de", "")
    assert is_german_service("shop.at", "")
    assert not is_german_service("amazon.com", "please verify your account")


def test_german_detection_by_subject():
    assert is_german_service("gmx.com", "Bitte bestätigen Sie Ihre Registrierung")


def test_pick_language_override():
    assert pick_language("en", "zalando.de", "Ihre Rechnung") == "en"
    assert pick_language("de", "amazon.com", "welcome") == "de"
    assert pick_language("auto", "zalando.de", "") == "de"


def test_render_contains_essentials():
    subj, body = render("de", "me@mail.de", "Max Muster", ["Maxi"], "LfDI BW")
    assert "Art. 17" in subj or "DSGVO" in subj
    assert "me@mail.de" in body
    assert "Max Muster" in body
    assert "Maxi" in body
    assert "LfDI BW" in body

    subj_en, body_en = render("en", "me@mail.com", "Max Muster", [], "")
    assert "GDPR" in subj_en
    assert "me@mail.com" in body_en
    assert "competent supervisory authority" in body_en
