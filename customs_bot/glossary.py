"""Arabic -> English product words, used to search GS1 GPC (published in English).

Keys are normalized (see arabic.normalize): ة->ه, ى->ي, no hamza.
"""
from __future__ import annotations

import re

from .arabic import is_arabic, normalize

AR_EN = {
    # legumes & grains
    "فاصوليا": "beans", "فاصوليا بيضا": "dried beans", "فول": "fava beans", "فول سوداني": "peanuts",
    "عدس": "lentils", "حمص": "chickpeas", "لوبيا": "cowpeas beans", "ترمس": "lupin",
    "بسله": "peas", "بازلا": "peas", "رز": "rice", "ارز": "rice", "قمح": "wheat", "شعير": "barley",
    "ذره": "corn maize", "ذره رفيعه": "sorghum", "سرغوم": "sorghum", "شوفان": "oats", "دقيق": "flour",
    "سميد": "semolina", "برغل": "bulgur", "كينوا": "quinoa", "سمسم": "sesame seeds",
    "عباد الشمس": "sunflower seeds", "لب": "seeds", "كتان": "flax seeds", "حلبه": "fenugreek",
    # herbs & spices
    "بابونج": "chamomile", "كاموميل": "chamomile", "يانسون": "anise", "ينسون": "anise", "شمر": "fennel",
    "شمار": "fennel", "كمون": "cumin", "كزبره": "coriander", "كراويه": "caraway", "نعناع": "mint",
    "مرميه": "sage", "زعتر": "thyme", "ريحان": "basil", "كركديه": "hibiscus", "كركدي": "hibiscus",
    "شاي": "tea", "قهوه": "coffee", "بن": "coffee", "فلفل": "pepper", "فلفل اسود": "black pepper",
    "شطه": "chilli", "قرفه": "cinnamon", "قرنفل": "cloves", "زنجبيل": "ginger", "كركم": "turmeric",
    "هيل": "cardamom", "حبهان": "cardamom", "ورق لورا": "bay leaves", "بقدونس": "parsley",
    "شبت": "dill", "اوريجانو": "oregano", "ملوخيه": "jute mallow molokhia", "حنه": "henna",
    "عرقسوس": "liquorice", "خروب": "carob", "تمر هندي": "tamarind", "زيوت عطريه": "essential oils",
    # fruit & vegetables
    "برتقال": "oranges", "يوسفي": "mandarins", "ليمون": "lemons", "عنب": "grapes", "مانجو": "mango",
    "فراوله": "strawberries", "رمان": "pomegranates", "تمر": "dates", "بلح": "dates", "تين": "figs",
    "جوافه": "guava", "موز": "bananas", "تفاح": "apples", "خوخ": "peaches", "مشمش": "apricots",
    "بطيخ": "watermelons", "كنتالوب": "melons", "زيتون": "olives", "بطاطس": "potatoes",
    "بطاطا": "sweet potatoes", "بصل": "onions", "ثوم": "garlic", "طماطم": "tomatoes", "خيار": "cucumbers",
    "جزر": "carrots", "كرنب": "cabbage", "خس": "lettuce", "سبانخ": "spinach", "فلفل رومي": "peppers",
    "باذنجان": "aubergines eggplant", "كوسه": "courgettes zucchini", "بامي": "okra", "قلقاس": "taro",
    "خضار مجمده": "frozen vegetables", "فواكه مجففه": "dried fruit", "زبيب": "raisins",
    # animal products & food
    "جبن": "cheese", "جبنه": "cheese", "لبن": "milk", "حليب": "milk", "زبادي": "yogurt", "زبده": "butter",
    "قشطه": "cream", "سمن": "ghee", "بيض": "eggs", "عسل": "honey", "لحم": "meat", "لحوم": "meat",
    "لحمه": "beef meat", "فراخ": "chicken", "دجاج": "chicken", "سمك": "fish", "تونه": "tuna",
    "سردين": "sardines", "جمبري": "shrimps", "سكر": "sugar", "ملح": "salt", "زيت": "oil",
    "زيت زيتون": "olive oil", "زيت عباد": "sunflower oil", "مربي": "jam", "شوكولاته": "chocolate",
    "بسكويت": "biscuits", "حلويات": "confectionery", "مكرونه": "pasta", "خبز": "bread", "عيش": "bread",
    "عصير": "juice", "مياه": "water", "مياه معدنيه": "mineral water", "مشروبات غازيه": "carbonated soft drinks",
    "صلصه": "sauce", "كاتشب": "ketchup", "مخللات": "pickles", "اكل حيوانات": "pet food", "علف": "feed",
    # household / personal care
    "صابون": "soap", "شامبو": "shampoo", "معجون اسنان": "toothpaste", "فرشه اسنان": "toothbrush",
    "منظفات": "detergents", "مسحوق غسيل": "laundry detergent", "مناديل": "tissues", "حفاضات": "nappies diapers",
    "عطر": "perfume", "عطور": "perfume", "مكياج": "make up cosmetics", "كريم": "cream skin care",
    "مزيل عرق": "deodorant", "شفرات حلاقه": "razor blades",
    # clothing & textiles
    "ملابس": "clothing", "هدوم": "clothing", "تيشيرت": "t-shirts", "قميص": "shirts", "بنطلون": "trousers",
    "جينز": "jeans", "فستان": "dresses", "جاكيت": "jackets", "شراب": "socks", "جوارب": "socks",
    "ملابس داخليه": "underwear", "احذيه": "footwear", "حذا": "footwear", "جزمه": "footwear", "شبشب": "slippers",
    "شنطه": "bags", "شنط": "bags", "قماش": "fabric", "اقمشه": "fabric", "قطن": "cotton", "غزل": "yarn",
    "سجاد": "carpets", "مفروشات": "bed linen", "فوط": "towels", "بطاطين": "blankets",
    # electronics & machines
    "موبايل": "mobile phones", "تليفون": "telephones", "لاب توب": "laptop computers", "كمبيوتر": "computers",
    "شاشه": "monitors television", "تلفزيون": "television", "ثلاجه": "refrigerators", "تلاجه": "refrigerators",
    "غساله": "washing machines", "تكييف": "air conditioners", "مكيف": "air conditioners", "مروحه": "fans",
    "خلاط": "blenders", "مكوه": "irons", "سخان": "water heaters", "بوتاجاز": "cookers", "ميكروويف": "microwave",
    "لمبه": "lamps", "لمبات": "lamps", "كابل": "cables", "كابلات": "cables", "بطاريه": "batteries",
    "سماعات": "headphones", "شاحن": "chargers", "كاميرا": "cameras", "طابعه": "printers", "ساعه": "watches",
    # construction, auto, misc
    "حديد": "iron steel", "اسمنت": "cement", "سيراميك": "ceramic tiles", "رخام": "marble", "زجاج": "glass",
    "دهانات": "paints", "بويه": "paints", "خشب": "wood", "بلاستيك": "plastic", "ورق": "paper",
    "كرتون": "cardboard", "عبوات": "containers", "اطارات": "tyres", "كاوتش": "tyres", "زيت موتور": "motor oil",
    "قطع غيار": "parts", "سياره": "cars", "سيارات": "cars", "عجل": "bicycles", "لعب": "toys", "العاب": "toys",
    "ادويه": "medicines", "مكملات غذائيه": "food supplements", "اسمده": "fertilisers", "سماد": "fertilisers",
    "مبيدات": "pesticides", "بذور": "seeds", "شتلات": "plants seedlings", "زهور": "flowers",
    "سجائر": "cigarettes", "معسل": "tobacco", "تبغ": "tobacco",
}


