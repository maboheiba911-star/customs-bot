"""Pure-Python stand-in for the part of rapidfuzz the search uses.

rapidfuzz is a compiled package. On an Android phone (Termux) there is no ready-made build of it and no
compiler by default, so the bot falls back to this module there. Same scale (0-100), close results.
"""
from __future__ import annotations

from difflib import SequenceMatcher


def ratio(a: str, b: str) -> float:
    if not a and not b:
        return 100.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio() * 100.0


def token_set_ratio(a: str, b: str) -> float:
    t1, t2 = set(a.split()), set(b.split())
    if not t1 or not t2:
        return 0.0
    inter, d1, d2 = t1 & t2, t1 - t2, t2 - t1
    if inter and (not d1 or not d2):          # one side's words all appear in the other
        return 100.0
    sect = " ".join(sorted(inter))
    c1 = f"{sect} {' '.join(sorted(d1))}".strip()
    c2 = f"{sect} {' '.join(sorted(d2))}".strip()
    scores = [ratio(c1, c2)]
    if sect:
        scores += [ratio(sect, c1), ratio(sect, c2)]
    return max(scores)
