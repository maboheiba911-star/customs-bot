"""Shipment file (ملف الشحنة): the papers for one shipment, from three sides — Egypt (export), the buyer's country
(import) and the customer — as a checklist the exporter ticks off.

Destination rules are short and each one carries its source. Countries without reviewed rules say so and point to
the official portals instead of guessing. Sources reviewed on 24 Nov 2026:
  EU     plant health: food.ec.europa.eu (Annex XI of Regulation (EU) 2019/2072); pyrrolizidine alkaloids:
         Regulation (EU) 2020/2040, carried into 2023/915; increased controls: Regulation (EU) 2019/1793
         (consolidated 30 Jun 2026; Egyptian oranges, sweet peppers, strawberries — pesticide residues).
  Kenya  USDA FAIRS reports / trade.gov: CoC under KEBS PVoC for most food; KEPHIS plant import permit and a
         phytosanitary certificate for plant material; Import Declaration Form.
  Nigeria  Form M via the Nigeria Single Window; NAQS import permit and phytosanitary certificate for plant
         products; NAFDAC registration for processed food (raw materials for production usually not).
  Morocco  phytosanitary certificate for plants and plant products (not for processed ones with no pest risk);
         grains/seeds for consumption need no import permit (MICoR); ONSSA inspects before customs clearance.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone

from . import agreements as A

GTH = "https://intracen.org/resources/tools/global-trade-helpdesk"
A2M = "https://trade.ec.europa.eu/access-to-markets/en/my-trade-assistant"
EU_PLANT = "https://food.ec.europa.eu/plants/plant-health-and-biosecurity/trade-plants-plant-products-non-eu-countries_en"
EU_1793 = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A02019R1793-20260630"
EU_PA = "https://eur-lex.europa.eu/eli/reg/2020/2040/oj"
KE_GUIDE = "https://trade.gov/country-commercial-guides/kenya-standards-trade"
KE_TTB = "https://www.ttb.gov/import-export/itd/international-affairs-resources-for-kenya"
NG_WKO = "https://www.wko.at/aussenwirtschaft/nigeria-export-import"
NG_NAQS = "https://naqs.gov.ng/"
NG_NAFDAC = "https://nafdac.gov.ng/food/"
MA_MICOR = "https://micor.agriculture.gov.au/plants/morocco_ma/other"
REVIEWED = "24 نوفمبر 2026"

PLANT_CHAPTERS = {"06", "07", "08", "09", "10", "11", "12", "13", "14"}


@dataclass(frozen=True)
class Rule:
    text: str
    source: str
    url: str = ""
    hs: tuple[str, ...] = ()          # applies only to codes starting with one of these
    plants: bool = False              # applies only to plant products (chapters 06-14)

    def applies(self, code: str) -> bool:
        if self.plants and code[:2] not in PLANT_CHAPTERS:
            return False
        return not self.hs or any(code.startswith(h) for h in self.hs)


EU_RULES = (
    Rule("شهادة صحة نباتية (Phytosanitary) من الحجر الزراعي المصري — مطلوبة للنباتات والمنتجات النباتية "
         "المذكورة في Annex XI من اللائحة الأوروبية 2019/2072. اتأكد إن بندك منهم.",
         "المفوضية الأوروبية — استيراد النباتات", EU_PLANT, plants=True),
    Rule("متبقيات المبيدات لازم تكون تحت الحدود الأوروبية (MRL) — اعمل تحليل متبقيات في معمل معتمد قبل الشحن.",
         "قواعد سلامة الغذاء في الاتحاد الأوروبي", A2M, plants=True),
    Rule("قلويدات البيروليزيدين (PAs): الحد الأوروبي 400 ميكروجرام/كجم للبابونج واليانسون لو هيتباعوا كمشروب "
         "أعشاب (جاف)، و200 لباقي مشروبات الأعشاب — اعمل التحليل.",
         "اللائحة 2020/2040 (اتنقلت للائحة 2023/915)", EU_PA, hs=("0909", "1211")),
    Rule("⚠️ البرتقال المصري عليه رقابة مشددة في حدود أوروبا (متبقيات مبيدات) — نسبة من الشحنات بتتفحص.",
         "اللائحة 2019/1793 (نسخة 30 يونيو 2026)", EU_1793, hs=("080510",)),
    Rule("⚠️ الفلفل الحلو المصري عليه رقابة مشددة في حدود أوروبا (متبقيات مبيدات) — نسبة من الشحنات بتتفحص.",
         "اللائحة 2019/1793 (نسخة 30 يونيو 2026)", EU_1793, hs=("070960", "071080")),
    Rule("⚠️ الفراولة المصرية اتضافت للرقابة المشددة في حدود أوروبا (متبقيات مبيدات) من يناير 2026.",
         "اللائحة 2019/1793 (نسخة 30 يونيو 2026)", EU_1793, hs=("081010",)),
)

COUNTRY_RULES: dict[str, tuple[Rule, ...]] = {
    "KE": (
        Rule("شهادة مطابقة (CoC) من برنامج PVoC بتاع هيئة المواصفات الكينية KEBS — لأغلب المنتجات الغذائية، "
             "بتطلع من وكيل معتمد (زي SGS) قبل الشحن، وصلاحيتها 3 شهور.", "تقارير USDA عن كينيا / trade.gov",
             KE_GUIDE),
        Rule("تصريح استيراد نباتات من KEPHIS (المستورد بيطلعه) + شهادة صحة نباتية — لأي منتج نباتي.",
             "تقارير USDA عن كينيا / trade.gov", KE_GUIDE, plants=True),
        Rule("نموذج إقرار الاستيراد IDF — المستورد بيقدّمه.", "ttb.gov — كينيا", KE_TTB),
    ),
    "NG": (
        Rule("Form M — المستورد بيفتحه عن طريق البنك على Nigeria Single Window قبل الشحن، وهو المستند الأساسي "
             "لأي استيراد تجاري.", "الغرفة التجارية النمساوية WKO / IHK", NG_WKO),
        Rule("تصريح استيراد من NAQS + شهادة صحة نباتية — للنباتات والمنتجات النباتية.",
             "تقارير USDA عن نيجيريا / NAQS", NG_NAQS, plants=True),
        Rule("لو المنتج مُعبأ للمستهلك: لازم تسجيل NAFDAC. الخامات اللي رايحة مصانع غالباً مش محتاجة تسجيل — اتأكد "
             "مع المستورد.", "NAFDAC / WKO", NG_NAFDAC),
    ),
    "MA": (
        Rule("شهادة صحة نباتية — مطلوبة لأغلب النباتات والمنتجات النباتية (إلا المصنّعة اللي مفيهاش خطر آفات). "
             "الحبوب والبذور للاستهلاك مش محتاجة تصريح استيراد.", "متطلبات المغرب + قاعدة MICoR الأسترالية",
             MA_MICOR, plants=True),
        Rule("الشحنة بتتفحص من ONSSA (مكتب السلامة الغذائية) قبل الإفراج الجمركي.", "تقارير USDA عن المغرب"),
    ),
}

BUYER_TEMPLATES = (
    ("coa", "📄 شهادة تحليل (COA) من معمل معتمد"),
    ("mrl", "🧪 تحليل متبقيات المبيدات"),
    ("myco", "🧪 تحليل أفلاتوكسين وسموم فطرية"),
    ("micro", "🧪 تحليل ميكروبيولوجي (سالمونيلا...)"),
    ("pa", "🧪 تحليل قلويدات البيروليزيدين (للأعشاب)"),
    ("spec", "📋 المواصفات: رطوبة، شوائب، كسر، لون"),
    ("organic", "🌱 أورجانيك — شهادة COI (على TRACES لأوروبا)"),
    ("gap", "🌍 GlobalG.A.P."),
    ("halal", "☪️ شهادة حلال"),
    ("kosher", "✡️ شهادة كوشر"),
    ("gfsi", "🏭 شهادة المصنع (BRC / IFS / FSSC 22000)"),
    ("psi", "🔍 فحص قبل الشحن (SGS / BV / Intertek)"),
    ("fumig", "💨 شهادة تبخير (Fumigation)"),
    ("pack", "📦 التعبئة المطلوبة (شكاير، وزن، بالتات)"),
    ("label", "🏷️ الليبل وعلامات الشحن"),
    ("photos", "📸 صور التحميل والحاوية"),
)
TEMPLATE_TEXT = dict(BUYER_TEMPLATES)


def egypt_items(code: str, iso: str, instructions: list[dict]) -> list[tuple[str, str, str]]:
    """(text, source, url) for the Egyptian side: the standard papers, the certificate of origin for this
    destination, and the export instructions the customs authority has on this tariff line."""
    ags = A.agreements_for(iso, direction="export")
    cert = ags[0].cert_export if ags and ags[0].cert_export and ags[0].cert_export != "—" else \
        "شهادة منشأ عادية من الغرفة التجارية"
    out = [("فاتورة تجارية (Commercial Invoice) فيها البند والمنشأ والقيمة", "", ""),
           ("قائمة تعبئة (Packing List)", "", ""),
           ("بوليصة الشحن (B/L) أو البوليصة الجوية (AWB)", "", ""),
           (f"شهادة المنشأ: {cert}", f"الاتفاقية: {ags[0].name}" if ags else "", "")]
    seen = set()
    for ins in instructions or []:
        text = " ".join((ins.get("text") or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        label = f"{ins.get('code', '')} — {text}".strip(" —")
        out.append((label[:230], "الجمارك المصرية — تعليمات الصادر على البند", ""))
    return out


def destination_items(code: str, iso: str) -> tuple[list[tuple[str, str, str]], bool]:
    """(items, reviewed): the buyer country's side, and whether we have reviewed rules for that country."""
    from . import cn2026
    out: list[tuple[str, str, str]] = []
    eu = iso == "EU" or iso in A.EU
    if eu:
        cns = cn2026.lines(code)
        if cns:
            codes = "، ".join(cn2026.fmt(c) for c, _ in cns[:3])
            out.append((f"البند الأوروبي في الفاتورة: {codes}", cn2026.SOURCE, ""))
        out += [(r.text, r.source, r.url) for r in EU_RULES if r.applies(code)]
        return out, True
    rules = COUNTRY_RULES.get(iso)
    if rules:
        return [(r.text, r.source, r.url) for r in rules if r.applies(code)], True
    name = A.country_name(iso)
    out.append((f"لسه مراجعتش شروط {name} بالتفصيل — اسأل المستورد عن: شهادة الصحة النباتية، تصاريح الاستيراد، "
                "شهادة المطابقة، وتسجيل المنتج.", "Global Trade Helpdesk", GTH))
    return out, False


