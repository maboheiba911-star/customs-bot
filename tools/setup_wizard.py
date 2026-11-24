"""Setup window for Windows (also works in a console elsewhere).

1. Gets the bot token from the clipboard (copy it in the BotFather chat), checks it with Telegram.
2. Writes .env, optionally adds the bot to Windows startup, starts the bot hidden in the background.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import unicodedata
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
LOG = ROOT / "data" / "bot.log"
# bot id : secret. The secret stops before a second copy of the same token (pasted twice) or any other character.
TOKEN_RE = re.compile(r"(\d{6,12}):([A-Za-z0-9_-]{30,50}?)(?=\1:|[^A-Za-z0-9_-]|$)", re.ASCII)
STARTUP_NAME = "CustomsBot.vbs"
IS_WIN = os.name == "nt"
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


# ----------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    """Undo what phones add when copying: Arabic digits, full-width letters, invisible direction marks."""
    text = unicodedata.normalize("NFKC", text or "").translate(_DIGITS)
    return "".join(ch for ch in text if ch in " \t\n" or unicodedata.category(ch) not in ("Cf", "Cc"))


def find_token(text: str) -> str | None:
    text = _clean(text)
    m = TOKEN_RE.search(text)
    if not m:              # copied across a line break on the phone: "1234:AAE… xyz" -> drop the spaces
        m = TOKEN_RE.search(re.sub(r"\s+", "", text))
    return f"{m.group(1)}:{m.group(2)}" if m else None


def mask(token: str) -> str:
    """What is safe to show on screen: the bot number (public) and the last 4 characters."""
    bot_id, _, secret = token.partition(":")
    return f"بوت رقم {bot_id} — آخره …{secret[-4:]}"


NETWORK_PREFIX = "مش قادر أوصل لتليجرام"
OLD_TOKEN_HELP = ("التوكن مش صحيح: تليجرام بيقول إنه قديم أو اتلغى.\n"
                  "   في BotFather: انزل لآخر رسالة خالص، ودوس على التوكن اللي فيها مرة واحدة — ومتدوسش Revoke.")


def check_token(token: str, tries: int = 3) -> tuple[bool, str]:
    """Ask Telegram who this token belongs to. -> (ok, @username or error). Retries a slow network."""
    import httpx

    data = None
    for i in range(tries):
        try:
            r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30)
            data = r.json()
            break
        except Exception as exc:          # no internet, DNS, TLS, a network that stalls Telegram ...
            err = exc
            if i + 1 < tries:
                time.sleep(3)
    if data is None:
        return False, f"{NETWORK_PREFIX} ({type(err).__name__}) — اتأكد من النت"
    if data.get("ok"):
        return True, "@" + data["result"].get("username", "")
    code = data.get("error_code")
    if code == 401:
        return False, OLD_TOKEN_HELP
    if code == 404:
        return False, "التوكن مش صحيح: ناقص أو فيه حرف زيادة — انسخه تاني بالضغط عليه مرة واحدة في BotFather"
    return False, f"التوكن مش صحيح — رد تليجرام: {code} {data.get('description', '')}".strip()


def read_secret(prompt: str = "> ") -> str:
    """One line from the user; on a real terminal it is not shown, so a screenshot can't leak the token."""
    try:
        tty = sys.stdin.isatty()
    except Exception:
        tty = False
    if tty:
        import getpass
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # a terminal without echo control just shows the text
            return getpass.getpass(prompt)
    return input(prompt)


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(values: dict[str, str]) -> None:
    current = read_env()
    current.update(values)
    current.setdefault("PRIVATE_MODE", "true")
    current.setdefault("LIVE_LOOKUP", "true")
    current.setdefault("AUTO_UPDATE_DAYS", "7")
    lines = ["# created by setup_wizard.py — the token is secret, do not share this file"]
    lines += [f"{k}={v}" for k, v in current.items()]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def pythonw() -> Path:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def startup_file() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not IS_WIN or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_NAME


