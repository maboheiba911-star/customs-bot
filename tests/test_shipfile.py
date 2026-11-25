"""Shipment file (ملف الشحنة): rules, storage and the bot flow."""
import re

from test_bot_e2e import USER, app_and_tg, cb_update, msg_update, send  # noqa: F401  (fixture)

from customs_bot import router, shipfile


def texts(items, section=None):
    return [t for sec, t, _, _ in items if section is None or sec == section]


def test_eu_anise_gets_cn_phyto_mrl_and_pa_rules():
    items = shipfile.build_items("0909610000", "IT", [], ["coa", "pa"])
    eg, dest, buyer = texts(items, "eg"), texts(items, "dest"), texts(items, "buyer")
    assert any("EUR.1" in t for t in eg)                          # certificate of origin from the agreement
    assert any("0909 61 00" in t for t in dest)                    # EU 8-digit code for the invoice
    assert any("Phytosanitary" in t for t in dest) and any("MRL" in t for t in dest)
    assert any("PAs" in t and "400" in t for t in dest)
    assert buyer == [shipfile.TEMPLATE_TEXT["coa"], shipfile.TEMPLATE_TEXT["pa"]]
    assert all(src for sec, _, src, _ in items if sec == "dest")   # every destination rule has a source


def test_rules_only_where_they_apply():
    beans_eu = texts(shipfile.build_items("0713330090", "DE", [], []), "dest")
    assert not any("PAs" in t for t in beans_eu)                  # PAs are for herbs, not beans
    machine_eu = texts(shipfile.build_items("8471300000", "DE", [], []), "dest")
    assert not any("Phytosanitary" in t for t in machine_eu)       # plant rules only for plant products
    oranges = texts(shipfile.build_items("0805100010", "NL", [], []), "dest")
    assert any("البرتقال" in t and "رقابة مشددة" in t for t in oranges)


def test_africa_rules_and_unknown_country():
    ke = texts(shipfile.build_items("0713330090", "KE", [], []), "dest")
    assert any("PVoC" in t for t in ke) and any("KEPHIS" in t for t in ke) and any("IDF" in t for t in ke)
    ng = texts(shipfile.build_items("1007900000", "NG", [], []), "dest")
    assert any("Form M" in t for t in ng) and any("NAQS" in t for t in ng)
    ma = texts(shipfile.build_items("1007900000", "MA", [], []), "dest")
    assert any("ONSSA" in t for t in ma)
    items, reviewed = shipfile.destination_items("0713330090", "GH")
    assert not reviewed and "لسه مراجعتش" in items[0][0] and items[0][2] == shipfile.GTH


def test_egypt_side_takes_customs_export_instructions():
    instr = [{"code": "غ1001", "text": "يخضع للفحص من الهيئة العامة للرقابة على الصادرات والواردات"},
             {"code": "غ1001", "text": "يخضع للفحص من الهيئة العامة للرقابة على الصادرات والواردات"}]
    eg = texts(shipfile.build_items("0713330090", "SA", instr, []), "eg")
    assert sum("الرقابة على الصادرات" in t for t in eg) == 1        # duplicates dropped
    assert any("شهادة المنشأ" in t for t in eg)


def test_router_ship_words():
    assert router.parse("ملف شحنة").kind == "ship" and router.parse("/ship").kind == "ship"
    assert router.parse("شحناتي").kind == "ships"
    assert router.parse("ملف شحنة فول").kind != "ship"            # a longer sentence is a search


def _sid(text):
    return int(re.search(r"ملف شحنة #(\d+)", text).group(1))


async def test_shipment_file_flow(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("📋 ملف شحنة"))
    assert "ابعت رقم البند" in tg.last_text()
    await send(app, msg_update("0909610000"))
    assert "لأنهي بلد" in tg.last_text()
    await send(app, cb_update("sc|IT"))
    assert "عميل جديد" in str(tg.last_markup())
    await send(app, cb_update("sb|new"))
    await send(app, msg_update("Rossi Spezie"))
    assert "متطلبات العميل" in tg.last_text()
    await send(app, cb_update("sr|coa"))
    await send(app, cb_update("sr|pa"))
    await send(app, cb_update("sr|text"))
    await send(app, msg_update("رطوبة أقل من 10%"))
    assert "رطوبة أقل من 10%" in tg.last_text()
    await send(app, cb_update("sr|save"))
    t = tg.last_text()
    sid = _sid(t)
    assert "Rossi Spezie" in t and "0909 61 00" in t and "PAs" in t and "رطوبة أقل من 10%" in t
    assert "التقدم: <b>0/" in t
    await send(app, cb_update(f"st|{sid}|0"))
    assert "✅ 1." in tg.last_text() and "التقدم: <b>1/" in tg.last_text()
    await send(app, cb_update(f"sd|{sid}"))
    name, body, _ = tg.documents[-1]["document"]
    assert name == f"shipment-{sid}.html" and "ملف شحنة #" in body.decode() and "eur-lex" in body.decode()
    # the saved buyer comes back with its requirements
    await send(app, msg_update("ملف شحنة"))
    await send(app, msg_update("0713330090"))
    await send(app, msg_update("كينيا"))
    markup = str(tg.last_markup())
    bid = re.search(r"sb\|(\d+)", markup).group(1)
    await send(app, cb_update(f"sb|{bid}"))
    t2 = tg.last_text()
    assert "PVoC" in t2 and shipfile.TEMPLATE_TEXT["coa"] in t2 and _sid(t2) == sid + 1
    await send(app, msg_update("🗂️ شحناتي"))
    assert f"#{sid}" in tg.last_text() and "1/" in tg.last_text()
    await send(app, cb_update(f"sz|{sid}"))
    assert "اتقفلت" in tg.last_text() and f"#{sid} " not in tg.last_text().split("\n", 2)[-1]


async def test_ship_without_buyer_and_strangers_cannot_open(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("/ship"))
    await send(app, msg_update("فاصوليا بيضاء"))
    await send(app, cb_update("sp|0713330090"))
    await send(app, cb_update("sc|NG"))
    await send(app, cb_update("sb|0"))
    t = tg.last_text()
    sid = _sid(t)
    assert "Form M" in t and "🤝" not in t.split("بلد المشتري")[-1]
    other = {"id": 3003, "is_bot": False, "first_name": "X"}
    app.bot_data["svc"].set_allowed(3003, True)
    await send(app, cb_update(f"so|{sid}", user=other))
    assert "مش موجودة" in tg.last_text()