def build_items(code: str, iso: str, instructions: list[dict], buyer_reqs: list[str]) -> list[tuple[str, str, str, str]]:
    """All checklist lines: (section, text, source, url). section: eg / dest / buyer."""
    items = [("eg", *x) for x in egypt_items(code, iso, instructions)]
    items += [("dest", *x) for x in destination_items(code, iso)[0]]
    for r in buyer_reqs:
        items.append(("buyer", TEMPLATE_TEXT.get(r, r), "", ""))
    return items


SECTION_TITLES = {"eg": "🇪🇬 <b>مصر (التصدير)</b>", "dest": "🌍 <b>بلد المشتري (الاستيراد)</b>",
                  "buyer": "🤝 <b>العميل</b>"}


def card(sh: dict, items: list[dict]) -> str:
    """The checklist message. sh: shipment row as dict; items: rows with id/section/text/source/done."""
    flag, name = A.country_flag(sh["iso"]), A.country_name(sh["iso"])
    done = sum(1 for it in items if it["done"])
    L = [f"📋 <b>ملف شحنة #{sh['id']}</b> — {html.escape(sh['title'])}",
         f"<code>{sh['code']}</code> → {flag} {html.escape(name)}" +
         (f" — العميل: {html.escape(sh['buyer_name'])}" if sh.get("buyer_name") else ""),
         f"التقدم: <b>{done}/{len(items)}</b> {'🎉 كله جاهز' if items and done == len(items) else ''}"]
    section = None
    for i, it in enumerate(items, 1):
        if it["section"] != section:
            section = it["section"]
            L += ["", SECTION_TITLES.get(section, section)]
        mark = "✅" if it["done"] else "⬜"
        L.append(f"{mark} {i}. {html.escape(it['text'])}")
        if it.get("source"):
            L.append(f"    <i>{html.escape(it['source'])}</i>")
    L += ["", "دوس رقم الورقة لما تخلص (ودوسه تاني لو عايز تلغي). <i>ملف للمشاركة فيه روابط المصادر.</i>"]
    return "\n".join(L)


