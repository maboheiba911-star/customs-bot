"""Download the full import tariff from customs.gov.eg and rebuild data/reference.db.

Run it from any PC that can open customs.gov.eg (for example your PC in Egypt):
    python tools/update_tariff.py
Then either restart the bot on the same machine, or send data/reference.db to the bot
(as the admin) and it will swap the data in.

Options:
    --raw-only      only save data/tariff_raw.json (no DB rebuild)
    --from-raw F    rebuild the DB from an existing raw JSON file (no download)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from customs_bot import builder  # noqa: E402
from customs_bot.official import crawl_tariff  # noqa: E402
from customs_bot.settings import load_settings  # noqa: E402


async def progress(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-only", action="store_true")
    ap.add_argument("--from-raw", type=Path)
    args = ap.parse_args()
    s = load_settings()

    if args.from_raw:
        records, meta = builder.load_raw_tariff(args.from_raw)
        fetched = (meta.get("fetched_at") or "")[:10]
    else:
        print("⏳ تحميل التعريفة من customs.gov.eg — ممكن ياخد 20-40 دقيقة...")
        records = asyncio.run(crawl_tariff(s.official_base_url, progress=progress))
        fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw = s.data_dir / "tariff_raw.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(json.dumps({"meta": {"fetched_at": fetched, "source": s.official_base_url},
                                   "records": records}, ensure_ascii=False), encoding="utf-8")
        print(f"✅ اتحفظ {len(records):,} بند في {raw}")
        if args.raw_only:
            return

    with builder.AtomicDB(s.reference_db) as conn:
        n = builder.write_tariff(conn, records, source="customs.gov.eg (Egyptian Customs Authority)",
                                 snapshot=fetched)
    print(f"✅ قاعدة البيانات اتحدثت: {n:,} بند → {s.reference_db}")


if __name__ == "__main__":
    main()
