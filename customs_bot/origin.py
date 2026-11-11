"""Origin & trade agreements on top of the official Egyptian tariff.

Import (into Egypt) — what the official line (customs.gov.eg, import view) says per origin:
  * agreement sections in Taxes   ("اتفاقيه تركيا" -> "ضريبة الوارد : 25.5%")
  * instruction codes             (ر6663 EU industrial goods -100%, ر6659 EU exclusion, ر6790 AfCFTA ...)
  translate() reads one instruction, analyze() turns a line into options per agreement / country,
  price() runs the bot's calculator on every option, and the cards rank them.

Export (from Egypt) — the export view records, on some lines, how partners treat Egyptian goods
  (غ6502: Turkey excludes Egyptian agricultural goods ...). translate_export() reads those notes and
  export_card() combines them with agreements.py (certificate, approved exporter, origin rule).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from html import escape

from . import agreements as A
from .arabic import normalize, to_ascii_digits
from .calc import CalcResult, calculate
from .formatters import SEP, money, short
from .taxparse import Agreement as TaxAgreement
from .taxparse import Component, ParsedTaxes, fmt_pct

DEFAULT_VALUE = 100_000.0
OPT_KEY = "__origin"
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _n(text: str) -> str:
    return normalize(text or "").replace(" ", "")


# =============================================================== import instructions
@dataclass
class Effect:
    """What one official import instruction means for the origin of the goods."""
    code: str
    agreement: str | None      # agreements.AGREEMENTS key; None = origin measure outside agreements
    kind: str                  # exempt | reduce | rates | reduce_unknown | excluded | conditional | note
                               # antidumping | safeguard | origin_check | origin_measure | shipping
    pct: float | None = None
    countries: tuple[str, ...] = ()   # only these members (empty = every member of the agreement)
    group: str = ""                   # AfCFTA group أ/ب, Mercosur list, Serbia group
    quota: bool = False
    text: str = ""


EFTA_WORDS = (("ايسلند", "IS"), ("نرويج", "NO"), ("سويسر", "CH"), ("ليختنشتاين", "LI"))
MERCOSUR_WORDS = (("ارجنتين", "AR"), ("برازيل", "BR"), ("اوروجواي", "UY"), ("اورجواي", "UY"),
                  ("باراجواي", "PY"), ("باراغواي", "PY"))
BILATERAL_WORDS = (("سودان", "sd"), ("مغرب", "ma"), ("سوري", "sy"), ("فلسطين", "ps"), ("ليبي", "ly"),
                   ("اردن", "jo"), ("تونس", "tn"))
# words that show an instruction is about a tariff preference (not, say, a European standard)
AGREEMENT_HINTS = ("اتفاق", "شراكه", "اطار", "ظل", "تجمع", "بروتوكول", "اعفا", "تعفي", "يعفي", "تخفض",
                   "تخفيض", "يخفض", "ستثن", "استثا")
CODE_AGREEMENT = {"ر6712": "mercosur"}          # text names no agreement; it sits in the Mercosur series
CODE_REASON = {"ر6712": "التخفيض حسب ما تحدده اللجنة المشتركة"}
_MEASURE_COUNTRIES = {
    "صين": "CN", "يابان": "JP", "هند": "IN", "تركيا": "TR", "كوريا": "KR", "روسيا": "RU", "اوكرانيا": "UA",
    "امريكا": "US", "اندونيسيا": "ID", "ماليزيا": "MY", "فيتنام": "VN", "تايوان": "TW", "تايلاند": "TH",
    "برازيل": "BR", "سعوديه": "SA", "امارات": "AE",
}


def _measure_countries(raw: str) -> tuple[str, ...]:
    """Countries named after 'منشأ' / 'من' in a measure ("منشأ الصين", "منشأ يابانى", "من الصين")."""
    out: list[str] = []
    for m in re.finditer(r"(?:^|\s)(?:منشا|منشاه|من)\s+(\S+)", normalize(raw)):
        w = m.group(1)
        w = w[2:] if w.startswith("ال") else w
        for cand in (w, re.sub(r"(?:يه|ي)$", "", w), w + "ا"):
            iso = _MEASURE_COUNTRIES.get(cand)
            if iso:
                if iso not in out:
                    out.append(iso)
                break
    return tuple(out)


def _afcfta_group(raw: str, n: str) -> str:
    m = re.search(r"\[\s*([أاب])\s*\]", raw)
    if m:
        return "ب" if m.group(1) == "ب" else "أ"
    m = re.search(r"افريقيه(القاريه)?(مجموعه)?([اب])?", n)
    if m and m.group(3):
        return "ب" if m.group(3) == "ب" else "أ"
    return ""


def _which_agreement(code: str, n: str, spaced: str, raw: str) -> tuple[str | None, tuple[str, ...], str]:
    if code in CODE_AGREEMENT:
        return CODE_AGREEMENT[code], (), ""
    if not any(h in n for h in AGREEMENT_HINTS):
        return None, (), ""
    if ("مملكه" in n and "متحده" in n) or "بريطاني" in n:
        return "uk", (), ""
    if "اوربي" in n or "اوروبي" in n:
        return "eu", (), ""
    efta = tuple(iso for w, iso in EFTA_WORDS if w in n)
    if "افتا" in n or efta:
        return "efta", efta, ""
    if "تركيا" in n:
        return "tr", (), ""
    if "ميركسور" in n:
        countries = tuple(dict.fromkeys(iso for w, iso in MERCOSUR_WORDS if w in n))
        m = re.search(r"قايمه ([ابجده])(?:\s|$)", spaced)
        group = {"ا": "أ", "ه": "هـ"}.get(m.group(1), m.group(1)) if m else ""
        return "mercosur", countries, group
    if "صربيا" in n:
        group = "1" if "الاولي" in n else "2" if "الثانيه" in n else "3" if "الثالثه" in n else ""
        return "serbia", (), group
    if "كوميسا" in n:
        return "comesa", ("SD",) if "سودان" in n else (), ""
    if "افريقي" in n and ("قاريه" in n or "حره" in n):
        return "afcfta", (), _afcfta_group(raw, n)
    if "gstp" in n:
        return "gstp", (), ""
    for word, key in BILATERAL_WORDS:
        if word in n:
            return key, (), ""
    return None, (), ""


def _which_kind(code: str, n: str, pct: float | None) -> str:
    if code in CODE_REASON:
        return "reduce_unknown"
    if "فيات" in n and "قرين" in n:                 # "طبقا للفئات الموضحة قرين كل بند"
        return "rates"
    if "الالمايرد" in n or "تقديممستند" in n or "يشترط" in n:
        return "conditional"
    if re.search(r"ستثن|استثا", n):
        return "excluded"
    if "قيوداستيراديه" in n or "ماستيراديه" in n:    # import approvals, not duties
        return "note"
    if pct is not None and any(w in n for w in ("تخفض", "تخفيض", "يخفض", "بنسبه")):
        return "exempt" if pct >= 100 else "reduce"
    if any(w in n for w in ("يعفي", "تعفي", "اعفاكامل")):
        return "exempt"
    if any(w in n for w in ("تخفض", "تخفيض", "يخفض")):
        return "reduce_unknown"
    return "note"


def translate(code: str, text: str) -> Effect | None:
    """One official import instruction -> its meaning for the origin, or None if unrelated."""
    raw = to_ascii_digits(text or "")
    spaced = normalize(raw)
    n = spaced.replace(" ", "")
    if not n:
        return None
    pcts = [float(x) for x in _PCT.findall(raw)]
    pct = pcts[0] if pcts else None
    applies = "تحصيل" in n or "تخضع" in n or "يخضع" in n
    if "اغراق" in n:                                  # anti-dumping (or the admin notes about it)
        return Effect(code, None, "antidumping", pct, _measure_countries(raw), text=text) if applies else None
    if "تدابيروقاييه" in n:                         # normalize() turns ئ into ي
        return Effect(code, None, "safeguard", pct, text=text) if applies else None
    if "اشعاع" in n and "منشا" in n:
        return Effect(code, None, "origin_check", None, _measure_countries(raw), text=text)
    if "مشحون" in n and "بلدالمنشا" in n:
        return Effect(code, None, "shipping", text=text)
    if "نقلالركاب" in n:                              # transport agreement, no tariff effect
        return None
    agreement, countries, group = _which_agreement(code, n, spaced, raw)
    if agreement is None:
        found = _measure_countries(raw)
        if found and "منشا" in n and applies:
            return Effect(code, None, "origin_measure", None, found, text=text)
        return None
    kind = _which_kind(code, n, pct)
    quota = "حصص" in n or "حصه" in n
    return Effect(code, agreement, kind, pct if kind == "reduce" else None, countries, group, quota, text)


# ====================================================================== options
EXEMPT = Component(kind="duty", label="ضريبة الوارد", pct=0.0)


def reduction(pct: float) -> Component:
    return Component(kind="duty", label="ضريبة الوارد", reduction_pct=float(pct))


# order of agreements in cards; Agadir members all sit in GAFTA, so it only shows in country cards
IMPORT_ORDER = ("eu", "uk", "efta", "tr", "mercosur", "serbia", "afcfta", "gstp", "gafta", "agadir", "comesa",
                "sd", "ma", "sy", "ps", "ly", "jo", "tn")
LINE_LEVEL = ("eu", "uk", "efta", "tr", "mercosur", "serbia", "afcfta")   # published per tariff line
BILATERAL = ("sd", "ma", "sy", "ps", "ly", "jo", "tn")
TAX_KEYS = {"eu": ("eu", ""), "uk": ("uk", ""), "tr": ("tr", ""), "mercosur": ("mercosur", ""),
            "efta": ("efta", ""), "afcfta_a": ("afcfta", "أ"), "afcfta_b": ("afcfta", "ب"), "gstp": ("gstp", "")}
STATUS_RANK = {"official": 0, "conditional": 1, "rule": 2, "unknown": 3, "excluded": 4}


@dataclass
class Option:
    agreement: str
    countries: tuple[str, ...] = ()          # only these members (empty = every member)
    status: str = "official"                 # official | conditional | rule | unknown | excluded
    duty: Component | None = None
    group: str = ""
    quota: bool = False
    excluding: tuple[str, ...] = ()          # members left out (shown on a separate option)
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    total: float | None = None
    res: CalcResult | None = None
    worse: bool = False                      # costs more than the general rate: no point using it

    @property
    def members(self) -> tuple[str, ...]:
        if self.countries:
            return self.countries
        return tuple(c for c in A.AGREEMENTS[self.agreement].members if c not in self.excluding)

    def covers(self, iso: str) -> bool:
        return iso in self.members


@dataclass
class Analysis:
    options: list[Option]
    measures: list[Effect]                   # anti-dumping, safeguards, checks by origin
    notes: list[Effect]                      # agreement notes without a duty effect
    live: bool                               # instructions came from the official site
    general: Component | None
    value: float = DEFAULT_VALUE
    general_total: float | None = None
    general_res: CalcResult | None = None

    def for_country(self, iso: str) -> list[Option]:
        rep = A.members_of(iso)[0]
        return sorted((o for o in self.options if o.covers(rep)), key=self.rank_key)

    def best_for(self, iso: str) -> Option | None:
        cands = [o for o in self.for_country(iso)
                 if o.duty is not None and o.status in ("official", "conditional", "rule") and not o.worse]
        return min(cands, key=self.rank_key) if cands else None

    def level(self, o: Option) -> float:
        d = o.duty
        if d is None:
            return 1e9
        if d.reduction_pct is not None and d.pct is None and d.specific is None:
            base = self.general.pct if self.general and self.general.pct is not None else 100.0
            return base * (1 - d.reduction_pct / 100.0)
        return d.pct if d.pct is not None else 1e9 - 1

    def rank_key(self, o: Option):
        return (o.total if o.total is not None else 1e18, self.level(o), STATUS_RANK.get(o.status, 9))


def _options_for(key: str, effects: list[Effect], sections: dict[str, Component], live: bool,
                 notes: list[Effect]) -> list[Option]:
    ag = A.AGREEMENTS[key]
    parts: dict[tuple, list[Effect]] = {}
    for e in effects:
        if e.kind == "note":
            notes.append(e)
            continue
        parts.setdefault((e.countries, e.group if key == "afcfta" else ""), []).append(e)
    for g in sections:
        parts.setdefault(((), g), [])
    out: list[Option] = []
    for (countries, pgroup), pe in parts.items():
        kinds = {e.kind for e in pe}
        o = Option(key, countries, group=pgroup or next((e.group for e in pe if e.group), ""),
                   quota=any(e.quota for e in pe), codes=[e.code for e in pe])
        sec = sections.get(pgroup) if not countries else None
        if sec is not None:
            o.status, o.duty = "official", sec
            if "excluded" in kinds:
                o.notes.append("مستثنى من الإعفاء الكامل — بيتطبق السعر المكتوب على البند")
        elif "excluded" in kinds:
            o.status = "excluded"
        elif "conditional" in kinds:
            o.status, o.duty = "conditional", EXEMPT
        elif "exempt" in kinds:
            o.status, o.duty = "official", EXEMPT
        elif "reduce" in kinds:
            o.status, o.duty = "official", reduction(max(e.pct for e in pe if e.kind == "reduce"))
        else:
            o.status = "unknown"
            o.reason = next((CODE_REASON[e.code] for e in pe if e.code in CODE_REASON), "")
            if not o.reason:
                o.reason = ("الفئة التفضيلية مش ظاهرة في تعريفة البند" if "rates" in kinds
                            else "فيه تخفيض بس النسبة مش مذكورة")
            if key == "mercosur" and o.group:
                o.reason += f" (قائمة {o.group})"
        o.notes += [short(e.text, 150) for e in pe if e.kind == "conditional"]
        out.append(o)
    if ag.import_rule == "exempt" and not any(not o.countries for o in out):
        covered = tuple(c for o in out for c in o.countries)
        out.append(Option(key, (), "rule", EXEMPT, excluding=covered))
    if not out and not live and key in LINE_LEVEL:
        out.append(Option(key, (), "unknown", reason="محتاج تعليمات البند من موقع الجمارك (مش متاح دلوقتي)"))
    return out


def analyze(parsed: ParsedTaxes, instructions: list[dict] | None, *, live: bool) -> Analysis:
    effects = [e for e in (translate(i.get("code", ""), i.get("text", "")) for i in instructions or []) if e]
    sections: dict[tuple[str, str], Component] = {}
    for ag in parsed.agreements:
        if ag.key in TAX_KEYS and "duty" in ag.components:
            sections[TAX_KEYS[ag.key]] = ag.components["duty"]
    options: list[Option] = []
    notes: list[Effect] = []
    for key in IMPORT_ORDER:
        options += _options_for(key, [e for e in effects if e.agreement == key],
                                {g: c for (k, g), c in sections.items() if k == key}, live, notes)
    return Analysis(options, [e for e in effects if e.agreement is None], notes, live, parsed.get("duty"))


def with_duty(parsed: ParsedTaxes, o: Option) -> ParsedTaxes:
    """A copy of the line where the import duty is the option's duty (for the calculator)."""
    p = copy.deepcopy(parsed)
    p.agreements = [TaxAgreement(key=OPT_KEY, name=plain_label(o), components={"duty": copy.deepcopy(o.duty)})]
    return p


