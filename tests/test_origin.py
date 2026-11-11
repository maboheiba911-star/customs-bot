"""Origin & agreements: real customs.gov.eg import-view data (tests/fixtures/origin_samples.json)."""
import json
from pathlib import Path

import httpx
import pytest
from test_bot_e2e import app_and_tg, cb_update, msg_update, send  # noqa: F401

from customs_bot import agreements as A
from customs_bot import origin as O
from customs_bot.builder import split_instruction
from customs_bot.refdb import ReferenceDB
from customs_bot.router import parse
from customs_bot.taxparse import parse_taxes

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "origin_samples.json").read_text(encoding="utf-8"))


def instructions(code):
    return [dict(zip(("code", "text"), split_instruction(SAMPLES["ins"][k], None))) for k in SAMPLES["lines"][code]["i"]]


def analysis(code, value=100_000):
    parsed = parse_taxes(SAMPLES["lines"][code]["t"])
    return O.price(O.analyze(parsed, instructions(code), live=True), parsed, value, code=code)


def by(an, key, countries=()):
    return next(o for o in an.options if o.agreement == key and o.countries == countries)


# ------------------------------------------------------------------ translator
def tr(i):
    code, body = split_instruction(SAMPLES["ins"][i], None)
    return O.translate(code, body)


def test_translator_reads_the_official_codes():
    eu_ind, uk_ind, efta, turkey = tr(35), tr(36), tr(34), tr(33)
    assert (eu_ind.agreement, eu_ind.kind) == ("eu", "exempt")
    assert (uk_ind.agreement, uk_ind.kind) == ("uk", "exempt")          # UK checked before the EU
    assert (efta.agreement, efta.kind) == ("efta", "exempt")
    assert (turkey.agreement, turkey.kind) == ("tr", "exempt")
    assert (tr(21).agreement, tr(21).kind) == ("eu", "excluded")
    assert (tr(22).agreement, tr(22).kind) == ("uk", "excluded")        # "استثاء" typo in the source
    assert (tr(28).kind, tr(29).kind) == ("rates", "rates")             # per-line rates in Taxes
    t6606 = tr(26)
    assert (t6606.agreement, t6606.kind, t6606.quota) == ("tr", "reduce_unknown", True)


def test_translator_countries_groups_and_percentages():
    iceland_norway = tr(16)
    assert iceland_norway.countries == ("IS", "NO") and iceland_norway.quota
    assert tr(57).countries == ("AR",) and tr(58).countries == ("BR", "UY")
    brazil = tr(27)
    assert (brazil.kind, brazil.pct, brazil.countries) == ("reduce", 30.0, ("BR",))
    assert (tr(25).kind, tr(25).group) == ("reduce_unknown", "هـ")
    assert (tr(5).group, tr(5).kind) == ("أ", "exempt") and (tr(6).group, tr(6).pct) == ("ب", 60.0)
    assert (tr(39).group, tr(40).group) == ("أ", "ب")
    assert (tr(15).agreement, tr(15).pct, tr(15).group) == ("serbia", 50.0, "2")
    assert (tr(53).agreement, tr(53).kind) == ("mercosur", "reduce_unknown")   # ر6712 names no agreement
    assert (tr(48).agreement, tr(48).countries, tr(48).kind) == ("comesa", ("SD",), "excluded")


def test_translator_bilateral_conditions_and_measures():
    assert (tr(0).agreement, tr(0).kind) == ("tn", "excluded")
    assert (tr(31).agreement, tr(31).kind) == ("jo", "excluded")
    assert (tr(32).agreement, tr(32).kind) == ("ly", "conditional")
    assert (tr(41).agreement, tr(41).kind) == ("tn", "conditional")
    assert (tr(7).agreement, tr(10).agreement, tr(43).agreement, tr(13).agreement) == ("sd", "ma", "sy", "ps")
    assert tr(8).kind == "note" and tr(17).kind == "note"            # import approvals, not duties
    assert (tr(12).kind, tr(12).countries) == ("origin_check", ("JP",))
    assert (tr(56).kind, tr(56).countries) == ("antidumping", ("CN",))
    assert (tr(54).kind, tr(54).pct, tr(55).pct) == ("safeguard", 13.0, 13.5)
    assert tr(52).kind == "safeguard" and tr(44).kind == "shipping"
    assert (tr(49).kind, tr(49).countries) == ("origin_measure", ("CN",))


def test_translator_ignores_unrelated_instructions():
    for i in (11, 18, 30, 45, 47, 50, 51, 59, 60, 61):     # free zone, VAT, alcohol, specs, admin notes ...
        assert tr(i) is None, SAMPLES["ins"][i]


