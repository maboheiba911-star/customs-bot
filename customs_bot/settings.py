"""Runtime settings: secrets from .env, tax assumptions from config.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:  # optional dependency; plain environment variables work without it
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # pragma: no cover
    pass


@dataclass
class CalcSettings:
    vat_default_pct: float = 14.0
    income_tax_advance_pct: float = 1.0
    income_tax_base: str = "value"            # value | value+duty
    dev_fee_base: str = "value+duty"          # value | value+duty
    table_tax_base: str = "value+duty"        # official note: من القيمة + ر.ض.جمركية
    fees_base: str = "value"                  # chamber / health fees
    vat_base_includes: list[str] = field(
        default_factory=lambda: ["duty", "dev", "table", "health", "chambers"]
    )
    round_to: int = 2


@dataclass
class Settings:
    token: str = ""
    admin_ids: set[int] = field(default_factory=set)
    allowed_ids: set[int] = field(default_factory=set)
    private_mode: bool = True          # only the owner / allowed users can use the bot
    data_dir: Path = ROOT / "data"
    reference_db: Path = ROOT / "data" / "reference.db"
    app_db: Path = ROOT / "data" / "app.db"
    live_lookup: bool = True
    live_cache_hours: int = 24
    auto_update_days: int = 7
    official_base_url: str = "https://customs.gov.eg"
    nafeza_url: str = "https://www.nafeza.gov.eg/"
    bot_name: str = "بوت الجمارك"
    calc: CalcSettings = field(default_factory=CalcSettings)
    dev_fees: dict[str, float] = field(default_factory=dict)   # HS prefix -> %


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on", "نعم"}


def load_settings(config_path: Path | None = None) -> Settings:
    s = Settings()
    s.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    s.admin_ids = {
        int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()
    }
    s.allowed_ids = {
        int(x) for x in os.getenv("ALLOWED_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()
    }
    s.private_mode = _bool(os.getenv("PRIVATE_MODE"), True)
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        s.data_dir = Path(data_dir)
        s.reference_db = s.data_dir / "reference.db"
        s.app_db = s.data_dir / "app.db"
    s.live_lookup = _bool(os.getenv("LIVE_LOOKUP"), True)
    s.live_cache_hours = int(os.getenv("LIVE_CACHE_HOURS", "24") or 24)
    s.auto_update_days = int(os.getenv("AUTO_UPDATE_DAYS", "7") or 0)
    s.bot_name = os.getenv("BOT_NAME", s.bot_name) or s.bot_name

    cfg_file = config_path or (ROOT / "config.json")
    if cfg_file.exists():
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        calc = cfg.get("calc", {})
        for k, v in calc.items():
            if hasattr(s.calc, k) and not k.startswith("_"):
                setattr(s.calc, k, v)
        s.dev_fees = {
            str(k): float(v) for k, v in (cfg.get("development_fee_by_hs_prefix") or {}).items()
            if not str(k).startswith("_")
        }
    return s
