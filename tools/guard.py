"""Keeps the bot running on the phone (Termux).

Android can stop background apps to save battery. Every few minutes this guard checks that the bot is up
(bot.py holds 127.0.0.1:LOCK_PORT while it runs) and starts it again when it is not, unless:
  - the owner stopped it on purpose (/shutdown writes data/STOPPED), or
  - the self-updater is swapping files and restarting the bot (apply_update.py is running).
One guard per phone: it holds GUARD_PORT while it runs. It exits by itself if the bot folder goes away.

    python tools/guard.py          run the guard (the installer, Termux:Boot and bot.py on Termux start it)
    python tools/guard.py --once   one check, print what it did, exit
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_PORT = 47391            # bot.py holds this while it runs
GUARD_PORT = 47392           # this guard holds this while it runs
FIRST, EVERY, SLOW = 20, 180, 900   # seconds: first look, normal interval, after the bot kept failing
GIVE_UP_AFTER = 5            # starts in a row that did not stick → check every SLOW seconds instead
LOG_MAX = 200_000


def data_dir() -> Path:
    return ROOT / "data"


def log(msg: str) -> None:
    path = data_dir() / "guard.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX:
            path.replace(path.with_name("guard.log.1"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def taken(port: int) -> bool:
    """Something (the bot / a guard) holds this port. A bind test, like the updater's: a connect would leave
    connections nobody accepts piling up on the bot's lock socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def update_in_progress(proc: Path = Path("/proc")) -> bool:
    """The self-updater's helper is swapping files and restarting the bot — stay out of its way."""
    if not proc.is_dir():
        return False
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            if b"apply_update.py" in (p / "cmdline").read_bytes():
                return True
        except OSError:
            continue
    return False


def start_bot() -> None:
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    with open(data / "boot.log", "a", encoding="utf-8") as out:      # errors before logging starts land here
        subprocess.Popen([sys.executable, str(ROOT / "bot.py")], cwd=str(ROOT), stdin=subprocess.DEVNULL,
                         stdout=out, stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)


def check() -> str:
    """One look at the bot. Returns: gone | stopped | ok | updating | started."""
    if not (ROOT / "bot.py").exists():
        return "gone"
    if (data_dir() / "STOPPED").exists():
        return "stopped"
    if taken(LOCK_PORT):
        return "ok"
    if update_in_progress():
        return "updating"
    start_bot()
    return "started"


def hold(port: int | None = None) -> socket.socket | None:
    port = GUARD_PORT if port is None else port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def run(sleep=time.sleep, rounds: int | None = None) -> int:
    """The guard loop. `rounds` limits the checks (tests); None = forever."""
    lock = hold()
    if lock is None:
        return 0                                    # another guard is already watching
    log("guard started")
    misses, done = 0, 0
    sleep(FIRST)
    while rounds is None or done < rounds:
        done += 1
        what = check()
        if what == "gone":
            log("bot folder is gone — guard exits")
            break
        if what == "started":
            misses += 1
            log(f"bot was not running — started it again ({misses})")
        elif what == "ok":
            misses = 0
        sleep(SLOW if misses >= GIVE_UP_AFTER else EVERY)
    lock.close()
    return 0


def spawn() -> bool:
    """Start the guard in the background unless one is already running. True = started one."""
    if taken(GUARD_PORT):
        return False
    subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(ROOT), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    return True


if __name__ == "__main__":
    if "--once" in sys.argv[1:]:
        print(check())
        sys.exit(0)
    sys.exit(run())
