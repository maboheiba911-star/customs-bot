"""Import a GS1 GPC file (JSON or XML as downloaded from gs1eg.org / gpc-browser.gs1.org).

    python tools/import_gpc.py "GPC as of May 2026 v20260528 EN.json"
    python tools/import_gpc.py gpc_ar.json        # a translated file adds searchable titles

The English file replaces the GPC tables; any other language is attached to the same codes.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from customs_bot import builder  # noqa: E402
from customs_bot.settings import load_settings  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("الاستخدام: python tools/import_gpc.py <ملف GPC json/xml>")
    path = Path(sys.argv[1])
    s = load_settings()
    bricks, lang, version = builder.read_gpc_file(path)
    if len(bricks) < 1000:
        sys.exit(f"❌ الملف فيه {len(bricks)} Brick بس — مش ملف GPC كامل")
    with builder.AtomicDB(s.reference_db) as conn:
        if lang == "EN":
            n = builder.write_gpc(conn, bricks, version=f"GPC {version} ({lang})",
                                  source="GS1 Global Product Classification")
        else:
            n = builder.add_gpc_translation(conn, bricks, lang)
    print(f"✅ GPC ({lang}, {version}): {n:,} Brick → {s.reference_db}")


if __name__ == "__main__":
    main()
