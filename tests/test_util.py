from LETHE.util import add_month, registrable_domain
import datetime as dt


def test_registrable_domain_basic():
    assert registrable_domain("mail.zalando.de") == "zalando.de"
    assert registrable_domain("shop.example.co.uk") == "example.co.uk"
    assert registrable_domain("EXAMPLE.COM.") == "example.com"
    assert registrable_domain("localhost") == "localhost"


def test_registrable_domain_strips_port():
    assert registrable_domain("foo.example.com:8080") == "example.com"


def test_add_month_regular():
    assert add_month(dt.date(2026, 3, 15)) == dt.date(2026, 4, 15)


def test_add_month_end_of_month():
    assert add_month(dt.date(2026, 1, 31)) == dt.date(2026, 2, 28)


def test_add_month_december():
    assert add_month(dt.date(2026, 12, 10)) == dt.date(2027, 1, 10)
