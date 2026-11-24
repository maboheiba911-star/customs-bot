"""Import duty in the buyer's country, from WITS (World Bank) — UNCTAD TRAINS tariffs at HS 6 digits.

    https://wits.worldbank.org/API/V1/SDMX/V21/datasource/trn/reporter/R/partner/P/product/HS6/year/Y/datatype/reported?format=JSON

R and P are ISO numeric country codes; the EU is one reporter (918). Partner 000 (World) gives the MFN rate. A
reporter-partner pair only returns rows when the two have a preferential arrangement, so "no rows" for Egypt means
the buyer pays the MFN rate. The value is the simple average of the ad valorem rates of the importer's national
lines inside the HS 6 code; lines with specific duties (per kg...) are counted in NBR_NA_LINES, not in the average.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from . import agreements as A

BASE = "https://wits.worldbank.org/API/V1/SDMX/V21/datasource/trn"
EU_REPORTER = "918"
EGYPT = "818"
WORLD = "000"
TTL = 30 * 86400                # tariff schedules change once a year: a month of cache is plenty
NO_DATA_TTL = 7 * 86400
YEARS_BACK = 7                  # TRAINS lags: look back this many years for the latest schedule
COMPETITORS = ("CN", "IN", "TR", "US", "AR")

NUMERIC = {
    "AE": "784", "AL": "008", "AM": "051", "AO": "024", "AR": "032", "AT": "040", "AU": "036", "AZ": "031",
    "BA": "070", "BD": "050", "BE": "056", "BF": "854", "BG": "100", "BH": "048", "BI": "108", "BJ": "204",
    "BR": "076", "BW": "072", "BY": "112", "CA": "124", "CD": "180", "CF": "140", "CG": "178", "CH": "756",
    "CI": "384", "CL": "152", "CM": "120", "CN": "156", "CO": "170", "CV": "132", "CY": "196", "CZ": "203",
    "DE": "276", "DJ": "262", "DK": "208", "DZ": "012", "EE": "233", "ER": "232", "ES": "724", "ET": "231",
    "FI": "246", "FR": "250", "GA": "266", "GB": "826", "GE": "268", "GH": "288", "GM": "270", "GN": "324",
    "GQ": "226", "GR": "300", "GW": "624", "HK": "344", "HR": "191", "HT": "332", "HU": "348", "ID": "360",
    "IE": "372", "IN": "356", "IQ": "368", "IR": "364", "IS": "352", "IT": "380", "JO": "400", "JP": "392",
    "KE": "404", "KM": "174", "KR": "410", "KW": "414", "KZ": "398", "LB": "422", "LI": "438", "LK": "144",
    "LR": "430", "LS": "426", "LT": "440", "LU": "442", "LV": "428", "LY": "434", "MA": "504", "MD": "498",
    "ME": "499", "MG": "450", "MK": "807", "ML": "466", "MR": "478", "MT": "470", "MU": "480", "MW": "454",
    "MX": "484", "MY": "458", "MZ": "508", "NA": "516", "NE": "562", "NG": "566", "NL": "528", "NO": "578",
    "NZ": "554", "OM": "512", "PE": "604", "PH": "608", "PK": "586", "PL": "616", "PS": "275", "PT": "620",
    "PY": "600", "QA": "634", "RO": "642", "RS": "688", "RU": "643", "RW": "646", "SA": "682", "SC": "690",
    "SD": "729", "SE": "752", "SG": "702", "SI": "705", "SK": "703", "SL": "694", "SN": "686", "SO": "706",
    "SS": "728", "ST": "678", "SY": "760", "SZ": "748", "TD": "148", "TG": "768", "TH": "764", "TN": "788",
    "TR": "792", "TW": "158", "TZ": "834", "UA": "804", "UG": "800", "US": "840", "UY": "858", "UZ": "860",
    "VN": "704", "YE": "887", "ZA": "710", "ZM": "894", "ZW": "716",
}


@dataclass
class Rate:
    partner: str
    year: int
    avg: float | None           # simple average of ad valorem rates, %
    kind: str                   # MFN / PREF / ...
    lines: int = 0
    pref_lines: int = 0
    na_lines: int = 0           # lines with non ad valorem (specific) duties, left out of the average

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def reporter_for(iso: str) -> str | None:
    """The TRAINS reporter for a country: the EU reports for all its members."""
    if iso in A.EU or iso == "EU":
        return EU_REPORTER
    return NUMERIC.get(iso)


def url(reporter: str, partner: str, hs6: str, year: int, datatype: str = "reported") -> str:
    return (f"{BASE}/reporter/{reporter}/partner/{partner}/product/{hs6}/year/{year}"
            f"/datatype/{datatype}?format=JSON")


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(data: dict, partner: str) -> list[Rate]:
    """SDMX-JSON from the WITS TRAINS API -> one Rate per observation (usually one: one product, one year)."""
    try:
        st = data["structure"]
        obs_attrs = st["attributes"]["observation"]
        time_dim = st["dimensions"]["observation"][0]["values"]
        series = data["dataSets"][0]["series"]
    except (KeyError, IndexError, TypeError):
        return []
    names = [a.get("id", "").upper() for a in obs_attrs]

    def attr(values: list, name: str):
        if name not in names:
            return None
        i = names.index(name)
        raw = values[i + 1] if i + 1 < len(values) else None
        if isinstance(raw, int) and obs_attrs[i].get("values") and raw < len(obs_attrs[i]["values"]):
            return obs_attrs[i]["values"][raw].get("id")
        return raw

    out = []
    for ser in series.values():
        for t, values in (ser.get("observations") or {}).items():
            if not isinstance(values, list) or not values:
                continue
            try:
                year = int(time_dim[int(t)].get("id"))
            except (ValueError, IndexError, TypeError):
                year = 0
            out.append(Rate(partner=partner, year=year, avg=_num(values[0]),
                            kind=str(attr(values, "TARIFFTYPE") or ""),
                            lines=int(_num(attr(values, "TOTALNOOFLINES")) or 0),
                            pref_lines=int(_num(attr(values, "NBR_PREF_LINES")) or 0),
                            na_lines=int(_num(attr(values, "NBR_NA_LINES")) or 0)))
    return out


class TariffSource:
    """Fetches and caches TRAINS rates. kv: the app's key-value store (get, set)."""

    def __init__(self, kv_get, kv_set, transport: httpx.AsyncBaseTransport | None = None, now=time.time):
        self.kv_get, self.kv_set, self.transport, self.now = kv_get, kv_set, transport, now

    def _cached(self, key: str):
        raw = self.kv_get(key)
        if not raw:
            return None
        try:
            obj = json.loads(raw)
        except ValueError:
            return None
        ttl = TTL if obj.get("v") is not None else NO_DATA_TTL     # "no data" is re-checked sooner
        return obj if self.now() - obj.get("at", 0) < ttl else None

    def _store(self, key: str, value) -> None:
        self.kv_set(key, json.dumps({"at": self.now(), "v": value}))

    async def _get(self, client: httpx.AsyncClient, reporter: str, partner: str, hs6: str, year: int) -> Rate | None:
        key = f"wits|{reporter}|{partner}|{hs6}|{year}"
        hit = self._cached(key)
        if hit is not None:
            v = hit.get("v")
            return Rate(**v) if v else None
        r = await client.get(url(reporter, partner, hs6, year))
        if r.status_code >= 500:
            r.raise_for_status()                                       # the service is down: not "no data"
        if r.status_code >= 400 or not r.content.strip():              # WITS says "no data" this way
            rate = None
        else:
            try:
                rates = parse(r.json(), partner)
            except ValueError:          # "No data" comes back as text, not JSON
                rates = []
            pref = [x for x in rates if x.kind.upper().startswith("PR")]
            rate = (pref or rates or [None])[0]
        self._store(key, rate.as_dict() if rate else None)
        return rate

    async def latest_year(self, client: httpx.AsyncClient, reporter: str, hs6: str) -> tuple[int, Rate] | None:
        key = f"wits_year|{reporter}|{hs6}"
        hit = self._cached(key)
        if hit is not None and hit.get("v"):
            y = int(hit["v"])
            rate = await self._get(client, reporter, WORLD, hs6, y)
            if rate is not None:
                return y, rate
        this_year = datetime.fromtimestamp(self.now(), timezone.utc).year
        for y in range(this_year - 1, this_year - 1 - YEARS_BACK, -1):
            rate = await self._get(client, reporter, WORLD, hs6, y)
            if rate is not None and rate.avg is not None:
                self._store(key, y)
                return y, rate
        return None

    async def compare(self, iso: str, hs6: str, competitors=COMPETITORS) -> dict | None:
        """{'year', 'mfn': Rate, 'egypt': Rate|None, 'rivals': {iso2: Rate|None}} or None if no schedule."""
        reporter = reporter_for(iso)
        if not reporter:
            return None
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, transport=self.transport,
                                     headers={"User-Agent": "customs-bot (+telegram)"}) as client:
            found = await self.latest_year(client, reporter, hs6)
            if found is None:
                return None
            year, mfn = found
            rivals = [c for c in competitors if c != iso and reporter_for(c) != reporter]
            partners = [EGYPT] + [NUMERIC[c] for c in rivals if c in NUMERIC]
            got = await asyncio.gather(*(self._get(client, reporter, p, hs6, year) for p in partners),
                                       return_exceptions=True)
        def ok(x):
            return x if isinstance(x, Rate) else None
        return {"year": year, "reporter": reporter, "mfn": mfn, "egypt": ok(got[0]),
                "rivals": {c: ok(g) for c, g in zip([c for c in rivals if c in NUMERIC], got[1:])}}


