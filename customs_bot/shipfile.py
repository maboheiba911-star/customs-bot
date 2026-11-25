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

Sources reviewed on 25 Nov 2026:
  Gulf   GCC plant quarantine law art. 15 (phytosanitary certificate with every plant consignment, via IPPC/Bahrain);
         GCC guide for imported food control (original health certificate); GCC common labels (trade.gov/Bahrain).
  Saudi  SFDA food clearance conditions (Apr 2025): phyto for grains/crops/produce, invoice with expiry + batch,
         Arabic label, Ghad registration; SFDA CoC mandatory for Egyptian produce/crops/spices (Intertek 2021,
         Bureau Veritas datasheet 2024: witnessed loading, sealing, lab tests); Mawani pallets rule (June 2025,
         phased over a year; bulk and jumbo bags exempt); Fasah declaration 48 h before arrival (trade.gov 2026).
  UAE    USDA FAIRS Nov 2025 (ZAD/FIRS registration, UAE.S GSO 9 sticker, MRL 1/2024, GSO 841); MoFA eDAS invoice
         attestation for imports of AED 10,000+ (Cabinet Resolution 38/2022).
  Kuwait / Qatar / Oman  trade.gov (chamber-legalized invoice, origin marking, import licence, Bayan); CFIA (WATHEQ).
  South Africa  gov.za plant import permit (Agricultural Pests Act); USDA FAIRS Jun 2026 (APS Act 119/1990 for
         dry beans, pulses, sorghum and other grains; R146 English labels).
  Algeria  USDA FAIRS certificates Feb 2026 (phyto + non-GE for plant products; grains: quality, fumigation with
         phosphine only, non-radioactivity; 120% deposit 30 days before shipment); trade.gov (PPI, Arabic labels).
  Tunisia  MICoR (pulses: phyto, no permit); trade.gov (licences/quotas for some farm goods; payment after
         shipping documents).
  Libya  USDA FAIRS 2022 (Ministry of Economy registration, health certificate, Arabic label, halal, non-GE letter
         for grains); Export Portal (permit + phyto, legalization); TÜV (CoI under CBL resolution 96/2015 for L/C).
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
GCC_PQ = "https://www.ippc.int/en/countries/bahrain/eventreporting/2017/10/import-phytosanitary-requirements/"
GCC_GUIDE = "https://members.wto.org/crnattachments/2017/sps/bhr/17_0268_00_e.pdf"
GCC_LABELS = "https://www.trade.gov/country-commercial-guides/bahrain-labeling-and-marking-requirements"
SA_SFDA = "https://sfda.gov.sa/sites/default/files/2025-04/ConditionsRequirementsFoodClearanceE.pdf"
SA_COC = ("https://cdn1-verigates.bureauveritas.com/sites/verigates/files/2024-09/"
          "SAUDI%20ARABIA-%20SFDA%20Food%20Program%20datasheet.pdf")
SA_PALLETS = ("https://www.hapag-lloyd.com/en/services-information/news/2025/05/"
              "saudi-arabia-imports--pallets-to-load-goods-mandatory.html")
SA_TRADE = "https://www.trade.gov/country-commercial-guides/saudi-arabia-import-requirements-and-documentation"
AE_FAIRS = "https://agriexchange.apeda.gov.in/ImportRegulations/1141920251222142541207.pdf"
AE_MOFA = "https://www.mofa.gov.ae/Services/EDAS-Attestation-v2"
KW_TRADE = "https://www.trade.gov/country-commercial-guides/kuwait-import-requirements-and-documentation"
KW_LABELS = "https://www.trade.gov/country-commercial-guides/kuwait-labelingmarking-requirements"
QA_WATHEQ = ("https://inspection.canada.ca/en/exporting-food-plants-animals/food-exports/requirements-library/"
             "qatar-manufactured-foods")
