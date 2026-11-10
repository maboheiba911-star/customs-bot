"""End-to-end: real python-telegram-bot Application, fake Telegram HTTP layer, fake customs.gov.eg."""
import json

import httpx
import pytest
from conftest import OFFICIAL_SAMPLES
from telegram import Update
from telegram.request import BaseRequest

import bot as bot_module
from customs_bot import handlers
from customs_bot.official import parse_codes, slashed

pytestmark = pytest.mark.asyncio

USER = {"id": 1001, "is_bot": False, "first_name": "أبو هيبة", "username": "abuheiba"}
OTHER = {"id": 2002, "is_bot": False, "first_name": "Guest"}
CHAT = {"id": 1001, "type": "private"}


class FakeTelegram(BaseRequest):
    """Answers Bot API calls locally and records what the bot sent."""

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self._mid = 100
        self.files: dict[str, bytes] = {}

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def read_timeout(self):
        return 5.0

    async def do_request(self, url, method, request_data=None, read_timeout=None, write_timeout=None,
                         connect_timeout=None, pool_timeout=None):
        if "/file/bot" in url:                                   # file download
            return 200, self.files[url.split("/file/bot", 1)[1].split("/", 1)[1]]
        endpoint = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.sent.append((endpoint, params))
        if endpoint == "getFile":
            fid = params["file_id"]
            result = {"file_id": fid, "file_unique_id": "u" + fid, "file_size": len(self.files[fid]),
                      "file_path": fid}
        elif endpoint == "getMe":
            result = {"id": 999, "is_bot": True, "first_name": "Customs", "username": "customs_test_bot",
                      "can_join_groups": True, "can_read_all_group_messages": False,
                      "supports_inline_queries": False}
        elif endpoint in ("sendMessage", "editMessageText"):
            self._mid += 1
            result = {"message_id": self._mid, "date": 0, "chat": {"id": int(params.get("chat_id", 1001)),
                      "type": "private"}, "text": params.get("text", "")}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    # helpers for assertions
    def texts(self):
        return [p.get("text", "") for e, p in self.sent if e in ("sendMessage", "editMessageText")]

    def last_text(self):
        return self.texts()[-1]

    def last_markup(self):
        for e, p in reversed(self.sent):
            if e in ("sendMessage", "editMessageText"):
                rm = p.get("reply_markup")
                return json.loads(rm) if isinstance(rm, str) else rm
        return None


_uid = [0]


def msg_update(text, user=USER):
    _uid[0] += 1
    m = {"message_id": _uid[0], "date": 0, "chat": {"id": user["id"], "type": "private"}, "from": user, "text": text}
    if text.startswith("/"):
        m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": _uid[0], "message": m}


def cb_update(data, user=USER):
    _uid[0] += 1
    return {"update_id": _uid[0], "callback_query": {
        "id": f"cb{_uid[0]}", "from": user, "chat_instance": "ci", "data": data,
        "message": {"message_id": 50, "date": 0, "chat": {"id": user["id"], "type": "private"}, "text": "x"}}}


@pytest.fixture()
async def app_and_tg(settings):
    tg = FakeTelegram()
    from telegram.ext import Application
    b = Application.builder().token(settings.token).request(tg).get_updates_request(FakeTelegram())
    app = bot_module.build_application(settings, builder=b)
    handlers._refused_at.clear()
    handlers._hits.clear()
    app.bot_data["svc"].fx_transport = httpx.MockTransport(lambda r: httpx.Response(503))   # CBE "down"
    app.bot_data["svc"].customs_fx_transport = httpx.MockTransport(lambda r: httpx.Response(503))
    await app.initialize()
    yield app, tg
    await app.shutdown()


async def send(app, upd):
    await app.process_update(Update.de_json(upd, app.bot))


async def test_start_and_help(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("/start"))
    assert "أهلاً" in tg.last_text()
    assert tg.last_markup()["keyboard"][0][0]["text"] == handlers.BTN_SEARCH
    await send(app, msg_update("/help"))
    assert "/setfx" in tg.last_text()                       # admin sees admin commands
    await send(app, msg_update("/help", user=OTHER))
    assert "/setfx" not in tg.last_text()


