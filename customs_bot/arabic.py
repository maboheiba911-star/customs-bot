"""Arabic / English text normalization, light stemming and colloquial synonyms.

Everything stored in the search index and every user query goes through
``normalize`` then ``stem`` so both sides meet in the same form.
"""
from __future__ import annotations

import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_NON_WORD = re.compile(r"[_\W]+", re.UNICODE)
_ARABIC_CHAR = re.compile(r"[؀-ۿ]")

_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
_AR_SUFFIXES = ("ات", "ون", "ين")
_EN_STOP = {
    "of", "the", "and", "or", "for", "in", "on", "with", "a", "an", "to", "not",
    "other", "than", "nes", "nec", "whether", "including", "by", "its", "their",
}
_AR_STOP = {
    "او", "و", "في", "فى", "من", "علي", "على", "عن", "مع", "الي", "إلى", "الى",
    "ما", "لا", "غير", "وان", "ان", "كانت", "كان", "بما", "فيها", "ذلك", "اخر",
    "اخري", "بها", "به", "التي", "الذي", "هذه", "هذا", "ثم", "عدا", "سواء",
}


def to_ascii_digits(text: str) -> str:
    return (text or "").translate(_DIGITS)


def is_arabic(text: str) -> bool:
    return bool(_ARABIC_CHAR.search(text or ""))


def normalize(text: str) -> str:
    """Lower-case, unify Arabic letter variants, drop diacritics/punctuation."""
    t = to_ascii_digits(text or "")
    t = _TASHKEEL.sub("", t).replace("ـ", "")
    t = re.sub("[إأآٱ]", "ا", t)
    t = (
        t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
        .replace("ء", "").replace("ی", "ي").replace("ک", "ك").replace("گ", "ك")
    )
    t = t.lower()
    t = _NON_WORD.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def stem(token: str) -> str:
    """Very light stemmer: enough to match singular/plural and the article."""
    if not token:
        return token
    if _ARABIC_CHAR.search(token):
        for p in _AR_PREFIXES:
            if token.startswith(p) and len(token) - len(p) >= 3:
                token = token[len(p):]
                break
        for s in _AR_SUFFIXES:
            if token.endswith(s) and len(token) - len(s) >= 3:
                token = token[: -len(s)]
                break
        else:
            if token.endswith("ه") and len(token) >= 4:
                token = token[:-1]
        if token.endswith("ي") and len(token) >= 5:   # nisba: رياضي/رياضيه -> رياض
            token = token[:-1]
        return token
    # English / Latin
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    out = []
    for tok in normalize(text).split():
        if not keep_stopwords and (tok in _AR_STOP or tok in _EN_STOP):
            continue
        out.append(tok)
    return out


def index_text(text: str) -> str:
    """Text as stored in the FTS index (normalized + stemmed tokens).

    Arabic words glued to a one-letter preposition/conjunction (و ب ل ك ف), e.g.
    "لمعالجة" or "وبذور", are indexed twice: as written and without the letter.
    """
    out: list[str] = []
    for t in tokens(text, keep_stopwords=True):
        out.append(stem(t))
        if len(t) >= 5 and t[0] in "وبلكف" and _ARABIC_CHAR.match(t) and not t.startswith(_AR_PREFIXES):
            out.append(stem(t[1:]))
    return " ".join(out)


# ---------------------------------------------------------------------------
# Egyptian colloquial / trade words -> words used in the official tariff text.
# Keys and values are written already normalized (ه not ة, ي not ى, no hamza).
# ---------------------------------------------------------------------------
PHRASES = {
    "لاب توب": "معالجه ذاتيه معلومات حمل",
    "لابتوب": "معالجه ذاتيه معلومات حمل",
    "ذره رفيعه": "سرغوم",
    "ذره صفرا": "ذره",
    "زيت زيتون": "زيت زيتون",
    "قطع غيار": "اجزا",
    "اسبير": "اجزا",
    "تكييف هوا": "تكييف هوا",
    "مكيف هوا": "تكييف هوا",
    "خشب زان": "خشب",
}