def price(an: Analysis, parsed: ParsedTaxes, value: float, *, code: str = "", settings=None,
          qty: dict | None = None, dev_fee: float | None = None, personal: bool = False) -> Analysis:
    an.value = value
    kw = dict(code=code, settings=settings, qty=qty, dev_fee_pct=dev_fee, personal=personal)
    an.general_res = calculate(parsed, value, **kw)
    an.general_total = None if an.general_res.incomplete else an.general_res.total
    for o in an.options:
        if o.duty is None:
            continue
        o.res = calculate(with_duty(parsed, o), value, origin=OPT_KEY, **kw)
        o.res.warnings = [w for w in o.res.warnings if "شهادة منشأ" not in w]
        o.total = None if o.res.incomplete else o.res.total
        o.worse = o.total is not None and an.general_total is not None and o.total > an.general_total + 0.5
    return an


# ===================================================================== labels
def plain_agreement(key: str) -> str:
    """Short agreement name without its emoji (for sentences and buttons)."""
    return _plain(key)


def _plain(key: str) -> str:
    name = A.short_name(key)
    first, _, rest = name.partition(" ")
    return rest if rest and not first[:1].isalpha() else name


def _names(isos) -> str:
    names = [A.country_name(c) for c in isos]
    if len(names) == 2:
        return f"{names[0]} و{names[1]}"
    return "، ".join(names)


