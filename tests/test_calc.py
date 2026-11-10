import pytest
from conftest import OFFICIAL_SAMPLES

from customs_bot.calc import calculate
from customs_bot.settings import CalcSettings
from customs_bot.taxparse import parse_taxes


def taxes(code):
    return parse_taxes(OFFICIAL_SAMPLES[code]["Taxes"])


def test_matches_captain_emad_screenshot():
    # Captain Emad bot: 846595 / CIF 1,522,500 -> VAT 213,150 ; profits 15,225 ; total 228,375 ; +goods 1,750,875
    r = calculate(taxes("8465950000"), 1_522_500, code="8465950000")
    assert r.amount("duty") == 0
    assert r.amount("dev") == 0
    assert r.amount("vat") == pytest.approx(213_150)
    assert r.amount("income") == pytest.approx(15_225)
    assert r.total == pytest.approx(228_375)
    assert r.grand_total == pytest.approx(1_750_875)
    assert r.vat_is_default


def test_table_tax_enters_vat_base():
    r = calculate(taxes("8703233090"), 500_000)
    assert r.amount("duty") == pytest.approx(200_000)
    assert r.amount("table") == pytest.approx(7_000)            # 1% x (500,000 + 200,000)
    assert r.amount("vat") == pytest.approx(0.14 * 707_000)      # value + duty + table
    assert r.amount("income") == pytest.approx(5_000)


def test_zero_vat_line():
    r = calculate(taxes("0713330090"), 250_000)
    assert r.amount("duty") == pytest.approx(5_000)
    assert r.amount("vat") == 0
    assert not r.vat_is_default


def test_specific_duty_needs_quantity():
    r = calculate(taxes("2402200000"), 100_000)
    assert r.incomplete
    r2 = calculate(taxes("2402200000"), 100_000, qty={"kg": 300})
    assert r2.amount("duty") == pytest.approx(30_000)
    health = [l for l in r2.lines if l.key == "health"][0]
    assert health.amount is None          # per-20-cigarettes levy still needs a stick count
    assert r2.incomplete


def test_minimum_specific_vat_applies():
    t = parse_taxes(["ضريبة الوارد :  9 جنية لكل كيلو جرام صافى",
                     "ضريبة قيمه مضافه :  75% (من القيمة + ر.ض.جمركية)",
                     "ضريبة قيمه مضافه :  بحد ادنى 60 جنية لكل كيلو جرام صافى",
                     "رسم محصلة لحساب غرفة دخان :  0.5%"])
    r = calculate(t, 100_000, qty={"kg": 2_000})
    assert r.amount("duty") == pytest.approx(18_000)
    assert r.amount("chamber_tobacco") == pytest.approx(500)
    assert r.amount("vat") == pytest.approx(120_000)            # 60 x 2000 > 75% x 118,500
    r_ton = calculate(t, 100_000, qty={"ton": 2})
    assert r_ton.amount("vat") == pytest.approx(120_000)


def test_agreement_rate_and_reduction():
    r = calculate(taxes("1905320000"), 100_000, origin="eu")
    assert r.amount("duty") == pytest.approx(15_000)
    assert r.origin_key == "eu"
    assert r.amount("vat") == pytest.approx(0.05 * 115_000)
    gstp = parse_taxes(["ضريبة الوارد :  5%", "GSTP", "ضريبة الوارد :  50% تخفيض"])
    r2 = calculate(gstp, 100_000, origin="gstp")
    assert r2.amount("duty") == pytest.approx(2_500)
    r3 = calculate(taxes("0713330090"), 100_000, origin="eu")    # no EU line -> general rate + warning
    assert r3.amount("duty") == pytest.approx(2_000) and r3.warnings


def test_personal_import_and_settings():
    r = calculate(taxes("8465950000"), 100_000, personal=True)
    assert r.amount("income") == 0
    s = CalcSettings(income_tax_advance_pct=0, vat_default_pct=10)
    r2 = calculate(taxes("8465950000"), 100_000, settings=s)
    assert r2.amount("vat") == pytest.approx(10_000) and r2.amount("income") == 0


def test_development_fee_override():
    r = calculate(taxes("8703233090"), 500_000, dev_fee_pct=3)
    assert r.amount("dev") == pytest.approx(0.03 * 700_000)      # (value + duty) x 3%
    assert r.amount("vat") == pytest.approx(0.14 * (700_000 + 21_000 + 7_000))


def test_dates_follow_the_local_clock(monkeypatch):
    import time

    from customs_bot.formatters import local_day
    monkeypatch.setenv("TZ", "EET-2")             # Egypt winter time, UTC+2
    time.tzset()
    try:
        assert local_day("2026-11-10T22:30:00Z") == "2026-11-11"     # 00:30 in Cairo is already the 11th
        assert local_day("2026-11-10T21:59:59Z") == "2026-11-10"
        assert local_day("") == "" and local_day("garbage") == "garbage"[:10]
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