# shaped like a real TRAINS answer (see tests/data/wits_us_mfn_2017.json) — for the offline self-test
SAMPLE = {
    "structure": {
        "dimensions": {
            "series": [{"id": "FREQ", "values": [{"id": "A"}]}, {"id": "REPORTER", "values": [{"id": "840"}]},
                       {"id": "PARTNER", "values": [{"id": "000"}]}, {"id": "PRODUCTCODE", "values": [{"id": "071333"}]},
                       {"id": "DATATYPE", "values": [{"id": "Reported"}]}],
            "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2017", "name": "2017"}]}]},
        "attributes": {"observation": [
            {"id": "NOMENCODE", "values": [{"id": "H5"}]}, {"id": "EXCLUDEDFROM", "values": []},
            {"id": "TARIFFTYPE", "values": [{"id": "MFN"}]}, {"id": "SUM_OF_RATES", "values": [{"id": "0"}]},
            {"id": "MIN_RATE", "values": [{"id": "0"}]}, {"id": "MAX_RATE", "values": [{"id": "0"}]},
            {"id": "TOTALNOOFLINES", "values": [{"id": "2"}]}, {"id": "NBR_PREF_LINES", "values": [{"id": "0"}]},
            {"id": "NBR_MFN_LINES", "values": [{"id": "1"}]}, {"id": "NBR_NA_LINES", "values": [{"id": "1"}]},
            {"id": "OBS_VALUE_MEASURE", "values": [{"id": "SimpleAverage"}]}]}},
    "dataSets": [{"series": {"0:0:0:0:0": {"observations": {"0": [0, 0, None, 0, 0, 0, 0, 0, 0, 0, 0, 0]}}}}],
}


