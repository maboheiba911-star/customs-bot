"""Running the bot on an Android phone (Termux): no rapidfuzz, little data, moving it between devices."""
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
from packaging.markers import Marker
from test_bot_e2e import app_and_tg, msg_update, send  # noqa: F401

from customs_bot import _fuzz, handlers, refdb
from customs_bot.refdb import ReferenceDB

ROOT = Path(__file__).resolve().parent.parent
SHA = "b" * 40


def test_search_works_without_rapidfuzz(monkeypatch):
    monkeypatch.setattr(refdb, "fuzz", _fuzz)
    ref = ReferenceDB(ROOT / "data" / "reference.db")
    for query, prefix in (("فاصوليا", "0713"), ("زيت زيتون", "1509"), ("olive oil", "1509"), ("بسكويت", "1905")):
        items, total = ref.search(query, 6, 0)
        assert total and any(i.code.startswith(prefix) for i in items), (query, [i.code for i in items])


def test_fallback_scores_like_rapidfuzz():
    from rapidfuzz import fuzz
    pairs = [("فاصوليا بيضا", "فاصوليا عاديه بيضا جافه"), ("olive oil", "virgin olive oil"),
             ("زيت عباد", "زيت عباد الشمس خام"), ("بن", "بن محمص غير منزوع الكافيين"), ("قطن", "خيوط من صوف")]
    for a, b in pairs:
        assert abs(fuzz.token_set_ratio(a, b) - _fuzz.token_set_ratio(a, b)) <= 12, (a, b)
    assert _fuzz.token_set_ratio("", "x") == 0.0


def test_rapidfuzz_is_skipped_on_android_only():
    line = next(x for x in (ROOT / "requirements.txt").read_text().splitlines() if x.startswith("rapidfuzz"))
    marker = Marker(line.split(";", 1)[1])
    assert marker.evaluate({"platform_release": "6.12.23-android16-5-g1a2b3c"}) is False   # Samsung S26 kernel
    assert marker.evaluate({"platform_release": "10"}) is True                             # Windows 10/11
    assert marker.evaluate({"platform_release": "6.8.0-45-generic"}) is True               # Linux server


async def test_update_check_downloads_40_bytes_when_nothing_is_new(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, text=SHA)
        return httpx.Response(200, json={"sha": SHA, "message": "رسالة\n\nتفاصيل", "committer": {"date": "d"}})

    monkeypatch.setattr(handlers, "upd_transport", httpx.MockTransport(handler))
    assert await handlers.latest_commit(SHA) == {"sha": SHA, "message": "", "date": ""}
    assert len(calls) == 1
    new = await handlers.latest_commit("a" * 40)
    assert new["message"] == "رسالة" and calls[-1].endswith(f"/git/commits/{SHA}")
    monkeypatch.setattr(handlers, "upd_transport", httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(ValueError):
        await handlers.latest_commit()


async def test_backup_sends_the_data_without_the_token(app_and_tg, settings):
    app, tg = app_and_tg
    (settings.data_dir / "config.json").write_text('{"dev_fee": {}}', encoding="utf-8")
    await send(app, msg_update("/start"))
    await send(app, msg_update("/backup"))
    assert tg.documents, tg.texts()[-2:]
    name, body, _ = tg.documents[-1]["document"]
    assert name == handlers.BACKUP_NAME
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert set(z.namelist()) == {"reference.db", "app.db", "config.json"}
        z.extract("reference.db", settings.data_dir / "restored")
        assert settings.token not in z.read("app.db").decode("latin-1")
    db = sqlite3.connect(settings.data_dir / "restored" / "reference.db")
    assert db.execute("select count(*) from tariff").fetchone()[0] > 8000


async def test_backup_and_shutdown_are_owner_only(app_and_tg):
    app, tg = app_and_tg
    other = {"id": 2002, "is_bot": False, "first_name": "Other"}
    await send(app, msg_update("/start"))                                  # the owner
    await send(app, msg_update("/backup", user=other))
    await send(app, msg_update("/shutdown yes", user=other))
    assert not tg.documents and not (app.bot_data["svc"].s.data_dir / "STOPPED").exists()


async def test_shutdown_asks_then_stops_for_good(app_and_tg, settings, monkeypatch):
    app, tg = app_and_tg
    stopped = []
    monkeypatch.setattr(handlers, "_stop_application", lambda application: stopped.append(1))
    await send(app, msg_update("/shutdown"))
    assert "/shutdown yes" in tg.last_text() and not (settings.data_dir / "STOPPED").exists()
    await send(app, msg_update("/shutdown yes"))
    assert (settings.data_dir / "STOPPED").exists() and stopped == [1]


def test_bot_does_not_start_on_a_device_it_moved_from(tmp_path):
    shutil.copy2(ROOT / "data" / "reference.db", tmp_path / "reference.db")
    (tmp_path / "STOPPED").write_text("x")
    env = dict(os.environ, DATA_DIR=str(tmp_path), TELEGRAM_BOT_TOKEN="1234567890:TEST-not-a-real-token-xxxxxxxxxxxx")
    r = subprocess.run([sys.executable, str(ROOT / "bot.py")], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "STOPPED" in r.stderr


def test_wizard_start_undoes_shutdown(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import setup_wizard as w

    started = []
    monkeypatch.setattr(w, "ROOT", tmp_path)
    monkeypatch.setattr(w.subprocess, "Popen", lambda cmd, **kw: started.append(kw))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "STOPPED").write_text("x")
    w.start_bot()
    assert not (tmp_path / "data" / "STOPPED").exists()
    assert started and (started[0].get("start_new_session") or started[0].get("creationflags"))


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_termux_installer_is_valid_bash():
    r = subprocess.run(["bash", "-n", str(ROOT / "termux_install.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert b"\r\n" not in (ROOT / "termux_install.sh").read_bytes()