# ------------------------------------------------------------------ ranking
def test_biscuits_ranking_matches_the_calculator():
    an = analysis("1905320000")
    assert an.general_total == 69_000
    assert by(an, "eu").total == 21_750 and by(an, "uk").total == 21_750
    turkey = by(an, "tr")
    assert turkey.total == 32_775 and turkey.quota
    assert by(an, "serbia").total == 37_500
    assert by(an, "mercosur", ("BR",)).total == 50_100                 # 30% off the 60% duty
    assert by(an, "mercosur").status == "unknown"                      # list هـ, no percentage
    assert by(an, "tn").status == "excluded"
    assert by(an, "gafta").status == "rule"
    best = an.best_for("DE")
    assert best.agreement == "eu" and best.total == 21_750
    assert an.best_for("CN") is None
    assert [iso for _, iso in O.rank_targets(an)] == ["EU", "GB", "TR", "RS", "BR"]


def test_exclusions_split_members_and_worse_rates():
    an = analysis("5210110000")
    comesa = [o for o in an.options if o.agreement == "comesa"]
    assert {(o.status, o.countries, o.excluding) for o in comesa} == {
        ("excluded", ("SD",), ()), ("rule", (), ("SD",))}
    assert an.best_for("KE").agreement == "comesa"
    iron = analysis("2601200000")                     # AfCFTA B 0.8% on a 0% line
    worse = [o for o in iron.options if o.worse]
    assert worse and worse[0].agreement == "afcfta"
    eu_excluded = analysis("1602100050")
    assert by(eu_excluded, "eu").status == "excluded" and eu_excluded.best_for("FR") is None


def test_local_copy_without_instructions_is_flagged():
    parsed = parse_taxes(SAMPLES["lines"]["1905320000"]["t"])
    an = O.price(O.analyze(parsed, [], live=False), parsed, 100_000)
    assert by(an, "eu").total == 21_750                                 # explicit section still works
    assert by(an, "efta").status == "unknown"
    text = O.rank_text(type("I", (), {"code": "1905320000", "desc_ar": "بسكويت"})(), an, source="src")
    assert "الموقع مش متاح دلوقتي" in text


# ------------------------------------------------------------------ export side
def test_export_notes_from_the_local_tariff():
    ref = ReferenceDB(ROOT / "data" / "reference.db")
    beans = ref.get("0713330090")
    instr = ref.instructions(beans.export_codes)
    turkey = O.export_card(beans, instr, "src", "TR")
    assert "مستثناة من الإعفاء" in turkey and "غ6502" in turkey and "no tariff preference" in turkey
    germany = O.export_card(beans, instr, "src", "DE")
    assert "EUR.1" in germany and "المصدّر المعتمد" in germany and "مستثناة" not in germany
    usa = O.export_card(beans, instr, "src", "US")
    assert "مفيش اتفاقية تفضيلية" in usa and "الغرفة التجارية" in usa
    sudan = O.export_card(beans, instr, "src", "SD")
    assert "شهادة منشأ الكوميسا" in sudan and "شهادة منشأ عربية" in sudan
    assert "ق4560" in turkey                                            # Egyptian export requirement


def test_export_translator():
    n = O.translate_export("غ6510", "فى اطارسوق مشتركة للشرق والجنوب الأفريقى الكوميسا اعفاء صادرات مصر الى السودان من الرسوم")
    assert (n.agreement, n.kind, n.countries) == ("comesa", "exempt", ("SD",))
    n = O.translate_export("غ6504", "يستثنى من الاعفاء الكامل واردات الجماعة الأوروبية المنتجات المصرية من المنتجات الزراعية")
    assert (n.agreement, n.kind) == ("eu", "partial")
    n = O.translate_export("غ1077", "عدم السماح بتصدير اية شحنات من  الصنف إلى ليبيا كى لاتتعرض للمصادرة")
    assert (n.kind, n.countries) == ("banned", ("LY",))
    assert O.translate_export("ق4560", "عرض الصنف على الهيئة القومية لسلامة الغذاء  [م 4590 ] عند التصدير") is None


# ------------------------------------------------------------------ data + router
def test_countries_and_agreements_lookup():
    assert A.resolve_country("المانيا") == "DE" and A.resolve_country("للسعودية") == "SA"
    assert A.resolve_country("المملكة المتحدة") == "GB" and A.resolve_country("اوروبا") == "EU"
    assert A.resolve_country("قهوة") is None
    assert {a.key for a in A.agreements_for("MA", direction="import")} >= {"gafta", "agadir", "ma"}
    assert [a.key for a in A.agreements_for("CN", direction="export")] == ["china0"]
    assert A.agreements_for("CN", direction="import") == []
    assert A.resolve_agreement("الكوميسا").key == "comesa"


@pytest.mark.parametrize("text,kind,code,query,country", [
    ("منشأ 1905320000", "origin", "1905320000", "", None),
    ("استيراد 1905320000 من الصين", "import_from", "1905320000", "", "CN"),
    ("استيراد جوز الهند من سريلانكا", "import_from", "", "جوز الهند", "LK"),
    ("تصدير 0713330090 إلى ألمانيا", "export_to", "0713330090", "", "DE"),
    ("تصدير فاصوليا لتركيا", "export_to", "", "فاصوليا", "TR"),
    ("تصدير جوز الهند", "search", "", "جوز الهند", None),            # a product name, not a destination
    ("تصدير قهوة تركي", "search", "", "قهوة تركي", None),
    ("اتفاقيات السعودية", "agreement", "", "السعودية", None),
    ("1905320000 100000 من المانيا", "calc", "1905320000", "", "DE"),
])
def test_router_origin_commands(text, kind, code, query, country):
    i = parse(text)
    assert (i.kind, i.code, i.query, i.country) == (kind, code, query, country)