async def test_search_details_calc_flow(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("فاصوليا بيضاء"))
    t = tg.last_text()
    assert "0713330090" in t and "وارد 2%" in t
    buttons = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "d|0713330090" in buttons
    await send(app, cb_update("d|0713330090"))
    t = tg.last_text()
    assert "البند 0713330090" in t and "ضريبة الوارد: <b>2%</b>" in t and "Phaseolus" in t
    assert "nafeza.gov.eg" in t and "0713330090 100000" in t
    await send(app, cb_update("k|0713330090"))
    assert "ابعت القيمة" in tg.last_text()
    await send(app, msg_update("250000"))
    t = tg.last_text()
    assert "حساب جمارك" in t and "250,000" in t and "5,000 ج" in t      # 2% duty


async def test_captain_emad_example_message(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("846595. 1522500"))
    t = tg.last_text()
    for expected in ("8465950000", "1,522,500", "213,150", "15,225", "228,375", "1,750,875"):
        assert expected in t, expected


async def test_currency_needs_rate_then_admin_sets_it(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("8465950000 1000$"))
    assert "مش قادر أجيب سعر USD" in tg.last_text()
    await send(app, msg_update("/setfx USD 50", user=OTHER))
    assert "خاص" in tg.last_text()                            # private bot: strangers are refused
    await send(app, msg_update("/setfx USD 50"))
    assert "اتسجل" in tg.last_text()
    await send(app, msg_update("8465950000 1000$"))
    t = tg.last_text()
    assert "50,000" in t and "1,000 USD × 50" in t


async def test_prefix_parent_and_choose_line(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("0713"))
    assert "بنود تبدأ بـ 0713" in tg.last_text()
    await send(app, msg_update("1509300000"))
    assert "عنوان فرعي" in tg.last_text() and "1509301000" in tg.last_text()
    await send(app, msg_update("071333 100000"))                 # 4 lines under 071333 -> choose
    assert "اختار" in tg.last_text()
    await send(app, cb_update("cv|0713330090"))
    assert "حساب جمارك — البند 0713330090" in tg.last_text()


async def test_gs1_flows(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("10007820"))
    t = tg.last_text()
    assert "Blessed Milkthistleplants" in t and "Edible Herbs" in t
    await send(app, msg_update("gs1 بابونج"))
    t = tg.last_text()
    assert "chamomile" in t.lower()
    await send(app, msg_update(handlers.BTN_GS1))
    await send(app, msg_update("white beans"))
    assert "GS1" in tg.last_text() and "Beans" in tg.last_text()
    await send(app, cb_update("g|0713330090"))
    assert "Brick" in tg.last_text()


async def test_devfee_admin_and_calc(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("/devfee 870323 3"))
    assert "رسم تنمية 3%" in tg.last_text()
    await send(app, msg_update("8703233090 500000"))
    t = tg.last_text()
    assert "رسم التنمية 3% = <b>21,000 ج</b>" in t


async def test_live_lookup_overrides_local(app_and_tg, settings):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    svc.s.live_lookup = True
    svc.live.enabled = True
    changed = dict(OFFICIAL_SAMPLES["0713330090"])
    changed["Taxes"] = ["ضريبة الوارد :  5%", "ضريبة قيمه مضافه :  صفر"]     # pretend the rate changed today

    def handler(request: httpx.Request):
        assert request.url.path == "/Services/TrfDetails"
        assert request.url.params["trfNumber"] == "07/13/33/00/90"
        return httpx.Response(200, json=changed)

    svc.live._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await send(app, msg_update("0713330090 100000"))
    t = tg.last_text()
    assert "5,000 ج" in t and "(مباشر)" in t
    # cached: works even if the site is now down
    svc.live._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    await send(app, msg_update("0713330090 100000"))
    assert "5,000 ج" in tg.last_text()