# ------------------------------------------------------------------ the card
A2M = "https://trade.ec.europa.eu/access-to-markets/en/my-trade-assistant"
GTH = "https://intracen.org/resources/tools/global-trade-helpdesk"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:g}%"


def eu_lines(hs6: str, limit: int = 6) -> list[str]:
    """The EU 8-digit codes under an HS 6 code, ready to put on the invoice for an EU buyer."""
    from html import escape

    from . import cn2026
    cns = cn2026.lines(hs6)
    if not cns:
        return []
    out = [f"🏷️ <b>البند في أوروبا ({cn2026.VERSION})</b> — اكتبه في الفاتورة للمشتري الأوروبي:"]
    for code, desc in cns[:limit]:
        out.append(f"• <code>{cn2026.fmt(code)}</code> — {escape(desc[:120])}")
    if len(cns) > limit:
        out.append(f"• و{len(cns) - limit} بند تاني — ابعت <code>بند اوروبا {hs6}</code>")
    return out


def card(iso: str, hs6: str, desc: str | None, cmp: dict | None, ags: list) -> str:
    """Egypt vs MFN vs competitors in the buyer's market, with the sales argument and the certificate."""
    from html import escape

    from .formatters import short

    flag, name = A.country_flag(iso), A.country_name(iso)
    eu = reporter_for(iso) == EU_REPORTER
    check = (f"🔗 تأكيد البند بالظبط (من مصر لأوروبا): {A2M}" if eu
             else f"🔗 تأكيد البند بالظبط: Global Trade Helpdesk — {GTH}")
    L = [f"🌍 <b>جمارك المشتري — البند {hs6} في {flag} {escape(name)}</b>"]
    if desc:
        L.append(f"<i>{escape(short(desc, 100))}</i>")
    if cmp is None:
        L += ["", "مش لاقي جدول جمارك للبلد دي في قاعدة WITS لآخر سنين.", check]
        return "\n".join(L)
    mfn, eg, year = cmp["mfn"], cmp["egypt"], cmp["year"]
    who = "الاتحاد الأوروبي" if eu else name
    L += [f"📅 جدول جمارك {escape(who)} — سنة {year}", ""]
    pref = eg is not None and eg.avg is not None
    eg_v = eg.avg if pref else mfn.avg
    tag = "✅ تفضيلي" if pref and (mfn.avg is None or eg.avg < mfn.avg) else ("(تفضيلي)" if pref else "(السعر العادي)")
    L.append(f"🇪🇬 <b>من مصر: {_pct(eg_v)}</b> {tag}")
    L.append(f"🌐 السعر العادي (MFN): {_pct(mfn.avg)}")
    vals: dict[str, float] = {}
    if cmp["rivals"]:
        L.append("🏁 <b>المنافسين:</b>")
        for c, r in cmp["rivals"].items():
            has = r is not None and r.avg is not None
            v = r.avg if has else mfn.avg
            if v is not None:
                vals[c] = v
            L.append(f"• {A.country_flag(c)} {escape(A.country_name(c))}: {_pct(v)} ({'تفضيلي' if has else 'عادي'})")
    L.append("")
    if eg_v is not None and vals:
        worst_c = max(vals, key=vals.get)
        best_c = min(vals, key=vals.get)
        if all(abs(v - eg_v) < 0.005 for v in vals.values()) and abs((mfn.avg or 0) - eg_v) < 0.005:
            L.append("💡 الجمارك واحدة على الكل هنا — ميزتك هتبقى في السعر والجودة والشحن، مش في الجمارك.")
        else:
            if vals[worst_c] > eg_v:
                gap = vals[worst_c] - eg_v
                L.append(f"💡 <b>ميزتك:</b> المشتري بيدفع على بضاعتك {eg_v:g}% بدل {vals[worst_c]:g}% لو اشترى من "
                         f"{escape(A.country_name(worst_c))} — يعني بيوفّر حوالي {gap * 100:,.0f}$ على كل 10,000$.")
            if vals[best_c] < eg_v:
                L.append(f"⚠️ {escape(A.country_name(best_c))} بتدخل بـ {vals[best_c]:g}% — أقل منك بـ "
                         f"{eg_v - vals[best_c]:g} نقطة.")
    if ags:
        main = ags[0]
        if pref:
            L.append(f"📜 عشان المشتري ياخد السعر التفضيلي: {escape(main.cert_export)}")
        else:
            L.append(f"ℹ️ مصر ليها اتفاقية مع البلد دي ({escape(main.name)})، بس WITS مفيهوش سعر تفضيلي للبند ده "
                     f"لسنة {year}: يا إما البند مستثنى، يا إما البيانات ناقصة — اتأكد.")
    if eu:
        L += eu_lines(hs6)
    na = max([mfn.na_lines] + ([eg.na_lines] if eg else []))
    if na:
        L.append(f"⚠️ فيه {na} بند تحت الـ 6 أرقام عليه رسوم نوعية (بالكيلو مثلاً) ومش داخلة في المتوسط.")
    L.append("\n<i>المصدر: WITS (البنك الدولي) — بيانات UNCTAD TRAINS. ده متوسط بنود بلد المشتري تحت الـ 6 أرقام، "
             "والبند عنده ممكن يتقسم لبنود أدق بأسعار مختلفة.</i>")
    L.append(check)
    return "\n".join(L)
