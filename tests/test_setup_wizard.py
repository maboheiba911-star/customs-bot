import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import setup_wizard as wiz  # noqa: E402

BOTFATHER_MSG = (
    "Done! Congratulations on your new bot. You will find it at t.me/ExampleCustomsBot.\n"
    "Use this token to access the HTTP API:\n1234567890:TEST-not-a-real-token-xxxxxxxxxxxx\n"
    "Keep your token secure and store it safely"
)


def test_find_token_in_botfather_message():
    assert wiz.find_token(BOTFATHER_MSG) == "1234567890:TEST-not-a-real-token-xxxxxxxxxxxx"
    assert wiz.find_token("مفيش توكن هنا") is None
    assert wiz.find_token("  1234567890:TEST-not-a-real-token-xxxxxxxxxxxx  ") is not None


def test_write_env_keeps_other_settings(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ADMIN_IDS=1001\nBOT_NAME=بوت\n", encoding="utf-8")
    monkeypatch.setattr(wiz, "ENV", env)
    wiz.write_env({"TELEGRAM_BOT_TOKEN": "123:abc"})
    values = wiz.read_env()
    assert values["TELEGRAM_BOT_TOKEN"] == "123:abc"
    assert values["ADMIN_IDS"] == "1001" and values["BOT_NAME"] == "بوت"
    assert values["PRIVATE_MODE"] == "true"


def test_check_token(monkeypatch):
    class R:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, timeout: R({"ok": True, "result": {"username": "ExampleCustomsBot"}}))
    assert wiz.check_token("1:x") == (True, "@ExampleCustomsBot")
    monkeypatch.setattr(httpx, "get", lambda url, timeout: R({"ok": False, "error_code": 401}))
    ok, msg = wiz.check_token("1:x")
    assert not ok and "مش صحيح" in msg

    def boom(url, timeout):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", boom)
    ok, msg = wiz.check_token("1:x")
    assert not ok and "النت" in msg


def test_autostart_file_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz, "IS_WIN", True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    msg = wiz.set_autostart(True)
    vbs = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "CustomsBot.vbs"
    text = vbs.read_text(encoding="utf-16")
    assert "WScript.Shell" in text and "bot.py" in text and ", 0, False" in text
    assert "لوحده" in msg
    wiz.set_autostart(False)
    assert not vbs.exists()


def test_wait_for_start_reads_new_log_lines(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("old run ... Traceback\n", encoding="utf-8")
    monkeypatch.setattr(wiz, "LOG", log)

    calls = {"n": 0}

    def tick():
        calls["n"] += 1
        if calls["n"] == 3:
            with open(log, "a", encoding="utf-8") as f:
                f.write("2026-11-10 INFO customs-bot: starting — tariff lines: 8802\n")

    ok, _ = wiz.wait_for_start(seconds=5, tick=tick)
    assert ok                                        # old errors before this run are ignored


@pytest.mark.parametrize("name", ["install_windows.bat", "stop_bot.bat", "start_bot.bat", "uninstall_windows.bat"])
def test_batch_files_are_ascii_with_crlf(name):
    data = (ROOT / name).read_bytes()
    assert all(b < 128 for b in data)                # cmd.exe misreads non-ASCII batch files
    assert data.count(b"\r\n") == data.count(b"\n")