def set_autostart(enabled: bool) -> str:
    target = startup_file()
    if target is None:
        if os.environ.get("PREFIX", "").startswith("/data/data/com.termux"):
            return "التشغيل التلقائي بعد إعادة تشغيل التليفون عن طريق Termux:Boot"
        return "التشغيل التلقائي متاح على ويندوز بس"
    if not enabled:
        target.unlink(missing_ok=True)
        return "التشغيل التلقائي مقفول"
    vbs = (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{ROOT}"\r\n'
        f'sh.Run """{pythonw()}"" ""{ROOT / "bot.py"}""", 0, False\r\n'
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(vbs, encoding="utf-16")          # UTF-16 so Arabic folder names work
    return "البوت هيشتغل لوحده كل ما الكمبيوتر يفتح"


def stop_running_bot() -> None:
    if not IS_WIN:
        return
    # if the running copy was started as Administrator, ask Windows for permission to stop it
    ps = ("$p = Get-NetTCPConnection -LocalPort 47391 -State Listen -ErrorAction SilentlyContinue | "
          "Select-Object -ExpandProperty OwningProcess -Unique; foreach ($i in $p) { try { "
          "Stop-Process -Id $i -Force -ErrorAction Stop } catch { Start-Process powershell -Verb RunAs "
          "-WindowStyle Hidden -Wait -ArgumentList ('-NoProfile -Command Stop-Process -Id ' + $i + ' -Force') } }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)


def start_bot() -> None:
    (ROOT / "data" / "STOPPED").unlink(missing_ok=True)   # starting it here on purpose: undo /shutdown
    kw = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True)
    if IS_WIN:
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True                  # keeps running after the terminal closes (Termux)
    subprocess.Popen([str(pythonw()), str(ROOT / "bot.py")], **kw)


def wait_for_start(seconds: int = 25, tick=None) -> tuple[bool, str]:
    start_size = LOG.stat().st_size if LOG.exists() else 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        for _ in range(10):
            time.sleep(0.1)
            if tick:
                tick()
        if LOG.exists():
            with open(LOG, encoding="utf-8", errors="ignore") as f:
                f.seek(start_size)
                new = f.read()
            if "Application started" in new or "starting —" in new:
                if "Conflict" in new or "InvalidToken" in new or "Unauthorized" in new:
                    break
                return True, ""
            if "Error" in new or "Traceback" in new:
                return False, new[-600:]
    return False, (LOG.read_text(encoding="utf-8", errors="ignore")[-600:] if LOG.exists() else "")


def finish(token: str, autostart: bool, tick=None) -> tuple[bool, list[str]]:
    msgs = []
    write_env({"TELEGRAM_BOT_TOKEN": token})
    msgs.append("✅ التوكن اتحفظ")
    msgs.append("✅ " + set_autostart(autostart))
    stop_running_bot()
    start_bot()
    ok, tail = wait_for_start(tick=tick)
    if ok:
        msgs.append("✅ البوت شغال دلوقتي في الخلفية")
    else:
        msgs.append("⚠️ البوت مش راضي يشتغل — ابعت محتوى data\\bot.log للدعم")
        if tail:
            msgs.append(tail[-300:])
    return ok, msgs


# --------------------------------------------------------------- GUI (Tk)
def run_gui() -> int:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("تثبيت بوت الجمارك")
    root.geometry("560x430")
    root.resizable(False, False)
    font = ("Segoe UI", 11)
    big = ("Segoe UI", 14, "bold")

    tk.Label(root, text="تثبيت بوت الجمارك", font=big).pack(pady=(14, 6))
    steps = ("1) افتح تليجرام وادخل على محادثة BotFather\n"
             "2) دوس مرة واحدة على التوكن (السطر البرتقالي) — هيتنسخ\n"
             "3) ارجع هنا ودوس «الصق التوكن»")
    tk.Label(root, text=steps, font=font, justify="right", anchor="e").pack(fill="x", padx=18)

    token_var = tk.StringVar(value=find_token(read_env().get("TELEGRAM_BOT_TOKEN", "")) or "")
    auto_var = tk.BooleanVar(value=True)
    status = tk.StringVar(value="")
    bot_name = {"v": ""}

    frame = tk.Frame(root)
    frame.pack(fill="x", padx=18, pady=10)
    entry = ttk.Entry(frame, textvariable=token_var, show="•", font=("Consolas", 11))
    entry.pack(side="left", fill="x", expand=True)

    def paste() -> None:
        try:
            clip = root.clipboard_get()
        except tk.TclError:
            clip = ""
        tok = find_token(clip)
        if tok:
            token_var.set(tok)
            status.set("تمام — التوكن اتلصق ✔")
        else:
            status.set("مفيش توكن في الحافظة — دوس على التوكن في BotFather الأول")

    ttk.Button(frame, text="📋 الصق التوكن", command=paste).pack(side="right", padx=(8, 0))
    ttk.Checkbutton(root, text="شغّل البوت لوحده مع تشغيل الكمبيوتر", variable=auto_var).pack(anchor="e", padx=18)
    tk.Label(root, textvariable=status, font=font, fg="#1a5d1a", wraplength=520, justify="right").pack(
        fill="x", padx=18, pady=8)

    buttons = tk.Frame(root)
    buttons.pack(pady=6)
    open_btn = ttk.Button(buttons, text="افتح البوت في تليجرام",
                          command=lambda: webbrowser.open(f"https://t.me/{bot_name['v'].lstrip('@')}"))

    def confirm() -> None:
        tok = find_token(token_var.get())
        if not tok:
            status.set("التوكن ناقص أو غلط — دوس «الصق التوكن»")
            return
        status.set("⏳ بتأكد من التوكن مع تليجرام...")
        root.update()
        ok, info = check_token(tok)
        if not ok:
            status.set("❌ " + info)
            return
        bot_name["v"] = info
        status.set(f"✅ التوكن صحيح للبوت {info} — بشغّل البوت...")
        root.update()
        ok, msgs = finish(tok, auto_var.get(), tick=root.update)
        status.set("\n".join(msgs) + (f"\n\nافتح {info} في تليجرام وابعت /start — أول /start بيسجّلك كمالك." if ok else ""))
        if ok:
            open_btn.pack(side="left", padx=6)

    ttk.Button(buttons, text="تأكيد وتشغيل", command=confirm).pack(side="left", padx=6)
    ttk.Button(buttons, text="إغلاق", command=root.destroy).pack(side="left", padx=6)
    root.mainloop()
    return 0


# ------------------------------------------------------------ console mode
def run_console() -> int:
    print("Paste the bot token from BotFather then press Enter / الصق التوكن ودوس Enter:")
    tok = None
    for _ in range(5):
        tok = find_token(input("> "))
        if tok:
            break
        print("Token not recognised, try again / التوكن مش واضح، جرّب تاني")
    if not tok:
        return 1
    ok, info = check_token(tok)
    print(("OK " if ok else "ERROR ") + info)
    if not ok:
        return 1
    ok, msgs = finish(tok, autostart=IS_WIN)
    print("\n".join(msgs))
    return 0 if ok else 1


def _apply_ipv4() -> None:
    if read_env().get("FORCE_IPV4", "").lower() in ("1", "true", "yes"):
        sys.path.insert(0, str(ROOT))
        from customs_bot import net
        net.prefer_ipv4()


def run_check_saved() -> int:
    """0 = the saved token still works (or the network can't tell us); 1 = none saved or Telegram rejects it."""
    tok = find_token(read_env().get("TELEGRAM_BOT_TOKEN", ""))
    if not tok:
        return 1
    _apply_ipv4()
    ok, info = check_token(tok)
    if ok:
        print(f"✅ التوكن المحفوظ شغال ({info})")
        return 0
    if info.startswith(NETWORK_PREFIX):
        return 0
    print("التوكن المحفوظ مبقاش شغال — هنحط الجديد.")
    return 1


def run_token_only() -> int:
    """Ask for the token in the terminal, check it with Telegram and save it — nothing else (Termux)."""
    _apply_ipv4()
    print("(التوكن مش هيظهر وانت بتلصقه — ده طبيعي عشان الأمان. الصق ودوس Enter)")
    for _ in range(8):
        try:
            tok = find_token(read_secret("> "))
        except EOFError:
            return 1
        if not tok:
            print("✖ مفيش توكن في اللي اتلصق — انسخه تاني من BotFather والصقه")
            continue
        print("وصلني: " + mask(tok))
        ok, info = check_token(tok)
        if ok:
            write_env({"TELEGRAM_BOT_TOKEN": tok})
            print(f"✅ التوكن شغال ({info})")
            return 0
        if info.startswith(NETWORK_PREFIX):     # the token looks right; only the network is slow — go on
            write_env({"TELEGRAM_BOT_TOKEN": tok})
            print("⚠️ النت مش واصل لتليجرام دلوقتي، فحفظت التوكن وهكمّل (لو فضلت المشكلة: جرّب الداتا بدل الواي فاي).")
            return 0
        print("✖ " + info)
    return 1


if __name__ == "__main__":
    if "--token-only" in sys.argv:
        sys.exit(run_token_only())
    if "--check-saved" in sys.argv:
        sys.exit(run_check_saved())
    if "--console" in sys.argv:
        sys.exit(run_console())
    try:
        sys.exit(run_gui())
    except Exception as exc:          # no Tk available -> console fallback
        print(f"(GUI unavailable: {exc}) — switching to console mode")
        sys.exit(run_console())
