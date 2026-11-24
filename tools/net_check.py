"""Can this device reach Telegram? Tries normally and over IPv4 only; saves FORCE_IPV4=1 when that helps.

    python tools/net_check.py          -> one line per check + a verdict (exit 0 when Telegram is reachable)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TELEGRAM = "https://api.telegram.org/"
CONTROL = "https://api.github.com/zen"


def reach(url: str, timeout: float = 15) -> str:
    import httpx
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=False)
        return f"ok ({r.status_code})"
    except Exception as exc:
        return type(exc).__name__


def main() -> int:
    from customs_bot import net
    import setup_wizard

    control = reach(CONTROL)
    normal = reach(TELEGRAM)
    print(f"  النت العادي (GitHub): {'✓' if control.startswith('ok') else '✗ ' + control}")
    print(f"  تليجرام: {'✓' if normal.startswith('ok') else '✗ ' + normal}")
    if normal.startswith("ok"):
        return 0
    net.prefer_ipv4()
    v4 = reach(TELEGRAM)
    print(f"  تليجرام عن طريق IPv4: {'✓' if v4.startswith('ok') else '✗ ' + v4}")
    if v4.startswith("ok"):
        setup_wizard.write_env({"FORCE_IPV4": "1"})
        print("✅ الحل: IPv4 — اتظبط في الإعدادات (FORCE_IPV4=1)")
        return 0
    net.restore()
    print("✖ تليجرام مش بيرد على الشبكة دي من Termux.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