SYNONYMS = {
    "موبايل": ["هاتف", "هواتف"], "موبيل": ["هاتف", "هواتف"], "محمول": ["هاتف", "هواتف"],
    "تليفون": ["هاتف", "هواتف"], "تلفون": ["هاتف", "هواتف"],
    "عربيه": ["سيارات"], "عربيات": ["سيارات"], "سياره": ["سيارات"], "ملاكي": ["ركوب"],
    "موتوسيكل": ["دراجات ناريه"], "مكنه": ["الات"], "مكن": ["الات"], "ماكينه": ["الات"],
    "ماكينات": ["الات"], "تكييف": ["تكييف"], "مكيف": ["تكييف"],
    "تلاجه": ["ثلاجات"], "ثلاجه": ["ثلاجات"], "غساله": ["غسل"], "غسالات": ["غسل"],
    "شاشه": ["تليفزيون", "شاشات"], "تلفزيون": ["تليفزيون"], "تليفزيون": ["تليفزيون"],
    "بطاريه": ["مركمات", "بطاريات"], "بطاريات": ["مركمات", "بطاريات"],
    "كاوتش": ["اطارات"], "كاوتشات": ["اطارات"], "اطار": ["اطارات"],
    "رز": ["ارز"], "ارز": ["ارز"], "عيش": ["خبز"], "دقيق": ["دقيق"],
    "فول": ["فول"], "عدس": ["عدس"], "حمص": ["حمص"], "لوبيا": ["لوبيا"],
    "فاصوليا": ["فاصوليا"], "فاصوليه": ["فاصوليا"],
    "سرغم": ["سرغوم"], "سورجم": ["سرغوم"], "سورغم": ["سرغوم"],
    "ينسون": ["يانسون"], "يانسون": ["يانسون"], "شمار": ["شمر"], "شمر": ["شمر"],
    "كاموميل": ["بابونج"], "بابونج": ["بابونج"], "كركدي": ["كركديه"],
    "كمون": ["كمون"], "كزبره": ["كزبره"], "حلبه": ["حلبه"], "سمسم": ["سمسم"],
    "جبنه": ["جبن"], "جبن": ["جبن"], "لبن": ["لبن", "حليب"], "حليب": ["حليب", "لبن"],
    "زبده": ["زبد"], "فراخ": ["دواجن", "ديوك", "دجاج"], "دواجن": ["دواجن"],
    "لحمه": ["لحوم"], "لحم": ["لحوم"], "سمك": ["اسماك"], "جمبري": ["قريدس", "جمبري"],
    "سكر": ["سكر"], "شاي": ["شاي"], "قهوه": ["بن"], "بن": ["بن"], "زيت": ["زيوت"],
    "عصير": ["عصير"], "مكرونه": ["معكرونه", "عجائن"], "شوكولاته": ["شوكولاته"],
    "قماش": ["اقمشه", "منسوجات"], "اقمشه": ["اقمشه"], "هدوم": ["ملابس", "البسه"],
    "ملابس": ["ملابس", "البسه"], "تيشيرت": ["قمصان", "تي شيرت"], "بنطلون": ["سراويل", "بنطلونات"],
    "جزم": ["احذيه"], "جزمه": ["احذيه"], "حذا": ["احذيه"], "شوز": ["احذيه"],
    "شنط": ["حقائب"], "شنطه": ["حقائب"], "ورق": ["ورق"], "كرتون": ["كرتون", "ورق مقوي"],
    "بلاستيك": ["لدائن"], "بلاستك": ["لدائن"], "حديد": ["حديد"], "صاج": ["الواح"],
    "الومنيوم": ["المونيوم"], "نحاس": ["نحاس"], "خشب": ["خشب"], "سيراميك": ["بلاط", "خزف"],
    "رخام": ["رخام"], "جرانيت": ["جرانيت"], "اسمنت": ["اسمنت"], "زجاج": ["زجاج"],
    "عطر": ["عطور"], "برفان": ["عطور"], "مكياج": ["تجميل"], "شامبو": ["شامبو"],
    "صابون": ["صابون"], "منظفات": ["محضرات غسيل", "منظفات"], "ادويه": ["ادويه"],
    "دوا": ["ادويه"], "سماد": ["اسمده"], "اسمده": ["اسمده"], "مبيد": ["مبيدات"],
    "كيماويات": ["كيماويه"], "بويه": ["دهانات", "طلا"], "دهان": ["دهانات"],
    "لعب": ["لعب"], "العاب": ["لعب"], "لمبات": ["مصابيح"], "لمبه": ["مصابيح"],
    "كابلات": ["كابلات", "اسلاك"], "سلك": ["اسلاك"], "مواتير": ["محركات"], "موتور": ["محركات"],
    "مولد": ["مولدات"], "طلمبه": ["مضخات"], "طلمبات": ["مضخات"], "ونش": ["روافع"],
    "كمبيوتر": ["معالجه ذاتيه معلومات"], "طابعه": ["طابعات"],
    "كاميرا": ["الات تصوير"], "ساعه": ["ساعات"], "نظاره": ["نظارات"],
    "سجاير": ["سجائر", "لفائف"], "سجائر": ["سجائر", "لفائف"], "دخان": ["تبغ"],
    "معسل": ["تبغ"], "خمور": ["مشروبات روحيه"], "بيره": ["جعه"],
    # English trade words -> wording of the HS nomenclature
    "mobile": ["telephone", "cellular"], "cell": ["cellular"], "phone": ["telephone"], "phones": ["telephone"],
    "conditioner": ["conditioning"], "conditioners": ["conditioning"],
    "smartphone": ["smartphone", "telephone"], "laptop": ["data processing portable"],
    "computer": ["data processing"], "car": ["motor car", "vehicle"], "cars": ["motor car", "vehicle"],
    "tv": ["television"], "shoe": ["footwear"], "shoes": ["footwear"], "sneakers": ["footwear"],
    "fridge": ["refrigerator"], "tyre": ["tyre", "tire"], "tire": ["tyre"], "tires": ["tyre"],
    "sugar": ["sugar"], "rice": ["rice"], "beans": ["bean"], "bean": ["bean"],
}

