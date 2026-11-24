"""Build customs_bot/cn2026.py — the EU Combined Nomenclature 2026 (8-digit CN codes) grouped by HS 6.

Source data: EU Combined Nomenclature 2026, SKOS RDF/XML from the Publications Office / Eurostat
(Regulation (EU) 2025/1926, Annex I), reused under CC BY 4.0 (Commission Decision 2011/833/EU).
Input used: the per-subheading text chunks in github.com/yorkzhangyong-bot/kestrel,
targets/hs_code/data/nomenclature_2026.jsonl (made from that SKOS file; "CN 2026 issued 2025-11-19, updated
2026-02-18"). Usage: python tools/build_cn2026.py path/to/nomenclature_2026.jsonl
"""
from __future__ import annotations

import base64
import json
import re
import sys
import zlib
from pathlib import Path

CN_LINE = re.compile(r"^CN (\d{4}) (\d{2}) (\d{2}) — (.+)$")


def build(src: Path) -> dict:
    out: dict[str, dict] = {}
    for raw in src.read_text(encoding="utf-8").splitlines():
        d = json.loads(raw)
        sub = (d.get("metadata") or {}).get("subheading", "")
        hs6 = sub.replace(".", "")
        if not re.fullmatch(r"\d{6}", hs6):
            continue
        lines = d["text"].splitlines()
        title = next((ln.split(" — ", 1)[1] for ln in lines if ln.startswith("Subheading ")), "")
        sub_i = next((i for i, ln in enumerate(lines) if ln.startswith("Subheading ")), None)
        sub_expl = lines[sub_i + 1] if sub_i is not None and sub_i + 1 < len(lines) \
            and not lines[sub_i + 1].startswith("CN ") else title
        cns = []
        for i, ln in enumerate(lines):
            m = CN_LINE.match(ln)
            if not m:
                continue
            label = m.group(4)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and not nxt.startswith("CN "):
                desc = nxt                                   # the self-explanatory text of this CN line
            elif label.strip(" :").lower() in ("other", "others"):
                desc = sub_expl                              # "Other" alone says nothing
            else:
                desc = label
            cns.append(["".join(m.group(1, 2, 3)), desc[:160]])
        if cns:
            out[hs6] = {"t": title[:160], "cn": cns}
    return out


def main() -> int:
    src = Path(sys.argv[1])
    data = build(src)
    blob = base64.b85encode(zlib.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                                          .encode("utf-8"), 9)).decode("ascii")
    n = sum(len(v["cn"]) for v in data.values())
    chunks = [blob[i: i + 100] for i in range(0, len(blob), 100)]
    body = "\n".join(f'    "{c}"' for c in chunks)
    Path(__file__).resolve().parent.parent.joinpath("customs_bot", "cn2026.py").write_text(f'''"""EU Combined Nomenclature 2026: the 8-digit CN codes under each HS 6 code ({len(data)} subheadings, {n} CN codes).

Source: European Commission — Combined Nomenclature 2026 (Regulation (EU) 2025/1926, Annex I), SKOS data from the
Publications Office / Eurostat, reused under CC BY 4.0 (Commission Decision 2011/833/EU). Built by
tools/build_cn2026.py; stored compressed to keep the download to the phone small.
"""
from __future__ import annotations

import base64
import json
import zlib
from functools import lru_cache

VERSION = "CN 2026"
SOURCE = "التعريفة الأوروبية CN 2026 — المفوضية الأوروبية (CC BY 4.0)"

_BLOB = (
{body}
)


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(zlib.decompress(base64.b85decode("".join(_BLOB))).decode("utf-8"))


def lines(hs6: str) -> list[tuple[str, str]]:
    """[(cn8, description)] under an HS 6 code (or under the first 6 digits of a longer code)."""
    rec = _data().get((hs6 or "")[:6])
    return [(c, d) for c, d in rec["cn"]] if rec else []


def title(hs6: str) -> str:
    rec = _data().get((hs6 or "")[:6])
    return rec["t"] if rec else ""


def fmt(cn8: str) -> str:
    return f"{{cn8[:4]}} {{cn8[4:6]}} {{cn8[6:8]}}"
''', encoding="utf-8")
    print(f"{len(data)} subheadings, {n} CN codes, {len(blob):,} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