async def test_live_lookup_failure_falls_back_to_local(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    svc.s.live_lookup = True
    svc.live.enabled = True
    calls = []

    def down(request):
        calls.append(request.url.path)
        return httpx.Response(503)

    svc.live._client = httpx.AsyncClient(transport=httpx.MockTransport(down))
    for _ in range(3):
        await send(app, msg_update("8465950000 1522500"))
        assert "228,375" in tg.last_text() and "نسخة محلية" in tg.last_text()
    assert len(calls) == 3
    assert not svc.live.available                               # circuit breaker opened
    await send(app, msg_update("8465950000 1522500"))
    assert len(calls) == 3                                      # no more calls while paused


async def test_admin_update_swaps_database(app_and_tg, monkeypatch):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    old_n = svc.ref.counts()["tariff"]

    async def fake_crawl(*a, **k):
        recs = []
        for code, duty in (("0713330090", "7%"), ("8465950000", "صفر")):
            r = dict(OFFICIAL_SAMPLES[code])
            r.setdefault("Desc", "بند تجريبي")
            r["Taxes"] = [f"ضريبة الوارد :  {duty}"]
            recs.append(r)
        return recs

    monkeypatch.setattr(handlers, "crawl_tariff", fake_crawl)
    summary = await handlers.run_update(app, None)
    assert "تم تحديث" in summary and "اتغيرت فئاتها: 1" in summary
    assert svc.ref.counts()["tariff"] == 2 and old_n > 8000
    assert svc.ref.get("0713330090").duty_text == "7%"


async def test_crawler_against_fake_site():
    from customs_bot.official import crawl_tariff

    pages = {
        (7, 1): "<a onclick=\"ShowTariffDetails('07/13/33/00/90')\">x</a><a href='?page=2'>2</a>",
        (7, 2): "<a onclick=\"ShowTariffDetails('07/13/33/00/30')\">x</a>",
    }

    def handler(request: httpx.Request):
        if request.url.path == "/Services/Tarif":
            ch, pg = int(request.url.params["chapterId"]), int(request.url.params["page"])
            return httpx.Response(200, text=pages.get((ch, pg), "<table></table>"))
        code = request.url.params["trfNumber"]
        return httpx.Response(200, json={"Number": code, "Desc": "x", "Taxes": ["ضريبة الوارد :  2%"]})

    recs = await crawl_tariff("https://customs.gov.eg", chapters=range(6, 9), throttle=0, min_codes=1,
                              transport=httpx.MockTransport(handler))
    assert sorted(r["Number"] for r in recs) == ["07/13/33/00/30", "07/13/33/00/90"]
    assert slashed("0713330090") == "07/13/33/00/90"
    assert parse_codes(pages[(7, 1)]) == ["07/13/33/00/90"]


def doc_update(file_id, file_name, size, user=USER):
    _uid[0] += 1
    return {"update_id": _uid[0], "message": {
        "message_id": _uid[0], "date": 0, "chat": {"id": user["id"], "type": "private"}, "from": user,
        "document": {"file_id": file_id, "file_unique_id": "u" + file_id, "file_name": file_name,
                     "file_size": size}}}


async def test_admin_uploads_zipped_gpc_and_db(app_and_tg, settings, tmp_path):
    import io
    import zipfile
    from conftest import ROOT
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    gpc_json = {"LanguageCode": "EN", "DateUtc": "5/28/2026", "Schema": [{
        "Level": 1, "Code": 50000000, "Title": "Food/Beverage", "Childs": [{
            "Level": 2, "Code": 50250000, "Title": "Fruits - Unprepared/Unprocessed (Shelf Stable)", "Childs": [{
                "Level": 3, "Code": 50250100, "Title": "Dried Fruit", "Childs": [
                    {"Level": 4, "Code": 10000000 + i, "Title": f"Dried Test Fruit {i}", "Definition": "test",
                     "Active": True, "Childs": []} for i in range(1200)]}]}]}]}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("GPC May 2026 EN.json", json.dumps(gpc_json))
    tg.files["gpczip"] = buf.getvalue()
    await send(app, doc_update("gpczip", "gpc.zip", len(buf.getvalue()), user=OTHER))
    assert "خاص" in tg.last_text()
    await send(app, doc_update("gpczip", "gpc.zip", len(buf.getvalue())))
    assert "GS1 GPC اتحدث (EN): 1,200 Brick" in tg.last_text()
    assert svc.ref.counts()["gpc_brick"] == 1200 and svc.ref.counts()["tariff"] > 8000   # tariff kept
    # a full reference.db replaces everything (GPC back to the shipped release)
    tg.files["refdb"] = (ROOT / "data" / "reference.db").read_bytes()
    await send(app, doc_update("refdb", "reference.db", 1000))
    assert "اتحدثت قاعدة البيانات" in tg.last_text()
    assert svc.ref.counts()["gpc_brick"] > 5000
    await send(app, doc_update("big", "huge.json", 25 * 1024 * 1024))
    assert "20MB" in tg.last_text()


async def test_bare_number_after_calc_recalculates(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("0713330090 100000"))
    assert "2,000 ج" in tg.last_text()
    await send(app, msg_update("300000"))
    assert "حساب جمارك — البند 0713330090" in tg.last_text() and "6,000 ج" in tg.last_text()
    await send(app, msg_update("0713"))                        # a real prefix is still a lookup
    assert "بنود تبدأ بـ 0713" in tg.last_text()


async def test_post_init_starts_and_stop_cancels_background_task(app_and_tg):
    import asyncio
    app, tg = app_and_tg
    await app.post_init(app)
    assert any(e == "setMyCommands" for e, _ in tg.sent)
    tasks = list(app.bot_data["bg_tasks"])
    assert tasks and not tasks[0].done()
    await asyncio.wait_for(app.post_stop(app), timeout=2)      # must not hang on the 10-minute sleep
    assert tasks[0].cancelled() or tasks[0].done()


async def test_split_message_keeps_lines_whole():
    long = "\n".join(f"<b>سطر {i}</b> " + "x" * 80 for i in range(200))
    parts = handlers.split_message(long, 1000)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)
    assert all(p.count("<b>") == p.count("</b>") for p in parts)