def label(o: Option) -> str:
    if o.agreement in BILATERAL:
        return A.short_name(o.agreement)
    if o.countries:
        flags = "".join(A.flag(c) for c in o.countries[:4])
        return f"{flags} {_names(o.countries)} ({_plain(o.agreement)})"
    text = A.short_name(o.agreement)
    if o.agreement == "afcfta" and o.group:
        text += f" — مجموعة {o.group}"
    if o.excluding:
        text += f" (ماعدا {_names(o.excluding)})"
    return text


def plain_label(o: Option) -> str:
    text = label(o)
    first, _, rest = text.partition(" ")
    return rest if rest and not first[:1].isalpha() else text


def detail(o: Option) -> str:
    if o.status == "excluded":
        return "مستثنى في البند ده"
    if o.status == "unknown":
        return o.reason
    d = o.duty
    if d is None:
        return ""
    if d.reduction_pct is not None and d.pct is None and d.specific is None:
        txt = "معفى" if d.reduction_pct >= 100 else f"تخفيض {fmt_pct(d.reduction_pct)}%"
    elif d.pct == 0 and d.specific is None and d.minimum is None:
        txt = "معفى"
    else:
        txt = f"وارد {d.display()}"
    if o.status == "rule":
        txt += " كقاعدة عامة"
    elif o.status == "conditional":
        txt += " بشرط"
    if o.quota:
        txt += " في حدود حصة"
    return txt