QA_TRADE = "https://www.trade.gov/country-commercial-guides/qatar-import-requirements-and-documentation"
OM_TRADE = "https://www.trade.gov/country-commercial-guides/oman-trade-barriers"
ZA_PERMIT = "https://www.gov.za/services/export-permits-import/phytosanitary-permit"
ZA_FAIRS = "https://agriexchange.apeda.gov.in/ImportRegulations/043652026622114414377.pdf"
DZ_FAIRS = "https://agriexchange.apeda.gov.in/ImportRegulations/040052026312161038314.pdf"
DZ_PPI = "https://www.trade.gov/market-intelligence/algeria-mandatory-import-forecast-program"
DZ_LABELS = "https://www.trade.gov/country-commercial-guides/algeria-labeling-and-marking-requirements"
TN_MICOR = "https://micor.agriculture.gov.au/plants/tunisia_tn/lentils"
TN_TRADE = "https://www.trade.gov/country-commercial-guides/tunisia-trade-barriers"
TN_PAY = "https://www.trade.gov/country-commercial-guides/tunisia-import-requirements-and-documentation"
LY_FAIRS = ("https://agriexchange.apeda.gov.in/ImportRegulations/"
            "Libya%20FAIRS%20Annual%20Country%20ReportRabatLibyaLY20220001.pdf")
LY_PORTAL = "https://www.exportportal.com/library_customs/country/libya-136"
LY_DOCS = "https://www.exportportal.com/international_standards/detail/country/libya-136"
LY_COI = ("https://www.tuv.com/content-media-files/united-arab-emirates/pdfs/s03/"
          "tuv_r_product_sheet_din_a4_coi-scheme-for-libya.pdf")

FIRST_REVIEW, SECOND_REVIEW = "24 نوفمبر 2026", "25 نوفمبر 2026"
REVIEWED = SECOND_REVIEW                      # the latest review of any country
REVIEWED_ON = {"EU": FIRST_REVIEW, "KE": FIRST_REVIEW, "NG": FIRST_REVIEW, "MA": FIRST_REVIEW}

PLANT_CHAPTERS = {"06", "07", "08", "09", "10", "11", "12", "13", "14"}
PROCESSED_FOOD = ("15", "16", "17", "18", "19", "20", "21", "22")


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

GCC_PHYTO = Rule("شهادة صحة نباتية سارية — لازم مع كل شحنة نباتات أو منتجات نباتية (قانون الحجر الزراعي الخليجي "
                 "الموحد، مادة 15).", "قانون الحجر الزراعي لدول الخليج — IPPC", GCC_PQ, plants=True)
GCC_HEALTH = Rule("للأغذية المصنّعة: شهادة صحية أصلية من الجهة الرسمية في مصر (هيئة سلامة الغذاء) مع كل شحنة.",
                  "دليل الرقابة الخليجي على الأغذية المستوردة", GCC_GUIDE, hs=PROCESSED_FOOD)