async def test_update_command_runs_in_background(app_and_tg, monkeypatch):
    import asyncio
    app, tg = app_and_tg

    async def fake_crawl(*a, progress=None, **k):
        if progress:
            await progress("🔎 اكتشاف البنود")
        r = dict(OFFICIAL_SAMPLES["0713330090"])
        r["Taxes"] = ["ضريبة الوارد :  3%"]
        return [r]

    monkeypatch.setattr(handlers, "crawl_tariff", fake_crawl)
    await send(app, msg_update("/update"))
    assert "بدأ تحديث" in tg.last_text()
    for _ in range(50):
        if any("تم تحديث التعريفة" in t for t in tg.texts()):
            break
        await asyncio.sleep(0.05)
    assert any("تم تحديث التعريفة" in t for t in tg.texts())
    await send(app, msg_update("/stats"))
    assert "المستخدمين" in tg.last_text() and "بنود التعريفة: 1" in tg.last_text()



async def test_private_mode_owner_claim_and_allow(settings):
    settings.admin_ids = set()                                  # fresh install: no owner yet
    tg = FakeTelegram()
    from telegram.ext import Application
    app = bot_module.build_application(
        settings, builder=Application.builder().token(settings.token).request(tg).get_updates_request(FakeTelegram()))
    handlers._refused_at.clear()
    handlers._hits.clear()
    await app.initialize()
    try:
        await send(app, msg_update("فاصوليا", user=OTHER))
        assert "/start" in tg.last_text()                       # nobody owns it yet
        await send(app, msg_update("/start"))
        assert any("اتسجلت كمالك" in t for t in tg.texts()) and "أهلاً" in tg.last_text()
        await send(app, msg_update("/start", user=OTHER))
        assert "خاص" in tg.last_text() and "/allow 2002" in tg.last_text()
        n = len(tg.texts())
        await send(app, msg_update("فاصوليا", user=OTHER))
        assert len(tg.texts()) == n                             # refusal is not repeated (no spam)
        await send(app, msg_update("/whoami"))
        assert "1001" in tg.last_text() and "المالك" in tg.last_text()
        await send(app, msg_update("/allow 2002"))
        await send(app, msg_update("فاصوليا بيضاء", user=OTHER))
        assert "0713330090" in tg.last_text()
        await send(app, msg_update("/stats", user=OTHER))
        assert "للأدمن" in tg.last_text()                       # allowed user, but not an admin
        await send(app, msg_update("/deny 2002"))
        handlers._refused_at.clear()
        await send(app, msg_update("فاصوليا بيضاء", user=OTHER))
        assert "خاص" in tg.last_text()
    finally:
        await app.shutdown()