def _tot(x: float | None) -> str:
    return f"{money(x)} ج" if x is not None else "محتاج الكمية"


def _saving(o: Option, an: Analysis) -> str:
    if o.total is None or an.general_total is None or an.general_total - o.total < 0.5:
        return ""
    return f" (توفير {money(an.general_total - o.total)})"


def measure_lines(an: Analysis, iso: str | None = None) -> list[str]:
    rep = A.members_of(iso)[0] if iso else None
    out: list[str] = []
    for e in an.measures:
        if rep and e.countries and rep not in e.countries:
            continue
        where = f" على منشأ {_names(e.countries)}" if e.countries else ""
        if e.kind == "antidumping":
            out.append(f"⚠️ رسم إغراق{where}: {escape(short(e.text, 110))} — اتأكد إن صنفك هو المقصود")
        elif e.kind == "safeguard":
            pct = f" {fmt_pct(e.pct)}%" if e.pct else ""
            out.append(f"⚠️ رسم وقاية{pct} على الوارد من أغلب المناشئ (حتى مع الاتفاقيات) — مش داخل في الأرقام")
        elif e.kind == "origin_check":
            what = "فحص إشعاعي في ميناء الوصول" if "اشعاع" in _n(e.text) else escape(short(e.text, 100))
            flags = "".join(A.flag(c) for c in e.countries)
            out.append(f"🔬 {flags} منشأ {_names(e.countries)}: {what}")
        elif e.kind == "origin_measure":
            out.append(f"⚠️ إجراء خاص{where}: {escape(short(e.text, 110))}")
        elif e.kind == "shipping":
            out.append("📦 شرط شحن: الوارد للتجارة لازم يتشحن من بلد المنشأ أو من مراكز الشركة المنتجة "
                       "(الشراء من تاجر في بلد تالت ممكن يتعارض مع الشرط ده)")
    return out


