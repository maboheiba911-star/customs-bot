"""Telegram message texts (HTML parse mode)."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from .calc import CalcResult
from .refdb import TariffItem
from .taxparse import KIND_LABELS, ParsedTaxes, fmt_pct

SEP = "━━━━━━━━━━━━"


def money(x: float | None) -> str:
    if x is None:
        return "؟"
    if abs(x - round(x)) < 0.005:
        return f"{int(round(x)):,}"
    return f"{x:,.2f}"


def short(text: str, n: int = 90) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def local_day(ts: str | None) -> str:
    """UTC timestamp (2026-11-10T22:30:00Z) -> the date on this computer's clock (Egypt on the owner's PC)."""
    if not ts:
        return ""
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return ts[:10]
    return t.astimezone().strftime("%Y-%m-%d")


def source_line(item: TariffItem, snapshot: str | None) -> str:
    if item.source == "live" and item.fetched_at:
        return f"🕒 المصدر: موقع مصلحة الجمارك (مباشر) — {local_day(item.fetched_at)}"
    snap = f" — نسخة {snapshot}" if snapshot else ""
    return f"🕒 المصدر: نسخة محلية من تعريفة مصلحة الجمارك{snap}"


def rates_inline(item: TariffItem) -> str:
    p = item.parsed
    duty = p.get("duty")
    vat = p.get("vat")
    parts = [f"وارد {duty.display()}" if duty else "وارد ؟"]
    parts.append(f"ق.م {vat.display()}" if vat else "ق.م 14%*")
    if p.get("table"):
        parts.append(f"جدول {p.get('table').display()}")
    return " | ".join(parts)


# --------------------------------------------------------------------- welcome
def welcome(name: str, bot_name: str) -> str:
    return (
        f"أهلاً {escape(name)} 👋\n"
        f"أنا <b>{escape(bot_name)}</b> — بحسبلك الجمارك والضرائب على أي بند في التعريفة المصرية.\n\n"
        "اللي أقدر أعمله:\n"
        "🔎 <b>بحث عن بند</b>: ابعت اسم المنتج (عربي أو إنجليزي) أو رقم البند\n"
        "🧮 <b>حساب الجمارك</b>: ابعت رقم البند + قيمة CIF\n"
        "📤 <b>متطلبات التصدير</b>: ابعت <code>تصدير</code> + رقم البند أو اسم المنتج\n"
        "🌍 <b>المنشأ والاتفاقيات</b>: أرخص منشأ للاستيراد، وكارت التصدير لأي بلد\n"
        "🧱 <b>تصنيف GS1</b>: ابعت <code>gs1</code> + اسم المنتج\n\n"
        "جرب كده:\n"
        "<code>فاصوليا بيضاء</code>\n"
        "<code>846595 1522500</code>\n"
        "<code>8703233090 20000$ @50.5</code>\n"
        "<code>تصدير 0909610000</code>\n"
        "<code>منشأ 1905320000</code>\n"
        "<code>تصدير 0713330090 إلى ألمانيا</code>\n"
        "<code>gs1 chamomile</code>\n\n"
        "اكتب /help لكل الأوامر."
    )


def help_text(is_admin: bool) -> str:
    t = (
        "<b>طريقة الاستخدام</b>\n\n"
        "🔎 <b>البحث</b>\n"
        "• اسم منتج: <code>زيت زيتون</code> أو <code>olive oil</code>\n"
        "• رقم بند كامل (10 أرقام): <code>0713330090</code>\n"
        "• جزء من البند لعرض البنود تحته: <code>0713</code>\n\n"
        "🧮 <b>الحساب</b> — رقم البند ثم القيمة CIF:\n"
        "• بالجنيه: <code>0713330090 250000</code> أو <code>0713330090 250k</code>\n"
        "• بعملة تانية: <code>0713330090 5000$</code> (بسعر الصرف المسجل) أو <code>0713330090 5000$ @50.5</code>\n"
        "• بالكمية (للفئات النوعية): <code>2401100010 100000 2000kg</code>\n"
        "• بمنشأ معيّن: <code>1905320000 100000 من المانيا</code> (أي بلد، أو اوروبا/افريقيا/ميركسور)\n"
        "• استيراد شخصي (بدون دفعة الأرباح التجارية): <code>... شخصي</code>\n\n"
        "📤 <b>التصدير</b>\n"
        "• <code>تصدير 0713330090</code> — متطلبات تصدير البند (سلامة الغذاء، الحجر الزراعي، الاتفاقيات)\n"
        "• <code>تصدير ينسون</code> — ابحث بالاسم وبعدين اختار البند\n\n"
        "🌍 <b>المنشأ والاتفاقيات</b> (/origin)\n"
        "• <code>منشأ 1905320000</code> — أرخص منشأ للاستيراد: ترتيب الاتفاقيات بالحساب\n"
        "• <code>استيراد 1905320000 من تركيا</code> — الحساب والشهادة المطلوبة من المورد\n"
        "• <code>تصدير 0713330090 إلى ألمانيا</code> — الاتفاقية، الشهادة، المصدّر المعتمد، قاعدة المنشأ\n"
        "• <code>اتفاقيات السعودية</code> أو <code>اتفاقية الكوميسا</code> (/agreements)\n\n"
        "🧱 <b>GS1 / GPC</b>\n"
        "• <code>gs1 white beans</code> أو <code>gs1 فاصوليا</code>\n"
        "• كود Brick مباشرة: <code>10007820</code>\n\n"
        "💱 /fx — أسعار الصرف (سعر التحويل الجمركي)\n"
        "🪪 /whoami — رقمك على تليجرام\n"
        "ℹ️ /about — مصدر البيانات وتاريخ التحديث\n"
        "🤖 /version — نسخة البوت والتحديث التلقائي\n\n"
        "⚠️ النتائج تقديرية للاسترشاد — الحساب النهائي حسب البيان الجمركي وتقييم الجمارك."
    )
    if is_admin:
        t += (
            "\n\n<b>أوامر الأدمن</b>\n"
            "/stats — إحصائيات الاستخدام\n"
            "/allow 123456789 — تسمح لحد تاني يستخدم البوت (و /deny للإلغاء)\n"
            "/setfx USD 50.5 — سعر صرف يدوي بدل السعر الرسمي (و /setfx USD 0 للرجوع للرسمي)\n"
            "/devfee 8703 3 — رسم تنمية 3% لكل بنود 8703 (و /devfee 8703 0 للإلغاء)\n"
            "/update — تحديث التعريفة كلها من موقع الجمارك\n"
            "/upgrade — تحديث كود البوت دلوقتي (بيحصل لوحده كل نص ساعة)\n"
            "/restart — إعادة تشغيل البوت\n"
            "ابعت ملف GPC (json/xml) أو reference.db كمستند لتحديث البيانات"
        )
    return t


# ------------------------------------------------------------------ details
def item_details(item: TariffItem, *, hs_en: str | None, instructions: list[dict], siblings: list[TariffItem],
                 snapshot: str | None, nafeza_url: str, dev_fee: float | None) -> str:
    p: ParsedTaxes = item.parsed
    lines = [f"📦 <b>البند {item.code}</b>", escape(short(item.desc_ar, 300))]
    if hs_en:
        lines.append(f"🇬🇧 <i>{escape(short(hs_en, 160))}</i>")
    lines.append("")
    lines.append("🧾 <b>الضرائب والرسوم</b>")
    duty = p.get("duty")
    lines.append(f"• {KIND_LABELS['duty']}: <b>{duty.display() if duty else 'غير مسجلة'}</b>")
    if dev_fee:
        lines.append(f"• {KIND_LABELS['dev']}: <b>{fmt_pct(dev_fee)}%</b> (مُعرّف في إعدادات البوت)")
    else:
        lines.append(f"• {KIND_LABELS['dev']}: لا يوجد")
    vat = p.get("vat")
    if vat and (vat.pct is not None or vat.specific is not None):
        lines.append(f"• {KIND_LABELS['vat']}: <b>{vat.display()}</b>")
    elif vat:
        lines.append(f"• {KIND_LABELS['vat']}: <b>14%</b> ({vat.display()})")
    else:
        lines.append(f"• {KIND_LABELS['vat']}: <b>14%</b> (السعر العام)")
    for kind in ("table", "health", "chamber_textile", "chamber_leather", "chamber_tobacco", "chamber_other", "other"):
        comp = p.get(kind)
        if comp:
            note = f" ({escape(comp.note)})" if comp.note else ""
            lines.append(f"• {KIND_LABELS.get(kind, escape(comp.label))}: <b>{comp.display()}</b>{note}")
    if p.agreements:
        lines.append("")
        lines.append("🤝 <b>أسعار الاتفاقيات</b> (بشهادة منشأ)")
        for ag in p.agreements[:8]:
            d = ag.components.get("duty")
            lines.append(f"• {escape(ag.name)}: {d.display() if d else '—'}")
    req = [i for i in instructions if not i["code"].startswith("ر")]       # ر = تخفيضات اتفاقيات
    agr = [i for i in instructions if i["code"].startswith("ر")]
    if req:
        lines.append("")
        lines.append("📋 <b>اشتراطات الاستيراد</b>")
        for ins in req[:4]:
            lines.append(f"• {escape(short(ins['text'], 140))}")
        if len(req) > 4:
            lines.append(f"  … و{len(req) - 4} تعليمات تانية (زرار «اشتراطات الاستيراد»)")
    if agr:
        lines.append(f"🤝 تخفيضات اتفاقيات في التعليمات: {len(agr)} (زرار «اشتراطات الاستيراد»)")
    if item.export_codes:
        lines.append(f"📤 متطلبات تصدير البند: {len(item.export_codes)} (زرار «متطلبات التصدير»)")
    if siblings:
        lines.append("")
        lines.append("🔍 <b>بنود مشابهة</b>")
        for s in siblings[:3]:
            lines.append(f"• <code>{s.code}</code> {escape(short(s.short_ar, 55))} | {rates_inline(s)}")
    lines.append("")
    lines.append(source_line(item, snapshot))
    lines.append(f"🔗 التأكد من نافذة: {escape(nafeza_url)}")
    lines.append("")
    lines.append("💡 عشان تحسبها ابعت:")
    lines.append(f"<code>{item.code} 100000</code>")
    return "\n".join(lines)


LEGEND = "<i>ق.م = ض. القيمة المضافة — (*) السعر العام 14% لعدم وجود سعر خاص</i>"


def parent_details(item: TariffItem, children: list[TariffItem]) -> str:
    desc = item.desc_ar if len(item.desc_ar.strip(" .")) > 1 else "(بدون وصف في المصدر)"
    lines = [f"📂 <b>البند {item.code}</b> — عنوان فرعي بدون فئة مستقلة", escape(short(desc, 200)), ""]
    if children:
        lines.append("اختار البند التفصيلي:")
        for c in children[:12]:
            lines.append(f"• <code>{c.code}</code> {escape(short(c.short_ar, 60))} | {rates_inline(c)}")
        lines.append(LEGEND)
    else:
        lines.append("مفيش بنود تفصيلية تحته في البيانات المحلية — راجع نافذة.")
    return "\n".join(lines)


def instructions_text(item: TariffItem, instructions: list[dict]) -> str:
    if not instructions:
        hint = "" if item.source == "live" else " في النسخة المحلية — بتظهر لما البوت يوصل لموقع الجمارك"
        return f"📋 البند {item.code}: مفيش اشتراطات استيراد مسجلة{hint}."
    lines = [f"📋 <b>اشتراطات وتعليمات الاستيراد — البند {item.code}</b>"]
    for ins in instructions:
        icon = "🤝" if ins["code"].startswith("ر") else "📥"
        lines.append(f"{icon} <b>{escape(ins['code'])}</b> — {escape(short(ins['text'], 300))}")
    return "\n".join(lines)


def export_text(item: TariffItem, instructions: list[dict], source: str, hs_en: str | None) -> str:
    lines = [f"📤 <b>متطلبات التصدير — البند {item.code}</b>", escape(short(item.desc_ar, 160))]
    if hs_en:
        lines.append(f"🇬🇧 <i>{escape(short(hs_en, 140))}</i>")
    lines.append("")
    if not instructions:
        lines.append("مفيش تعليمات تصدير مسجلة للبند ده على منظومة الجمارك.")
    else:
        lines.append("اللي مسجل على البند عند التصدير:")
        for ins in instructions:
            lines.append(f"• <b>{escape(ins['code'])}</b> — {escape(short(ins['text'], 300))}")
    lines.append("")
    lines.append(f"🕒 المصدر: {escape(source)}")
    lines.append("ℹ️ ده اللي على منظومة الجمارك المصرية بس — متطلبات بلد الاستيراد (شهادات، تحاليل، ملصقات) منفصلة.")
    return "\n".join(lines)


def prefix_list(prefix: str, items: list[TariffItem], total: int, offset: int, hs_en: str | None) -> str:
    lines = [f"📂 <b>بنود تبدأ بـ {prefix}</b> — {total} بند"]
    if hs_en:
        lines.append(f"🇬🇧 <i>{escape(short(hs_en, 140))}</i>")
    lines.append("")
    for i, it in enumerate(items, start=offset + 1):
        lines.append(f"{i}) <code>{it.code}</code> {escape(short(it.short_ar, 70))}\n    {rates_inline(it)}")
    lines.append(LEGEND)
    return "\n".join(lines)


def search_results(query: str, items: list[TariffItem], total: int, offset: int, hint: str | None) -> str:
    if not items:
        t = f"🔎 مفيش نتايج لـ «{escape(query)}»."
        if hint:
            t += f"\n💡 {escape(hint)}"
        t += "\nجرب كلمة تانية أو الاسم بالإنجليزي، أو ابعت أول 4 أرقام من البند."
        return t
    lines = [f"🔎 نتائج «{escape(query)}» — {total} بند"]
    if hint:
        lines.append(f"💡 {escape(hint)}")
    lines.append("")
    for i, it in enumerate(items, start=offset + 1):
        lines.append(f"{i}) <code>{it.code}</code> {escape(short(it.short_ar, 75))}\n    {rates_inline(it)}")
    lines.append(LEGEND)
    lines.append("")
    lines.append("اضغط رقم البند للتفاصيل، أو ابعت: <code>البند القيمة</code> للحساب.")
    return "\n".join(lines)


# --------------------------------------------------------------------- calc
def calc_text(item: TariffItem, res: CalcResult, *, currency_note: str | None, snapshot: str | None) -> str:
    lines = [f"🧮 <b>حساب جمارك — البند {item.code}</b>", escape(short(item.desc_ar, 120))]
    lines.append(f"💰 قيمة CIF: <b>{money(res.value)} ج</b>" + (f"  ({escape(currency_note)})" if currency_note else ""))
    lines.append("")
    for l in res.lines:
        amt = f"{money(l.amount)} ج" if l.amount is not None else "محتاج الكمية"
        lines.append(f"• {escape(l.label)} {escape(l.rate_text)} = <b>{amt}</b>")
        if l.key in ("vat", "table") and l.base is not None and abs(l.base - res.value) > 0.005:
            what = "القيمة + الوارد + الرسوم" if l.key == "vat" else "القيمة + الوارد"
            lines.append(f"   ↳ على {money(l.base)} ({what})")
        if l.note:
            lines.append(f"   ↳ {escape(l.note)}")
    lines.append(SEP)
    total_label = "إجمالي الضرائب والرسوم" + (" (ناقص بنود تحتاج كمية)" if res.incomplete else "")
    lines.append(f"<b>{total_label}: {money(res.total)} ج</b>")
    lines.append(f"<b>الإجمالي + البضاعة: {money(res.grand_total)} ج</b>")
    lines.append(f"📊 العبء الضريبي: {res.effective_pct:.1f}% من القيمة")
    for w in res.warnings:
        lines.append(f"⚠️ {escape(w)}")
    lines.append("")
    lines.append(source_line(item, snapshot))
    lines.append("ℹ️ تقديري للاسترشاد — النهائي حسب البيان الجمركي وتقييم الجمارك.")
    return "\n".join(lines)


def choose_line_text(prefix: str, items: list[TariffItem], value: float) -> str:
    lines = [f"البند <code>{prefix}</code> تحته أكتر من بند تفصيلي — اختار اللي هتحسب عليه ({money(value)} ج):", ""]
    for i, it in enumerate(items, start=1):
        lines.append(f"{i}) <code>{it.code}</code> {escape(short(it.short_ar, 70))}\n    {rates_inline(it)}")
    lines.append(LEGEND)
    return "\n".join(lines)


# ---------------------------------------------------------------------- GPC
def gpc_brick(b: dict, *, with_definition: bool = False) -> str:
    lines = [
        f"🧱 <b>GS1 Brick:</b> {escape(b['title'])}",
        f"🔢 Brick Code: <code>{b['code']}</code>",
        f"📁 Segment: {escape(b['segment_title'])}",
        f"📁 Family: {escape(b['family_title'])}",
        f"📄 Class: {escape(b['class_title'])}",
    ]
    if b.get("title_ar"):
        lines.insert(1, f"🇪🇬 {escape(b['title_ar'])}")
    if with_definition and b.get("definition"):
        lines.append(f"📝 {escape(short(b['definition'], 350))}")
        if b.get("excludes"):
            lines.append(f"🚫 {escape(short(b['excludes'], 250))}")
    return "\n".join(lines)


def gpc_results(query: str, translated: str | None, bricks: list[dict], total: int, offset: int,
                version: str | None) -> str:
    if not bricks:
        t = f"🧱 مفيش نتايج GS1 لـ «{escape(query)}»."
        t += "\nجرب الاسم بالإنجليزي (مثال: <code>gs1 dried beans</code>)."
        return t
    head = f"🧱 نتائج GS1 لـ «{escape(query)}» — {total} Brick"
    if translated and translated.strip().lower() != query.strip().lower():
        head += f"\n🔄 بحثت بـ: <i>{escape(translated)}</i>"
    parts = [head]
    for i, b in enumerate(bricks, start=offset + 1):
        parts.append(f"{i}) " + gpc_brick(b))
    if version:
        parts.append(f"📚 {escape(version)} — للتأكيد: gpc-browser.gs1.org")
    return "\n\n".join(parts)


def about_text(meta: dict, counts: dict, live_ok: bool) -> str:
    return (
        "<b>مصادر البيانات</b>\n"
        f"• التعريفة: {escape(meta.get('tariff_source', '?'))}\n"
        f"  تاريخ النسخة: {escape(meta.get('tariff_snapshot', '?'))} — {counts.get('tariff', 0):,} بند\n"
        f"  الاستعلام المباشر من موقع الجمارك: {'شغال ✅' if live_ok else 'متوقف مؤقتاً ⏸'}\n"
        f"• أوصاف HS بالإنجليزي: {escape(meta.get('hs_source', '?'))}\n"
        f"• GS1 GPC: {escape(meta.get('gpc_version', '?'))} — {counts.get('gpc_brick', 0):,} Brick\n\n"
        "⚠️ البوت أداة استرشادية — الحساب النهائي حسب البيان الجمركي وتقييم الجمارك ونافذة."
    )
