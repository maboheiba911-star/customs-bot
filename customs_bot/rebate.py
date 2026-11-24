"""Egypt's export rebate (رد الأعباء التصديرية) — estimate for one shipment.

The program was approved by the cabinet on 5 Nov 2026 and applies to shipments from the start of November 2026.
The rules below come from what the Export Development Fund and the ministries announced (press, 15–24 Nov 2026):
  * extra incentive for target markets: 25% of the basic support (e.g. basic 4% -> 5%)
  * target markets: Africa, Latin America & Caribbean, China, Hong Kong, Taiwan, CIS & Russia, Australia,
    New Zealand, Japan, South Korea, USA & Canada, South-East Asia (11 countries)
  * sea / land freight: 50% of the actual cost (e-invoice) to Africa, Latin America, Australia, SE Asia, Russia
  * Ro-Ro: 25% of the e-invoice; air freight for agricultural products: 25% to Europe, 50% to the UK
  * inland transport: 25% of the cost, at most EGP 250,000 per company per year; insurance: 25% of the policy
  * Upper Egypt and border areas: 40% (was 25%)
The basic support rate of each product is not in those announcements, so the exporter enters it (export council /
the fund). Everything here is an estimate: the fund pays under its own conditions and documents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from . import agreements as A

PROGRAM = "البرنامج الجديد لرد الأعباء — اتوافق عليه 5 نوفمبر 2026، وبيتطبق على شحنات نوفمبر 2026 وبعدها"
SOURCE = "تصريحات صندوق تنمية الصادرات ووزارة الاستثمار (15–24 نوفمبر 2026)"

TARGET_EXTRA = 0.25
SEA_LAND = 0.50
RORO = 0.25
AIR_EUROPE, AIR_UK = 0.25, 0.50
INLAND, INLAND_CAP = 0.25, 250_000.0
INSURANCE = 0.25
UPPER_EGYPT = 0.40

LATAM = frozenset({
    "MX", "GT", "BZ", "SV", "HN", "NI", "CR", "PA", "CU", "DO", "HT", "JM", "TT", "BB", "BS", "GD", "LC", "VC",
    "AG", "DM", "KN", "CO", "VE", "EC", "PE", "BO", "BR", "PY", "UY", "AR", "CL", "GY", "SR"})
SE_ASIA = frozenset({"BN", "KH", "ID", "LA", "MY", "MM", "PH", "SG", "TH", "VN", "TL"})
CIS = frozenset({"RU", "BY", "KZ", "KG", "TJ", "UZ", "AM", "AZ", "MD", "TM"})
AFRICA = frozenset(A.AFRICA)
EUROPE = frozenset(A.EU) | frozenset(A.EFTA) | {"GB", "RS", "BA", "ME", "MK", "AL", "UA", "MD"}
TARGET = (AFRICA | LATAM | SE_ASIA | CIS
          | {"CN", "HK", "TW", "AU", "NZ", "JP", "KR", "US", "CA"})
SEA_LAND_MARKETS = AFRICA | LATAM | SE_ASIA | {"AU", "RU"}

MODES = {"sea": "🚢 بحري", "land": "🚛 بري", "air": "✈️ جوي", "roro": "🚢 رورو", "none": "⏭️ من غير شحن"}


@dataclass
class Shipment:
    iso: str
    fob_egp: float
    basic_rate: float | None = None      # % of the export value, from the export council / the fund
    mode: str = "none"                   # sea / land / air / roro / none
    freight_egp: float | None = None
    inland_egp: float | None = None
    insurance_egp: float | None = None
    upper_egypt: bool = False
    agricultural: bool = True


@dataclass
class Line:
    label: str
    amount: float | None                 # None = can't be computed yet (missing input)
    note: str = ""


@dataclass
class Result:
    lines: list[Line] = field(default_factory=list)
    total: float = 0.0
    notes: list[str] = field(default_factory=list)
    target: bool = False


def is_target(iso: str) -> bool:
    return iso in TARGET


def freight_share(iso: str, mode: str, agricultural: bool = True) -> tuple[float, str]:
    """(share of the freight cost, why) for a destination and a transport mode."""
    if mode in ("sea", "land"):
        if iso in SEA_LAND_MARKETS:
            return SEA_LAND, "50% من تكلفة الشحن الفعلية (الفاتورة الإلكترونية)"
        return 0.0, "الشحن البحري والبري مدعوم لأفريقيا وأمريكا اللاتينية وأستراليا وجنوب شرق آسيا وروسيا بس"
    if mode == "roro":
        return RORO, "25% من قيمة الفاتورة الإلكترونية"
    if mode == "air":
        if not agricultural:
            return 0.0, "دعم الشحن الجوي المعلن خاص ببرنامج الحاصلات الزراعية"
        if iso == "GB":
            return AIR_UK, "50% للشحن الجوي لإنجلترا (برنامج الحاصلات الزراعية)"
        if iso in EUROPE:
            return AIR_EUROPE, "25% للشحن الجوي لأوروبا (برنامج الحاصلات الزراعية)"
        return 0.0, "الدعم الجوي المعلن لأوروبا وإنجلترا بس"
    return 0.0, ""


def compute(sh: Shipment) -> Result:
    res = Result(target=is_target(sh.iso))
    basic = sh.fob_egp * sh.basic_rate / 100 if sh.basic_rate is not None else None
    res.lines.append(Line(f"المساندة الأساسية ({_pct(sh.basic_rate)} من القيمة)" if sh.basic_rate is not None
                          else "المساندة الأساسية", basic,
                          "" if basic is not None else "دوس «✏️ نسبة المساندة» وحط نسبة بندك من المجلس التصديري"))
    if res.target:
        res.lines.append(Line("حافز السوق المستهدف (+25% من الأساسية)",
                              basic * TARGET_EXTRA if basic is not None else None))
    if sh.upper_egypt:
        res.lines.append(Line("حافز الصعيد والمناطق الحدودية (40% من الأساسية)",
                              basic * UPPER_EGYPT if basic is not None else None,
                              "الأخبار قالت 40% ومقالتش محسوبة على إيه — اتأكد من الصندوق"))
    if sh.mode != "none":
        share, why = freight_share(sh.iso, sh.mode, sh.agricultural)
        label = f"دعم الشحن {MODES.get(sh.mode, sh.mode)}"
        if sh.freight_egp is None:
            res.lines.append(Line(label, None, "ابعت تكلفة الشحن"))
        else:
            res.lines.append(Line(label, sh.freight_egp * share, why))
    if sh.inland_egp:
        amount = min(sh.inland_egp * INLAND, INLAND_CAP)
        res.lines.append(Line("النقل الداخلي (25%)", amount, "بحد أقصى 250 ألف جنيه للشركة في السنة"))
    if sh.insurance_egp:
        res.lines.append(Line("التأمين على الشحنة (25% من البوليصة)", sh.insurance_egp * INSURANCE))
    res.total = sum(ln.amount for ln in res.lines if ln.amount)
    if not res.target:
        res.notes.append("البلد دي مش من الأسواق المستهدفة، فمفيش حافز الـ 25% الإضافي.")
    return res


def _pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:g}%"


def _money(egp: float, usd_rate: float | None) -> str:
    s = f"{egp:,.0f} ج"
    if usd_rate:
        s += f" (≈ {egp / usd_rate:,.0f}$)"
    return s


def card(sh: Shipment, res: Result, usd_rate: float | None = None, value_note: str | None = None) -> str:
    flag, name = A.country_flag(sh.iso), A.country_name(sh.iso)
    L = [f"💰 <b>رد الأعباء — شحنة لـ {flag} {escape(name)}</b>",
         ("🎯 سوق مستهدف ✅" if res.target else "🎯 مش سوق مستهدف"),
         f"📦 قيمة الشحنة: {_money(sh.fob_egp, usd_rate)}" + (f" — {escape(value_note)}" if value_note else ""),
         ""]
    for i, ln in enumerate(res.lines, 1):
        val = _money(ln.amount, usd_rate) if ln.amount is not None else "❔"
        if ln.amount == 0:
            val = "0"
        L.append(f"{i}) {escape(ln.label)}: <b>{val}</b>")
        if ln.note:
            L.append(f"   <i>{escape(ln.note)}</i>")
    L.append("")
    share = res.total / sh.fob_egp * 100 if sh.fob_egp else 0
    L.append(f"💵 <b>الإجمالي التقريبي: {_money(res.total, usd_rate)}</b> = {share:.1f}% من قيمة الشحنة")
    if any(ln.amount is None for ln in res.lines):
        L.append("❔ = ناقصه رقم منك — الإجمالي من غيره.")
    for n in res.notes:
        L.append(f"ℹ️ {escape(n)}")
    L.append(f"\n⚠️ <i>تقدير حسب {escape(PROGRAM)}. المصدر: {escape(SOURCE)}. "
             "الصرف بشروط الصندوق ومستنداته (منها الفاتورة الإلكترونية)، فاتأكد من نسبة بندك مع المجلس التصديري.</i>")
    return "\n".join(L)
