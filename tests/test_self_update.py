"""Self-update: GitHub check, archive checks, self-test gate, the helper that swaps files, rollback."""
import asyncio
import importlib.util
import io
import json
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from conftest import ROOT
from test_bot_e2e import app_and_tg, msg_update, send  # noqa: F401  (app_and_tg: fixture re-export)

from customs_bot import handlers

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
TOP = f"maboheiba911-star-customs-bot-{SHA[:7]}"


def real_tree() -> dict[str, bytes]:
    """The files of this working copy, as GitHub would serve them."""
    out: dict[str, bytes] = {}
    for pattern in ("bot.py", "requirements.txt", "*.bat", "README.md", "customs_bot/*.py", "tools/*.py"):
        for p in ROOT.glob(pattern):
            out[p.relative_to(ROOT).as_posix()] = p.read_bytes()
    return out


def gh_zip(files: dict[str, bytes | str], top: str = TOP, extra_names: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(top + "/", "")
        for rel, body in files.items():
            z.writestr(f"{top}/{rel}", body)
        for name in extra_names:
            z.writestr(name, "x = 1\n")
    return buf.getvalue()


# ------------------------------------------------------------------ archive checks
def test_stage_update_takes_only_allowed_files(tmp_path):
    files = real_tree()
    files[".env"] = "TELEGRAM_BOT_TOKEN=1234567890:TEST-not-a-real-token-xxxxxxxxxxxx\n"
    files["data/reference.db"] = b"not code"
    files["tests/test_x.py"] = "def test_x():\n    pass\n"
    files["customs_bot/notes.txt"] = "x"
    staged = handlers.stage_update(gh_zip(files), tmp_path / "stage")
    assert {"bot.py", "customs_bot/handlers.py", "customs_bot/service.py", "install_windows.bat",
            "requirements.txt", "tools/setup_wizard.py"} <= set(staged)
    assert not [f for f in staged if f.startswith(("data/", "tests/")) or f in (".env", "customs_bot/notes.txt")]
    assert (tmp_path / "stage/customs_bot/service.py").read_bytes() == (ROOT / "customs_bot/service.py").read_bytes()
    assert not (tmp_path / "stage/.env").exists()


@pytest.mark.parametrize("case", ["traversal", "syntax", "missing", "two_tops"])
def test_stage_update_rejects_bad_archives(tmp_path, case):
    files = real_tree()
    extra: tuple[str, ...] = ()
    if case == "traversal":
        extra = (f"{TOP}/customs_bot/../../evil.py",)
    elif case == "syntax":
        files["customs_bot/service.py"] = "def broken(:\n"
    elif case == "missing":
        del files["customs_bot/handlers.py"]
    elif case == "two_tops":
        extra = ("another-top/bot.py",)
    with pytest.raises(Exception):
        handlers.stage_update(gh_zip(files, extra_names=extra), tmp_path / "stage")


# ------------------------------------------------------------------ the helper process
def load_helper(tmp_path):
    path = tmp_path / "apply_update.py"
    path.write_text(handlers._HELPER_SRC, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("apply_update_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProc:
    def __init__(self, code=None):
        self.returncode = code
        self.pid = 4242

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def make_install(tmp_path):
    bot_dir = tmp_path / "bot"
    (bot_dir / "customs_bot").mkdir(parents=True)
    (bot_dir / "bot.py").write_text("old bot", encoding="utf-8")
    (bot_dir / "customs_bot/service.py").write_text("old service", encoding="utf-8")
    upd = tmp_path / "updates"
    stage = upd / "stage"
    (stage / "customs_bot").mkdir(parents=True)
    (stage / "bot.py").write_text("new bot", encoding="utf-8")
    (stage / "customs_bot/service.py").write_text("new service", encoding="utf-8")
    (stage / "customs_bot/extra.py").write_text("new file", encoding="utf-8")
    (upd / "installed.json").write_text(json.dumps({"sha": "old1111"}), encoding="utf-8")
    args = {"mode": "update", "sha": SHA, "message": "تاريخ بتوقيت مصر", "stage": str(stage),
            "files": ["bot.py", "customs_bot/extra.py", "customs_bot/service.py"], "bot_dir": str(bot_dir),
            "upd_dir": str(upd), "python": sys.executable, "port": 1, "boot": "b1",
            "crash_wait": 0.2, "slow_wait": 0.6, "stop_wait": 0.6}
    return bot_dir, upd, stage, args


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_helper_applies_update_and_reports_ok(tmp_path, monkeypatch):
    m = load_helper(tmp_path)
    bot_dir, upd, stage, args = make_install(tmp_path)
    h = m.Helper(args)
    started = []

    def fake_start(boot):
        started.append(boot)
        (upd / "heartbeat.json").write_text(json.dumps({"boot": boot}), encoding="utf-8")   # new bot reports in
        return FakeProc()

    monkeypatch.setattr(h, "port_free", lambda: True)
    monkeypatch.setattr(h, "start_bot", fake_start)
    assert h.run() == 0
    assert (bot_dir / "bot.py").read_text(encoding="utf-8") == "new bot"
    assert (bot_dir / "customs_bot/extra.py").exists()
    assert read(upd / "installed.json")["sha"] == SHA
    assert read(upd / "result.json")["status"] == "ok"
    assert started == ["b1"] and not stage.exists()
    (backup,) = list((upd / "backups").iterdir())
    assert (backup / "bot.py").read_text(encoding="utf-8") == "old bot"


def test_helper_rolls_back_when_the_new_version_dies(tmp_path, monkeypatch):
    m = load_helper(tmp_path)
    bot_dir, upd, stage, args = make_install(tmp_path)
    h = m.Helper(args)
    started = []

    def fake_start(boot):
        started.append(boot)
        if boot == "b1":
            return FakeProc(code=1)                       # new version crashes at once
        (upd / "heartbeat.json").write_text(json.dumps({"boot": boot}), encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(h, "port_free", lambda: True)
    monkeypatch.setattr(h, "start_bot", fake_start)
    assert h.run() == 1
    assert (bot_dir / "bot.py").read_text(encoding="utf-8") == "old bot"
    assert (bot_dir / "customs_bot/service.py").read_text(encoding="utf-8") == "old service"
    assert not (bot_dir / "customs_bot/extra.py").exists()
    assert read(upd / "installed.json")["sha"] == "old1111"
    assert SHA in read(upd / "bad.json")
    res = read(upd / "result.json")
    assert res["status"] == "rolled_back" and "exit code 1" in res["reason"]
    assert started == ["b1", "b1-rollback"]


def test_helper_rolls_back_when_no_heartbeat(tmp_path, monkeypatch):
    m = load_helper(tmp_path)
    bot_dir, upd, stage, args = make_install(tmp_path)
    h = m.Helper(args)
    monkeypatch.setattr(h, "port_free", lambda: True)
    monkeypatch.setattr(h, "start_bot", lambda boot: FakeProc())      # alive but never reports in
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    assert h.run() == 1
    assert (bot_dir / "bot.py").read_text(encoding="utf-8") == "old bot"
    assert read(upd / "result.json")["status"] == "rolled_back"


def test_helper_changes_nothing_if_the_old_bot_keeps_running(tmp_path, monkeypatch):
    m = load_helper(tmp_path)
    bot_dir, upd, stage, args = make_install(tmp_path)
    h = m.Helper(args)
    monkeypatch.setattr(h, "port_free", lambda: False)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    assert h.run() == 1
    assert (bot_dir / "bot.py").read_text(encoding="utf-8") == "old bot"
    assert read(upd / "result.json")["status"] == "failed"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


FAKE_BOT = """import json, os, socket, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
lock = socket.socket(); lock.bind(("127.0.0.1", {port})); lock.listen(1)
if "{crash}" == "yes":
    sys.exit(3)
(here.parent / "updates" / "heartbeat.json").write_text(json.dumps({{"boot": os.environ.get("CUSTOMS_BOT_BOOT"),
    "who": "{who}"}}), encoding="utf-8")
time.sleep(4)
"""


@pytest.mark.parametrize("crash", [False, True])
def test_helper_as_a_real_process(tmp_path, crash):
    """Run the helper exactly as the bot does: separate process, real bot processes, real port lock."""
    port = _free_port()
    bot_dir, upd, stage, args = make_install(tmp_path)
    old_src = FAKE_BOT.format(port=port, crash="no", who="old")
    (bot_dir / "bot.py").write_text(old_src, encoding="utf-8")
    (stage / "bot.py").write_text(FAKE_BOT.format(port=port, crash="yes" if crash else "no", who="new"),
                                  encoding="utf-8")
    args.update({"port": port, "crash_wait": 20, "slow_wait": 30, "stop_wait": 10})
    script = upd / "apply_update.py"
    script.write_text(handlers._HELPER_SRC, encoding="utf-8")
    (upd / "helper_args.json").write_text(json.dumps(args), encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(upd / "helper_args.json")], timeout=90)
    res = read(upd / "result.json")
    hb = read(upd / "heartbeat.json")
    if not crash:
        assert r.returncode == 0 and res["status"] == "ok"
        assert hb == {"boot": "b1", "who": "new"} and read(upd / "installed.json")["sha"] == SHA
    else:
        assert r.returncode == 1 and res["status"] == "rolled_back"
        assert hb == {"boot": "b1-rollback", "who": "old"}            # the old version is running again
        assert (bot_dir / "bot.py").read_text(encoding="utf-8") == old_src           # old files are back
        assert read(upd / "installed.json")["sha"] == "old1111"
    time.sleep(4.5)                                                   # let the fake bot exit


# ------------------------------------------------------------------ through Telegram
def github(zip_bytes: bytes, sha: str = SHA, message: str = "تاريخ السعر بتوقيت مصر\n\nتفاصيل"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": sha, "commit": {
                "message": message, "committer": {"date": "2026-11-11T01:00:00Z"}}})
        if request.url.path.endswith(f"/zipball/{sha}"):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture()
def no_restart(monkeypatch):
    calls = {"spawn": [], "stop": []}
    monkeypatch.setattr(handlers, "_spawn_helper", lambda upd, args: calls["spawn"].append(args))
    monkeypatch.setattr(handlers, "_stop_application", lambda application: calls["stop"].append(application))
    return calls


@pytest.mark.asyncio
async def test_upgrade_installs_new_commit(app_and_tg, monkeypatch, no_restart):
    app, tg = app_and_tg
    await send(app, msg_update("/version"))
    assert "manual" in tg.last_text() and "متوقف" in tg.last_text()
    monkeypatch.setattr(handlers, "upd_transport", github(gh_zip(real_tree())))
    await send(app, msg_update("/upgrade"))                  # downloads, checks, runs the real self-test
    assert no_restart["spawn"] and no_restart["stop"], tg.texts()[-2:]
    args = no_restart["spawn"][0]
    assert args["sha"] == SHA and args["from_sha"] == "manual" and args["mode"] == "update"
    assert "customs_bot/handlers.py" in args["files"] and Path(args["stage"], "bot.py").exists()
    assert "نسخة جديدة" in tg.last_text() and SHA[:7] in tg.last_text()


@pytest.mark.asyncio
async def test_upgrade_when_already_current(app_and_tg, monkeypatch, settings, no_restart):
    app, tg = app_and_tg
    upd = settings.data_dir / "updates"
    upd.mkdir(exist_ok=True)
    (upd / "installed.json").write_text(json.dumps({"sha": SHA, "message": "x"}), encoding="utf-8")
    monkeypatch.setattr(handlers, "upd_transport", github(b""))
    await send(app, msg_update("/upgrade"))
    assert "آخر نسخة" in tg.last_text() and not no_restart["spawn"]


@pytest.mark.asyncio
async def test_broken_version_is_rejected_and_remembered(app_and_tg, monkeypatch, settings, no_restart):
    app, tg = app_and_tg
    files = real_tree()
    files["customs_bot/service.py"] = files["customs_bot/service.py"] + b"\nraise RuntimeError('boom')\n"
    monkeypatch.setattr(handlers, "upd_transport", github(gh_zip(files)))
    await send(app, msg_update("/upgrade"))
    assert "متركبتش" in tg.last_text() and "boom" in tg.last_text()
    assert not no_restart["spawn"]
    assert SHA in json.loads((settings.data_dir / "updates/bad.json").read_text(encoding="utf-8"))
    text, restarting = await handlers.check_and_apply(app)      # the automatic check skips it from now on
    assert "فشلت قبل كده" in text and not restarting


@pytest.mark.asyncio
async def test_github_down(app_and_tg, monkeypatch, no_restart):
    app, tg = app_and_tg
    monkeypatch.setattr(handlers, "upd_transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    await send(app, msg_update("/upgrade"))
    assert "GitHub" in tg.last_text() and not no_restart["spawn"]
    await send(app, msg_update("/version"))
    assert "آخر فحص" in tg.last_text()


@pytest.mark.asyncio
async def test_restart_command(app_and_tg, no_restart):
    app, tg = app_and_tg
    await send(app, msg_update("/restart"))
    assert no_restart["spawn"][0]["mode"] == "restart" and no_restart["stop"]
    assert "بيعيد التشغيل" in tg.last_text()


@pytest.mark.asyncio
async def test_start_writes_heartbeat_and_announces_the_update(app_and_tg, monkeypatch, settings):
    app, tg = app_and_tg
    upd = settings.data_dir / "updates"
    upd.mkdir(exist_ok=True)
    (upd / "installed.json").write_text(json.dumps({"sha": SHA}), encoding="utf-8")
    (upd / "result.json").write_text(json.dumps({"status": "ok", "sha": SHA, "message": "تاريخ بتوقيت مصر"}),
                                     encoding="utf-8")
    monkeypatch.setenv("CUSTOMS_BOT_BOOT", "boot-1")
    await app.post_init(app)
    try:
        hb = json.loads((upd / "heartbeat.json").read_text(encoding="utf-8"))
        assert hb["boot"] == "boot-1" and hb["sha"] == SHA
        for _ in range(100):
            if any("حدّث نفسه" in t for t in tg.texts()):
                break
            await asyncio.sleep(0.02)
        assert any(f"حدّث نفسه للنسخة <code>{SHA[:7]}</code>" in t and "بتوقيت مصر" in t for t in tg.texts())
        assert not (upd / "result.json").exists()
    finally:
        await app.post_stop(app)


@pytest.mark.asyncio
async def test_rollback_is_announced_to_the_owner(app_and_tg, monkeypatch, settings):
    app, tg = app_and_tg
    upd = settings.data_dir / "updates"
    upd.mkdir(exist_ok=True)
    (upd / "result.json").write_text(json.dumps({"status": "rolled_back", "sha": SHA, "reason": "exit code 1"}),
                                     encoding="utf-8")
    monkeypatch.setenv("CUSTOMS_BOT_BOOT", "boot-1-rollback")
    await app.post_init(app)
    try:
        for _ in range(100):
            if any("مشتغلتش" in t for t in tg.texts()):
                break
            await asyncio.sleep(0.02)
        assert any("مشتغلتش" in t and "exit code 1" in t for t in tg.texts())
    finally:
        await app.post_stop(app)
