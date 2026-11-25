"""Watch tariff lines on the customs site: duties and taxes, import requirements, export requirements.

A watch keeps the last snapshot seen live. The background check fetches each watched line fresh (both views) and
tells the people watching it what changed. A watch made while the site is unreachable gets its baseline at the
first successful check, without a false alarm; local data is never compared with live data.
"""
from __future__ import annotations

import hashlib
import html
import json

from .builder import parse_instructions

FIRST_CHECK = 10 * 60          # seconds after start
EVERY = 12 * 3600              # twice a day
MAX_WATCHES = 30               # per person — keeps the twice-daily check light on the phone's data


def _norm(s: str) -> str:
    return " ".join(str(s).split())


def snapshot(rec_import: dict, rec_export: dict) -> dict:
    """What we compare: taxes, and the import and export instructions (code — text), all normalized."""
    imp = parse_instructions(rec_import.get("Instructions"), rec_import.get("InstructionCodes"), "import")
    exp = parse_instructions(rec_export.get("Instructions"), rec_export.get("InstructionCodes"), "export")
    return {
        "taxes": sorted(_norm(t) for t in rec_import.get("Taxes") or [] if isinstance(t, str) and t.strip()),
        "imp": sorted(_norm(f"{i['code']} — {i['text']}") for i in imp),
        "exp": sorted(_norm(f"{i['code']} — {i['text']}") for i in exp),
    }


def fingerprint(snap: dict) -> str:
    return hashlib.sha1(json.dumps(snap, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


SECTIONS = (("taxes", "💰 الضرايب والرسوم"), ("imp", "📥 شروط الاستيراد"), ("exp", "📤 شروط التصدير"))


def diff_lines(old: dict, new: dict) -> list[str]:
    out: list[str] = []
    for key, title in SECTIONS:
        before, after = set(old.get(key) or []), set(new.get(key) or [])
        gone, came = sorted(before - after), sorted(after - before)
        if not gone and not came:
            continue
        out.append(f"<b>{title}</b>")
        out += [f"❌ اتشال: {html.escape(x[:200])}" for x in gone]
        out += [f"🆕 اتضاف: {html.escape(x[:200])}" for x in came]
    return out


def alert_text(code: str, name: str, old: dict, new: dict, when: str) -> str:
    lines = [f"🔔 <b>تغيير في البند <code>{code}</code></b>", f"<i>{html.escape(name[:90])}</i>",
             f"اتلاحظ في فحص {html.escape(when)} من موقع مصلحة الجمارك:", ""]
    lines += diff_lines(old, new)
    lines += ["", "راجع الحسابات والعقود والشحنات اللي على البند ده."]
    return "\n".join(lines)
