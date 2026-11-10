import pytest

from customs_bot.router import parse, parse_amount


@pytest.mark.parametrize("text,code,value", [
    ("846595. 1522500", "846595", 1_522_500),            # exactly as typed to Captain Emad
    ("04069010 100000", "04069010", 100_000),
    ("0713330090 1,250,000", "0713330090", 1_250_000),
    ("0713330090 250k", "0713330090", 250_000),
    ("٠٧١٣٣٣٠٠٩٠ ٢٥٠٠٠٠", "0713330090", 250_000),        # Arabic-Indic digits
    ("0713.33.00.90 150 الف", "0713330090", 150_000),
])
def test_calc_intents(text, code, value):
    i = parse(text)
    assert i.kind == "calc" and i.code == code and i.value == value and i.currency == "EGP"


def test_currency_rate_qty_origin_personal():
    i = parse("8703233090 20000$ @50.5")
    assert (i.kind, i.currency, i.fx, i.value) == ("calc", "USD", 50.5, 20000)
    i = parse("2401100010 100000 2000 كجم")
    assert i.qty == {"kg": 2000}
    i = parse("2401100010 100000 2t")
    assert i.qty == {"ton": 2}
    i = parse("1905320000 100000 من تركيا")
    assert i.origin == "tr"
    i = parse("0909610000 50000 يورو اوروبا شخصي")
    assert (i.currency, i.origin, i.personal) == ("EUR", "eu", True)
    i = parse("0909610000 5000 دولار")
    assert i.currency == "USD"


def test_lookup_search_and_gs1():
    assert parse("0713330090").kind == "code"
    assert parse("0713").kind == "code"
    assert parse("فاصوليا بيضاء").kind == "search"
    assert parse("white beans").kind == "search"
    i = parse("gs1 white beans")
    assert (i.kind, i.query) == ("gpc", "white beans")
    i = parse("جي اس 1 بابونج")
    assert (i.kind, i.query) == ("gpc", "بابونج")
    assert parse("10007820").kind == "gpc_code"
    assert parse("10079000").kind == "code"                 # sorghum HS code is not a GPC brick
    assert parse("chamomile", mode="gs1").kind == "gpc"


def test_value_after_code_was_chosen():
    i = parse("250000", mode="calc", pending_code="0713330090")
    assert (i.kind, i.code, i.value) == ("calc", "0713330090", 250_000)
    i = parse("5000$", pending_code="0713330090")
    assert (i.kind, i.currency) == ("calc", "USD")
    # without a pending code a lone number is a tariff code
    assert parse("846595").kind == "code"


def test_amount_parser_rejects_bad_numbers():
    assert parse_amount("12,34") is None
    assert parse_amount("1.5m") == (1_500_000, None)
    assert parse_amount("€2000") == (2000, "EUR")
