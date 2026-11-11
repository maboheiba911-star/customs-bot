"""Understand a free-text message: tariff code, calculation, product search or GS1 search.

Examples that are understood:
    0713330090                      -> show tariff line
    0713                            -> list lines under heading 0713
    846595 1522500                  -> calculate (value in EGP, CIF)
    8703233090 25000$ @50.5         -> value in USD, converted at 50.5
    2401100010 100000 2000kg        -> with quantity (for specific duties)
    1905320000 100000 اوروبا        -> apply EU agreement rate
    0909610000 50000 شخصي           -> personal import (no advance income-tax payment)
    فاصوليا بيضاء / white beans      -> search the tariff
    gs1 chamomile / جي اس 1 بابونج   -> search GS1 GPC
    تصدير 0713330090                 -> export requirements of the line
    منشأ 1905320000 [قيمة]           -> best origin to import the line from (agreements ranked)
    استيراد 1905320000 من تركيا       -> import the line from one country (calc + certificate)
    تصدير 0713330090 إلى ألمانيا      -> export the line to one country (agreement, certificate, origin rule)
    اتفاقيات السعودية / اتفاقية الكوميسا -> agreements of a country / one agreement card
    1905320000 100000 من المانيا     -> calculation at the best rate for that origin
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .agreements import resolve_country
from .arabic import normalize, to_ascii_digits

CURRENCIES = {
    "$": "USD", "usd": "USD", "دولار": "USD", "دولارات": "USD", "دولار امريكي": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "يورو": "EUR",
    "£": "GBP", "gbp": "GBP", "استرليني": "GBP", "جنيه استرليني": "GBP",
    "cny": "CNY", "rmb": "CNY", "يوان": "CNY",
    "sar": "SAR", "ريال": "SAR", "ريال سعودي": "SAR",
    "aed": "AED", "درهم": "AED", "درهم اماراتي": "AED",
    "kwd": "KWD", "دينار كويتي": "KWD", "qar": "QAR", "ريال قطري": "QAR", "omr": "OMR", "ريال عماني": "OMR",
    "bhd": "BHD", "دينار بحريني": "BHD", "jod": "JOD", "دينار اردني": "JOD", "chf": "CHF", "فرنك": "CHF",
    "cad": "CAD", "دولار كندي": "CAD", "aud": "AUD", "دولار استرالي": "AUD", "jpy": "JPY", "ين": "JPY",
    "sek": "SEK", "nok": "NOK", "dkk": "DKK",
    "egp": "EGP", "جنيه": "EGP", "ج": "EGP", "جم": "EGP", "le": "EGP",
}
ORIGINS = {
    "eu": "eu", "اوروبا": "eu", "اوربا": "eu", "اوروبي": "eu", "اوربي": "eu", "الاوروبي": "eu",
    "uk": "uk", "بريطانيا": "uk", "انجلترا": "uk",
    "tr": "tr", "turkey": "tr", "تركيا": "tr", "تركي": "tr",
    "mercosur": "mercosur", "ميركسور": "mercosur", "الميركسور": "mercosur",
    "efta": "efta", "افتا": "efta", "الافتا": "efta", "سويسرا": "efta", "النرويج": "efta",
    "afcfta": "afcfta", "افريقيا": "afcfta", "افريقي": "afcfta", "الافريقيه": "afcfta",
    "gstp": "gstp", "gatt": "gatt", "الجات": "gatt", "wto": "gatt",
}
PERSONAL = {"شخصي", "personal", "استخدام شخصي"}
MULTIPLIERS = {"k": 1e3, "الف": 1e3, "الاف": 1e3, "m": 1e6, "مليون": 1e6, "ملايين": 1e6}
UNITS = {
    "kg": "kg", "كجم": "kg", "كيلو": "kg", "كج": "kg", "ك": "kg", "كيلوجرام": "kg",
    "l": "l", "لتر": "l", "ltr": "l", "liter": "l", "litre": "l",
    "ton": "ton", "t": "ton", "طن": "ton", "tons": "ton", "اطنان": "ton",
}
ORIGIN_WORDS = {"منشا", "المنشا", "origin", "/origin"}
IMPORT_WORDS = {"استيراد", "استورد", "هستورد", "import"}
AGREEMENT_WORDS = {"اتفاقيه", "اتفاقيات", "الاتفاقيه", "الاتفاقيات", "agreement", "agreements"}
FROM_WORDS = {"من", "from"}
TO_WORDS = {"الي", "لـ", "ل", "to"}
GS1_PREFIX = re.compile(r"^\s*(?:/?gs1|/?gpc|جي\s*اس\s*1?|جى\s*اس\s*1?|جي اس ون)\b[:\s]*", re.I)
EXPORT_PREFIX = re.compile(r"^\s*(?:/?export|تصدير|صادر|متطلبات التصدير)\s+", re.I)


@dataclass
class Intent:
    kind: str                      # calc | code | search | gpc | gpc_code | export | empty
                                   # origin | import_from | export_to | agreement
    code: str = ""
    value: float | None = None
    currency: str = "EGP"
    fx: float | None = None
    qty: dict[str, float] = field(default_factory=dict)
    origin: str | None = None
    personal: bool = False
    query: str = ""
    ignored: list[str] = field(default_factory=list)
    export: bool = False           # "تصدير <منتج>": search, then open the export view
    country: str | None = None     # ISO2 (or a bloc: EU / EFTA / MERCOSUR) for origin / destination


def _clean(text: str) -> str:
    t = to_ascii_digits(text or "")
    t = t.replace("٫", ".").replace("٬", ",").replace("،", " ").replace("ـ", "")
    return re.sub(r"\s+", " ", t).strip()


def is_code_token(tok: str) -> bool:
    if not re.fullmatch(r"\d[\d./\-]*", tok):
        return False
    d = re.sub(r"\D", "", tok)
    return 2 <= len(d) <= 10 and ("," not in tok)


def parse_amount(tok: str) -> tuple[float, str | None] | None:
    """'1522500', '1,522,500', '25000$', '$25k', '30k', '1.5m', '2000usd' -> (value, currency)."""
    t = tok.strip().lower()
    cur = None
    for sym in ("$", "€", "£"):
        if sym in t:
            cur = CURRENCIES[sym]
            t = t.replace(sym, "")
    m = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)([a-z؀-ۿ]*)", t)
    if not m:
        return None
    num, suffix = m.group(1), m.group(2)
    if "," in num and not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", num):
        return None
    val = float(num.replace(",", ""))
    if suffix:
        if suffix in MULTIPLIERS:
            val *= MULTIPLIERS[suffix]
        elif normalize(suffix) in CURRENCIES:
            cur = CURRENCIES[normalize(suffix)]
        else:
            return None
    return val, cur


def _parse_qty(tokens: list[str], i: int) -> tuple[str, float, int] | None:
    tok = tokens[i].lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z؀-ۿ]+)", tok)
    if m and normalize(m.group(2)) in UNITS:
        return UNITS[normalize(m.group(2))], float(m.group(1)), 1
    if re.fullmatch(r"\d+(?:\.\d+)?", tok) and i + 1 < len(tokens) and normalize(tokens[i + 1]) in UNITS:
        return UNITS[normalize(tokens[i + 1])], float(tok), 2
    return None


def _parse_extras(intent: Intent, rest: list[str]) -> None:
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("$", "€", "£"):
            intent.currency = CURRENCIES[tok]
            i += 1
            continue
        n = normalize(tok)
        if n in MULTIPLIERS and intent.value is not None and i == 0:
            intent.value *= MULTIPLIERS[n]
            i += 1
            continue
        if tok.startswith("@") or n in ("سعر", "بسعر", "rate"):
            num = tok[1:] if tok.startswith("@") else (rest[i + 1] if i + 1 < len(rest) else "")
            try:
                intent.fx = float(num.replace(",", ""))
            except ValueError:
                pass
            i += 1 if tok.startswith("@") else 2
            continue
        q = _parse_qty(rest, i)
        if q:
            unit, amount, used = q
            intent.qty[unit] = amount
            i += used
            continue
        two = normalize(" ".join(rest[i: i + 2]))
        if two in CURRENCIES and len(rest[i: i + 2]) == 2:
            intent.currency = CURRENCIES[two]
            i += 2
            continue
        if n in CURRENCIES:
            intent.currency = CURRENCIES[n]
            i += 1
            continue
        if n in ORIGINS:
            intent.origin = ORIGINS[n]
            intent.country = resolve_country(tok) or intent.country
            i += 1
            continue
        if n in PERSONAL or two in PERSONAL:
            intent.personal = True
            i += 2 if two in PERSONAL else 1
            continue
        if n in ("من", "بلد", "منشا", "المنشا", "from", "origin"):
            for j in (3, 2, 1):
                phrase = rest[i + 1: i + 1 + j]
                iso = resolve_country(" ".join(phrase)) if len(phrase) == j else None
                if iso:
                    intent.country = iso
                    pn = normalize(" ".join(phrase))
                    if pn in ORIGINS:
                        intent.origin = ORIGINS[pn]
                    i += 1 + j
                    break
            else:
                i += 1
            continue
        iso = resolve_country(" ".join(rest[i: i + 2])) if i + 1 < len(rest) else None
        if iso:
            intent.country = iso
            i += 2
            continue
        iso = resolve_country(tok)
        if iso:
            intent.country = iso
            i += 1
            continue
        intent.ignored.append(tok)
        i += 1


def _split_country(tokens: list[str], preps: set[str], *, attached: bool = False
                   ) -> tuple[list[str], str | None, list[str]]:
    """Find '<prep> <country>' (the last one wins) -> (tokens before, ISO, tokens after).
    attached=True also accepts the preposition glued to the name at the end: "لتركيا", "للسعودية"."""
    for i in range(len(tokens) - 1, -1, -1):
        if normalize(tokens[i]) in preps:
            for j in (3, 2, 1):
                phrase = tokens[i + 1: i + 1 + j]
                iso = resolve_country(" ".join(phrase)) if len(phrase) == j else None
                if iso:
                    return tokens[:i], iso, tokens[i + 1 + j:]
        elif attached and i > 0 and normalize(tokens[i]).startswith("ل"):
            for j in (3, 2, 1):
                if i + j != len(tokens):
                    continue
                iso = resolve_country(" ".join(tokens[i:]))
                if iso:
                    return tokens[:i], iso, []
    return tokens, None, []


def _product_intent(kind: str, tokens: list[str], country: str | None = None) -> Intent:
    """'<code> [value] [extras]' or '<product name>' after an origin keyword."""
    intent = Intent(kind=kind, country=country)
    if tokens and is_code_token(tokens[0]):
        intent.code = re.sub(r"\D", "", tokens[0])
        rest = tokens[1:]
        if rest:
            amt = parse_amount(rest[0])
            if amt:
                intent.value = amt[0]
                if amt[1]:
                    intent.currency = amt[1]
                rest = rest[1:]
            _parse_extras(intent, rest)
            if country:
                intent.country = country
    else:
        intent.query = " ".join(tokens).strip()
    return intent


def parse(text: str, *, mode: str | None = None, pending_code: str | None = None) -> Intent:
    t = _clean(text)
    if not t:
        return Intent(kind="empty")

    words = t.split(" ")
    n0 = normalize(words[0])
    if n0 in AGREEMENT_WORDS:
        return Intent(kind="agreement", query=" ".join(words[1:]).strip())
    if n0 in ("افضل", "احسن") and len(words) > 1 and normalize(words[1]) in ORIGIN_WORDS:
        return _product_intent("origin", words[2:])
    if n0 in ORIGIN_WORDS or (n0 in IMPORT_WORDS and len(words) == 1):
        return _product_intent("origin", words[1:])
    if n0 in IMPORT_WORDS:
        before, iso, after = _split_country(words[1:], FROM_WORDS)
        return _product_intent("import_from" if iso else "origin", before + after, iso)

    m = EXPORT_PREFIX.match(t)
    if m:
        rest = t[m.end():].strip()
        before, iso, _ = _split_country(rest.split(" "), TO_WORDS, attached=True)
        if iso and before:
            return _product_intent("export_to", before, iso)
        if rest.replace(".", "").replace(" ", "").replace("/", "").isdigit():
            return Intent(kind="export", code=re.sub(r"\D", "", rest))
        return Intent(kind="search", query=rest, export=True)

    m = GS1_PREFIX.match(t)
    if m or mode == "gs1":
        q = t[m.end():].strip() if m else t
        digits = re.sub(r"\D", "", q)
        if digits and len(digits) == 8 and q.replace(" ", "").isdigit():
            return Intent(kind="gpc_code", code=digits, query=q)
        return Intent(kind="gpc", query=q)

    toks = t.split(" ")
    if re.fullmatch(r"1000\d{4}", toks[0]) and len(toks) == 1:
        return Intent(kind="gpc_code", code=toks[0], query=toks[0])

    first_amount = parse_amount(toks[0])
    # a value sent right after the bot asked for it (code already chosen)
    second_is_amount = len(toks) >= 2 and parse_amount(toks[1]) is not None
    if pending_code and first_amount and not second_is_amount and (
        mode == "calc" or not is_code_token(toks[0])
    ):
        intent = Intent(kind="calc", code=pending_code, value=first_amount[0])
        if first_amount[1]:
            intent.currency = first_amount[1]
        _parse_extras(intent, toks[1:])
        return intent

    if is_code_token(toks[0]):
        code = re.sub(r"\D", "", toks[0])
        if len(toks) >= 2:
            amt = parse_amount(toks[1])
            if amt:
                intent = Intent(kind="calc", code=code, value=amt[0])
                if amt[1]:
                    intent.currency = amt[1]
                _parse_extras(intent, toks[2:])
                return intent
        if len(toks) == 1:
            return Intent(kind="code", code=code)

    return Intent(kind="search", query=t)
