"""Client for the public tariff service of the Egyptian Customs Authority (customs.gov.eg).

* ``LiveLookup.detail(code)``  -> fresh record for one tariff line (cached, with a circuit breaker)
* ``crawl_tariff(...)``         -> full download of the import tariff (used by /update and tools/)

Endpoints (public, no login):
  GET /Services/Tarif?type=1&chapterId=<n>&page=<p>      HTML list of codes "07/13/33/00/90"
  GET /Services/TrfDetails?trfNumber=<code>&trfType=<t>   JSON {Number, ShortDesc, Desc, Taxes[], Instructions[], ...}
      trfType=1 "وارد"  import view: all taxes incl. VAT + import requirements  (used for calculations)
      trfType=2 "صادر"  export view: export requirements (food safety, quarantine, export agreements)
      (trfType=0 is the site default and returns the export view)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (customs-bot; tariff lookup)",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "ar,en;q=0.8",
}
_CODE_RE = re.compile(r"ShowTariffDetails\((?:&#39;|')([^'&#]+)(?:&#39;|')\)")
_HS_RE = re.compile(r"\b(\d{2}/\d{2}/\d{2}/\d{2}/\d{2})\b")
IMPORT, EXPORT = 1, 2


def slashed(code10: str) -> str:
    c = re.sub(r"\D", "", code10)
    return "/".join(c[i: i + 2] for i in range(0, 10, 2))


def parse_codes(html: str) -> list[str]:
    codes = _CODE_RE.findall(html) or _HS_RE.findall(html)
    return list(dict.fromkeys(codes))


def has_next_page(html: str, page: int) -> bool:
    return f"page={page + 1}" in html


class LiveLookup:
    """Fetch one tariff line from customs.gov.eg with cache + circuit breaker."""

    def __init__(self, base_url: str, appdb, *, cache_hours: int = 24, timeout: float = 8.0, enabled: bool = True):
        self.base_url = base_url.rstrip("/")
        self.appdb = appdb
        self.cache_hours = cache_hours
        self.timeout = timeout
        self.enabled = enabled
        self._fail_count = 0
        self._open_until = 0.0
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=HEADERS, timeout=self.timeout, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def available(self) -> bool:
        return self.enabled and time.monotonic() >= self._open_until

    async def detail(self, code10: str, trf_type: int = IMPORT) -> tuple[dict, str] | None:
        """Return (record, fetched_at) or None. Never raises."""
        key = f"{code10}:{trf_type}"
        cached = self.appdb.cache_get(key, self.cache_hours)
        if cached:
            return cached
        if not self.available:
            return None
        try:
            r = await self._http().get(
                f"{self.base_url}/Services/TrfDetails", params={"trfNumber": slashed(code10), "trfType": trf_type}
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or "Taxes" not in data:
                raise ValueError("unexpected payload")
            if re.sub(r"\D", "", str(data.get("Number", ""))) not in ("", code10):
                raise ValueError("code mismatch")
        except Exception as exc:  # network, HTTP, JSON, format
            self._fail_count += 1
            if self._fail_count >= 3:
                self._open_until = time.monotonic() + 30 * 60   # pause live lookups for 30 min
                self._fail_count = 0
            log.warning("live lookup failed for %s: %s", code10, exc)
            return None
        self._fail_count = 0
        ts = self.appdb.cache_put(key, data)
        return data, ts


ProgressCB = Callable[[str], Awaitable[None]] | None


async def crawl_tariff(
    base_url: str = "https://customs.gov.eg",
    *,
    chapters: range = range(1, 98),
    concurrency: int = 3,
    throttle: float = 0.25,
    retries: int = 4,
    timeout: float = 45.0,
    progress: ProgressCB = None,
    min_codes: int = 1000,
    transport: httpx.AsyncBaseTransport | None = None,
    with_export: bool = True,
) -> list[dict]:
    """Download the full tariff: import view (taxes + import requirements) and, optionally,
    the export view's requirements. Returns raw records in the official JSON shape plus
    ExportInstructions / ExportInstructionCodes."""
    base = base_url.rstrip("/")
    sem = asyncio.Semaphore(concurrency)

    async def get(client: httpx.AsyncClient, path: str, params: dict) -> httpx.Response | None:
        for attempt in range(1, retries + 1):
            async with sem:
                await asyncio.sleep(throttle)
                try:
                    r = await client.get(base + path, params=params)
                    if r.status_code == 200:
                        return r
                    if r.status_code in (429, 503):
                        await asyncio.sleep(2 ** attempt)
                except (httpx.HTTPError, asyncio.TimeoutError):
                    pass
            await asyncio.sleep(min(30, 2 ** attempt))
        return None

    async def discover(client: httpx.AsyncClient, chapter: int) -> list[str]:
        codes: list[str] = []
        page = 1
        while page < 400:
            r = await get(client, "/Services/Tarif", {"type": IMPORT, "chapterId": chapter, "page": page})
            if r is None:
                break
            found = parse_codes(r.text)
            new = [c for c in found if c not in codes]
            if not new:
                break
            codes.extend(new)
            if not has_next_page(r.text, page):
                break
            page += 1
        return codes

    async def one(client: httpx.AsyncClient, code: str, trf_type: int) -> dict | None:
        r = await get(client, "/Services/TrfDetails", {"trfNumber": code, "trfType": trf_type})
        if r is None:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) and data.get("Number") else None

    async def detail(client: httpx.AsyncClient, code: str) -> dict | None:
        rec = await one(client, code, IMPORT)
        if rec is None:
            return None
        if with_export:
            exp = await one(client, code, EXPORT)
            if exp is not None:
                rec["ExportInstructions"] = exp.get("Instructions") or []
                rec["ExportInstructionCodes"] = exp.get("InstructionCodes") or []
        return rec

    async with httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True,
                                 transport=transport) as client:
        all_codes: list[str] = []
        seen: set[str] = set()
        done = 0
        for chunk_start in range(0, len(chapters), 10):
            chunk = list(chapters)[chunk_start: chunk_start + 10]
            results = await asyncio.gather(*(discover(client, ch) for ch in chunk))
            for codes in results:
                for c in codes:
                    if c not in seen:
                        seen.add(c)
                        all_codes.append(c)
            done += len(chunk)
            if progress:
                await progress(f"🔎 اكتشاف البنود: {done}/{len(chapters)} فصل — {len(all_codes):,} بند")
        if len(all_codes) < min_codes:
            raise RuntimeError(f"discovery returned only {len(all_codes)} codes — site layout changed or blocked")

        records: list[dict] = []
        batch = 200
        for i in range(0, len(all_codes), batch):
            part = all_codes[i: i + batch]
            got = await asyncio.gather(*(detail(client, c) for c in part))
            records.extend(g for g in got if g)
            if progress:
                await progress(f"⬇️ تحميل التفاصيل: {min(i + batch, len(all_codes)):,}/{len(all_codes):,}")
        missing = len(all_codes) - len(records)
        if missing > len(all_codes) * 0.05:
            raise RuntimeError(f"{missing} of {len(all_codes)} details failed — update aborted")
        return records
