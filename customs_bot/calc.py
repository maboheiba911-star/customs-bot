"""Customs duty & import-tax calculator for Egypt.

Order of computation (all amounts in EGP, value = customs value / CIF):
  1. Import duty        = value x duty%            (+ specific EGP/unit if any)
  2. Development fee    = (value + duty) x dev%    (only if configured for the HS prefix)
  3. Table tax          = (value + duty) x table%  (official note: "من القيمة + ر.ض.جمركية")
  4. Chamber / health fees = value x fee%
  5. VAT                = (value + duty + dev + table + fees) x VAT%   (VAT law: customs value
                          + customs duties + other taxes and fees + table tax)
  6. Advance income-tax payment (أ.ت.ص) = value x 1%  (imports for trade / manufacturing)
Every base is configurable in config.json.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .settings import CalcSettings
from .taxparse import KIND_LABELS, Component, ParsedTaxes, fmt_pct

FEE_KINDS = ("health", "chamber_textile", "chamber_leather", "chamber_tobacco", "chamber_other", "other")


@dataclass
class Line:
    key: str
    label: str
    rate_text: str
    base: float | None
    amount: float | None
    note: str = ""


@dataclass
class CalcResult:
    code: str
    value: float
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    origin_key: str | None = None
    origin_name: str | None = None
    vat_is_default: bool = False

    @property
    def total(self) -> float:
        return sum(l.amount for l in self.lines if l.amount is not None)

    @property
    def incomplete(self) -> bool:
        return any(l.amount is None for l in self.lines)

    @property
    def grand_total(self) -> float:
        return self.value + self.total

    @property
    def effective_pct(self) -> float:
        return (self.total / self.value * 100.0) if self.value else 0.0

    def amount(self, key: str) -> float:
        return sum(l.amount or 0.0 for l in self.lines if l.key == key)


def normalize_qty(qty: dict[str, float] | None) -> dict[str, float]:
    q = dict(qty or {})
    if "kg" in q and "ton" not in q:
        q["ton"] = q["kg"] / 1000.0
    if "ton" in q and "kg" not in q:
        q["kg"] = q["ton"] * 1000.0
    return q


def _component_amount(comp: Component, base: float, qty: dict[str, float]) -> tuple[float | None, str]:
    """Amount of one tax component; None when a quantity is needed but missing."""
    amount = 0.0
    note = ""
    known = True
    if comp.pct is not None:
        amount += base * comp.pct / 100.0
    if comp.specific is not None:
        q = qty.get(comp.unit or "")
        if q is None:
            known = False
            note = "فئة نوعية بالوحدة — ابعت الكمية (مثال: 500kg)"
        else:
            amount += comp.specific * q
    if comp.minimum is not None:
        q = qty.get(comp.min_unit or "")
        if q is None:
            note = "له حد أدنى بالوحدة — ابعت الكمية علشان يتطبق"
        else:
            floor = comp.minimum * q
            if floor > amount:
                amount = floor
                note = "اتطبق الحد الأدنى بالوحدة"
    return (amount if known else None), note


def calculate(
    taxes: ParsedTaxes,
    value: float,
    *,
    code: str = "",
    settings: CalcSettings | None = None,
    qty: dict[str, float] | None = None,
    origin: str | None = None,
    dev_fee_pct: float | None = None,
    personal: bool = False,
) -> CalcResult:
    s = settings or CalcSettings()
    q = normalize_qty(qty)
    res = CalcResult(code=code, value=float(value))

    # 1) import duty (general rate, or a trade-agreement rate when an origin is given)
    duty_comp = taxes.get("duty")
    duty_label = KIND_LABELS["duty"]
    if origin:
        ag = taxes.agreement(origin)
        if ag and "duty" in ag.components:
            res.origin_key, res.origin_name = ag.key, ag.name
            ac = ag.components["duty"]
            if ac.reduction_pct is not None and duty_comp is not None:
                reduced = copy.deepcopy(duty_comp)
                factor = 1.0 - ac.reduction_pct / 100.0
                if reduced.pct is not None:
                    reduced.pct = reduced.pct * factor
                if reduced.specific is not None:
                    reduced.specific = reduced.specific * factor
                duty_comp = reduced
            else:
                duty_comp = ac
            duty_label = f"ضريبة الوارد ({ag.name})"
            res.warnings.append("السعر التفضيلي محتاج شهادة منشأ مقبولة (EUR.1 / شهادة الاتفاقية) وقواعد منشأ مستوفاة.")
        else:
            res.warnings.append("مفيش سعر تفضيلي مسجل للبند ده تحت الاتفاقية دي — اتحسب بالسعر العام.")
    if duty_comp is None:
        res.lines.append(Line("duty", duty_label, "غير مسجلة", res.value, 0.0))
        res.warnings.append("مفيش ضريبة وارد مسجلة للبند ده في التعريفة — اتحسبت صفر، راجع نافذة.")
        duty = 0.0
    else:
        amt, note = _component_amount(duty_comp, res.value, q)
        res.lines.append(Line("duty", duty_label, duty_comp.display(), res.value, amt, note))
        duty = amt or 0.0

    # 2) development fee (configured per HS prefix; not published on customs.gov.eg)
    dev_comp = taxes.get("dev")
    dev_pct = dev_fee_pct if dev_fee_pct is not None else (dev_comp.pct if dev_comp else None)
    dev_base = res.value + duty if s.dev_fee_base == "value+duty" else res.value
    dev = dev_base * dev_pct / 100.0 if dev_pct else 0.0
    res.lines.append(Line("dev", KIND_LABELS["dev"], f"{fmt_pct(dev_pct or 0)}%", dev_base, dev))

    # 3) table tax
    table = 0.0
    table_comp = taxes.get("table")
    if table_comp is not None:
        base = res.value + duty if s.table_tax_base == "value+duty" else res.value
        if s.table_tax_base == "value+duty+dev":
            base = res.value + duty + dev
        amt, note = _component_amount(table_comp, base, q)
        res.lines.append(Line("table", KIND_LABELS["table"], table_comp.display(), base, amt, note))
        table = amt or 0.0

    # 4) chamber / health / other fees
    fees = {"health": 0.0, "chambers": 0.0}
    for kind in FEE_KINDS:
        comp = taxes.get(kind)
        if comp is None:
            continue
        base = res.value + duty if s.fees_base == "value+duty" else res.value
        amt, note = _component_amount(comp, base, q)
        res.lines.append(Line(kind, KIND_LABELS.get(kind, comp.label), comp.display(), base, amt, note))
        fees["health" if kind == "health" else "chambers"] += amt or 0.0

    # 5) VAT
    vat_comp = taxes.get("vat")
    if vat_comp is None or (vat_comp.pct is None and vat_comp.specific is None):
        default = Component(kind="vat", label=KIND_LABELS["vat"], pct=float(s.vat_default_pct))
        if vat_comp is not None:   # only a minimum was published
            default.minimum, default.min_unit = vat_comp.minimum, vat_comp.min_unit
        vat_comp = default
        res.vat_is_default = True
    inc = set(s.vat_base_includes or [])
    vat_base = res.value
    vat_base += duty if "duty" in inc else 0.0
    vat_base += dev if "dev" in inc else 0.0
    vat_base += table if "table" in inc else 0.0
    vat_base += fees["health"] if "health" in inc else 0.0
    vat_base += fees["chambers"] if "chambers" in inc else 0.0
    amt, note = _component_amount(vat_comp, vat_base, q)
    if res.vat_is_default:
        note = (note + " — " if note else "") + "السعر العام (البند مش مذكور له سعر خاص)"
    res.lines.append(Line("vat", KIND_LABELS["vat"], vat_comp.display(), vat_base, amt, note))

    # 6) advance payment of commercial & industrial profits tax
    if not personal and s.income_tax_advance_pct:
        base = res.value + duty if s.income_tax_base == "value+duty" else res.value
        res.lines.append(
            Line("income", "أرباح تجارية (دفعة تحت حساب الضريبة)", f"{fmt_pct(s.income_tax_advance_pct)}%",
                 base, base * float(s.income_tax_advance_pct) / 100.0)
        )
    return res
