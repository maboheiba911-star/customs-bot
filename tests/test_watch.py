"""Watched tariff lines (🔔): snapshots, diffs, the twice-daily check and the bot flow."""
import httpx
from conftest import OFFICIAL_SAMPLES
from test_bot_e2e import USER, app_and_tg, cb_update, msg_update, send  # noqa: F401  (fixture)

from customs_bot import router
from customs_bot import watch as W

CODE = "0713330090"
EXPORT = {"Number": "07/13/33/00/90", "Taxes": [], "InstructionCodes": ["ق4075"],
          "Instructions": ["ق4075-لايصرح بتصدير الصنف الا بقرار من الحجر الزراعى"]}


def site(state: dict):
    """A fake customs.gov.eg: state['imp'] / state['exp'] are the records; state['down'] makes it fail."""
    def handler(request: httpx.Request) -> httpx.Response:
        if state.get("down"):
            return httpx.Response(500)
        state["calls"] = state.get("calls", 0) + 1
        rec = state["imp"] if request.url.params["trfType"] == "1" else state["exp"]
        return httpx.Response(200, json=rec)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def live(svc, state):
    svc.s.live_lookup = True
    svc.live.enabled = True
    svc.live._client = site(state)


def test_snapshot_and_diff():
    a = W.snapshot({"Taxes": ["ضريبة الوارد :  2%"], "Instructions": ["غ6502-نص"], "InstructionCodes": ["غ6502"]},
                   EXPORT)
    b = W.snapshot({"Taxes": ["ضريبة الوارد :  5%"], "Instructions": ["غ6502-نص"], "InstructionCodes": ["غ6502"]},
                   {**EXPORT, "Instructions": EXPORT["Instructions"] + ["ق4560-عرض على هيئة سلامة الغذاء"],
                    "InstructionCodes": ["ق4075", "ق4560"]})
    assert W.fingerprint(a) == W.fingerprint(dict(a)) and W.fingerprint(a) != W.fingerprint(b)
    lines = W.diff_lines(a, b)
    assert "❌ اتشال: ضريبة الوارد : 2%" in lines and "🆕 اتضاف: ضريبة الوارد : 5%" in lines
    assert any("ق4560" in x for x in lines) and "<b>📥 شروط الاستيراد</b>" not in lines
    assert router.parse("راقب 0713330090").kind == "watch" and router.parse("/unwatch 0713330090").kind == "unwatch"
    assert router.parse("تابع").kind == "watch" and router.parse("تابع الشحنة").kind != "watch"


async def test_watch_alerts_only_on_real_changes(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    state = {"imp": dict(OFFICIAL_SAMPLES[CODE]), "exp": dict(EXPORT)}
    live(svc, state)
    r = await svc.watch_add(USER["id"], 1001, CODE)
    assert "هراقب البند" in r.text and svc.app.watches_for(USER["id"])[0]["fp"]
    assert await svc.watch_check() == []                                  # nothing changed
    calls = state["calls"]
    state["imp"] = {**state["imp"], "Taxes": ["ضريبة الوارد :  5%", "ضريبة قيمه مضافه :  صفر"]}
    state["exp"] = {**EXPORT, "Instructions": EXPORT["Instructions"] + ["ق4560-عرض الصنف على هيئة سلامة الغذاء"],
                    "InstructionCodes": ["ق4075", "ق4560"]}
    alerts = await svc.watch_check()
    assert state["calls"] > calls                                         # fresh, not from the cache
    assert len(alerts) == 1 and alerts[0][0] == 1001
    text = alerts[0][1]
    assert CODE in text and "ضريبة الوارد : 2%" in text and "ضريبة الوارد : 5%" in text and "ق4560" in text
    assert await svc.watch_check() == []                                  # told once
    assert svc.app.watches_for(USER["id"])[0]["changed_at"]
    state["down"] = True
    assert await svc.watch_check() == []                                  # site down is not a change


async def test_watch_made_while_site_is_down_takes_baseline_silently(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    state = {"imp": dict(OFFICIAL_SAMPLES[CODE]), "exp": dict(EXPORT), "down": True}
    live(svc, state)
    r = await svc.watch_add(USER["id"], 1001, CODE)
    assert "أول فحص" in r.text and svc.app.watches_for(USER["id"])[0]["fp"] is None
    state["down"] = False
    svc.live._open_until = 0                                              # circuit breaker closed again
    assert await svc.watch_check() == []                                  # baseline, no false alarm
    assert svc.app.watches_for(USER["id"])[0]["fp"]


async def test_watch_in_the_bot(app_and_tg):
    app, tg = app_and_tg
    svc = app.bot_data["svc"]
    live(svc, {"imp": dict(OFFICIAL_SAMPLES[CODE]), "exp": dict(EXPORT)})
    await send(app, msg_update(CODE))
    buttons = [b["callback_data"] for row in tg.last_markup()["inline_keyboard"] for b in row]
    assert f"wa|{CODE}" in buttons
    await send(app, cb_update(f"wa|{CODE}"))
    assert "هراقب البند" in tg.last_text()
    await send(app, msg_update("راقب 0713330090"))
    assert "متراقب بالفعل" in tg.last_text()
    await send(app, msg_update("🔔 مراقبة البنود"))
    assert CODE in tg.last_text()
    await send(app, cb_update(f"wu|{CODE}"))
    assert "شيلت المراقبة" in tg.last_text() and "مفيش بنود متراقبة" in tg.last_text()
    await send(app, msg_update("راقب 0713"))
    assert "10 أرقام" in tg.last_text()
