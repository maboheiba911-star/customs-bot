"""Running the bot on an Android phone (Termux): no rapidfuzz, little data, moving it between devices."""
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import time
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
    def now(text):
        u = msg_update(text)
        u["message"]["date"] = int(time.time()) + 5
        return u

    await send(app, msg_update("/shutdown yes"))           # sent before this copy started: ignored
    assert not (settings.data_dir / "STOPPED").exists() and not stopped
    await send(app, now("/shutdown"))
    assert "/shutdown yes" in tg.last_text() and not (settings.data_dir / "STOPPED").exists()
    await send(app, now("/shutdown yes"))
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


def test_backup_closes_every_database_it_opens(settings, monkeypatch):
    """On Windows an open SQLite file can't be deleted, so the temporary copies must be closed."""
    opened = []
    real = sqlite3.connect

    class Tracked:
        def __init__(self, *a, **kw):
            self.conn = real(*a, **kw)
            self.closed = False
            opened.append(self)

        def backup(self, other):
            return self.conn.backup(other.conn)

        def close(self):
            self.closed = True
            self.conn.close()

    monkeypatch.setattr(handlers.sqlite3, "connect", Tracked)
    s = type("S", (), {"s": settings})()
    data = handlers.make_backup(s)
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["reference.db"]
    assert opened and all(c.closed for c in opened)


async def test_backup_on_a_slow_upload_says_so(app_and_tg):
    app, tg = app_and_tg
    tg.document_timeout = True
    await send(app, msg_update("/start"))
    await send(app, msg_update("/backup"))
    assert "النت بطيء" in tg.last_text()


# ------------------------------------------------------------------ the backup straight from Telegram
class FakeTelegramAPI:
    """A tiny Bot API on localhost for tools/fetch_backup.py (stdlib urllib, so no httpx mocks)."""

    def __init__(self, blob: bytes):
        import http.server
        import json as _json
        import threading
        import urllib.parse as up

        self.calls, self.sent, self.blob = [], [], blob
        api = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = body if isinstance(body, bytes) else _json.dumps(body).encode()
                self.send_response(code)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._reply(200, api.blob)                      # /file/bot<token>/<path>

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                n = int(self.headers.get("Content-Length") or 0)
                params = dict(up.parse_qsl(self.rfile.read(n).decode()))
                api.calls.append((method, params))
                if method == "getMe":
                    return self._reply(200, {"ok": True, "result": {"username": "customs_test_bot"}})
                if method == "getUpdates":
                    polls = [c for c in api.calls if c[0] == "getUpdates"]
                    if len(polls) == 1:                          # the computer copy is still polling
                        return self._reply(409, {"ok": False, "error_code": 409, "description": "Conflict"})
                    if params.get("offset") == "12":
                        return self._reply(200, {"ok": True, "result": []})
                    return self._reply(200, {"ok": True, "result": [
                        {"update_id": 10, "message": {"chat": {"id": 1001}, "text": "/shutdown yes"}},
                        {"update_id": 11, "message": {"chat": {"id": 1001}, "document": {
                            "file_id": "F1", "file_name": "customs-bot-data.zip"}}}]})
                if method == "getFile":
                    return self._reply(200, {"ok": True, "result": {"file_path": "documents/f.zip"}})
                if method == "sendMessage":
                    api.sent.append(params)
                    return self._reply(200, {"ok": True, "result": {}})
                self._reply(404, {"ok": False})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def test_fetch_backup_waits_for_the_forwarded_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import fetch_backup

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(ROOT / "data" / "reference.db", "reference.db")
        z.writestr("app.db", b"x")
    api = FakeTelegramAPI(buf.getvalue())
    monkeypatch.setattr(fetch_backup, "API", api.url)
    monkeypatch.setattr(fetch_backup.time, "sleep", lambda s: None)
    said = []
    try:
        assert fetch_backup.wait_for_backup("123:ABC", tmp_path, minutes=1, poll=1, say=said.append)
    finally:
        api.close()
    assert (tmp_path / "reference.db").stat().st_size > 1_000_000 and (tmp_path / "app.db").exists()
    assert any("/shutdown yes" in s for s in said)                    # told to stop the computer copy
    assert api.sent and "وصل" in api.sent[0]["text"]
    offsets = [c[1].get("offset") for c in api.calls if c[0] == "getUpdates"]
    assert offsets[-1] == "12"                                          # both updates marked as handled