async def test_public_mode_lets_everyone_in(app_and_tg):
    app, tg = app_and_tg
    app.bot_data["svc"].s.private_mode = False
    await send(app, msg_update("فاصوليا بيضاء", user=OTHER))
    assert "0713330090" in tg.last_text()


async def test_export_view_and_routes(app_and_tg):
    app, tg = app_and_tg
    await send(app, msg_update("تصدير 0713330090"))
    t = tg.last_text()
    assert "متطلبات التصدير — البند 0713330090" in t and "سلامة الغذاء" in t and "نسخة محلية" in t
    await send(app, msg_update("تصدير ينسون"))
    kb = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "x|0909610000" in kb and "لمتطلبات التصدير" in tg.last_text()
    await send(app, cb_update("x|0909610000"))
    assert "متطلبات التصدير — البند 0909610000" in tg.last_text()
    await send(app, cb_update("d|0909610000"))
    kb = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert "x|0909610000" in kb and "i|0909610000" in kb


CBE_HTML = """<p>All prices are expressed in pounds. <br /> Rates for Date: 10/11/2026 </p>
<table><thead><tr><th>Currency</th><th>Buy</th><th>Sell</th></tr></thead><tbody>
<tr class="content-height"> <td class="column-width table-cell"> US Dollar </td>
 <td class="column-width table-cell"> 53.2491 </td> <td class="column-width table-cell"> 53.3874 </td></tr>
<tr><td class="column-width table-cell"> Euro </td><td class="column-width table-cell"> 59.7296 </td>
 <td class="column-width table-cell"> 59.8900 </td></tr>
<tr><td class="column-width table-cell"> Japanese Yen 100 </td><td class="column-width table-cell"> 33.6551 </td>
 <td class="column-width table-cell"> 33.7468 </td></tr>
<tr><td class="column-width table-cell"> Chinese Yuan </td><td class="column-width table-cell"> 7.4512 </td>
 <td class="column-width table-cell"> 7.4719 </td></tr></tbody></table>"""


async def test_parse_cbe_sample():
    from customs_bot.fx import parse_cbe
    date, rates = parse_cbe(CBE_HTML)
    assert date == "10/11/2026"
    assert rates["USD"] == (53.2491, 53.3874)
    assert rates["JPY"][1] == pytest.approx(0.337468)          # quoted per 100 yen
    assert rates["CNY"][0] == 7.4512


