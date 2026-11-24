"""Get the bot's data (customs-bot-data.zip from /backup) straight from Telegram — no file permissions needed.

The owner forwards the backup file to the bot; this script waits for it with the bot token, downloads it
and unpacks reference.db / app.db / config.json into the data folder. Standard library only.

    python tools/fetch_backup.py <data-folder>        (the token comes from .env or TELEGRAM_BOT_TOKEN)
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
NAMES = ("reference.db", "app.db", "config.json")


def token_from_env() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    env = ROOT / ".env"
    if not tok and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    return tok


def ipv4_only() -> None:
    """Some networks stall IPv6 to Telegram: resolve names to IPv4 addresses only from now on."""
    import socket
    real = socket.getaddrinfo
    if getattr(real, "_v4", False):
        return

    def v4(host, *a, **kw):
        res = real(host, *a, **kw)
        return [r for r in res if r[0] == socket.AF_INET] or res
    v4._v4 = True
    socket.getaddrinfo = v4


def call(token: str, method: str, net_timeout: float = 70, **params) -> dict:
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    try:
        with urllib.request.urlopen(f"{API}/bot{token}/{method}", data=data, timeout=net_timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:        # Telegram answers errors (409, 401 ...) with a JSON body
        try:
            return json.loads(e.read().decode())
        except ValueError:
            return {"ok": False, "error_code": e.code, "description": str(e)}
    except (OSError, ValueError) as e:         # no network / timeout / DNS: the caller retries
        return {"ok": False, "error_code": 0, "description": f"network: {type(e).__name__}"}


def unpack(blob: bytes, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    done = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in NAMES:
            if name in z.namelist():
                (dest / name).write_bytes(z.read(name))
                done.append(name)
    if "reference.db" not in done:
        raise ValueError("الملف مفيهوش reference.db")
    return done


def wait_for_backup(token: str, dest: Path, minutes: float = 15, poll: int = 30, say=print) -> bool:
    deadline = time.time() + minutes * 60
    offset = None
    told_conflict = told_network = False
    network_errors = 0
    while time.time() < deadline:
        res = call(token, "getUpdates", net_timeout=poll + 20, offset=offset, timeout=poll,
                   allowed_updates='["message"]')
        if not res.get("ok"):
            if res.get("error_code") == 0:                  # network
                network_errors += 1
                if network_errors == 2:
                    ipv4_only()
                if network_errors >= 3 and not told_network:
                    say("⏳ النت مش واصل لتليجرام — لو على واي فاي جرّب الداتا. هفضل أحاول…")
                    told_network = True
                time.sleep(5)
                continue
            if res.get("error_code") == 409 and not told_conflict:
                say("⏳ البوت لسه شغال على الكمبيوتر — ابعتله من تليجرام: /shutdown yes")
                told_conflict = True
            elif res.get("error_code") == 401:
                say("✖ التوكن غلط.")
                return False
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            doc = msg.get("document") or {}
            if not doc.get("file_name", "").startswith("customs-bot-data"):
                continue
            f = call(token, "getFile", net_timeout=30, file_id=doc["file_id"])
            if not f.get("ok"):
                say("✖ مقدرتش أنزّل الملف من تليجرام — ابعته تاني.")
                continue
            with urllib.request.urlopen(f"{API}/file/bot{token}/{f['result']['file_path']}", timeout=120) as r:
                blob = r.read()
            try:
                names = unpack(blob, dest)
            except (zipfile.BadZipFile, ValueError) as exc:
                say(f"✖ الملف ده مش سليم ({exc}) — ابعت /backup من الكمبيوتر تاني.")
                continue
            call(token, "getUpdates", net_timeout=10, offset=offset, timeout=0)   # mark it as handled
            call(token, "sendMessage", chat_id=msg["chat"]["id"],
                 text="✅ ملف البيانات وصل للتليفون — كمّل في Termux.")
            say("✅ وصلت البيانات: " + "، ".join(names))
            return True
    return False


def main() -> int:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data"
    token = token_from_env()
    if not token:
        print("✖ التوكن مش موجود في .env")
        return 1
    me = {}
    for i in range(6):                         # a slow network: keep trying for a couple of minutes
        me = call(token, "getMe", net_timeout=30)
        if me.get("ok") or me.get("error_code") not in (0, None):
            break
        if i == 1:
            ipv4_only()
        print("⏳ بحاول أوصل لتليجرام… (لو على واي فاي جرّب الداتا)")
        time.sleep(5)
    if not me.get("ok"):
        print("✖ مقدرتش أكلّم تليجرام بالتوكن ده: " + str(me.get("description", "")))
        return 1
    print(f"\nدلوقتي في تليجرام (شات البوت @{me['result'].get('username', '')}):")
    print("  1) ابعت للبوت:  /shutdown yes   (عشان يقف على الكمبيوتر)")
    print("  2) دوسة طويلة على ملف customs-bot-data.zip ← Forward (إعادة توجيه) ← اختار نفس شات البوت")
    print("مستني الملف…")
    return 0 if wait_for_backup(token, dest) else 1


if __name__ == "__main__":
    sys.exit(main())