GCC_LABEL = Rule("الليبل بالعربي (أو عربي وإنجليزي) وعليه تاريخ الإنتاج والانتهاء — مواصفات خليجية موحدة.",
                 "trade.gov — اشتراطات الليبل الخليجية (ديسمبر 2025)", GCC_LABELS)

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
    "SA": (
        Rule("⚠️ شهادة مطابقة (CoC) لكل شحنة من جهة معتمدة من هيئة الغذاء والدواء SFDA (زي SGS أو Bureau Veritas) — "
             "إلزامية للخضار والفاكهة والمحاصيل والتوابل المصرية: فحص وعينة وتحليل في معمل معتمد (متبقيات، ولبعض "
             "الأصناف فيروس الكبد A)، وحضور التحميل وختم الحاوية. اتفق معاهم قبل التحميل.",
             "SFDA — برنامج شهادات المطابقة (Intertek 2021 / Bureau Veritas 2024)", SA_COC, plants=True),
        Rule("شهادة صحة نباتية — مطلوبة للحبوب والمحاصيل الزراعية والخضار والفاكهة الطازجة.",
             "SFDA — شروط فسح الأغذية (أبريل 2025)", SA_SFDA, plants=True),
        Rule("الفاتورة لازم يبقى فيها الكمية وتاريخ الانتهاء ورقم التشغيلة (Batch) لكل صنف.",
             "SFDA — شروط فسح الأغذية (أبريل 2025)", SA_SFDA),
        Rule("الليبل بالعربي (SFDA.FD/GSO 9) وعليه تاريخ الانتهاء (SFDA.FD/GSO 150-1). أي «حلال» أو «أورجانيك» "
             "على الليبل لازم شهادة تثبته.", "SFDA — شروط فسح الأغذية (أبريل 2025)", SA_SFDA),
        Rule("📦 البضاعة جوه الحاوية لازم تبقى على بالتات (إلزامي بعد سنة تدرّج من يونيو 2025) — إلا البضاعة السايبة "
             "والجامبو باج التقيلة. الحاوية المخالفة ممكن تترفض أو تتغرّم.",
             "هيئة الموانئ السعودية «موانئ» — Hapag-Lloyd مايو 2025", SA_PALLETS),
        Rule("المستورد لازم يكون مسجّل منشأته والأصناف على منصة Ghad بتاعة SFDA، ويقدّم البيان على «فسح» قبل وصول "
             "الشحنة بـ 48 ساعة على الأقل — ابعتله الفاتورة والبوليصة وشهادة المنشأ بدري.",
             "SFDA / trade.gov — السعودية (مايو 2026)", SA_TRADE),
    ),
    "AE": (
        GCC_PHYTO,
        Rule("تصريح الاستيراد (وزارة التغير المناخي والبيئة) مطلوب للبذور والشتلات — للأكل غالباً لأ، اتأكد مع المستورد.",
             "تقرير USDA عن الإمارات (نوفمبر 2025)", AE_FAIRS, plants=True),
        Rule("المستورد لازم يسجّل المنتج قبل الاستيراد على نظام ZAD (أو FIRS لو دبي)، وممكن يطلبوا تحليل في معمل البلدية.",
             "تقرير USDA عن الإمارات (نوفمبر 2025)", AE_FAIRS),
        Rule("الليبل بالعربي (UAE.S GSO 9) — ينفع استيكر عربي واحد متوافق عليه ويتلزق قبل التصدير. البقوليات والحبوب "
             "الجافة والتوابل غالباً معفية من تاريخ الانتهاء بس لازم تاريخ إنتاج أو تعبئة.",
             "تقرير USDA عن الإمارات (نوفمبر 2025)", AE_FAIRS),
        Rule("متبقيات المبيدات حسب UAE.S MRL 1/2024 والأفلاتوكسين حسب GSO 841 — اعمل التحليل قبل الشحن.",
             "تقرير USDA عن الإمارات (نوفمبر 2025)", AE_FAIRS, plants=True),
        Rule("الفاتورة لو 10,000 درهم أو أكتر لازم تتوثّق إلكترونياً من الخارجية الإماراتية (eDAS، رسوم 150 درهم) — "
             "المستورد بيعملها خلال 14 يوم، ومش مطلوبة للمناطق الحرة. خلي الفاتورة مطابقة لباقي الورق بالظبط.",
             "قرار مجلس الوزراء 38 لسنة 2022 — الخارجية الإماراتية", AE_MOFA),
        GCC_HEALTH,
    ),
    "KW": (
        GCC_PHYTO,
        Rule("الفاتورة لازم تتصدّق من الغرفة التجارية، وشهادة المنشأ أصل + نسختين.", "trade.gov — الكويت", KW_TRADE),
        Rule("بلد المنشأ يتكتب على البضاعة بطريقة متتشالش ولا تتعدّل (Product of Egypt).", "trade.gov — الكويت",
             KW_LABELS),
        Rule("المستورد لازم يكون معاه رخصة استيراد من وزارة التجارة ومسجّل في غرفة تجارة الكويت.", "trade.gov — الكويت",
             KW_TRADE),
        GCC_LABEL,
        GCC_HEALTH,
    ),
    "QA": (
        GCC_PHYTO,
        Rule("المنتج لازم يكون متسجّل على نظام WATHEQ بتاع وزارة الصحة القطرية، والنظام بيحدد مستوى الفحص في "
             "المنفذ — اتفق مع المستورد على التسجيل.", "الوكالة الكندية لفحص الأغذية CFIA — شروط قطر", QA_WATHEQ),
        Rule("المستورد لازم يكون معاه رخصة استيراد.", "trade.gov — قطر", QA_TRADE),
        GCC_LABEL,
        GCC_HEALTH,
    ),
    "OM": (
        GCC_PHYTO,
        Rule("المستورد لازم يكون مسجّل في وزارة التجارة والصناعة عن طريق منصة «بيان».", "trade.gov — عُمان", OM_TRADE),
        GCC_LABEL,
        GCC_HEALTH,
    ),
    "BH": (
        GCC_PHYTO,
        GCC_LABEL,
        GCC_HEALTH,
    ),
    "ZA": (
        Rule("تصريح استيراد نباتات (Import permit) من وزارة الزراعة في جنوب أفريقيا — لو المنتج مش معفي، المستورد "
             "بيطلعه قبل الشحن ويبعتلك نسخة، وصالح سنة. الشحنة لازم تطابق شروطه.", "gov.za — تصريح استيراد النباتات",
             ZA_PERMIT, plants=True),
        Rule("شهادة صحة نباتية أصلية تسافر مع البضاعة، وفيها الإقرارات الإضافية اللي مكتوبة في التصريح.",
             "gov.za — تصريح استيراد النباتات", ZA_PERMIT, plants=True),
        Rule("البقوليات الجافة والحبوب (فاصوليا، فول، ذرة رفيعة...) لازم تطابق مواصفات التدريج والتعبئة والعلامات "
             "المحلية (قانون 119 لسنة 1990) — بتتفحص في الميناء.", "تقرير USDA عن جنوب أفريقيا (يونيو 2026)", ZA_FAIRS,
             hs=("0713", "10", "1202")),
        Rule("الليبل بالإنجليزي (لائحة R146): المكونات، بيانات المستورد، «Product of Egypt»، رقم التشغيلة، التاريخ، "
             "والوزن بالمتري.", "تقرير USDA عن جنوب أفريقيا (يونيو 2026)", ZA_FAIRS),
    ),
    "DZ": (
        Rule("شهادة صحة نباتية — لكل المنتجات النباتية، ومعاها شهادة إن المنتج مش معدّل وراثياً (Non-GMO).",
             "تقرير USDA عن شهادات الجزائر (فبراير 2026)", DZ_FAIRS, plants=True),
        Rule("الحبوب كمان: شهادة جودة ومطابقة، وشهادة تبخير (فوسفيد الألومنيوم أو الماغنسيوم بس)، وشهادة خلو من الإشعاع.",
             "تقرير USDA عن شهادات الجزائر (فبراير 2026)", DZ_FAIRS, hs=("10",)),
        Rule("الأصول (المنشأ والصحة النباتية) لازم تسافر مع الشحنة — وفي فحص مطابقة في الميناء قبل الإفراج "
             "(التجارة والزراعة).", "تقرير USDA عن شهادات الجزائر (فبراير 2026)", DZ_FAIRS),
        Rule("⏱️ لو البضاعة للبيع: المستورد لازم يودع 120% من قيمة الصفقة في بنك جزائري قبل الشحن بـ 30 يوم — "
             "متشحنش قبل ما يأكدلك.", "تقرير USDA عن شهادات الجزائر (فبراير 2026)", DZ_FAIRS),
        Rule("المستورد لازم يكون قدّم «برنامج الاستيراد التقديري» (PPI) لوزارة التجارة الخارجية قبل أي استيراد.",
             "trade.gov — الجزائر", DZ_PPI),
        Rule("الليبل بالعربي — إلزامي وبيتطبّق بصرامة (الفرنساوي مش إلزامي بس مفيد).", "trade.gov — الجزائر (يناير 2023)",
             DZ_LABELS),
    ),
    "TN": (
        Rule("شهادة صحة نباتية — للمنتجات النباتية الخام. البقوليات زي العدس للاستهلاك مش محتاجة تصريح استيراد.",
             "MICoR / تقرير USDA عن تونس", TN_MICOR, plants=True),
        Rule("أغلب المنتجات استيرادها حر، بس في منتجات زراعية محتاجة رخصة من وزارة التجارة أو عليها حصص — اتأكد إن "
             "منتجك مش منهم.", "trade.gov — تونس", TN_TRADE),
        Rule("💵 المستورد مش بيقدر يحوّل الفلوس غير بعد ما يقدّم للبنك مستندات بتثبت الشحن — ابعتله البوليصة أول ما تطلع.",
             "trade.gov — تونس", TN_PAY),
    ),
    "LY": (
        Rule("تصريح استيراد + شهادة صحة نباتية — لكل النباتات والمنتجات النباتية.", "Export Portal — لوائح ليبيا",
             LY_PORTAL, plants=True),
        Rule("المنتج لازم يتسجّل في وزارة الاقتصاد الليبية قبل التخليص: قائمة مكونات + شهادة بيع حر + شهادة تحليل من "
             "جهة رسمية في مصر.", "تقرير USDA عن ليبيا (2022)", LY_FAIRS),
        Rule("شهادة صحية (Health Certificate) من جهة رسمية — مطلوبة في الجمارك الليبية مع باقي الورق.",
             "تقرير USDA عن ليبيا (2022)", LY_FAIRS),
        Rule("الفاتورة وشهادة المنشأ بتتصدّق من الغرفة التجارية، وكمان من السفارة الليبية — اتأكد مع المستورد.",
             "Export Portal — لوائح ليبيا", LY_DOCS),
        Rule("الليبل بالعربي وعليه تاريخ الانتهاء والوزن الصافي والمكونات وطريقة التخزين.", "تقرير USDA عن ليبيا (2022)",
             LY_FAIRS),
        Rule("شهادة حلال — مطلوبة لاستيراد المنتجات الغذائية (اتأكد مع المستورد لو خامات).", "تقرير USDA عن ليبيا (2022)",
             LY_FAIRS),
        Rule("شحنات الحبوب لازم معاها خطاب إنها مش معدّلة وراثياً.", "تقرير USDA عن ليبيا (2022)", LY_FAIRS,
             hs=("10",)),
        Rule("لو الدفع بالاعتماد المستندي (L/C): شهادة فحص قبل الشحن (CoI) حسب قرار المصرف المركزي الليبي 96 لسنة 2015.",
             "TÜV Rheinland — برنامج ليبيا", LY_COI),
    ),
}


def reviewed_on(iso: str) -> str:
    """When the rules shown for this country were last checked against their sources ('' = not reviewed)."""
    if iso == "EU" or iso in A.EU:
        return REVIEWED_ON["EU"]
    if iso not in COUNTRY_RULES:
        return ""
    return REVIEWED_ON.get(iso, SECOND_REVIEW)

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
    rv = reviewed_on(sh["iso"])
    review = f"شروط البلد دي اتراجعت في {rv} من المصادر المكتوبة جنب كل بند،" if rv else \
        "شروط البلد دي لسه متراجعتش بالتفصيل،"
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
<div class="foot">ELHEIBALAND — من بوت الجمارك. {review}
واتأكد دايماً مع المستورد قبل الشحن.</div></body></html>"""
    return page.encode("utf-8")