async def test_cbe_rate_used_and_manual_override(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    calls = []

    def cbe(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=CBE_HTML)

    svc.fx_transport = httpx.MockTransport(cbe)
    await send(app, msg_update("8465950000 1000$"))
    t = tg.last_text()
    assert "53,387.40" in t and "سعر بيع البنك المركزي 10/11/2026" in t
    await send(app, msg_update("8465950000 1000$"))
    assert len(calls) == 1                                      # cached, not fetched again
    await send(app, msg_update("/fx"))
    assert "53.3874" in tg.last_text() and "البنك المركزي" in tg.last_text()
    await send(app, msg_update("/setfx USD 50"))
    await send(app, msg_update("8465950000 1000$"))
    assert "50,000" in tg.last_text() and "سعر يدوي" in tg.last_text()
    await send(app, msg_update("/setfx USD 0"))
    await send(app, msg_update("8465950000 1000 يوان"))
    assert "7,471.90" in tg.last_text()



async def test_production_builder_with_persistence(settings):
    """Exactly the production setup (PicklePersistence): the service must survive initialize()."""
    settings.admin_ids = set()
    tg = FakeTelegram()
    app = bot_module.build_application(settings, requests=(tg, FakeTelegram()))
    handlers._refused_at.clear()
    handlers._hits.clear()
    app.bot_data["svc"].fx_transport = httpx.MockTransport(lambda r: httpx.Response(503))
    app.bot_data["svc"].customs_fx_transport = httpx.MockTransport(lambda r: httpx.Response(503))
    await app.initialize()
    await app.post_init(app)
    try:
        assert "svc" in app.bot_data
        await send(app, msg_update("/start"))
        assert not any("خطأ" in t for t in tg.texts())
        assert any("اتسجلت كمالك" in t for t in tg.texts()) and "أهلاً" in tg.last_text()
        await send(app, msg_update("846595 1522500"))
        assert "228,375" in tg.last_text()
        await app.update_persistence()                          # saving state must not fail
        assert (settings.data_dir / "bot_state.pickle").exists()
    finally:
        await app.post_stop(app)
        await app.shutdown()


CUSTOMS_FX_PAGE1 = """<table><tr><th>الدولة</th><th>الاسم</th><th>سعر التحويل</th></tr>
<tr><td><span class="flag-icon flag-icon-us"></span> </td> <td> <span class="h5">دولار امريكـــي</span>
 <span class="badge bg-secondary">USD</span> </td> <td> 53.39 </td> </tr>
<tr><td></td><td> <span class="h5">يورو</span> <span class="badge bg-secondary">EUR</span> </td> <td> 59.89 </td></tr>
<tr><td></td><td> <span class="h5">جنيه مصري</span> <span class="badge bg-secondary">EGP</span> </td> <td> 1.00 </td></tr>
</table><ul class="pagination"><li><a href="/Services/Currency?page=2">2</a></li></ul>"""
CUSTOMS_FX_PAGE2 = """<table><tr><td></td><td> <span class="h5">يوان صيني</span>
 <span class="badge bg-secondary">CNY</span> </td> <td> 8.00 </td></tr>
<tr><td></td><td> <span class="h5">ريال سعودي</span> <span class="badge bg-secondary">SAR</span> </td> <td> 14.22 </td></tr></table>"""


async def test_parse_customs_rates():
    from customs_bot.service import parse_customs_rates
    assert parse_customs_rates(CUSTOMS_FX_PAGE1) == {"USD": 53.39, "EUR": 59.89}
    assert parse_customs_rates(CUSTOMS_FX_PAGE2) == {"CNY": 8.0, "SAR": 14.22}


async def test_customs_rate_is_preferred(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    pages = {"1": CUSTOMS_FX_PAGE1, "2": CUSTOMS_FX_PAGE2}

    def customs(request):
        assert request.url.path == "/Services/Currency"
        return httpx.Response(200, text=pages.get(request.url.params.get("page"), "<table></table>"))

    svc.customs_fx_transport = httpx.MockTransport(customs)
    svc.fx_transport = httpx.MockTransport(lambda r: httpx.Response(200, text=CBE_HTML))
    await send(app, msg_update("8465950000 1000$"))
    t = tg.last_text()
    assert "53,390" in t and "سعر التحويل الجمركي" in t
    await send(app, msg_update("8465950000 1000 ريال"))
    assert "14,220" in tg.last_text()
    await send(app, msg_update("8465950000 1000 يوان"))
    assert "8,000" in tg.last_text()                            # customs CNY (8.00), not the central bank's 7.47
    await send(app, msg_update("/fx"))
    t = tg.last_text()
    assert "سعر التحويل الجمركي" in t and "USD <b>53.39</b>" in tg.sent[-1][1]["text"]
    customs_calls = []

    def customs_down(request):
        customs_calls.append(1)
        return httpx.Response(503)

    svc.customs_fx_transport = httpx.MockTransport(customs_down)
    svc.app.kv_set("customs_fx", "")                            # customs unavailable -> central bank
    await send(app, msg_update("8465950000 1000$"))
    t = tg.last_text()
    assert "53,387.40" in t and "سعر بيع البنك المركزي" in t
    await send(app, msg_update("8465950000 2000$"))
    assert "106,774.80" in tg.last_text()
    assert len(customs_calls) == 1                              # a failed source is not hammered on every message


async def test_stale_customs_rate_used_only_as_last_resort(app_and_tg):
    import json as _json
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    svc.app.kv_set("customs_fx", _json.dumps({"rates": {"USD": 50.0}, "fetched_at": "2026-01-01T00:00:00Z"}))
    await send(app, msg_update("8465950000 1000$"))              # both sources down (fixture)
    t = tg.last_text()
    assert "50,000" in t and "آخر سعر تحويل جمركي متاح (2026-01-01)" in t
