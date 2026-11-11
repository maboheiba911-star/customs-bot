"""Entry point: python bot.py   (on Windows the installer runs it hidden with pythonw)"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import logging.handlers
import os
import shutil
import socket
import sys
from pathlib import Path

from telegram.ext import Application, ApplicationBuilder, PersistenceInput, PicklePersistence

from customs_bot.appdb import AppDB
from customs_bot.handlers import auto_update_loop, fx_loop, register
from customs_bot.official import LiveLookup
from customs_bot.refdb import ReferenceDB
from customs_bot.service import CustomsService
from customs_bot.settings import ROOT, load_settings

log = logging.getLogger("customs-bot")
LOCK_PORT = 47391          # one bot per computer: a second copy exits instead of fighting for updates


def setup_logging(data_dir: Path) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    data_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(data_dir / "bot.log", maxBytes=1_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:          # pythonw (no console) has no stderr
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def single_instance() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def build_application(settings=None, *, builder: ApplicationBuilder | None = None,
                      requests: tuple | None = None) -> Application:
    """`builder` replaces the production builder entirely (tests); `requests` keeps the production
    builder (with persistence) but swaps the HTTP layer — used by the end-to-end tests."""
    s = settings or load_settings()
    ref = ReferenceDB(s.reference_db)
    app_db = AppDB(s.app_db)
    live = LiveLookup(s.official_base_url, app_db, cache_hours=s.live_cache_hours, enabled=s.live_lookup)
    service = CustomsService(s, ref, app_db, live)

    async def post_init(application: Application) -> None:
        application.bot_data["svc"] = service      # bot_data must always carry the service
        await application.bot.set_my_commands([
            ("start", "ابدأ"), ("help", "طريقة الاستخدام"), ("hs", "بحث عن بند"),
            ("calc", "حساب الجمارك"), ("origin", "المنشأ والاتفاقيات"), ("export", "متطلبات التصدير"),
            ("agreements", "اتفاقيات مصر التجارية"), ("gs1", "تصنيف GS1"),
            ("fx", "أسعار الصرف"), ("about", "مصادر البيانات"), ("version", "نسخة البوت والتحديث"),
        ])
        # plain asyncio tasks (not application.create_task) so stopping the bot never waits on them
        loop = asyncio.get_running_loop()
        for coro in (auto_update_loop(application), fx_loop(application)):
            application.bot_data.setdefault("bg_tasks", set()).add(loop.create_task(coro))

    async def post_stop(application: Application) -> None:
        for task in list(application.bot_data.get("bg_tasks", ())):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def post_shutdown(application: Application) -> None:
        await live.close()

    if builder is None:
        # only user_data (modes, pending code) is saved across restarts; bot_data holds live objects
        # (database connections) that must never be replaced by a saved copy or pickled
        persistence = PicklePersistence(
            filepath=s.data_dir / "bot_state.pickle",
            store_data=PersistenceInput(bot_data=False, chat_data=False, user_data=True, callback_data=False),
        )
        builder = Application.builder().token(s.token).persistence(persistence)
        if requests:
            builder = builder.request(requests[0]).get_updates_request(requests[1])
    b = builder
    application = b.post_init(post_init).post_stop(post_stop).post_shutdown(post_shutdown).build()
    application.bot_data["svc"] = service
    register(application)
    return application


def selftest() -> int:
    """Offline check of this copy of the code (python bot.py --selftest). The self-updater runs it on a
    new version, against a copy of the data, before switching to it. Exit code 0 = ok."""
    import tempfile

    from customs_bot import router

    data = Path(os.environ.get("DATA_DIR") or tempfile.mkdtemp(prefix="customs-selftest-"))
    data.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(data)
    os.environ["LIVE_LOOKUP"] = "0"
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:SELFTEST")
    src = Path(os.environ.get("SELFTEST_REFERENCE_DB") or ROOT / "data" / "reference.db")
    if not (data / "reference.db").exists() and src.exists():
        shutil.copy2(src, data / "reference.db")
    app = build_application(load_settings())
    svc = app.bot_data["svc"]

    async def checks() -> None:
        try:
            assert svc.about()
            r = svc.search("فاصوليا", 0)
            assert "0713" in r.text, r.text[:300]
            r = await svc.calculate(router.parse("0713330090 100000"))
            assert "إجمالي" in r.text, r.text[:300]
            r = await svc.calculate(router.parse("0713330090 1000$ @50"))
            assert "50,000" in r.text, r.text[:300]
            r = await svc.origin_intent(router.parse("منشأ 1905320000"))          # origin & agreements
            assert "21,750" in r.text, r.text[:300]
            r = await svc.origin_intent(router.parse("تصدير 0713330090 لتركيا"))
            assert "EUR.1" in r.text, r.text[:300]
            assert "oa|eu" in str(svc.agreements_reply().keyboard.to_dict())
        finally:
            await svc.live.close()

    asyncio.run(checks())
    print("selftest ok")
    return 0


def main() -> None:
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    s = load_settings()
    setup_logging(s.data_dir)
    if not s.token:
        log.error("TELEGRAM_BOT_TOKEN missing — run install_windows.bat (or copy .env.example to .env)")
        sys.exit("❌ TELEGRAM_BOT_TOKEN مش متسجل — شغّل install_windows.bat أو حط التوكن في ملف .env")
    lock = single_instance()
    if lock is None:
        log.info("another copy of the bot is already running on this computer — exiting")
        sys.exit(0)
    bundled = ROOT / "data" / "reference.db"
    if not s.reference_db.exists() and bundled.exists() and bundled != s.reference_db:
        s.reference_db.parent.mkdir(parents=True, exist_ok=True)   # first start on an empty volume
        shutil.copy2(bundled, s.reference_db)
    if not s.reference_db.exists():
        sys.exit(f"❌ ملف البيانات مش موجود: {s.reference_db}")
    app = build_application(s)
    counts = app.bot_data["svc"].ref.counts()
    log.info("starting — tariff lines: %s, GPC bricks: %s, private: %s", counts["tariff"], counts["gpc_brick"],
             s.private_mode)
    # keep messages sent while the computer was off (Telegram holds them up to 24h);
    # retry forever if the internet is not up yet (e.g. right after Windows starts)
    app.run_polling(drop_pending_updates=False, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
