"""Parse the "Taxes" list returned by customs.gov.eg (Services/TrfDetails).

Examples of raw entries (as served by the official site):
    "ضريبة الوارد :  2%"
    "ضريبة قيمه مضافه :  صفر"
    "ضريبة الجدول :  8% (من القيمة + ر.ض.جمركية)"
    "ضريبة الوارد :  100 جنية لكل كيلو جرام صافى"
    "ضريبة قيمه مضافه :  بحد ادنى 60 جنية لكل كيلو جرام صافى"
    "اتفاقيه الشراكه المصريه الاوربيه"          <- agreement header (no colon)
    "ضريبة الوارد :  15%"                         <- rate under that agreement
    "ضريبة الوارد :  50% تخفيض"                   <- reduction under GSTP
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .arabic import normalize, to_ascii_digits

KIND_LABELS = {
    "duty": "ضريبة الوارد",
    "vat": "ض. القيمة المضافة",
    "table": "ضريبة الجدول",
    "dev": "رسم التنمية",
    "health": "رسم تأمين صحي",
    "chamber_textile": "رسم غرفة النسيج",
    "chamber_leather": "رسم غرفة الجلود",
    "chamber_tobacco": "رسم غرفة الدخان",
    "chamber_other": "رسم غرفة",
    "other": "رسم آخر",
}

# Agreement header (normalized) -> (key, Arabic display name)
AGREEMENTS = {
    "اتفاقيه الشراكه المصريه الاوربيه": ("eu", "الشراكة المصرية الأوروبية (EU)"),
    "اتفاقيه بريطانيا": ("uk", "اتفاقية بريطانيا (UK)"),
    "اتفاقيه تركيا": ("tr", "اتفاقية تركيا"),
    "اتفاقيه امريكا اللاتينيه الميركسور": ("mercosur", "الميركسور (أمريكا اللاتينية)"),
    "الافتا": ("efta", "الإفتا (EFTA)"),
    "حره افريقيه ا": ("afcfta_a", "التجارة الحرة القارية الأفريقية - أ"),
    "حره افريقيه ب": ("afcfta_b", "التجارة الحرة القارية الأفريقية - ب"),
    "gstp": ("gstp", "النظام العالمي للأفضليات التجارية (GSTP)"),
    "نظام الجات": ("gatt", "نظام الجات (WTO)"),
}

UNITS = [
    # (normalized fragment, unit key, display)
    ("عشرون سيجاره", "20cig", "كل 20 سيجارة"),
    ("كج مصنع صافي", "kg", "كجم صافي"),
    ("كيلو جرام صافي", "kg", "كجم صافي"),
    ("كيلو جرام", "kg", "كجم"),
    ("لتر سايل", "l", "لتر"),
    ("لتر", "l", "لتر"),
    ("طن", "ton", "طن"),
]

_NUM = r"(\d+(?:[.,]\d+)*)"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class Component:
    kind: str
    label: str
    pct: float | None = None            # ad valorem %
    specific: float | None = None       # EGP per unit
    unit: str | None = None
    minimum: float | None = None        # minimum EGP per unit ("بحد ادنى")
    min_unit: str | None = None
    reduction_pct: float | None = None  # "% تخفيض" (agreements)
    note: str | None = None             # e.g. "من القيمة + ر.ض.جمركية"
    raw: list[str] = field(default_factory=list)

    def display(self) -> str:
        parts = []
        if self.pct is not None:
            parts.append(f"{fmt_pct(self.pct)}%")
        if self.specific is not None:
            parts.append(f"{fmt_pct(self.specific)} ج/{unit_label(self.unit)}")
        if self.reduction_pct is not None:
            parts.append(f"تخفيض {fmt_pct(self.reduction_pct)}%")
        if self.minimum is not None:
            parts.append(f"بحد أدنى {fmt_pct(self.minimum)} ج/{unit_label(self.min_unit)}")
        return " + ".join(parts) if parts else "؟"


@dataclass
class Agreement:
    key: str
    name: str
    components: dict[str, Component] = field(default_factory=dict)


@dataclass
class ParsedTaxes:
    general: dict[str, Component] = field(default_factory=dict)
    agreements: list[Agreement] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    def get(self, kind: str) -> Component | None:
        return self.general.get(kind)

    def agreement(self, key: str) -> Agreement | None:
        for a in self.agreements:
            if a.key == key:
                return a
        return None


def fmt_pct(x: float) -> str:
    if x is None:
        return "؟"
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}"
    return f"{x:,.3f}".rstrip("0").rstrip(".")


def unit_label(unit: str | None) -> str:
    for _, key, disp in UNITS:
        if key == unit:
            return disp
    return unit or "وحدة"


def classify_label(label: str) -> str:
    n = normalize(label).replace(" ", "")
    if "وارد" in n:
        return "duty"
    if "قيمهمضافه" in n or "القيمهالمضافه" in n:
        return "vat"
    if "جدول" in n:
        return "table"
    if "تنميه" in n:
        return "dev"
    if "تامينصحي" in n:
        return "health"
    if "غرفه" in n:
        if "نسيج" in n:
            return "chamber_textile"
        if "جلود" in n:
            return "chamber_leather"
        if "دخان" in n:
            return "chamber_tobacco"
        return "chamber_other"
    return "other"


def _parse_unit(text_norm: str) -> str | None:
    for frag, key, _ in UNITS:
        if frag in text_norm:
            return key
    return None


def parse_rate(rate_raw: str) -> dict:
    """Parse the part after the colon into fields of ``Component``."""
    r = to_ascii_digits(rate_raw or "").replace("٪", "%").replace("٫", ".").strip()
    out: dict = {}
    m = re.search(r"\(([^)]*)\)\s*$", r)
    if m:
        out["note"] = m.group(1).strip()
        r = r[: m.start()].strip()
    rn = normalize(r)
    if not rn:
        out["unparsed"] = rate_raw
        return out
    if rn in ("صفر", "معفي", "معفاه"):
        out["pct"] = 0.0
        return out
    if "بحد" in rn and "ادني" in rn:
        m = re.search(_NUM, r)
        if m:
            out["minimum"] = _num(m.group(1))
            out["min_unit"] = _parse_unit(rn)
        return out
    m = re.match(r"^\s*" + _NUM + r"\s*%\s*(.*)$", r)
    if m:
        val = _num(m.group(1))
        rest = normalize(m.group(2))
        if "تخفيض" in rest:
            out["reduction_pct"] = val
        else:
            out["pct"] = val
        return out
    m = re.match(r"^\s*" + _NUM + r"\s*(?:جنيه|جنية|جنيها|ج)", r)
    if m:
        out["specific"] = _num(m.group(1))
        out["unit"] = _parse_unit(rn)
        return out
    out["unparsed"] = rate_raw
    return out


def _merge(target: dict[str, Component], kind: str, label: str, fields: dict, raw: str) -> None:
    comp = target.get(kind)
    if comp is None:
        comp = Component(kind=kind, label=label)
        target[kind] = comp
    comp.raw.append(raw)
    for k, v in fields.items():
        if k == "unparsed":
            continue
        if getattr(comp, k, None) is None:
            setattr(comp, k, v)


def parse_taxes(entries: list[str] | None) -> ParsedTaxes:
    res = ParsedTaxes()
    current: Agreement | None = None
    for raw in entries or []:
        entry = (raw or "").strip()
        if not entry:
            continue
        if ":" not in entry:
            n = normalize(entry)
            key, name = AGREEMENTS.get(n, (n.replace(" ", "_")[:24] or "agreement", entry))
            current = Agreement(key=key, name=name)
            res.agreements.append(current)
            continue
        label, rate = entry.split(":", 1)
        kind = classify_label(label)
        fields = parse_rate(rate)
        if "unparsed" in fields and len(fields) == 1:
            res.unparsed.append(entry)
            continue
        target = current.components if current is not None else res.general
        _merge(target, kind, label.strip(), fields, entry)
    return res


def summary_rates(parsed: ParsedTaxes) -> dict:
    """Numbers used for list views / sorting (general rates only)."""
    duty = parsed.get("duty")
    vat = parsed.get("vat")
    return {
        "duty_pct": duty.pct if duty else None,
        "duty_text": duty.display() if duty else None,
        "vat_pct": vat.pct if vat else None,
        "vat_text": vat.display() if vat else None,
        "has_table": 1 if parsed.get("table") else 0,
        "has_agreements": 1 if parsed.agreements else 0,
    }