# ============================================================== import: ranking
def rank_targets(an: Analysis) -> list[tuple[str, str]]:
    """(button label, country / bloc) for the best options, to open the country card."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    natural = {"eu": "EU", "uk": "GB", "tr": "TR", "serbia": "RS", "efta": "CH", "mercosur": "BR",
               "sd": "SD", "ma": "MA", "sy": "SY", "ps": "PS", "ly": "LY", "jo": "JO", "tn": "TN"}
    for o in sorted(an.options, key=an.rank_key):
        if o.status not in ("official", "conditional") or o.worse:
            continue
        iso = o.countries[0] if o.countries else natural.get(o.agreement)
        if not iso or iso in seen:
            continue
        seen.add(iso)
        out.append((f"{A.country_flag(iso)} {A.country_name(iso)}", iso))
    return out[:6]


def rank_text(item, an: Analysis, *, source: str, currency_note: str | None = None) -> str:
    L = [f"🏆 <b>أفضل منشأ للاستيراد — البند {item.code}</b>", escape(short(item.desc_ar, 110))]
    L.append(f"💰 على قيمة <b>{money(an.value)} ج</b> CIF" + (f" ({escape(currency_note)})" if currency_note else ""))
    gen = an.general
    L.append(f"📍 من غير اتفاقية: {('وارد ' + gen.display()) if gen else 'وارد غير مسجل'} ← "
             f"إجمالي الضرائب <b>{_tot(an.general_total)}</b>")
    zero = gen is None or (gen.pct in (None, 0) and gen.specific is None and gen.minimum is None)
    if zero:
        L.append("")
        L.append("ℹ️ الوارد على البند ده صفر أصلاً — المنشأ مش هيغيّر الجمارك. "
                 "الفرق هيبقى في الاشتراطات والفحوص بس.")
    else:
        official = sorted((o for o in an.options if o.status in ("official", "conditional") and not o.worse),
                          key=an.rank_key)
        if official:
            L.append("")
            L.append("✅ <b>تفضيلات مسجلة على البند</b> (تعريفة الجمارك)")
            for i, o in enumerate(official[:9], 1):
                L.append(f"{i}) {escape(label(o))} — {escape(detail(o))} ← <b>{_tot(o.total)}</b>{_saving(o, an)}")
            if any(o.agreement == "afcfta" for o in official):
                L.append("   ↳ الأفريقية القارية: مجموعة البلد وتفعيله للاتفاقية بيتأكدوا مع المستخلص")
        rules = sorted((o for o in an.options if o.status == "rule" and o.agreement != "agadir"),
                       key=an.rank_key)
        if rules:
            L.append("")
            L.append("🌍 <b>قاعدة عامة — يتأكد مع المستخلص</b>")
            for o in rules:
                L.append(f"• {escape(label(o))} — معفى عادةً ← {_tot(o.total)}")
        unknown = [o for o in an.options if o.status == "unknown"]
        if unknown:
            L.append("")
            L.append("❓ <b>مش واضح على البند</b>")
            for o in unknown[:6]:
                L.append(f"• {escape(label(o))}: {escape(o.reason)}")
        worse = [o for o in an.options if o.worse]
        if worse:
            L.append("↗️ سعر الاتفاقية أعلى من العام (ادفع بالعام): " + "، ".join(escape(plain_label(o)) for o in worse))
    excluded = [o for o in an.options if o.status == "excluded"]
    if excluded:
        L.append("")
        L.append("⛔ <b>مستثنى في البند ده:</b> " + "، ".join(escape(plain_label(o)) for o in excluded))
        if any(o.agreement in ("tn", "jo") for o in excluded):
            L.append("   ↳ الاستثناء من الاتفاقية الثنائية — العربية الكبرى ممكن تنطبق (يتأكد)")
    extra = measure_lines(an)
    quota = [o for o in an.options if o.quota and o.status != "excluded"]
    if quota:
        extra.append("⚖️ «في حدود حصة»: التخفيض لكمية محددة في السنة، وبعدها بالسعر العام")
    if extra:
        L.append("")
        L += extra
    if not an.live:
        L.append("")
        L.append("⚠️ الإعفاءات والاستثناءات اللي في تعليمات البند بتيجي من موقع الجمارك مباشرة، "
                 "والموقع مش متاح دلوقتي — الترتيب ده من النسخة المحلية وناقص.")
    L.append("")
    L.append("📌 أي سعر تفضيلي لازمله شهادة منشأ مقبولة وقواعد منشأ مستوفاة، وإلا بيتحاسب بالعام.")
    L.append(f"💡 قيمة تانية: <code>منشأ {item.code} 250000</code> — بلد معيّن: <code>استيراد {item.code} من الصين</code>")
    L.append(escape(source))
    return "\n".join(L)


# ======================================================= import: one country
def _agreement_title(key: str) -> str:
    return A.AGREEMENTS[key].name if key in BILATERAL else _plain(key)


def _status_line(o: Option) -> str:
    icon = {"official": "✅", "conditional": "🟡", "rule": "🌍", "unknown": "❓", "excluded": "⛔"}[o.status]
    text = f"{icon} {escape(_agreement_title(o.agreement))}: {escape(detail(o))}"
    if o.status == "rule":
        text += " — يتأكد مع المستخلص"
    if o.agreement == "afcfta" and o.group:
        text += f" (مجموعة {o.group} — يتأكد إن البلد فيها ومفعّل الاتفاقية)"
    if o.worse:
        text += " — أعلى من السعر العام"
    return text


def calc_lines(res: CalcResult) -> list[str]:
    out = []
    for ln in res.lines:
        if ln.key == "dev" and not ln.amount:
            continue
        amt = f"{money(ln.amount)} ج" if ln.amount is not None else "محتاج الكمية"
        out.append(f"• {escape(ln.label)} {escape(ln.rate_text)} = <b>{amt}</b>")
        if ln.note:
            out.append(f"   ↳ {escape(ln.note)}")
    return out


def import_card(item, an: Analysis, iso: str, *, source: str, currency_note: str | None = None) -> str:
    rep = A.members_of(iso)[0]
    L = [f"📥 <b>استيراد البند {item.code} من {A.country_flag(iso)} {escape(A.country_name(iso))}</b>",
         escape(short(item.desc_ar, 110))]
    covering = A.agreements_for(iso, direction="import")
    opts = an.for_country(iso)
    best = an.best_for(iso)
    L.append("")
    if not covering:
        L.append("🤝 مفيش اتفاقية تجارة تفضيلية بين مصر والبلد ده — بيتحاسب بالسعر العام.")
        if rep == "CN":
            L.append("ℹ️ إعفاء الصين (من 1 مايو 2026) للصادرات المصرية بس — الوارد من الصين لمصر مش معفى.")
    else:
        L.append("🤝 <b>الاتفاقيات اللي بتغطي البلد:</b>")
        shown = set()
        for o in opts:
            L.append(_status_line(o))
            shown.add(o.agreement)
        for ag in covering:
            if ag.key in shown or ag.key in ("gatt",):
                continue
            if ag.key in BILATERAL or ag.key == "gstp":
                continue
            L.append(f"➖ {escape(_agreement_title(ag.key))}: " + ("مفيش تفضيل مسجل على البند"
                                                        if an.live else "محتاج تعليمات البند من موقع الجمارك"))
        for o in opts:
            for note in o.notes:
                L.append(f"   ↳ {escape(note)}")
        for e in an.notes:
            if e.agreement in {o.agreement for o in opts} or (e.agreement and rep in A.AGREEMENTS[e.agreement].members):
                L.append(f"   ↳ {escape(short(e.text, 140))}")
    if best is not None:
        ag = A.AGREEMENTS[best.agreement]
        L.append("")
        L.append(f"📜 <b>المطلوب من المورد:</b> {escape(ag.cert_import)}")
        if best.status == "rule" and any(o.status == "excluded" for o in opts):
            L.append("⚠️ البند مستثنى من اتفاقية تانية مع البلد ده — اتأكد إن القاعدة العامة بتنطبق "
                     "قبل ما تعتمد على الإعفاء.")
    extra = measure_lines(an, iso)
    if extra:
        L.append("")
        L += extra
    res = best.res if best is not None and best.res is not None else an.general_res
    if res is not None:
        L.append("")
        what = f"سعر {plain_label(best)}" if best is not None else "السعر العام"
        cur = f" ({escape(currency_note)})" if currency_note else ""
        L.append(f"🧮 <b>الحساب على {money(an.value)} ج{cur} — {escape(what)}</b>")
        L += calc_lines(res)
        L.append(SEP)
        total = None if res.incomplete else res.total
        line = f"<b>إجمالي الضرائب: {_tot(total)}</b>"
        if best is not None:
            line += _saving(best, an)
        L.append(line)
        if best is not None and an.general_total is not None:
            L.append(f"ℹ️ من غير شهادة منشأ مقبولة: {money(an.general_total)} ج (السعر العام)")
    if not an.live:
        L.append("⚠️ تعليمات البند (الإعفاءات والاستثناءات) محتاجة موقع الجمارك، ومش متاح دلوقتي — النتيجة ناقصة.")
    L.append(escape(source))
    return "\n".join(L)


def no_preference_reason(an: Analysis, iso: str) -> str:
    covering = A.agreements_for(iso, direction="import")
    if not covering:
        return f"مفيش اتفاقية تفضيلية مع {A.country_name(iso)} — اتحسب بالسعر العام."
    opts = an.for_country(iso)
    if any(o.status == "excluded" for o in opts):
        return f"البند مستثنى من اتفاقية {A.country_name(iso)} — اتحسب بالسعر العام."
    if any(o.status == "unknown" for o in opts) or not an.live:
        return f"تفضيل {A.country_name(iso)} على البند مش واضح — اتحسب بالسعر العام (يتأكد)."
    return f"مفيش تفضيل مسجل على البند لـ{A.country_name(iso)} — اتحسب بالسعر العام."


def cert_note(o: Option) -> str:
    return f"السعر التفضيلي ده بشرط: {A.AGREEMENTS[o.agreement].cert_import}."


# ====================================================================== export
@dataclass
class ExportNote:
    code: str
    agreement: str | None
    kind: str                     # excluded | partial | exempt | covered | requirement | banned
    countries: tuple[str, ...]    # destination countries (empty = every member of the agreement)
    scope: str
    text: str


_DEST = (("تركيا", "TR"), ("ايسلند", "IS"), ("سويسر", "CH"), ("نرويج", "NO"), ("ليختنشتاين", "LI"),
         ("سودان", "SD"), ("ليبيا", "LY"), ("هايتي", "HT"))
_EU_WORDS = ("الجماعهالاوروبيه", "المجموعهالاوروبيه", "الاتحادالاوروبي", "الجماعهالاوربيه", "شراكهاوربيه")


def translate_export(code: str, text: str) -> ExportNote | None:
    """An export-view instruction -> what it says about a destination, or None."""
    n = _n(text)
    if not n:
        return None
    dests = tuple(dict.fromkeys(iso for w, iso in _DEST if w in n))
    eu = any(w in n for w in _EU_WORDS)
    scope = "زراعي" if ("زراعي" in n or "سمك" in n or "اسماك" in n) else ("صناعي" if "صناعي" in n else "")
    if ("عدمالسماحبتصدير" in n or "يحظرتصدير" in n) and dests:
        return ExportNote(code, None, "banned", dests, scope, text)
    agreement = ("efta" if "افتا" in n else "comesa" if "كوميسا" in n else "eu" if eu
                 else "tr" if "TR" in dests and any(w in n for w in ("اعفا", "تخفيض", "ستثن")) else None)
    if agreement is None:
        if dests and ("يشترط" in n or "شروط" in n):
            return ExportNote(code, None, "requirement", dests, scope, text)
        return None
    if agreement == "eu" and ("شروط" in n or "يشترط" in n):
        return ExportNote(code, "eu", "requirement", (), scope, text)
    if "ستثن" in n:
        kind = "partial" if "الكامل" in n else "excluded"
    elif "اعفا" in n or "تعفي" in n or "يعفي" in n:
        kind = "exempt"
    else:
        kind = "covered"
    countries = tuple(d for d in dests if agreement != "eu")
    return ExportNote(code, agreement, kind, countries, scope, text)


def _note_applies(note: ExportNote, rep: str) -> bool:
    if note.countries:
        return rep in note.countries
    return bool(note.agreement) and rep in A.AGREEMENTS[note.agreement].members


def _export_meaning(note: ExportNote, name: str) -> str:
    what = {"زراعي": "الزراعية", "صناعي": "الصناعية"}.get(note.scope, "")
    goods = f"الصادرات {what} المصرية" if what else "الصادرات المصرية"
    if note.kind == "excluded":
        return f"⛔ {goods} إلى {name} مستثناة من الإعفاء والتخفيض ← المستورد هيدفع جمارك {name} العادية."
    if note.kind == "partial":
        return (f"🟡 {goods} مش معفاة بالكامل في {name} — ممكن حصص أو تخفيض جزئي. "
                "راجع سعر البند عندهم قبل ما تسعّر.")
    if note.kind == "exempt":
        return f"✅ {goods} معفاة من الرسوم في {name} (بشهادة منشأ الاتفاقية)."
    if note.kind == "covered":
        if note.scope == "صناعي":
            return f"✅ السلع الصناعية المصرية داخلة في الاتفاقية عند التصدير إلى {name} — معفاة عادةً."
        return f"✅ البند داخل في الاتفاقية عند التصدير إلى {name} — المعاملة حسب الاتفاق الزراعي الثنائي (يتأكد للبند)."
    if note.kind == "requirement":
        return f"📋 شرط للتصدير إلى {name}: {escape(short(note.text, 140))}"
    if note.kind == "banned":
        return f"🚫 {escape(short(note.text, 140))}"
    return escape(short(note.text, 140))


def origin_hint(code: str) -> str:
    ch = int(code[:2]) if code[:2].isdigit() else 0
    if ch <= 14:
        rule = ("منتج زراعي: بياخد منشأ مصري لو متزرع ومتحصد في مصر. "
                "لو مستورد وبتعيد تصديره بعد فرز أو تعبئة بس، مش هيبقى منشأ مصري.")
    elif ch <= 24:
        rule = ("مصنّع غذائي: الخامات المستوردة لازم يحصلها تشغيل كافي حسب قاعدة البند "
                "(غالباً تغيير البند أو حد أقصى لقيمة الخامات المستوردة).")
    elif ch <= 27:
        rule = "خامات: بتاخد منشأ مصري لو مستخرجة من مصر."
    elif 50 <= ch <= 63:
        rule = "منسوجات وملابس: غالباً لازم مرحلتين تصنيع في مصر (زي من الغزل للقماش للملبوس) — يتأكد من قاعدة البند."
    else:
        rule = "منتج صناعي: لازم تشغيل كافي في مصر حسب قاعدة البند (تغيير البند أو نسبة قيمة مضافة)."
    return rule + " العمليات البسيطة لوحدها (تعبئة، فرز، تغليف، لصق علامات) مش بتدي منشأ."


def buyer_line(ag: A.Agreement | None, excluded: bool) -> str:
    if ag is None or not ag.cert_export_en:
        return "Origin: Egypt. A certificate of origin can be provided."
    if excluded:
        return f"Origin: Egypt. Note: this product gets no tariff preference under the {ag.en}."
    return f"Origin: Egypt. Preferential proof of origin under the {ag.en} can be provided ({ag.cert_export_en})."


def export_card(item, instructions: list[dict], source: str, iso: str) -> str:
    rep = A.members_of(iso)[0]
    name = A.country_name(iso)
    L = [f"📤 <b>تصدير البند {item.code} إلى {A.country_flag(iso)} {escape(name)}</b>",
         escape(short(item.desc_ar, 110)), ""]
    notes, reqs = [], []
    for ins in instructions or []:
        note = translate_export(ins.get("code", ""), ins.get("text", ""))
        if note is None:
            reqs.append(ins)
        elif _note_applies(note, rep):
            notes.append(note)
    ags = [a for a in A.agreements_for(iso, direction="export")]
    main = None
    if not ags:
        L.append("🤝 <b>مفيش اتفاقية تفضيلية</b> — المستورد هيدفع جمارك بلده العادية.")
        if rep == "US":
            L.append("ℹ️ فيه نظام المناطق الصناعية المؤهلة (QIZ): منتجات مصانع QIZ بتدخل أمريكا معفاة بشروط — يتأكد.")
        if rep in ("RU", "BY", "KZ", "AM"):
            L.append("ℹ️ مفيش اتفاقية سارية مع الاتحاد الاقتصادي الأوراسي لحد دلوقتي.")
        L.append("📜 <b>الشهادة:</b> شهادة منشأ عادية من الغرفة التجارية (بتثبت المنشأ من غير تخفيض جمارك).")
    else:
        main = next((a for a in ags if a.key in {nt.agreement for nt in notes}), ags[0])
        L.append("🤝 <b>الاتفاقية:</b> " + "، ".join(escape(a.name) for a in ags))
        line_notes = [nt for nt in notes if nt.kind in ("excluded", "partial", "exempt", "covered")]
        if line_notes:
            L.append("📌 <b>مسجل على البند في الجمارك المصرية:</b>")
            grouped: dict[str, list[ExportNote]] = {}
            for nt in line_notes:
                grouped.setdefault(_export_meaning(nt, name), []).append(nt)
            for meaning, group in grouped.items():
                L.append(meaning)
                for nt in group:
                    L.append(f"   <i>{escape(nt.code)} — {escape(short(nt.text, 110))}</i>")
        else:
            L.append(f"📌 <b>المعاملة:</b> {escape(main.export_treatment)}")
        certs = [a for a in [main] + [a for a in ags if a is not main] if a.cert_export and a.cert_export != "—"]
        if len(certs) == 1:
            L.append(f"📜 <b>الشهادة:</b> {escape(main.cert_export)}")
        else:
            L.append("📜 <b>الشهادة</b> (اختار اللي تناسب المشتري):")
            for a in certs[:3]:
                L.append(f"• {escape(_plain(a.key))}: {escape(a.cert_export)}")
        if main.approved_exporter:
            L.append("🏅 <b>المصدّر المعتمد:</b> متاح في الاتفاقية دي — بتكتب إقرار المنشأ على الفاتورة "
                     "بدل شهادة لكل شحنة (بتقدّم عليه في هيئة الرقابة على الصادرات والواردات).")
        if main.origin_rule:
            L.append(f"🧬 <b>قاعدة المنشأ:</b> {escape(main.origin_rule)}")
    L.append(f"🌱 <b>منشأ منتجك:</b> {escape(origin_hint(item.code))}")
    other = [nt for nt in notes if nt.kind in ("requirement", "banned")]
    for nt in other:
        L.append(_export_meaning(nt, name))
    if reqs:
        L.append("")
        L.append("🇪🇬 <b>متطلبات التصدير من مصر على البند:</b>")
        for ins in reqs[:5]:
            L.append(f"• <b>{escape(ins.get('code', ''))}</b> — {escape(short(ins.get('text', ''), 120))}")
        if len(reqs) > 5:
            L.append(f"  … و{len(reqs) - 5} تانيين (زرار «متطلبات التصدير»)")
    excluded = any(nt.kind == "excluded" for nt in notes)
    L.append("")
    L.append(f"✍️ <b>للمشتري:</b> <code>{escape(buyer_line(main, excluded))}</code>")
    L.append("🔎 جمارك البلد نفسه على منتجك: macmap.org — وقاعدة منشأ البند: findrulesoforigin.org")
    L.append(f"🕒 المصدر: {escape(source)}")
    return "\n".join(L)


# ================================================================ library cards
def menu_text(code: str | None = None, desc: str | None = None) -> str:
    if code:
        head = f"🌍 <b>المنشأ والاتفاقيات — البند {code}</b>"
        if desc:
            head += "\n" + escape(short(desc, 100))
        return head + "\n\nهتستورد البند ده لمصر ولا هتصدّره؟"
    return ("🌍 <b>الاتفاقيات والمنشأ</b>\n\n"
            "📥 <b>استيراد لمصر:</b> أرخص منشأ للبند، والحساب من بلد معيّن، والشهادة المطلوبة من المورد.\n"
            "📤 <b>تصدير من مصر:</b> الاتفاقية مع بلد المشتري، والشهادة ومين بيطلّعها، والمصدّر المعتمد، "
            "وقاعدة المنشأ.\n"
            "📚 <b>الاتفاقيات:</b> كارت لكل اتفاقية.\n\n"
            "أو اكتب على طول:\n"
            "<code>منشأ 1905320000</code>\n"
            "<code>استيراد 1905320000 من تركيا</code>\n"
            "<code>تصدير 0713330090 إلى ألمانيا</code>\n"
            "<code>اتفاقيات السعودية</code>")


LIBRARY = ("eu", "uk", "efta", "tr", "gafta", "agadir", "comesa", "afcfta", "mercosur", "serbia", "china0", "gstp")


def library_text() -> str:
    L = ["📚 <b>اتفاقيات مصر التجارية</b>", ""]
    for key in LIBRARY:
        ag = A.AGREEMENTS[key]
        way = "استيراد وتصدير" if ag.imports and ag.exports else ("تصدير بس" if ag.exports else "استيراد بس")
        L.append(f"{A.short_name(key)} — {way}")
    L.append("")
    L.append("اضغط على اتفاقية للتفاصيل، أو اكتب: <code>اتفاقيات السعودية</code>")
    return "\n".join(L)


def agreement_text(key: str) -> str:
    ag = A.AGREEMENTS[key]
    L = [f"{ag.icon} <b>{escape(ag.name)}</b>", f"<i>{escape(ag.en)}</i>"]
    if ag.status:
        L.append(f"📅 {escape(ag.status)}")
    if ag.members:
        names = [A.country_name(c) for c in ag.members]
        shown = "، ".join(names[:12]) + (f" … ({len(names)} دولة)" if len(names) > 12 else "")
        L.append(f"👥 <b>الأعضاء:</b> {escape(shown)}")
    if ag.imports:
        L.append("")
        L.append("📥 <b>الاستيراد لمصر</b>")
        if key in LINE_LEVEL or key == "gstp":
            L.append("• السعر المخفض بيختلف لكل بند — شوفه من «🌍 المنشأ والاتفاقيات» على البند.")
        elif ag.import_rule == "exempt":
            L.append("• معفى كقاعدة عامة (بشهادة المنشأ) — يتأكد للبند مع المستخلص.")
        if ag.cert_import and ag.cert_import != "—":
            L.append(f"• المطلوب من المورد: {escape(ag.cert_import)}")
    if ag.exports:
        L.append("")
        L.append("📤 <b>التصدير من مصر</b>")
        if ag.export_treatment:
            L.append(f"• {escape(ag.export_treatment)}")
        L.append(f"• الشهادة: {escape(ag.cert_export)}")
        L.append("• المصدّر المعتمد: " + ("متاح ✅" if ag.approved_exporter else
                                          "لسه بيتضاف للاتفاقية دي" if key in A.APPROVED_EXPORTER_COMING
                                          else "مش متاح في الاتفاقية دي"))
    if ag.origin_rule:
        L.append(f"🧬 <b>قاعدة المنشأ:</b> {escape(ag.origin_rule)}")
    L.append("")
    L.append("🔗 الشهادات: هيئة الرقابة على الصادرات والواردات (goeic.gov.eg) — قواعد المنشأ: findrulesoforigin.org")
    return "\n".join(L)


def country_text(iso: str) -> str:
    name = A.country_name(iso)
    L = [f"{A.country_flag(iso)} <b>اتفاقيات مصر مع {escape(name)}</b>", ""]
    imp = A.agreements_for(iso, direction="import")
    exp = A.agreements_for(iso, direction="export")
    if not imp and not exp:
        L.append("مفيش اتفاقية تجارة تفضيلية — الجمارك بالسعر العام في الاتجاهين.")
        if A.members_of(iso)[0] == "US":
            L.append("ℹ️ للتصدير لأمريكا فيه نظام QIZ لمنتجات مصانع المناطق الصناعية المؤهلة — يتأكد.")
    for title, ags in (("📤 <b>تصدير من مصر</b>", exp), ("📥 <b>استيراد لمصر</b>", imp)):
        if not ags:
            continue
        L.append(title)
        for ag in ags:
            if ag.key in ("gatt", "gstp"):
                continue
            cert = ag.cert_export if title.startswith("📤") else ag.cert_import
            L.append(f"• {escape(ag.name)} — {escape(cert)}")
        L.append("")
    L.append("💡 لبند معيّن: <code>تصدير 0713330090 إلى " + escape(name) + "</code> أو "
             "<code>استيراد 1905320000 من " + escape(name) + "</code>")
    return "\n".join(L)