# ------------------------------------------------------------------ end to end
def _live(app, handler):
    s = app.bot_data["svc"]
    s.s.live_lookup = True
    s.live.enabled = True
    s.live._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return s


def _biscuits(request: httpx.Request):
    assert request.url.params["trfNumber"] == "19/05/32/00/00"
    ins = [SAMPLES["ins"][k] for k in SAMPLES["lines"]["1905320000"]["i"]]
    return httpx.Response(200, json={"Number": "19/05/32/00/00", "Desc": "بسكويت محلى", "ShortDesc": "بسكويت",
                                     "Taxes": SAMPLES["lines"]["1905320000"]["t"], "Instructions": ins,
                                     "InstructionCodes": []})


async def test_menu_button_and_best_origin_flow(app_and_tg):
    app, tg = app_and_tg
    _live(app, _biscuits)
    await send(app, msg_update("/start"))
    assert any(b["text"] == "🌍 الاتفاقيات والمنشأ" for row in tg.last_markup()["keyboard"] for b in row)
    await send(app, msg_update("🌍 الاتفاقيات والمنشأ"))
    assert "استيراد لمصر" in tg.last_text()
    await send(app, cb_update("oo|i"))
    await send(app, msg_update("1905320000"))
    t = tg.last_text()
    assert "أفضل منشأ" in t and "21,750 ج" in t and "توفير 47,250" in t and "(مباشر)" in t
    kb = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "oc|1905320000|EU" in kb and "oq|i|1905320000" in kb
    await send(app, cb_update("oc|1905320000|EU"))
    t = tg.last_text()
    assert "EUR.1" in t and "21,750 ج" in t and "69,000 ج (السعر العام)" in t
    await send(app, cb_update("oq|i|1905320000"))
    await send(app, msg_update("تونس"))
    t = tg.last_text()
    assert "تونس" in t and "مستثنى" in t and "اتأكد إن القاعدة العامة بتنطبق" in t


async def test_one_line_commands_and_calc_with_country(app_and_tg):
    app, tg = app_and_tg
    _live(app, _biscuits)
    await send(app, msg_update("استيراد 1905320000 من الصين 200000"))
    t = tg.last_text()
    assert "مفيش اتفاقية تجارة تفضيلية" in t and "138,000 ج" in t
    await send(app, msg_update("1905320000 100000 من المانيا"))
    t = tg.last_text()
    assert "21,750 ج" in t and "EUR.1" in t
    await send(app, msg_update("1905320000 100000 اوروبا"))
    assert "21,750 ج" in tg.last_text()
    await send(app, msg_update("منشأ 1905320000 5000$ @50"))
    assert "250,000 ج" in tg.last_text()


async def test_export_to_country_flow(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("تصدير 0713330090 لتركيا"))
    t = tg.last_text()
    assert "تركيا" in t and "مستثناة من الإعفاء" in t and "هيئة الرقابة على الصادرات والواردات" in t
    await send(app, msg_update("0713330090"))
    await send(app, cb_update("od|0713330090"))
    await send(app, cb_update("oe|0713330090"))
    assert "لأنهي بلد" in tg.last_text()
    await send(app, msg_update("المملكه المتحده"))
    assert "بريطانيا" in tg.last_text() and "EUR.1" in tg.last_text()
    await send(app, msg_update("تصدير فاصوليا للسعودية"))
    kb = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "ox|0713330090|SA" in kb
    await send(app, cb_update("ox|0713330090|SA"))
    assert "شهادة منشأ عربية" in tg.last_text()


async def test_agreements_library_and_cards(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("اتفاقيات"))
    kb = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "oa|eu" in kb and "oa|comesa" in kb
    await send(app, cb_update("oa|agadir"))
    assert "أغادير" in tg.last_text() and "المصدّر المعتمد" in tg.last_text()
    await send(app, msg_update("اتفاقيات السعودية"))
    assert "العربية الكبرى" in tg.last_text()
    await send(app, msg_update("/agreements الكوميسا"))
    assert "الكوميسا" in tg.last_text()


async def test_unknown_country_keeps_the_question_and_bad_callbacks_are_ignored(app_and_tg):
    app, tg = app_and_tg
    await send(app, cb_update("oq|e|0713330090"))
    await send(app, msg_update("بلاد الواق واق"))
    assert "مش لاقي بلد" in tg.last_text()
    n = len(tg.texts())
    await send(app, cb_update("oc|0713330090|XX"))                     # not a country we know
    assert len(tg.texts()) == n
    await send(app, msg_update("0713330090"))                           # a code ends the question
    assert "البند 0713330090" in tg.last_text()
