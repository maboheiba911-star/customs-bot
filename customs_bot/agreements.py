"""Egypt's trade agreements and the countries they cover.

Two directions use this table:
  * import into Egypt  -> which agreement can lower the Egyptian duty for goods from a country
                          (the line-level numbers come from the official tariff, see origin.py)
  * export from Egypt  -> which agreement gives Egyptian-origin goods a preference abroad, and which
                          certificate the exporter gets in Egypt

Statements here are general. Anything that varies by product says so ("يتأكد") and the bot always
points to the official sources. Update this file (it is code, so the self-updater delivers it)
whenever an agreement changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .arabic import normalize

GOEIC = "هيئة الرقابة على الصادرات والواردات"


def flag(iso2: str) -> str:
    if len(iso2) != 2 or not iso2.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())


# ISO2 -> (Arabic name, English name, extra aliases)
COUNTRIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # European Union
    "AT": ("النمسا", "Austria", ()), "BE": ("بلجيكا", "Belgium", ()), "BG": ("بلغاريا", "Bulgaria", ()),
    "HR": ("كرواتيا", "Croatia", ()), "CY": ("قبرص", "Cyprus", ()), "CZ": ("التشيك", "Czechia", ("تشيكيا",)),
    "DK": ("الدنمارك", "Denmark", ("الدنمرك",)), "EE": ("إستونيا", "Estonia", ()), "FI": ("فنلندا", "Finland", ()),
    "FR": ("فرنسا", "France", ()), "DE": ("ألمانيا", "Germany", ("المانيا",)), "GR": ("اليونان", "Greece", ()),
    "HU": ("المجر", "Hungary", ()), "IE": ("أيرلندا", "Ireland", ("ايرلندا",)), "IT": ("إيطاليا", "Italy", ()),
    "LV": ("لاتفيا", "Latvia", ()), "LT": ("ليتوانيا", "Lithuania", ()), "LU": ("لوكسمبورج", "Luxembourg", ("لوكسمبورغ",)),
    "MT": ("مالطا", "Malta", ()), "NL": ("هولندا", "Netherlands", ()), "PL": ("بولندا", "Poland", ()),
    "PT": ("البرتغال", "Portugal", ()), "RO": ("رومانيا", "Romania", ()), "SK": ("سلوفاكيا", "Slovakia", ()),
    "SI": ("سلوفينيا", "Slovenia", ()), "ES": ("إسبانيا", "Spain", ("اسبانيا",)), "SE": ("السويد", "Sweden", ()),
    # EFTA, UK, Turkey, Serbia
    "CH": ("سويسرا", "Switzerland", ()), "NO": ("النرويج", "Norway", ()), "IS": ("أيسلندا", "Iceland", ("ايسلندا",)),
    "LI": ("ليختنشتاين", "Liechtenstein", ()),
    "GB": ("بريطانيا", "United Kingdom", ("المملكة المتحدة", "انجلترا", "إنجلترا", "uk", "england", "britain")),
    "TR": ("تركيا", "Turkey", ("turkiye",)), "RS": ("صربيا", "Serbia", ()),
    # Arab countries (members of the Greater Arab Free Trade Area unless noted)
    "SA": ("السعودية", "Saudi Arabia", ("المملكة العربية السعودية", "ksa", "saudi")),
    "AE": ("الإمارات", "United Arab Emirates", ("الامارات", "uae", "dubai", "دبي")),
    "KW": ("الكويت", "Kuwait", ()), "QA": ("قطر", "Qatar", ()), "BH": ("البحرين", "Bahrain", ()),
    "OM": ("عمان", "Oman", ("سلطنة عمان",)), "JO": ("الأردن", "Jordan", ("الاردن",)), "LB": ("لبنان", "Lebanon", ()),
    "SY": ("سوريا", "Syria", ("سورية",)), "IQ": ("العراق", "Iraq", ()), "PS": ("فلسطين", "Palestine", ()),
    "LY": ("ليبيا", "Libya", ()), "TN": ("تونس", "Tunisia", ()), "MA": ("المغرب", "Morocco", ()),
    "SD": ("السودان", "Sudan", ()), "YE": ("اليمن", "Yemen", ()), "DZ": ("الجزائر", "Algeria", ()),
    "MR": ("موريتانيا", "Mauritania", ()), "SO": ("الصومال", "Somalia", ()), "DJ": ("جيبوتي", "Djibouti", ()),
    "KM": ("جزر القمر", "Comoros", ("القمر",)),
    # Rest of Africa
    "AO": ("أنجولا", "Angola", ("انغولا",)), "BJ": ("بنين", "Benin", ()), "BW": ("بوتسوانا", "Botswana", ()),
    "BF": ("بوركينا فاسو", "Burkina Faso", ()), "BI": ("بوروندي", "Burundi", ()),
    "CV": ("الرأس الأخضر", "Cabo Verde", ("كاب فيردي",)), "CM": ("الكاميرون", "Cameroon", ()),
    "CF": ("أفريقيا الوسطى", "Central African Republic", ()), "TD": ("تشاد", "Chad", ()),
    "CG": ("الكونغو", "Congo", ("الكونغو برازافيل",)), "CD": ("الكونغو الديمقراطية", "DR Congo", ()),
    "CI": ("ساحل العاج", "Côte d'Ivoire", ("كوت ديفوار",)), "GQ": ("غينيا الاستوائية", "Equatorial Guinea", ()),
    "ER": ("إريتريا", "Eritrea", ("اريتريا",)), "SZ": ("إسواتيني", "Eswatini", ("سوازيلاند",)),
    "ET": ("إثيوبيا", "Ethiopia", ("اثيوبيا",)), "GA": ("الجابون", "Gabon", ("الغابون",)),
    "GM": ("جامبيا", "Gambia", ("غامبيا",)), "GH": ("غانا", "Ghana", ()), "GN": ("غينيا", "Guinea", ()),
    "GW": ("غينيا بيساو", "Guinea-Bissau", ()), "KE": ("كينيا", "Kenya", ()), "LS": ("ليسوتو", "Lesotho", ()),
    "LR": ("ليبيريا", "Liberia", ()), "MG": ("مدغشقر", "Madagascar", ()), "MW": ("ملاوي", "Malawi", ()),
    "ML": ("مالي", "Mali", ()), "MU": ("موريشيوس", "Mauritius", ()), "MZ": ("موزمبيق", "Mozambique", ()),
    "NA": ("ناميبيا", "Namibia", ()), "NE": ("النيجر", "Niger", ()), "NG": ("نيجيريا", "Nigeria", ()),
    "RW": ("رواندا", "Rwanda", ()), "ST": ("ساو تومي وبرينسيب", "Sao Tome and Principe", ()),
    "SN": ("السنغال", "Senegal", ()), "SC": ("سيشل", "Seychelles", ()), "SL": ("سيراليون", "Sierra Leone", ()),
    "ZA": ("جنوب أفريقيا", "South Africa", ("جنوب افريقيا",)), "SS": ("جنوب السودان", "South Sudan", ()),
    "TZ": ("تنزانيا", "Tanzania", ()), "TG": ("توجو", "Togo", ("توغو",)), "UG": ("أوغندا", "Uganda", ("اوغندا",)),
    "ZM": ("زامبيا", "Zambia", ()), "ZW": ("زيمبابوي", "Zimbabwe", ()),
    # Mercosur
    "AR": ("الأرجنتين", "Argentina", ("الارجنتين",)), "BR": ("البرازيل", "Brazil", ()),
    "PY": ("باراجواي", "Paraguay", ("باراغواي",)), "UY": ("أوروجواي", "Uruguay", ("اورجواي", "اوروغواي")),
    # Main partners without a free-trade agreement with Egypt
    "CN": ("الصين", "China", ("china",)), "IN": ("الهند", "India", ()),
    "US": ("أمريكا", "United States", ("الولايات المتحدة", "امريكا", "usa", "us", "america")),
    "JP": ("اليابان", "Japan", ()), "KR": ("كوريا الجنوبية", "South Korea", ("كوريا",)),
    "RU": ("روسيا", "Russia", ()), "UA": ("أوكرانيا", "Ukraine", ("اوكرانيا",)), "BY": ("بيلاروسيا", "Belarus", ()),
    "KZ": ("كازاخستان", "Kazakhstan", ()), "ID": ("إندونيسيا", "Indonesia", ("اندونيسيا",)),
    "MY": ("ماليزيا", "Malaysia", ()), "VN": ("فيتنام", "Vietnam", ()), "TH": ("تايلاند", "Thailand", ()),
    "PH": ("الفلبين", "Philippines", ()), "SG": ("سنغافورة", "Singapore", ()), "PK": ("باكستان", "Pakistan", ()),
    "BD": ("بنجلاديش", "Bangladesh", ("بنغلاديش",)), "LK": ("سريلانكا", "Sri Lanka", ()),
    "IR": ("إيران", "Iran", ("ايران",)), "TW": ("تايوان", "Taiwan", ()), "HK": ("هونج كونج", "Hong Kong", ()),
    "AU": ("أستراليا", "Australia", ("استراليا",)), "NZ": ("نيوزيلندا", "New Zealand", ()),
    "CA": ("كندا", "Canada", ()), "MX": ("المكسيك", "Mexico", ()), "CL": ("تشيلي", "Chile", ()),
    "PE": ("بيرو", "Peru", ()), "CO": ("كولومبيا", "Colombia", ()), "GE": ("جورجيا", "Georgia", ()),
    "AZ": ("أذربيجان", "Azerbaijan", ("اذربيجان",)), "AM": ("أرمينيا", "Armenia", ("ارمينيا",)),
    "UZ": ("أوزبكستان", "Uzbekistan", ("اوزبكستان",)), "AL": ("ألبانيا", "Albania", ("البانيا",)),
    "BA": ("البوسنة", "Bosnia and Herzegovina", ("البوسنة والهرسك",)), "MK": ("مقدونيا الشمالية", "North Macedonia", ()),
    "ME": ("الجبل الأسود", "Montenegro", ()), "MD": ("مولدوفا", "Moldova", ()),
    "HT": ("هايتي", "Haiti", ()),
}

EU = ("AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE")
EFTA = ("CH", "NO", "IS", "LI")
GAFTA = ("SA", "AE", "KW", "QA", "BH", "OM", "JO", "LB", "SY", "IQ", "PS", "LY", "TN", "MA", "SD", "YE", "DZ")
AGADIR = ("JO", "MA", "TN")
COMESA_FTA = ("BI", "KM", "DJ", "KE", "LY", "MG", "MW", "MU", "RW", "SC", "SD", "TN", "UG", "ZM", "ZW")
AFRICA = ("DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM", "CG", "CD", "CI", "DJ", "GQ", "SZ", "ET",
          "GA", "GM", "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ", "NA", "NE",
          "NG", "RW", "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD", "TZ", "TG", "TN", "UG", "ZM", "ZW")
MERCOSUR = ("AR", "BR", "PY", "UY")
BLOCS = {"EU": ("الاتحاد الأوروبي", EU), "EFTA": ("دول الإفتا", EFTA), "MERCOSUR": ("الميركسور", MERCOSUR)}


@dataclass(frozen=True)
class Agreement:
    key: str
    name: str                      # Arabic display name
    en: str
    icon: str
    members: tuple[str, ...]
    imports: bool                  # Egypt grants preferences on imports from members
    exports: bool                  # Egyptian-origin goods get preferences in members
    cert_import: str               # what the foreign supplier must send with the goods
    cert_export: str               # what the Egyptian exporter gets, and from whom
    cert_export_en: str = ""
    approved_exporter: bool = False
    import_rule: str | None = None   # general rule when the tariff line says nothing: "exempt"
    export_treatment: str = ""
    origin_rule: str = ""
    status: str = ""
    tariff_keys: tuple[str, ...] = ()   # sections in the official Taxes list (taxparse keys)
    notes: tuple[str, ...] = field(default_factory=tuple)


PEM_CERT = "EUR.1 أو EUR-MED، أو إقرار منشأ على الفاتورة من مصدّر معتمد"
AGREEMENTS: dict[str, Agreement] = {a.key: a for a in [
    Agreement(
        "eu", "اتفاقية الشراكة المصرية الأوروبية", "EU-Egypt Association Agreement", "🇪🇺", EU, True, True,
        cert_import=PEM_CERT + " (من بلد المورد)",
        cert_export=f"EUR.1 أو EUR-MED من {GOEIC}، أو إقرار منشأ على الفاتورة لو انت مصدّر معتمد",
        cert_export_en="an EUR.1 / EUR-MED certificate or an approved-exporter origin declaration",
        approved_exporter=True,
        export_treatment="المنتجات الصناعية المصرية بتدخل الاتحاد الأوروبي معفاة. الزراعية معظمها معفى، "
                         "وبعضها بحصص أو أسعار دخول — يتأكد للبند.",
        origin_rule="قواعد المنشأ الأورومتوسطية (PEM)، ومسموح بتراكم المنشأ مع دول الأورومتوسطي.",
        status="سارية من يونيو 2004.", tariff_keys=("eu",)),
    Agreement(
        "uk", "اتفاقية الشراكة المصرية البريطانية", "UK-Egypt Association Agreement", "🇬🇧", ("GB",), True, True,
        cert_import="EUR.1 أو إقرار منشأ على الفاتورة (من المورد البريطاني)",
        cert_export=f"EUR.1 من {GOEIC}، أو إقرار منشأ على الفاتورة لو انت مصدّر معتمد",
        cert_export_en="an EUR.1 certificate or an approved-exporter origin declaration",
        approved_exporter=True,
        export_treatment="نفس معاملة الاتفاقية الأوروبية تقريباً: الصناعي معفى، والزراعي معظمه معفى — يتأكد للبند.",
        origin_rule="قواعد منشأ زي الاتفاقية الأوروبية.", status="سارية من يناير 2021.", tariff_keys=("uk",)),
    Agreement(
        "efta", "اتفاقية الإفتا", "EFTA-Egypt FTA", "🏔️", EFTA, True, True,
        cert_import=PEM_CERT + " (من بلد المورد)",
        cert_export=f"EUR.1 أو EUR-MED من {GOEIC}، أو إقرار المصدّر المعتمد",
        cert_export_en="an EUR.1 / EUR-MED certificate or an approved-exporter origin declaration",
        approved_exporter=True,
        export_treatment="المنتجات الصناعية معفاة. الزراعية حسب اتفاق ثنائي مع كل دولة — يتأكد للبند.",
        origin_rule="قواعد المنشأ الأورومتوسطية (PEM).", status="سارية من أغسطس 2007.", tariff_keys=("efta",)),
    Agreement(
        "tr", "اتفاقية التجارة الحرة مع تركيا", "Egypt-Turkey FTA", "🇹🇷", ("TR",), True, True,
        cert_import="EUR.1 أو إقرار منشأ على الفاتورة (من المورد التركي)",
        cert_export=f"EUR.1 من {GOEIC}، أو إقرار منشأ على الفاتورة لو انت مصدّر معتمد",
        cert_export_en="an EUR.1 certificate or an approved-exporter origin declaration",
        approved_exporter=True,
        export_treatment="الصناعي معفى. الزراعي حسب بروتوكول الاتفاقية، وبعضه بحصص — يتأكد للبند.",
        origin_rule="قواعد PEM المعدّلة بين البلدين من 26 فبراير 2026.",
        status="سارية من مارس 2007.", tariff_keys=("tr",)),
    Agreement(
        "gafta", "منطقة التجارة الحرة العربية الكبرى", "Greater Arab Free Trade Area (GAFTA)", "🌍", GAFTA, True, True,
        cert_import="شهادة منشأ عربية مصدّقة من بلد المورد",
        cert_export=f"شهادة منشأ عربية من {GOEIC}",
        cert_export_en="an Arab Certificate of Origin",
        import_rule="exempt",
        export_treatment="السلع العربية المنشأ معفاة من الجمارك بين الدول الأعضاء، "
                         "إلا السلع الممنوعة لأسباب دينية أو صحية أو أمنية — يتأكد للبند.",
        origin_rule="متحصل عليه بالكامل، أو قيمة مضافة عربية بالنسبة المحددة في قواعد المنشأ العربية — يتأكد.",
        status="إعفاء كامل من 2005."),
    Agreement(
        "agadir", "اتفاقية أغادير", "Agadir Agreement", "🌊", AGADIR, True, True,
        cert_import=PEM_CERT + " (من بلد المورد)",
        cert_export=f"EUR.1 أو EUR-MED من {GOEIC}، أو إقرار المصدّر المعتمد",
        cert_export_en="an EUR.1 / EUR-MED certificate or an approved-exporter origin declaration",
        approved_exporter=True, import_rule="exempt",
        export_treatment="معفى بين الدول الأعضاء، بقواعد المنشأ الأورومتوسطية.",
        origin_rule="قواعد المنشأ الأورومتوسطية، ومسموح بتراكم المنشأ.", status="سارية من 2007."),
    Agreement(
        "comesa", "منطقة التجارة الحرة للكوميسا", "COMESA Free Trade Area", "🌍", COMESA_FTA, True, True,
        cert_import="شهادة منشأ الكوميسا من بلد المورد",
        cert_export=f"شهادة منشأ الكوميسا من {GOEIC}",
        cert_export_en="a COMESA Certificate of Origin",
        import_rule="exempt",
        export_treatment="معفى في الدول اللي دخلت منطقة التجارة الحرة للكوميسا — يتأكد لكل دولة.",
        origin_rule="متحصل عليه بالكامل، أو تشغيل كافي حسب قواعد الكوميسا (قيمة مضافة أو تغيير بند) — يتأكد.",
        status="منطقة التجارة الحرة من 2000."),
    Agreement(
        "afcfta", "اتفاقية التجارة الحرة القارية الأفريقية", "African Continental Free Trade Area (AfCFTA)", "🌍",
        AFRICA, True, True,
        cert_import="شهادة منشأ أفريقية (AfCFTA) من بلد المورد",
        cert_export=f"شهادة منشأ أفريقية (AfCFTA) من {GOEIC}",
        cert_export_en="an AfCFTA Certificate of Origin",
        export_treatment="تخفيضات تدريجية حسب جدول كل دولة، والدولة لازم تكون بدأت تطبّق الاتفاقية — يتأكد.",
        origin_rule="متحصل عليه بالكامل، أو القاعدة الخاصة بالبند في قواعد المنشأ الأفريقية.",
        status="التجارة بيها بدأت 2021. مصر طلّعت 6,604 شهادة منشأ أفريقية من 2023 لحد أكتوبر 2026.",
        tariff_keys=("afcfta_a", "afcfta_b")),
    Agreement(
        "mercosur", "اتفاقية الميركسور", "Egypt-Mercosur FTA", "🌎", MERCOSUR, True, True,
        cert_import="شهادة منشأ الميركسور من بلد المورد",
        cert_export=f"شهادة منشأ الاتفاقية من {GOEIC}",
        cert_export_en="a Mercosur-Egypt Certificate of Origin",
        export_treatment="تخفيضات حسب جداول الاتفاقية، وآخر التخفيضات اكتملت في سبتمبر 2026 — يتأكد للبند.",
        origin_rule="قواعد المنشأ في الاتفاقية.", status="سارية من سبتمبر 2017.", tariff_keys=("mercosur",)),
    Agreement(
        "serbia", "اتفاقية التجارة الحرة مع صربيا", "Egypt-Serbia FTA", "🇷🇸", ("RS",), True, True,
        cert_import="شهادة المنشأ اللي بتحددها الاتفاقية (من المورد الصربي)",
        cert_export=f"شهادة المنشأ اللي بتحددها الاتفاقية، من {GOEIC}",
        cert_export_en="the certificate of origin required by the agreement",
        export_treatment="تخفيضات حسب قوائم الاتفاقية — يتأكد للبند.",
        origin_rule="قواعد المنشأ في الاتفاقية.", status="سارية من 1 سبتمبر 2025."),
    Agreement(
        "china0", "إعفاء الصين للصادرات الأفريقية", "China zero-tariff treatment for African exports", "🇨🇳",
        ("CN",), False, True,
        cert_import="—",
        cert_export="شهادة منشأ مصرية حسب اشتراطات الصين — يتأكد من النموذج المطلوب",
        cert_export_en="a certificate of origin as required by China",
        export_treatment="الصين بتعفي الصادرات المصرية من الجمارك من 1 مايو 2026. "
                         "الإعفاء ده من طرف واحد، يعني الواردات الصينية لمصر مش معفاة.",
        origin_rule="قواعد المنشأ اللي بتحددها الصين.", status="من 1 مايو 2026."),
    Agreement(
        "gstp", "النظام العالمي للأفضليات التجارية (GSTP)", "Global System of Trade Preferences", "🌐", (), True, False,
        cert_import="شهادة منشأ GSTP من بلد المورد",
        cert_export="—", export_treatment="",
        origin_rule="قواعد منشأ النظام.", status="تخفيضات للواردات من الدول الأقل نمواً حسب جداول مصر.",
        tariff_keys=("gstp",)),
    Agreement(
        "gatt", "نظام الجات", "WTO bound rate", "🌐", (), True, False,
        cert_import="—", cert_export="—", export_treatment="", origin_rule="",
        status="فئة مذكورة في التعريفة لبعض البنود.", tariff_keys=("gatt",)),
    # bilateral agreements Egypt applies on imports (line-level instructions in the tariff)
    Agreement("sd", "الاتفاقية الثنائية مع السودان", "Egypt-Sudan agreement", "🇸🇩", ("SD",), True, False,
              cert_import="شهادة منشأ سودانية", cert_export="—"),
    Agreement("ma", "الاتفاقية مع المغرب", "Egypt-Morocco agreement", "🇲🇦", ("MA",), True, False,
              cert_import="شهادة منشأ مغربية حسب الاتفاقية", cert_export="—"),
    Agreement("sy", "الاتفاقية مع سوريا", "Egypt-Syria agreement", "🇸🇾", ("SY",), True, False,
              cert_import="شهادة منشأ سورية حسب الاتفاقية", cert_export="—"),
    Agreement("ps", "اتفاقية التبادل التجاري مع فلسطين", "Egypt-Palestine trade agreement", "🇵🇸", ("PS",), True, False,
              cert_import="شهادة منشأ فلسطينية (منتج وطني)", cert_export="—"),
    Agreement("ly", "الاتفاقية مع ليبيا", "Egypt-Libya agreement", "🇱🇾", ("LY",), True, False,
              cert_import="شهادة منشأ ليبية", cert_export="—"),
    Agreement("jo", "الاتفاقية الثنائية مع الأردن", "Egypt-Jordan agreement", "🇯🇴", ("JO",), True, False,
              cert_import="شهادة منشأ أردنية", cert_export="—"),
    Agreement("tn", "الاتفاقية الثنائية مع تونس", "Egypt-Tunisia agreement", "🇹🇳", ("TN",), True, False,
              cert_import="شهادة منشأ تونسية", cert_export="—"),
]}

# approved-exporter system announced for these, not in force yet
APPROVED_EXPORTER_COMING = ("gafta", "comesa")

# short labels used in lists and buttons
SHORT_NAMES = {
    "eu": "🇪🇺 الاتحاد الأوروبي", "uk": "🇬🇧 بريطانيا", "efta": "🏔️ دول الإفتا", "tr": "🇹🇷 تركيا",
    "mercosur": "🌎 الميركسور", "serbia": "🇷🇸 صربيا", "afcfta": "🌍 الأفريقية القارية",
    "gstp": "🌐 GSTP (الدول الأقل نمواً)", "gatt": "🌐 الجات", "gafta": "🌍 الدول العربية (GAFTA)",
    "agadir": "🌊 أغادير", "comesa": "🌍 الكوميسا", "china0": "🇨🇳 الصين (إعفاء من طرف واحد)",
    "sd": "🇸🇩 السودان", "ma": "🇲🇦 المغرب", "sy": "🇸🇾 سوريا", "ps": "🇵🇸 فلسطين", "ly": "🇱🇾 ليبيا",
    "jo": "🇯🇴 الأردن", "tn": "🇹🇳 تونس",
}


def short_name(key: str) -> str:
    return SHORT_NAMES.get(key) or AGREEMENTS[key].name


# words people use for each agreement (normalized when matched)
AGREEMENT_ALIASES = {
    "eu": ("اوروبا", "اوروبي", "الاتحاد الاوروبي", "الشراكه الاوروبيه", "eu", "europe"),
    "uk": ("بريطانيا", "المملكه المتحده", "انجلترا", "uk"),
    "efta": ("افتا", "الافتا", "efta"),
    "tr": ("تركيا", "turkey"),
    "gafta": ("العربيه", "الدول العربيه", "التجاره الحره العربيه", "العربيه الكبري", "gafta", "عربي"),
    "agadir": ("اغادير", "agadir"),
    "comesa": ("كوميسا", "الكوميسا", "comesa"),
    "afcfta": ("افريقيا", "الافريقيه", "القاريه", "afcfta"),
    "mercosur": ("ميركسور", "الميركسور", "mercosur"),
    "serbia": ("صربيا", "serbia"),
    "china0": ("الصين", "china"),
    "gstp": ("gstp",),
}


def _norm(text: str) -> str:
    return normalize(text).replace(" ", "")


def _aliases() -> dict[str, str]:
    out: dict[str, str] = {}
    for iso, (ar, en, extra) in COUNTRIES.items():
        for name in (ar, en, *extra):
            n = _norm(name)
            out.setdefault(n, iso)
            if n.startswith("ال") and len(n) > 4:
                out.setdefault(n[2:], iso)
    for bloc, (ar, _) in BLOCS.items():
        out.setdefault(_norm(ar), bloc)
    for word in ("اوروبا", "اوربا", "الاتحادالاوروبي", "الاتحادالاوربي", "eu", "europe"):
        out[_norm(word)] = "EU"
    for word in ("الافتا", "افتا", "efta"):
        out[_norm(word)] = "EFTA"
    out[_norm("الميركسور")] = "MERCOSUR"
    return out


_COUNTRY_INDEX = _aliases()


def resolve_country(text: str) -> str | None:
    """Arabic / English / colloquial country name (or a bloc like 'اوروبا') -> ISO2 or bloc key."""
    n = _norm(text or "")
    if not n:
        return None
    for cand in (n, n[1:] if n.startswith("ل") else "", n[2:] if n.startswith("لل") else "",
                 "ال" + n[2:] if n.startswith("لل") else ""):
        if cand and cand in _COUNTRY_INDEX:
            return _COUNTRY_INDEX[cand]
    if len(n) == 2 and n.upper() in COUNTRIES:
        return n.upper()
    return None


def country_name(iso: str) -> str:
    if iso in BLOCS:
        return BLOCS[iso][0]
    return COUNTRIES.get(iso, (iso, iso, ()))[0]


def country_en(iso: str) -> str:
    if iso == "EU":
        return "the European Union"
    return COUNTRIES.get(iso, (iso, iso, ()))[1]


def country_flag(iso: str) -> str:
    return {"EU": "🇪🇺", "EFTA": "🏔️", "MERCOSUR": "🌎"}.get(iso) or flag(iso)


def members_of(iso: str) -> tuple[str, ...]:
    """A country or a bloc -> the member countries."""
    if iso in BLOCS:
        return BLOCS[iso][1]
    return (iso,)


def agreements_for(iso: str, *, direction: str) -> list[Agreement]:
    """Agreements that cover a country (or every member of a bloc) in one direction ('import'/'export')."""
    members = set(members_of(iso))
    out = []
    for ag in AGREEMENTS.values():
        if (direction == "import" and not ag.imports) or (direction == "export" and not ag.exports):
            continue
        if ag.members and members <= set(ag.members):
            out.append(ag)
    return out


def resolve_agreement(text: str) -> Agreement | None:
    n = _norm(text or "")
    if not n:
        return None
    for key, words in AGREEMENT_ALIASES.items():
        if any(_norm(w) == n or (len(_norm(w)) > 3 and _norm(w) in n) for w in words):
            return AGREEMENTS[key]
    return AGREEMENTS.get(n)
