from conftest import OFFICIAL_SAMPLES

from customs_bot.taxparse import parse_rate, parse_taxes


def test_plain_and_zero():
    p = parse_taxes(OFFICIAL_SAMPLES["0713330090"]["Taxes"])
    assert p.get("duty").pct == 2.0
    assert p.get("vat").pct == 0.0          # "صفر"
    assert not p.agreements


def test_missing_vat_and_empty_entries():
    p = parse_taxes(OFFICIAL_SAMPLES["8465950000"]["Taxes"])
    assert p.get("duty").pct == 0.0
    assert p.get("vat") is None             # -> calculator falls back to 14%
    assert not p.unparsed


def test_specific_duty_and_health_levy():
    p = parse_taxes(OFFICIAL_SAMPLES["2402200000"]["Taxes"])
    d = p.get("duty")
    assert d.pct is None and d.specific == 100.0 and d.unit == "kg"
    h = p.get("health")
    assert h.specific == 0.1 and h.unit == "20cig"


def test_table_tax_with_base_note():
    p = parse_taxes(OFFICIAL_SAMPLES["8703233090"]["Taxes"])
    t = p.get("table")
    assert t.pct == 1.0 and "ر.ض.جمركية" in t.note


def test_agreements():
    p = parse_taxes(OFFICIAL_SAMPLES["1905320000"]["Taxes"])
    assert p.get("duty").pct == 60.0 and p.get("vat").pct == 5.0
    keys = [a.key for a in p.agreements]
    assert keys == ["eu", "tr", "uk"]
    assert p.agreement("tr").components["duty"].pct == 25.5
    p2 = parse_taxes(OFFICIAL_SAMPLES["9018391035"]["Taxes"])
    assert p2.agreement("afcfta_b").components["duty"].pct == 2.0


def test_rate_variants():
    assert parse_rate("  50% تخفيض") == {"reduction_pct": 50.0}
    assert parse_rate(" بحد ادنى 60 جنية لكل كيلو جرام صافى") == {"minimum": 60.0, "min_unit": "kg"}
    assert parse_rate(" 1,950%")["pct"] == 1950.0
    assert parse_rate(" 15 جنية لكل لتر سائل") == {"specific": 15.0, "unit": "l"}
    assert parse_rate(" ٥٪ ") == {"pct": 5.0}                 # Arabic digits / percent sign
    assert "unparsed" in parse_rate(" حسب القرار الوزاري")    # unknown format is reported, not guessed