# Products the tariff does not name explicitly -> where they are usually classified.
HINTS = {
    "بابونج": ("121190", "البابونج مش مذكور بالاسم في التعريفة — غالباً بيتصنف تحت 1211.90 (نباتات طبية وعطرية)."),
    "كاموميل": ("121190", "البابونج مش مذكور بالاسم في التعريفة — غالباً بيتصنف تحت 1211.90 (نباتات طبية وعطرية)."),
    "chamomile": ("121190", "Chamomile is not named in the tariff — usually classified under 1211.90."),
    "نعناع": ("121190", "النعناع المجفف للمشروبات/الطب غالباً تحت 1211.90 — والطازج كخضار ممكن يختلف."),
    "مرميه": ("121190", "المرمية غالباً تحت 1211.90 (نباتات طبية وعطرية)."),
    "ملوخيه": ("071290", "الملوخية المجففة غالباً تحت 0712.90 (خضر مجففة أخرى)."),
}


def classification_hint(text: str) -> tuple[str, str] | None:
    for tok in normalize(text).split():
        if tok in HINTS:
            return HINTS[tok]
    return None


def expand_query(text: str) -> list[list[list[str]]]:
    """Turn a user query into search groups.

    Result: one group per query word (all groups must match). Each group is a
    list of alternatives (any may match); each alternative is a list of stems
    that must all match (multi-word synonyms such as "الات معالجه بيانات").
    """
    t = " " + normalize(text) + " "
    for k, v in PHRASES.items():
        t = t.replace(" " + k + " ", " " + v + " ")
    groups: list[list[list[str]]] = []
    for tok in t.split():
        if tok in _AR_STOP or tok in _EN_STOP or len(tok) < 2:
            continue
        alternatives: list[list[str]] = []
        for alt in [tok] + SYNONYMS.get(tok, []):
            stems = [stem(p) for p in alt.split() if p not in _AR_STOP and p not in _EN_STOP]
            stems = [s for s in stems if s]
            if stems and stems not in alternatives:
                alternatives.append(stems)
        if alternatives:
            groups.append(alternatives)
    return groups


def fts_query(groups: list[list[list[str]]], *, mode: str = "AND") -> str:
    """Build an FTS5 MATCH expression from ``expand_query`` groups."""
    parts = []
    for alts in groups:
        alt_exprs = []
        for stems in alts:
            terms = [f'"{s}"*' for s in stems if s.replace('"', "")]
            if terms:
                alt_exprs.append("(" + " AND ".join(terms) + ")" if len(terms) > 1 else terms[0])
        if alt_exprs:
            parts.append("(" + " OR ".join(alt_exprs) + ")" if len(alt_exprs) > 1 else alt_exprs[0])
    return f" {mode} ".join(parts)
