"""Official exchange rates from the Central Bank of Egypt (cbe.org.eg), server-rendered HTML table:
    Currency | Buy | Sell      e.g.  US Dollar | 53.2491 | 53.3874      "Rates for Date: 10/11/2026"
"""
from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger(__name__)

CBE_URL = "https://www.cbe.org.eg/en/economic-research/statistics/cbe-exchange-rates"
NAMES = {
    "us dollar": ("USD", 1), "euro": ("EUR", 1), "pound sterling": ("GBP", 1), "canadian dollar": ("CAD", 1),
    "danish krone": ("DKK", 1), "norwegian krone": ("NOK", 1), "swedish krona": ("SEK", 1),
    "swiss franc": ("CHF", 1), "japanese yen 100": ("JPY", 100), "saudi riyal": ("SAR", 1),
    "kuwaiti dinar": ("KWD", 1), "uae dirham": ("AED", 1), "australian dollar": ("AUD", 1),
    "bahraini dinar": ("BHD", 1), "omani riyal": ("OMR", 1), "qatari riyal": ("QAR", 1),
    "jordanian dinar": ("JOD", 1), "chinese yuan": ("CNY", 1),
}
_ROW = re.compile(
    r"<td[^>]*>\s*([A-Za-z][A-Za-z .()0-9]*?)\s*</td>\s*<td[^>]*>\s*([\d.,]+)\s*</td>\s*<td[^>]*>\s*([\d.,]+)\s*</td>",
    re.S,
)
_DATE = re.compile(r"Rates\s+for\s+Date:\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)


def parse_cbe(html: str) -> tuple[str | None, dict[str, tuple[float, float]]]:
    """-> (rate date dd/mm/yyyy, {ISO code: (buy, sell)}) with JPY converted to 1 yen."""
    rates: dict[str, tuple[float, float]] = {}
    for name, buy, sell in _ROW.findall(html or ""):
        key = " ".join(name.lower().split())
        if key in NAMES:
            code, unit = NAMES[key]
            try:
                rates[code] = (float(buy.replace(",", "")) / unit, float(sell.replace(",", "")) / unit)
            except ValueError:
                continue
    m = _DATE.search(html or "")
    return (m.group(1) if m else None), rates


async def fetch_cbe(url: str = CBE_URL, *, timeout: float = 15.0,
                    transport: httpx.AsyncBaseTransport | None = None) -> tuple[str | None, dict]:
    headers = {"User-Agent": "Mozilla/5.0 (customs-bot; exchange rates)", "Accept-Language": "en"}
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True,
                                 transport=transport) as client:
        r = await client.get(url)
        r.raise_for_status()
        date, rates = parse_cbe(r.text)
        if "USD" not in rates:
            raise ValueError("CBE page layout changed (no US Dollar row)")
        return date, rates