def translate(query: str) -> str | None:
    """Best-effort Arabic -> English for GPC search; None if nothing known."""
    if not is_arabic(query):
        return None
    words = normalize(query).split()
    out: list[str] = []
    i = 0
    while i < len(words):
        hit = None
        for n in (3, 2, 1):
            phrase = " ".join(words[i: i + n])
            if len(words[i: i + n]) == n and phrase in AR_EN:
                hit = (AR_EN[phrase], n)
                break
            if n == 1 and phrase.startswith("ال") and phrase[2:] in AR_EN:
                hit = (AR_EN[phrase[2:]], 1)
        if hit:
            out.append(hit[0])
            i += hit[1]
        else:
            i += 1
    return " ".join(out) if out else None


_GENERIC_EN = {
    "other", "whether", "not", "or", "and", "of", "the", "for", "in", "a", "an", "with", "including",
    "than", "n.e.c", "nec", "heading", "no", "kind", "kinds", "used", "products", "product", "chapter",
    "fresh", "chilled", "frozen", "dried", "shelled", "skinned", "split", "species", "excluding",
    "parts", "thereof", "similar", "articles", "preparations", "neither", "crushed", "ground",
}


def hs_to_gpc_terms(hs_desc: str, limit: int = 4) -> str:
    """Pick the meaningful nouns of an HS description to query GPC."""
    text = hs_desc.split(";", 1)[-1] if ";" in hs_desc else hs_desc
    words = [w for w in re.findall(r"[a-zA-Z]+", text.lower()) if w not in _GENERIC_EN and len(w) > 2]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return " ".join(seen[:limit])
