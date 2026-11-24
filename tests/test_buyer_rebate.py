"""Buyer's import duty (WITS / UNCTAD TRAINS) and the export rebate (رد الأعباء) — logic and bot flows."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from test_bot_e2e import USER, app_and_tg, cb_update, msg_update, send  # noqa: F401  (fixture)

from customs_bot import rebate as rb
from customs_bot import router, wits
from customs_bot.service import parse_money

DATA = Path(__file__).parent / "data"
NOW = datetime(2026, 11, 24, tzinfo=timezone.utc).timestamp()


def wits_json(avg, kind="MFN", year=2024, lines=1, pref=0, na=0) -> dict:
    """An answer shaped like the WITS TRAINS API (see tests/data/wits_us_mfn_2017.json, a real one)."""
    def coded(*ids):
        return [{"id": str(i), "name": str(i)} for i in ids]
    return {
        "structure": {
            "dimensions": {"series": [{"id": "FREQ", "values": coded("A")}],
                           "observation": [{"id": "TIME_PERIOD", "values": coded(year)}]},
            "attributes": {"observation": [
                {"id": "NOMENCODE", "values": coded("H6")}, {"id": "EXCLUDEDFROM", "values": []},
                {"id": "TARIFFTYPE", "values": coded(kind)}, {"id": "SUM_OF_RATES", "values": coded(avg)},
                {"id": "MIN_RATE", "values": coded(avg)}, {"id": "MAX_RATE", "values": coded(avg)},
                {"id": "TOTALNOOFLINES", "values": coded(lines)}, {"id": "NBR_PREF_LINES", "values": coded(pref)},
                {"id": "NBR_MFN_LINES", "values": coded(lines - pref)}, {"id": "NBR_NA_LINES", "values": coded(na)},
                {"id": "OBS_VALUE_MEASURE", "values": coded("SimpleAverage")}]}},
        "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [avg, 0, None, 0, 0, 0, 0, 0, 0, 0, 0, 0]}}}}],
    }


def fake_wits(table: dict, calls: list | None = None) -> httpx.MockTransport:
    """table: {(reporter, partner, year): json} — anything else answers 'no data' like WITS does."""
    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        key = (parts[parts.index("reporter") + 1], parts[parts.index("partner") + 1], int(parts[parts.index("year") + 1]))
        if calls is not None:
            calls.append(key)
        if key in table:
            return httpx.Response(200, json=table[key])
        return httpx.Response(404, text="No data")
    return httpx.MockTransport(handler)


EU_TABLE = {
    ("918", "000", 2024): wits_json(3.2, "MFN", lines=2),
    ("918", "818", 2024): wits_json(0, "PREF", lines=2, pref=2),
    ("918", "792", 2024): wits_json(1, "PREF", lines=2, pref=1),
}


# ------------------------------------------------------------------ router & money
def test_router_understands_rebate_and_buyer():
    i = router.parse("رد الأعباء كينيا")
    assert (i.kind, i.country) == ("rebate", "KE")
    assert router.parse("دعم التصدير").kind == "rebate"
    i = router.parse("جمارك المشتري 071333 إيطاليا")
    assert (i.kind, i.code, i.country) == ("buyer", "071333", "IT")
    i = router.parse("جمارك المشتري فول ابيض في ألمانيا")
    assert (i.kind, i.query, i.country) == ("buyer", "فول ابيض", "DE")
    assert router.parse("0713330090 250000").kind == "calc"           # nothing else changed
    assert router.parse("رد").kind == "search"


def test_parse_money():
    assert parse_money("50000$") == (50000, "USD")
    assert parse_money("50 الف دولار") == (50000, "USD")
    assert parse_money("٢٠٠٠ يورو") == (2000, "EUR")
    assert parse_money("15000") == (15000, None)
    assert parse_money("مفيش") is None


# ------------------------------------------------------------------ rebate rules
def test_rebate_target_market_by_sea():
    res = rb.compute(rb.Shipment(iso="KE", fob_egp=1_000_000, basic_rate=4, mode="sea", freight_egp=100_000))
    amounts = [ln.amount for ln in res.lines]
    assert amounts == [40_000, 10_000, 50_000]                    # basic, +25% target market, 50% sea freight
    assert res.total == 100_000 and res.target


def test_rebate_europe_sea_gets_no_freight_or_market_incentive():
    res = rb.compute(rb.Shipment(iso="IT", fob_egp=1_000_000, basic_rate=4, mode="sea", freight_egp=100_000))
    assert res.total == 40_000 and not res.target
    assert any("مش من الأسواق المستهدفة" in n for n in res.notes)
    assert res.lines[-1].amount == 0 and "أفريقيا" in res.lines[-1].note


def test_rebate_air_freight_europe_and_uk():
    it = rb.compute(rb.Shipment(iso="IT", fob_egp=100_000, mode="air", freight_egp=10_000))
    uk = rb.compute(rb.Shipment(iso="GB", fob_egp=100_000, mode="air", freight_egp=10_000))
    gulf = rb.compute(rb.Shipment(iso="SA", fob_egp=100_000, mode="air", freight_egp=10_000))
    assert it.lines[-1].amount == 2_500 and uk.lines[-1].amount == 5_000 and gulf.lines[-1].amount == 0


def test_rebate_caps_inland_and_handles_missing_basic_rate():
    res = rb.compute(rb.Shipment(iso="NG", fob_egp=1_000_000, inland_egp=2_000_000, insurance_egp=8_000))
    by = {ln.label: ln.amount for ln in res.lines}
    assert by["المساندة الأساسية"] is None                       # not entered yet
    assert by["النقل الداخلي (25%)"] == 250_000                  # capped
    assert by["التأمين على الشحنة (25% من البوليصة)"] == 2_000
    assert res.total == 252_000
    text = rb.card(rb.Shipment(iso="NG", fob_egp=1_000_000), rb.compute(rb.Shipment(iso="NG", fob_egp=1_000_000)))
    assert "❔" in text and "تقدير" in text


def test_rebate_regions():
    assert rb.is_target("KE") and rb.is_target("US") and rb.is_target("VN") and rb.is_target("BR")
    assert not rb.is_target("IT") and not rb.is_target("SA") and not rb.is_target("GB")
    assert rb.freight_share("ZA", "sea")[0] == 0.5 and rb.freight_share("US", "sea")[0] == 0


# ------------------------------------------------------------------ WITS
def test_wits_parses_a_real_answer():
    data = json.loads((DATA / "wits_us_mfn_2017.json").read_text())
    rates = wits.parse(data, "000")
    assert [r.avg for r in rates] == [0.0, 0.0, 6.0]
    assert all(r.kind == "MFN" and r.year == 2017 and r.lines == 1 for r in rates)
    assert wits.parse({}, "000") == [] and wits.parse({"structure": None}, "000") == []


def test_wits_reporters_and_url():
    assert wits.reporter_for("IT") == wits.reporter_for("EU") == "918"
    assert wits.reporter_for("KE") == "404" and wits.reporter_for("ZZ") is None
    assert wits.url("918", "818", "071333", 2024).endswith(
        "/reporter/918/partner/818/product/071333/year/2024/datatype/reported?format=JSON")


async def test_wits_compare_finds_latest_year_and_caches():
    kv: dict = {}
    calls: list = []
    src = wits.TariffSource(kv.get, kv.__setitem__, transport=fake_wits(EU_TABLE, calls), now=lambda: NOW)
    cmp = await src.compare("IT", "071333")
    assert cmp["year"] == 2024 and cmp["mfn"].avg == 3.2
    assert cmp["egypt"].avg == 0 and cmp["egypt"].kind == "PREF"
    assert cmp["rivals"]["TR"].avg == 1 and cmp["rivals"]["CN"] is None
    assert ("918", "000", 2025) in calls                          # tried the newer year first
    n = len(calls)
    again = await src.compare("IT", "071333")
    assert again["mfn"].avg == 3.2 and len(calls) == n             # all from the cache


async def test_wits_unknown_schedule_returns_none():
    kv: dict = {}
    src = wits.TariffSource(kv.get, kv.__setitem__, transport=fake_wits({}), now=lambda: NOW)
    assert await src.compare("IT", "071333") is None


def test_buyer_card_texts():
    m = wits.Rate("000", 2024, 3.2, "MFN", 2)
    e = wits.Rate("818", 2024, 0.0, "PREF", 2, 2)
    from customs_bot import agreements as A
    text = wits.card("IT", "071333", "فاصوليا", {"year": 2024, "reporter": "918", "mfn": m, "egypt": e,
                                                "rivals": {"CN": None, "TR": wits.Rate("792", 2024, 1.0, "PREF")}},
                     A.agreements_for("IT", direction="export"))
    assert "من مصر: 0%" in text and "ميزتك" in text and "320$" in text and "EUR.1" in text
    same = wits.card("NG", "071333", None, {"year": 2023, "reporter": "566", "mfn": wits.Rate("000", 2023, 5.0, "MFN"),
                                           "egypt": None, "rivals": {"CN": None}}, [])
    assert "الجمارك واحدة على الكل" in same
    assert "مش لاقي جدول" in wits.card("IT", "071333", None, None, [])


# ------------------------------------------------------------------ in the bot
async def test_buyer_duty_from_the_menu(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    svc.tariffs.transport = fake_wits(EU_TABLE)
    svc.tariffs.now = lambda: NOW
    await send(app, msg_update("🌍 جمارك المشتري"))
    assert "ابعت رقم البند" in tg.last_text()
    await send(app, msg_update("0713330090"))
    assert "أنهي بلد" in tg.last_text()
    await send(app, msg_update("إيطاليا"))
    t = tg.last_text()
    assert "جمارك المشتري" in t and "من مصر: 0%" in t and "3.2%" in t and "EUR.1" in t
    buttons = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "rb|c|IT" in buttons and "ox|0713330090|IT" in buttons


async def test_buyer_duty_one_line_and_from_export_card(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    svc.tariffs.transport = fake_wits(EU_TABLE)
    svc.tariffs.now = lambda: NOW
    await send(app, msg_update("جمارك المشتري 071333 ألمانيا"))
    assert "من مصر: 0%" in tg.last_text()
    await send(app, msg_update("تصدير 0713330090 إلى ألمانيا"))
    buttons = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "ob|0713330090|DE" in buttons and "rb|c|DE" in buttons
    await send(app, cb_update("ob|0713330090|DE"))
    assert "ميزتك" in tg.last_text()


async def test_buyer_duty_when_wits_is_down(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    svc.tariffs.transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))
    await send(app, msg_update("جمارك المشتري 071333 إيطاليا"))
    assert "مقدرتش أوصل" in tg.last_text()


async def test_rebate_wizard(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("/setfx USD 50"))
    await send(app, msg_update("💰 رد الأعباء"))
    assert "لأنهي بلد" in tg.last_text()
    await send(app, cb_update("rb|c|KE"))
    assert "سوق مستهدف ✅" in tg.last_text() and "FOB" in tg.last_text()
    await send(app, msg_update("20000$"))                          # 1,000,000 EGP
    assert "الشحن هيبقى إزاي" in tg.last_text()
    await send(app, cb_update("rb|m|sea"))
    await send(app, msg_update("2000$"))                           # 100,000 EGP -> 50,000
    t = tg.last_text()
    assert "رد الأعباء — شحنة لـ" in t and "50,000 ج" in t and "❔" in t   # basic rate not entered yet
    await send(app, cb_update("rb|e|basic"))
    await send(app, msg_update("4"))
    t = tg.last_text()
    assert "100,000 ج" in t and "10.0% من قيمة الشحنة" in t        # 40k + 10k + 50k
    await send(app, cb_update("rb|new"))                           # the rate is remembered for next time
    await send(app, cb_update("rb|c|KE"))
    await send(app, msg_update("1000000"))
    await send(app, cb_update("rb|m|none"))
    assert "(4% من القيمة)" in tg.last_text()


async def test_rebate_one_line_and_leaving_the_wizard(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("رد الأعباء المغرب"))
    assert "FOB" in tg.last_text()
    await send(app, msg_update("فاصوليا بيضاء جافة اللي بنصدرها"))    # a sentence, not a value: normal search
    assert "نتايج" in tg.last_text() and "اكتب رقم" not in tg.last_text()
    await send(app, msg_update("250000"))                           # no stale rebate question left
    assert "FOB" not in tg.last_text() and "الشحن هيبقى إزاي" not in tg.last_text()


async def test_wits_no_data_is_rechecked_sooner_and_5xx_is_an_error():
    kv: dict = {}
    t = [NOW]
    calls: list = []
    src = wits.TariffSource(kv.get, kv.__setitem__, transport=fake_wits(EU_TABLE, calls), now=lambda: t[0])
    async with httpx.AsyncClient(transport=src.transport) as client:
        assert await src._get(client, "918", "156", "071333", 2024) is None      # no agreement -> no rows
        n = len(calls)
        assert await src._get(client, "918", "156", "071333", 2024) is None and len(calls) == n   # cached
        t[0] += 8 * 86400
        await src._get(client, "918", "156", "071333", 2024)
        assert len(calls) == n + 1                                               # asked again after a week
    down = wits.TariffSource(kv.get, kv.__setitem__, transport=httpx.MockTransport(lambda r: httpx.Response(503)),
                             now=lambda: NOW)
    with pytest.raises(httpx.HTTPStatusError):
        await down.compare("KE", "100790")