def html_doc(sh: dict, items: list[dict]) -> bytes:
    """A standalone page to share or print (opens in any browser, Save as PDF from there)."""
    flag, name = A.country_flag(sh["iso"]), A.country_name(sh["iso"])
    rows, section = [], None
    titles = {"eg": "🇪🇬 مصر — التصدير", "dest": f"{flag} {name} — الاستيراد", "buyer": "🤝 متطلبات العميل"}
    for i, it in enumerate(items, 1):
        if it["section"] != section:
            section = it["section"]
            rows.append(f"<tr class='sec'><td colspan='3'>{html.escape(titles.get(section, section))}</td></tr>")
        src = html.escape(it.get("source") or "")
        if it.get("url"):
            src = f"<a href='{html.escape(it['url'])}'>{src or 'المصدر'}</a>"
        rows.append(f"<tr><td>{'✅' if it['done'] else '⬜'}</td><td>{i}. {html.escape(it['text'])}</td>"
                    f"<td class='src'>{src}</td></tr>")
    done = sum(1 for it in items if it["done"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    page = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ملف شحنة #{sh['id']}</title>
<style>body{{font-family:system-ui,Tahoma,Arial,sans-serif;margin:24px;color:#1b1b1b}}h1{{font-size:20px;margin:0}}
.meta{{color:#555;margin:6px 0 16px}}table{{border-collapse:collapse;width:100%}}td{{border-bottom:1px solid #ddd;
padding:8px;vertical-align:top;font-size:14px}}tr.sec td{{background:#f2f4f7;font-weight:bold}}td.src{{color:#666;
font-size:12px;width:28%}}.foot{{color:#777;font-size:12px;margin-top:16px}}</style></head><body>
<h1>ملف شحنة #{sh['id']} — {html.escape(sh['title'])}</h1>
<div class="meta">البند {sh['code']} → {flag} {html.escape(name)}{' — العميل: ' + html.escape(sh['buyer_name'])
                                                                 if sh.get('buyer_name') else ''}
 · التقدم {done}/{len(items)} · {now}</div>
<table>{''.join(rows)}</table>
<div class="foot">ELHEIBALAND — من بوت الجمارك. شروط الدول اتراجعت في {REVIEWED} من المصادر المكتوبة جنب كل بند،
واتأكد دايماً مع المستورد قبل الشحن.</div></body></html>"""
    return page.encode("utf-8")