def test_wizard_token_only_saves_the_token(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import setup_wizard as w

    monkeypatch.setattr(w, "ENV", tmp_path / ".env")
    answers = iter(["not a token", "1234567890:TEST-not-a-real-token-xxxxxxxxxxxx"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(w, "check_token", lambda tok: (True, "@customs_test_bot"))
    assert w.run_token_only() == 0
    assert "TELEGRAM_BOT_TOKEN=1234567890:TEST" in (tmp_path / ".env").read_text()


def test_token_with_a_space_from_copying_on_the_phone():
    sys.path.insert(0, str(ROOT / "tools"))
    import setup_wizard as w
    tok = "1234567890:TEST-not-a-real-token-xxxxxxxxxxxx"
    assert w.find_token(tok[:20] + " " + tok[20:]) == tok
    assert w.find_token("  " + tok + "\n") == tok


def test_token_is_kept_when_only_the_network_is_slow(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import setup_wizard as w

    monkeypatch.setattr(w, "ENV", tmp_path / ".env")
    monkeypatch.setattr("builtins.input", lambda prompt="": "1234567890:TEST-not-a-real-token-xxxxxxxxxxxx")
    monkeypatch.setattr(w, "check_token", lambda tok: (False, w.NETWORK_PREFIX + " (ReadTimeout) — اتأكد من النت"))
    assert w.run_token_only() == 0
    assert "TELEGRAM_BOT_TOKEN=" in (tmp_path / ".env").read_text()


def test_fetch_backup_survives_network_errors(monkeypatch, tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import fetch_backup

    import socket
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)          # undo ipv4_only() after the test
    monkeypatch.setattr(fetch_backup, "API", "http://127.0.0.1:9")          # nothing listens there
    monkeypatch.setattr(fetch_backup.time, "sleep", lambda s: None)
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(fetch_backup.time, "time", lambda: next(clock))
    said = []
    assert fetch_backup.wait_for_backup("123:ABC", tmp_path, minutes=2, poll=1, say=said.append) is False
    assert any("الداتا" in s for s in said)


# ------------------------------------------------------------------ networks that stall IPv6 to Telegram
def test_prefer_ipv4_keeps_only_ipv4_addresses(monkeypatch):
    import socket
    from customs_bot import net

    fake = [(socket.AF_INET6, 1, 6, "", ("2001:db8::1", 443, 0, 0)), (socket.AF_INET, 1, 6, "", ("1.2.3.4", 443))]
    monkeypatch.setattr(net, "_real_getaddrinfo", lambda *a, **k: fake)
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)       # restored after the test
    net.prefer_ipv4()
    assert [r[0] for r in socket.getaddrinfo("api.telegram.org", 443)] == [socket.AF_INET]
    monkeypatch.setattr(net, "_real_getaddrinfo", lambda *a, **k: fake[:1])
    assert socket.getaddrinfo("v6-only.example", 443) == fake[:1]       # nothing else to use: keep IPv6


def test_net_check_turns_on_ipv4_when_that_reaches_telegram(monkeypatch, tmp_path, capsys):
    import socket
    sys.path.insert(0, str(ROOT / "tools"))
    import net_check
    import setup_wizard
    from customs_bot import net

    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(setup_wizard, "ENV", tmp_path / ".env")
    answers = iter(["ok (200)", "ReadTimeout", "ok (302)"])                 # GitHub, Telegram, Telegram over IPv4
    monkeypatch.setattr(net_check, "reach", lambda url, timeout=15: next(answers))
    assert net_check.main() == 0
    assert "FORCE_IPV4=1" in (tmp_path / ".env").read_text()
    assert "IPv4" in capsys.readouterr().out
    net.restore()


def test_bot_uses_ipv4_when_asked(tmp_path):
    shutil.copy2(ROOT / "data" / "reference.db", tmp_path / "reference.db")
    (tmp_path / "STOPPED").write_text("x")                                  # exits right after the setup
    env = dict(os.environ, DATA_DIR=str(tmp_path), FORCE_IPV4="1",
               TELEGRAM_BOT_TOKEN="1234567890:TEST-not-a-real-token-xxxxxxxxxxxx")
    subprocess.run([sys.executable, str(ROOT / "bot.py")], env=env, capture_output=True, text=True, timeout=60)
    assert "FORCE_IPV4" in (tmp_path / "bot.log").read_text(encoding="utf-8")
