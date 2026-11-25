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


GULF = ("SA", "AE", "KW", "QA", "OM", "BH")
NEW_AFRICA = ("ZA", "DZ", "TN", "LY")


def test_every_country_rule_has_a_source_and_a_link():
    for iso, rules in shipfile.COUNTRY_RULES.items():
        for r in rules:
            assert r.source, (iso, r.text)
            if iso not in ("KE", "NG", "MA"):                      # first batch: some rules cite reports only
                assert r.url.startswith("https://"), (iso, r.text)
    for iso in GULF + NEW_AFRICA:
        items, reviewed = shipfile.destination_items("0713330090", iso)
        assert reviewed and items and shipfile.reviewed_on(iso) == shipfile.SECOND_REVIEW
    assert shipfile.reviewed_on("IT") == shipfile.reviewed_on("KE") == shipfile.FIRST_REVIEW
    assert shipfile.reviewed_on("GH") == ""


def test_gulf_rules():
    sa = texts(shipfile.build_items("0713330090", "SA", [], []), "dest")
    assert any("CoC" in t and "SFDA" in t for t in sa) and any("بالتات" in t for t in sa)
    assert any("صحة نباتية" in t for t in sa) and any("Batch" in t for t in sa) and any("فسح" in t for t in sa)
    sa_machine = texts(shipfile.build_items("8471300000", "SA", [], []), "dest")
    assert not any("CoC" in t or "صحة نباتية" in t for t in sa_machine)   # SFDA CoC and phyto: plant products only
    assert any("بالتات" in t for t in sa_machine)                       # pallets: every container
    ae = texts(shipfile.build_items("0909610000", "AE", [], []), "dest")
    assert any("eDAS" in t and "10,000" in t for t in ae) and any("ZAD" in t for t in ae)
    assert any("GSO 841" in t for t in ae) and any("صحة نباتية" in t for t in ae)
    kw = texts(shipfile.build_items("0713330090", "KW", [], []), "dest")
    assert any("الغرفة التجارية" in t for t in kw) and any("بالعربي" in t for t in kw)
    assert any("WATHEQ" in t for t in texts(shipfile.build_items("0713330090", "QA", [], []), "dest"))
    assert any("بيان" in t for t in texts(shipfile.build_items("0713330090", "OM", [], []), "dest"))
    for iso in GULF:
        beans = texts(shipfile.build_items("0713330090", iso, [], []), "dest")
        jam = texts(shipfile.build_items("2007999000", iso, [], []), "dest")
        assert any("صحة نباتية" in t for t in beans) and not any("صحة نباتية" in t for t in jam), iso
        if iso != "SA":
            assert any("شهادة صحية" in t for t in jam) and not any("شهادة صحية" in t for t in beans), iso


def test_south_and_north_africa_rules():
    za_beans = texts(shipfile.build_items("0713330090", "ZA", [], []), "dest")
    assert any("تصريح استيراد" in t for t in za_beans) and any("119" in t for t in za_beans)
    assert any("R146" in t for t in za_beans)
    za_anise = texts(shipfile.build_items("0909610000", "ZA", [], []), "dest")
    assert any("تصريح استيراد" in t for t in za_anise) and not any("119" in t for t in za_anise)
    dz_sorghum = texts(shipfile.build_items("1007900000", "DZ", [], []), "dest")
    assert any("Non-GMO" in t for t in dz_sorghum) and any("تبخير" in t and "الإشعاع" in t for t in dz_sorghum)
    assert any("120%" in t for t in dz_sorghum) and any("PPI" in t for t in dz_sorghum)
    dz_anise = texts(shipfile.build_items("0909610000", "DZ", [], []), "dest")
    assert not any("تبخير" in t for t in dz_anise)                     # grain certificates: grains only
    tn = texts(shipfile.build_items("0713400000", "TN", [], []), "dest")
    assert any("صحة نباتية" in t for t in tn) and any("البوليصة" in t for t in tn)
    ly_sorghum = texts(shipfile.build_items("1007900000", "LY", [], []), "dest")
    assert any("وزارة الاقتصاد" in t for t in ly_sorghum) and any("معدّلة وراثياً" in t for t in ly_sorghum)
    assert any("CoI" in t for t in ly_sorghum)
    ly_beans = texts(shipfile.build_items("0713330090", "LY", [], []), "dest")
    assert not any("معدّلة وراثياً" in t for t in ly_beans)


def test_share_page_says_when_rules_were_reviewed():
    sh = {"id": 1, "title": "فاصوليا", "code": "0713330090", "iso": "SA", "buyer_name": ""}
    items = [{"section": "dest", "text": "x", "source": "s", "url": "https://example.org", "done": 0}]
    assert shipfile.SECOND_REVIEW in shipfile.html_doc(sh, items).decode()
    assert shipfile.FIRST_REVIEW in shipfile.html_doc({**sh, "iso": "DE"}, items).decode()
    assert "لسه متراجعتش" in shipfile.html_doc({**sh, "iso": "GH"}, items).decode()


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
