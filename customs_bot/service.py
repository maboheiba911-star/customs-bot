"""Bot logic independent of Telegram plumbing: every function returns a Reply."""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup

from . import agreements as agr
from . import formatters as fmt
from . import origin as origin_mod
from .appdb import AppDB
from html import escape as html_escape

from .arabic import classification_hint, is_arabic
from .builder import parse_instructions
from .calc import calculate
from .fx import fetch_cbe
from .glossary import hs_to_gpc_terms, translate
from .official import EXPORT, HEADERS, IMPORT, LiveLookup
from .refdb import ReferenceDB, TariffItem
from .router import Intent
from .settings import Settings

PAGE = 6
GPC_PAGE = 4

# Official customs conversion rates: customs.gov.eg/Services/Currency (server-rendered, ~10 per page)
#   <span class="h5">دولار امريكـــي</span> <span class="badge bg-secondary">USD</span> </td> <td> 53.39 </td>
_CUSTOMS_FX_RE = re.compile(
    r'<span class="badge[^"]*">\s*([A-Z]{3})\s*</span>\s*</td>\s*<td>\s*([\d.,]+)\s*</td>')


def parse_customs_rates(html: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for code, val in _CUSTOMS_FX_RE.findall(html or ""):
        if code != "EGP":
            try:
                out[code] = float(val.replace(",", ""))
            except ValueError:
                pass
    return out


async def fetch_customs_rates(base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, float]:
    rates: dict[str, float] = {}
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True,
                                 transport=transport) as client:
        for page in range(1, 6):
            r = await client.get(f"{base_url.rstrip('/')}/Services/Currency", params={"page": page})
            r.raise_for_status()
            new = {k: v for k, v in parse_customs_rates(r.text).items() if k not in rates}
            if not new:
                break
            rates.update(new)
    if "USD" not in rates:
        raise ValueError("customs currency page layout changed (no USD)")
    return rates


_local_day = fmt.local_day          # UTC stamp -> local date (Egypt on the owner's PC)


def _hours_since(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


@dataclass
class Reply:
    text: str
    keyboard: InlineKeyboardMarkup | None = None
    state: dict = field(default_factory=dict)      # updates for context.user_data


class CustomsService:
    def __init__(self, settings: Settings, ref: ReferenceDB, app: AppDB, live: LiveLookup | None):
        self.s = settings
        self.ref = ref
        self.app = app
        self.live = live
        self._fx_failed_at: dict[str, float] = {}

    # ------------------------------------------------------------ access control
    def owner_id(self) -> int | None:
        v = self.app.kv_get("owner_id")
        return int(v) if v and v.isdigit() else None

    def admins(self) -> set[int]:
        owner = self.owner_id()
        return set(self.s.admin_ids) | ({owner} if owner else set())

    def allowed(self) -> set[int]:
        extra = {int(x) for x in (self.app.kv_get("allowed_ids") or "").split(",") if x.isdigit()}
        return self.admins() | set(self.s.allowed_ids) | extra

    def set_allowed(self, user_id: int, allow: bool) -> None:
        ids = {int(x) for x in (self.app.kv_get("allowed_ids") or "").split(",") if x.isdigit()}
        (ids.add if allow else ids.discard)(user_id)
        self.app.kv_set("allowed_ids", ",".join(str(i) for i in sorted(ids)))

    # ---------------------------------------------------------------- helpers
    @property
    def snapshot(self) -> str | None:
        return self.ref.meta().get("tariff_snapshot")

    def dev_fee_for(self, code: str) -> float | None:
        fees = dict(self.s.dev_fees)
        fees.update(self.app.dev_fees())
        for n in range(len(code), 1, -1):
            if code[:n] in fees:
                return fees[code[:n]] or None
        return None

    async def _live(self, code: str, trf_type: int) -> tuple[dict, str] | None:
        if self.live is None or not self.s.live_lookup:
            return None
        try:
            return await asyncio.wait_for(self.live.detail(code, trf_type), timeout=self.live.timeout + 2)
        except asyncio.TimeoutError:
            return None

    async def item(self, code: str, *, live: bool = True) -> tuple[TariffItem | None, list[dict]]:
        """Tariff line (official import view) + its import requirements."""
        local = self.ref.get(code)
        got = await self._live(code, IMPORT) if live else None
        if got:
            rec, ts = got
            instr = parse_instructions(rec.get("Instructions"), rec.get("InstructionCodes"), "import")
            desc = (rec.get("Desc") or rec.get("ShortDesc") or (local.desc_ar if local else "")).strip()
            it = TariffItem(
                code=code, desc_ar=desc, short_ar=(rec.get("ShortDesc") or desc).strip(),
                taxes=[t for t in rec.get("Taxes") or [] if isinstance(t, str)],
                instr_codes=[i["code"] for i in instr], source="live", fetched_at=ts,
                export_codes=local.export_codes if local else [],
            )
            if it.taxes or local is None:
                return it, instr
        if local is None:
            return None, []
        return local, self.ref.instructions(local.instr_codes)

    async def export_requirements(self, code: str) -> tuple[TariffItem | None, list[dict], str]:
        """Export-view requirements for a line: live from the site, else the local copy."""
        local = self.ref.get(code)
        got = await self._live(code, EXPORT)
        if got:
            rec, ts = got
            return local, parse_instructions(rec.get("Instructions"), rec.get("InstructionCodes"), "export"), \
                f"موقع مصلحة الجمارك (مباشر) — {_local_day(ts)}"
        if local is None:
            return None, [], ""
        return local, self.ref.instructions(local.export_codes), f"نسخة محلية — {self.snapshot or ''}"

    def _nav(self, prefix: str, offset: int, total: int, size: int) -> list[Btn]:
        nav = []
        if offset > 0:
            nav.append(Btn("◀️ السابق", callback_data=f"{prefix}|{max(0, offset - size)}"))
        if offset + size < total:
            nav.append(Btn("التالي ▶️", callback_data=f"{prefix}|{offset + size}"))
        return nav

    @staticmethod
    def _code_buttons(items: list[TariffItem], start: int, action: str = "d") -> list[list[Btn]]:
        row, rows = [], []
        for i, it in enumerate(items, start=start):
            row.append(Btn(f"{i}) {it.code}", callback_data=f"{action}|{it.code}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows

    # ---------------------------------------------------------------- tariff
    async def details(self, code: str) -> Reply:
        code = re.sub(r"\D", "", code)
        if len(code) < 10:
            return await self.prefix(code, 0)
        item, instr = await self.item(code)
        if item is None:
            return Reply(f"❌ البند <code>{code}</code> مش موجود في التعريفة. تأكد من الرقم أو ابحث بالاسم.")
        if not item.taxes and self.ref.is_parent(code):
            kids = self.ref.children(code)
            kb = InlineKeyboardMarkup(self._code_buttons(kids[:8], 1)) if kids else None
            return Reply(fmt.parent_details(item, kids), kb)
        text = fmt.item_details(
            item, hs_en=self.ref.hs_desc(code), instructions=instr, siblings=self.ref.siblings(code, 3),
            snapshot=self.snapshot, nafeza_url=self.s.nafeza_url, dev_fee=self.dev_fee_for(code),
        )
        kb = InlineKeyboardMarkup([
            [Btn("🧮 احسب الجمارك", callback_data=f"k|{code}"),
             Btn("🌍 المنشأ والاتفاقيات", callback_data=f"od|{code}")],
            [Btn("📤 متطلبات التصدير", callback_data=f"x|{code}"), Btn("📋 اشتراطات الاستيراد", callback_data=f"i|{code}")],
            [Btn("🧱 GS1 مقترح", callback_data=f"g|{code}")],
        ])
        return Reply(text, kb, state={"last_code": code})

    async def instructions(self, code: str) -> Reply:
        item, instr = await self.item(code)
        if item is None:
            return Reply("❌ البند مش موجود.")
        return Reply(fmt.instructions_text(item, instr))

    async def export_view(self, code: str) -> Reply:
        code = re.sub(r"\D", "", code)
        if len(code) != 10:
            return Reply("ابعت رقم البند كامل (10 أرقام) بعد كلمة تصدير — مثال: <code>تصدير 0713330090</code>")
        item, instr, source = await self.export_requirements(code)
        if item is None:
            return Reply(f"❌ البند <code>{code}</code> مش موجود في التعريفة.")
        kb = InlineKeyboardMarkup([[Btn("🌐 تصدير لبلد معيّن", callback_data=f"oe|{code}")],
                                   [Btn("📦 تفاصيل البند", callback_data=f"d|{code}"),
                                    Btn("🧱 GS1 مقترح", callback_data=f"g|{code}")]])
        return Reply(fmt.export_text(item, instr, source, self.ref.hs_desc(code)), kb)

    async def prefix(self, prefix: str, offset: int) -> Reply:
        if len(prefix) < 2:
            return Reply("ابعت على الأقل أول رقمين من البند (الفصل)، أو 4 أرقام (البند الرئيسي).")
        items, total = self.ref.by_prefix(prefix, PAGE, offset)
        if not items:
            return Reply(f"❌ مفيش بنود بتبدأ بـ <code>{prefix}</code>.")
        if total == 1:
            return await self.details(items[0].code)
        rows = self._code_buttons(items, offset + 1)
        nav = self._nav(f"p|{prefix}", offset, total, PAGE)
        if nav:
            rows.append(nav)
        text = fmt.prefix_list(prefix, items, total, offset, self.ref.hs_desc(prefix) if len(prefix) >= 4 else None)
        return Reply(text, InlineKeyboardMarkup(rows))

    def search(self, query: str, offset: int = 0, *, export: bool = False) -> Reply:
        items, total = self.ref.search(query, PAGE, offset)
        hint = None
        h = classification_hint(query)
        if h:
            hint = h[1]
            if total < 3:
                extra, n = self.ref.by_prefix(h[0], PAGE, 0)
                seen = {i.code for i in items}
                items = items + [e for e in extra if e.code not in seen]
                total = len(items)
        if not items:
            return Reply(fmt.search_results(query, [], 0, 0, hint), state={"last_query": query})
        rows = self._code_buttons(items, offset + 1, action="x" if export else "d")
        nav = self._nav("sx" if export else "s", offset, total, PAGE)
        if nav:
            rows.append(nav)
        text = fmt.search_results(query, items, total, offset, hint)
        if export:
            text = text.replace("اضغط رقم البند للتفاصيل", "اضغط رقم البند لمتطلبات التصدير")
        return Reply(text, InlineKeyboardMarkup(rows), state={"last_query": query})

    # ------------------------------------------------------------------ calc
    def _resolve_calc_code(self, code: str) -> tuple[str | None, list[TariffItem]]:
        if len(code) == 10 and self.ref.get(code) and not self.ref.is_parent(code):
            return code, []
        items, total = self.ref.by_prefix(code, 20, 0)
        items = [i for i in items if not self.ref.is_parent(i.code)]
        if len(items) == 1:
            return items[0].code, []
        if not items and len(code) == 10 and self.live is not None and self.live.available:
            return code, []          # maybe a line added after the local snapshot: try the official site
        return None, items

    async def calculate(self, intent: Intent) -> Reply:
        value = intent.value or 0.0
        if value <= 0:
            return Reply("ابعت قيمة أكبر من صفر.")
        currency_note = None
        if intent.currency and intent.currency != "EGP":
            rate, when = (intent.fx, "سعر مكتوب في الرسالة") if intent.fx else await self.rate_for(intent.currency)
            if not rate:
                return Reply(
                    f"💱 مش قادر أجيب سعر {intent.currency} دلوقتي (موقع الجمارك والبنك المركزي مش متاحين).\n"
                    f"ابعتها بالسعر كده: <code>{intent.code or 'البند'} {value:g}{'$' if intent.currency == 'USD' else ' ' + intent.currency} @50.5</code>\n"
                    "أو سجّل سعر ثابت: <code>/setfx USD 50.5</code>"
                )
            currency_note = f"{fmt.money(value)} {intent.currency} × {rate:g} — {when}"
            value = value * rate
        code, options = self._resolve_calc_code(intent.code)
        if code is None:
            if not options:
                return Reply(f"❌ البند <code>{intent.code}</code> مش موجود في التعريفة.")
            rows = self._code_buttons(options[:8], 1, action="cv")
            return Reply(fmt.choose_line_text(intent.code, options[:8], value), InlineKeyboardMarkup(rows),
                         state={"pending_calc": {"value": value, "currency_note": currency_note,
                                                 "qty": intent.qty, "origin": intent.origin,
                                                 "personal": intent.personal, "country": intent.country}})
        return await self.calculate_code(code, value, currency_note=currency_note, qty=intent.qty,
                                         origin=intent.origin, personal=intent.personal, country=intent.country)

    # agreement words in a calculation -> the country / bloc the origin module ranks
    ORIGIN_COUNTRY = {"eu": "EU", "uk": "GB", "tr": "TR", "efta": "EFTA", "mercosur": "MERCOSUR"}

    async def calculate_code(self, code: str, value: float, *, currency_note: str | None = None,
                             qty: dict | None = None, origin: str | None = None, personal: bool = False,
                             country: str | None = None) -> Reply:
        item, instr = await self.item(code)
        if item is None:
            return Reply(f"❌ البند <code>{code}</code> مش موجود.")
        iso = country or self.ORIGIN_COUNTRY.get(origin or "")
        if iso:
            an = origin_mod.price(origin_mod.analyze(item.parsed, instr, live=item.source == "live"), item.parsed,
                                  value, code=code, settings=self.s.calc, qty=qty, dev_fee=self.dev_fee_for(code),
                                  personal=personal)
            best = an.best_for(iso)
            if best is not None and best.res is not None:
                res = best.res
                res.warnings.insert(0, origin_mod.cert_note(best))
                if best.status == "rule":
                    res.warnings.insert(1, "الإعفاء ده قاعدة عامة للاتفاقية — يتأكد للبند مع المستخلص.")
                if best.quota:
                    res.warnings.append("التخفيض في حدود حصة سنوية — بعد الحصة بالسعر العام.")
            else:
                res = an.general_res
                res.warnings.append(origin_mod.no_preference_reason(an, iso))
            if origin_mod.measure_lines(an, iso):
                res.warnings.append(f"فيه إجراءات خاصة على منشأ {agr.country_name(iso)} — "
                                    "شوف زرار «🌍 أفضل منشأ».")
        else:
            origin_key = origin
            if origin == "afcfta":
                ag = next((a for a in item.parsed.agreements if a.key.startswith("afcfta")), None)
                origin_key = ag.key if ag else "afcfta_b"
            res = calculate(item.parsed, value, code=code, settings=self.s.calc, qty=qty, origin=origin_key,
                            dev_fee_pct=self.dev_fee_for(code), personal=personal)
        text = fmt.calc_text(item, res, currency_note=currency_note, snapshot=self.snapshot)
        kb = InlineKeyboardMarkup([[Btn("📦 تفاصيل البند", callback_data=f"d|{code}"),
                                    Btn("🔁 قيمة تانية", callback_data=f"k|{code}")],
                                   [Btn("🌍 أفضل منشأ بنفس القيمة", callback_data=f"oi|{code}|{int(round(value))}")]])
        return Reply(text, kb, state={"pending_code": code})

    # ------------------------------------------------------------ origin & agreements
    IMPORT_QUICK = ("CN", "TR", "EU", "IN", "SA", "AE", "US", "GB", "BR")
    EXPORT_QUICK = ("SA", "AE", "EU", "GB", "TR", "SD", "LY", "US", "CN")

    async def _value_egp(self, intent: Intent) -> tuple[float | None, str | None, Reply | None]:
        """The value of an origin request in EGP (converted when sent in another currency)."""
        if intent.value is None:
            return None, None, None
        if intent.value <= 0:
            return None, None, Reply("ابعت قيمة أكبر من صفر.")
        if intent.currency and intent.currency != "EGP":
            rate, when = (intent.fx, "سعر مكتوب في الرسالة") if intent.fx else await self.rate_for(intent.currency)
            if not rate:
                return None, None, Reply(f"💱 مش قادر أجيب سعر {intent.currency} دلوقتي — اكتب السعر في الرسالة، "
                                         f"مثال: <code>منشأ {intent.code or 'البند'} 5000$ @50.5</code>")
            return intent.value * rate, f"{fmt.money(intent.value)} {intent.currency} × {rate:g} — {when}", None
        return intent.value, None, None

    async def origin_intent(self, intent: Intent) -> Reply:
        """One-line origin commands: منشأ / استيراد ... من / تصدير ... إلى / اتفاقيات."""
        if intent.kind == "agreement":
            return self.agreements_reply(intent.query)
        if intent.kind == "export_to":
            if intent.code:
                return await self.export_to(intent.code, intent.country)
            return self.origin_search(intent.query, "e", intent.country)
        value, note, err = await self._value_egp(intent)
        if err is not None:
            return err
        if intent.kind == "import_from":
            if intent.code:
                return await self.import_from(intent.code, intent.country, value, currency_note=note,
                                              qty=intent.qty, personal=intent.personal)
            return self.origin_search(intent.query, "i", intent.country)
        if intent.code:
            return await self.best_origin(intent.code, value, currency_note=note, qty=intent.qty,
                                          personal=intent.personal)
        if intent.query:
            return self.origin_search(intent.query, "i")
        return self.origin_menu()

    def origin_menu(self, code: str | None = None) -> Reply:
        if code:
            item = self.ref.get(code)
            kb = [[Btn("📥 استيراد لمصر", callback_data=f"oi|{code}"), Btn("📤 تصدير من مصر", callback_data=f"oe|{code}")],
                  [Btn("📚 الاتفاقيات", callback_data="ol")]]
            return Reply(origin_mod.menu_text(code, item.desc_ar if item else None), InlineKeyboardMarkup(kb),
                         state={"origin": None})
        kb = [[Btn("📥 استيراد لمصر", callback_data="oo|i"), Btn("📤 تصدير من مصر", callback_data="oo|e")],
              [Btn("📚 مكتبة الاتفاقيات", callback_data="ol")]]
        return Reply(origin_mod.menu_text(), InlineKeyboardMarkup(kb), state={"origin": None})

    def ask_product(self, direction: str) -> Reply:
        what = "هتستورده" if direction == "i" else "هتصدّره"
        return Reply(f"{'📥' if direction == 'i' else '📤'} ابعت رقم البند أو اسم المنتج اللي {what}.\n"
                     "مثال: <code>1905320000</code> أو <code>بسكويت</code>",
                     state={"origin": {"dir": direction, "await": "code"}})

    def ask_country(self, direction: str, code: str, value: float | None = None) -> Reply:
        code = re.sub(r"\D", "", code)
        v = f"|{int(round(value))}" if value else ""
        quick = self.IMPORT_QUICK if direction == "i" else self.EXPORT_QUICK
        action = "oc" if direction == "i" else "ox"
        buttons = [Btn(f"{agr.country_flag(c)} {agr.country_name(c)}",
                       callback_data=f"{action}|{code}|{c}{v if direction == 'i' else ''}") for c in quick]
        rows = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
        q = (f"🌐 هتستورد البند <code>{code}</code> من أنهي بلد؟" if direction == "i"
             else f"🌐 هتصدّر البند <code>{code}</code> لأنهي بلد؟")
        return Reply(q + "\nاختار من الزراير أو اكتب اسم البلد (مثال: ألمانيا، كينيا، اليابان).",
                     InlineKeyboardMarkup(rows),
                     state={"origin": {"dir": direction, "code": code, "value": value, "await": "country"}})

    def country_not_found(self, text: str, st: dict) -> Reply:
        r = self.ask_country(st.get("dir", "i"), st.get("code", ""), st.get("value"))
        r.text = f"مش لاقي بلد اسمها «{html_escape(text[:40])}».\n" + r.text
        return r

    def _pick_line(self, code: str, action: str, suffix: str = "") -> tuple[str | None, Reply | None]:
        """A full tariff line, or a reply asking to choose one under a heading."""
        code = re.sub(r"\D", "", code or "")
        if len(code) < 2:
            return None, Reply("ابعت رقم البند (10 أرقام) أو أوله على الأقل.")
        line, options = self._resolve_calc_code(code)
        if line is not None:
            return line, None
        if not options:
            return None, Reply(f"❌ البند <code>{code}</code> مش موجود في التعريفة.")
        rows, row = [], []
        for i, it in enumerate(options[:8], 1):
            row.append(Btn(f"{i}) {it.code}", callback_data=f"{action}|{it.code}{suffix}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        lines = [f"البند <code>{code}</code> تحته أكتر من بند تفصيلي — اختار البند:", ""]
        for i, it in enumerate(options[:8], 1):
            lines.append(f"{i}) <code>{it.code}</code> {html_escape(fmt.short(it.short_ar, 70))}\n    {fmt.rates_inline(it)}")
        return None, Reply("\n".join(lines), InlineKeyboardMarkup(rows))

    async def _analysis(self, line: str, value: float | None, qty: dict | None, personal: bool):
        item, instr = await self.item(line)
        if item is None:
            return None, None
        an = origin_mod.analyze(item.parsed, instr, live=item.source == "live")
        origin_mod.price(an, item.parsed, value or origin_mod.DEFAULT_VALUE, code=line, settings=self.s.calc,
                         qty=qty, dev_fee=self.dev_fee_for(line), personal=personal)
        return item, an

    async def best_origin(self, code: str, value: float | None = None, *, currency_note: str | None = None,
                          qty: dict | None = None, personal: bool = False) -> Reply:
        v = f"|{int(round(value))}" if value else ""
        line, ask = self._pick_line(code, "oi", v)
        if ask is not None:
            return ask
        item, an = await self._analysis(line, value, qty, personal)
        if item is None:
            return Reply(f"❌ البند <code>{line}</code> مش موجود.")
        text = origin_mod.rank_text(item, an, source=fmt.source_line(item, self.snapshot), currency_note=currency_note)
        targets = [Btn(lbl, callback_data=f"oc|{line}|{iso}{v}") for lbl, iso in origin_mod.rank_targets(an)]
        rows = [targets[i: i + 3] for i in range(0, len(targets), 3)]
        rows.append([Btn("🌐 من بلد معيّن", callback_data=f"oq|i|{line}{v}"),
                     Btn("📤 تصدير البند", callback_data=f"oe|{line}")])
        rows.append([Btn("📦 تفاصيل البند", callback_data=f"d|{line}"), Btn("📚 الاتفاقيات", callback_data="ol")])
        return Reply(text, InlineKeyboardMarkup(rows),
                     state={"origin": {"dir": "i", "code": line, "value": value, "qty": qty, "personal": personal},
                            "last_code": line})

    async def import_from(self, code: str, iso: str, value: float | None = None, *,
                          currency_note: str | None = None, qty: dict | None = None, personal: bool = False) -> Reply:
        if iso not in agr.COUNTRIES and iso not in agr.BLOCS:
            return Reply("مش عارف البلد دي — اكتب اسمها بالعربي.")
        v = f"|{int(round(value))}" if value else ""
        line, ask = self._pick_line(code, "oc", f"|{iso}{v}")
        if ask is not None:
            return ask
        item, an = await self._analysis(line, value, qty, personal)
        if item is None:
            return Reply(f"❌ البند <code>{line}</code> مش موجود.")
        text = origin_mod.import_card(item, an, iso, source=fmt.source_line(item, self.snapshot),
                                      currency_note=currency_note)
        rows = [[Btn("🏆 أفضل منشأ", callback_data=f"oi|{line}{v}"), Btn("🌐 بلد تاني", callback_data=f"oq|i|{line}{v}")],
                [Btn("📦 تفاصيل البند", callback_data=f"d|{line}")]]
        return Reply(text, InlineKeyboardMarkup(rows),
                     state={"origin": {"dir": "i", "code": line, "value": value, "qty": qty, "personal": personal},
                            "last_code": line})

    async def export_to(self, code: str, iso: str) -> Reply:
        if iso not in agr.COUNTRIES and iso not in agr.BLOCS:
            return Reply("مش عارف البلد دي — اكتب اسمها بالعربي.")
        line, ask = self._pick_line(code, "ox", f"|{iso}")
        if ask is not None:
            return ask
        item, instr, source = await self.export_requirements(line)
        if item is None:
            item, _ = await self.item(line)
        if item is None:
            return Reply(f"❌ البند <code>{line}</code> مش موجود في التعريفة.")
        text = origin_mod.export_card(item, instr, source or fmt.source_line(item, self.snapshot), iso)
        rows = [[Btn("🌐 بلد تاني", callback_data=f"oq|e|{line}"), Btn("📥 الاستيراد", callback_data=f"oi|{line}")]]
        ags = agr.agreements_for(iso, direction="export")
        last = [Btn("📦 تفاصيل البند", callback_data=f"d|{line}")]
        if ags:
            last.append(Btn(f"📚 {origin_mod.plain_agreement(ags[0].key)}", callback_data=f"oa|{ags[0].key}"))
        rows.append(last)
        return Reply(text, InlineKeyboardMarkup(rows),
                     state={"origin": {"dir": "e", "code": line}, "last_code": line})

    def origin_search(self, query: str, direction: str, iso: str | None = None) -> Reply:
        items, total = self.ref.search(query, PAGE, 0)
        h = classification_hint(query)
        hint = h[1] if h else None
        if h and total < 3:
            extra, _ = self.ref.by_prefix(h[0], PAGE, 0)
            seen = {i.code for i in items}
            items = items + [e for e in extra if e.code not in seen]
            total = len(items)
        state = {"origin": {"dir": direction, "await": "code"} if not items else None}
        if not items:
            return Reply(fmt.search_results(query, [], 0, 0, hint), state=state)
        if iso:
            action, suffix = ("oc" if direction == "i" else "ox"), f"|{iso}"
        else:
            action, suffix = ("oi" if direction == "i" else "oe"), ""
        rows, row = [], []
        for i, it in enumerate(items[:PAGE], 1):
            row.append(Btn(f"{i}) {it.code}", callback_data=f"{action}|{it.code}{suffix}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        text = fmt.search_results(query, items[:PAGE], total, 0, hint)
        where = ""
        if iso:
            where = (f" من {agr.country_name(iso)}" if direction == "i" else f" إلى {agr.country_name(iso)}")
        doing = "الاستيراد" if direction == "i" else "التصدير"
        text = text.replace("اضغط رقم البند للتفاصيل، أو ابعت: <code>البند القيمة</code> للحساب.",
                            f"اضغط رقم البند علشان أكمّل {doing}{html_escape(where)}.")
        return Reply(text, InlineKeyboardMarkup(rows), state=state)

    def agreements_reply(self, query: str = "") -> Reply:
        q = (query or "").strip()
        bloc_key = {"EU": "eu", "EFTA": "efta", "MERCOSUR": "mercosur"}
        if q:
            iso = agr.resolve_country(q)
            if iso in bloc_key:
                return self.agreement_card(bloc_key[iso])
            if iso:
                return Reply(origin_mod.country_text(iso))
            ag = agr.resolve_agreement(q)
            if ag is not None:
                return self.agreement_card(ag.key)
        rows, row = [], []
        for key in origin_mod.LIBRARY:
            row.append(Btn(agr.short_name(key), callback_data=f"oa|{key}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        text = origin_mod.library_text()
        if q:
            text = f"مش عارف «{html_escape(q[:40])}» — دي الاتفاقيات:\n\n" + text
        return Reply(text, InlineKeyboardMarkup(rows))

    def agreement_card(self, key: str) -> Reply:
        if key not in agr.AGREEMENTS:
            return self.agreements_reply()
        return Reply(origin_mod.agreement_text(key),
                     InlineKeyboardMarkup([[Btn("📚 كل الاتفاقيات", callback_data="ol")]]))

    # ------------------------------------------------------------------- GPC
    def gpc_code(self, code: str) -> Reply:
        b = self.ref.gpc_get(code)
        if not b:
            return Reply(f"❌ كود Brick <code>{code}</code> مش موجود في نسخة GPC الحالية.")
        return Reply(fmt.gpc_brick(b, with_definition=True))

    def gpc_search(self, query: str, offset: int = 0) -> Reply:
        q = query.strip()
        if not q:
            return Reply("ابعت اسم المنتج بعد gs1 — مثال: <code>gs1 white beans</code> أو <code>gs1 فاصوليا</code>",
                         state={"mode": "gs1"})
        translated = None
        search_q = q
        if is_arabic(q):
            translated = translate(q)
            if translated:
                search_q = translated
            else:   # bridge through the tariff: Arabic name -> HS -> English nomenclature words
                items, _ = self.ref.search(q, 1, 0)
                hs = self.ref.hs_desc(items[0].code) if items else None
                if hs:
                    search_q = translated = hs_to_gpc_terms(hs)
        bricks, total = self.ref.gpc_search(search_q, GPC_PAGE, offset)
        rows = []
        nav = self._nav("gp", offset, total, GPC_PAGE)
        if nav:
            rows.append(nav)
        text = fmt.gpc_results(q, translated, bricks, total, offset, self.ref.meta().get("gpc_version"))
        return Reply(text, InlineKeyboardMarkup(rows) if rows else None, state={"last_gpc": q})

    async def gpc_for_hs(self, code: str) -> Reply:
        hs = self.ref.hs_desc(code)
        if not hs:
            return Reply("مفيش وصف HS إنجليزي للبند ده علشان أقترح GS1.")
        terms = hs_to_gpc_terms(hs)
        bricks, total = self.ref.gpc_search(terms, GPC_PAGE, 0)
        text = fmt.gpc_results(f"HS {code[:6]}", terms, bricks, total, 0, self.ref.meta().get("gpc_version"))
        text += "\n\n⚠️ اقتراح آلي من وصف البند — اختار الـ Brick اللي بيوصف منتجك بالظبط."
        return Reply(text, state={"last_gpc": terms})

    # ----------------------------------------------------------------- misc
    # ------------------------------------------------------------ exchange rates
    fx_transport = None            # tests inject an httpx MockTransport here (central bank)
    customs_fx_transport = None    # tests inject an httpx MockTransport here (customs)
    fx_retry_after = 600.0         # seconds before retrying a rate source that just failed

    def _source_down(self, key: str) -> bool:
        t = self._fx_failed_at.get(key)
        return t is not None and time.monotonic() - t < self.fx_retry_after

    def customs_rates(self) -> tuple[dict[str, float], str | None]:
        raw = self.app.kv_get("customs_fx")
        if not raw:
            return {}, None
        try:
            d = json.loads(raw)
        except ValueError:
            return {}, None
        return d.get("rates") or {}, d.get("fetched_at")

    async def refresh_customs_rates(self, *, max_age_hours: float = 6.0) -> bool:
        rates, ts = self.customs_rates()
        age = _hours_since(ts)
        if rates and age is not None and age < max_age_hours:
            return True
        if self._source_down("customs"):
            return bool(rates)
        try:
            new = await fetch_customs_rates(self.s.official_base_url, transport=self.customs_fx_transport)
        except Exception as exc:
            self._fx_failed_at["customs"] = time.monotonic()
            self.app.kv_set("customs_fx_error", f"{type(exc).__name__}: {exc}"[:200])
            return bool(rates)
        self._fx_failed_at.pop("customs", None)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.app.kv_set("customs_fx", json.dumps({"rates": new, "fetched_at": stamp}))
        return True

    async def refresh_cbe(self, *, max_age_hours: float = 6.0) -> bool:
        """Refresh the official customs rates and the central-bank rates. Never raises."""
        await self.refresh_customs_rates(max_age_hours=max_age_hours)
        age = self.app.auto_fx_age_hours()
        if age is not None and age < max_age_hours:
            return True
        if self._source_down("cbe"):
            return False
        try:
            date, rates = await fetch_cbe(transport=self.fx_transport)
        except Exception as exc:
            self._fx_failed_at["cbe"] = time.monotonic()
            self.app.kv_set("cbe_error", f"{type(exc).__name__}: {exc}"[:200])
            return False
        self._fx_failed_at.pop("cbe", None)
        self.app.put_auto_fx(date, rates)
        return True

    async def rate_for(self, currency: str) -> tuple[float | None, str]:
        """Manual rate (/setfx) wins; then the customs conversion rate; then the central bank sell rate."""
        cur = currency.upper()
        manual = self.app.get_fx(cur)
        if manual and manual[0] > 0:
            return manual[0], f"سعر يدوي مسجل {_local_day(manual[1])}"
        await self.refresh_customs_rates(max_age_hours=12)
        rates, ts = self.customs_rates()
        age = _hours_since(ts)
        if rates.get(cur) and age is not None and age < 72:
            return rates[cur], f"سعر التحويل الجمركي {_local_day(ts)}"
        await self.refresh_cbe(max_age_hours=24)
        row = self.app.get_auto_fx(cur)
        if row and row["sell"]:
            return row["sell"], f"سعر بيع البنك المركزي {row['rate_date'] or _local_day(row['fetched_at'])}"
        if rates.get(cur) and ts:      # both sources down: an older customs rate, clearly dated
            return rates[cur], f"⚠️ آخر سعر تحويل جمركي متاح ({_local_day(ts)}) — راجعه"
        return None, ""

    async def fx_text(self) -> str:
        await self.refresh_customs_rates(max_age_hours=6)
        try:                            # don't keep the reply waiting on a slow central-bank site
            await asyncio.wait_for(self.refresh_cbe(max_age_hours=6), timeout=8)
        except asyncio.TimeoutError:
            self._fx_failed_at["cbe"] = time.monotonic()
        customs, cts = self.customs_rates()
        auto = {r["currency"]: r for r in self.app.all_auto_fx()}
        manual = {r["currency"]: r for r in self.app.all_fx() if r["rate"] > 0}
        if not customs and not auto and not manual:
            return ("💱 مش قادر أوصل لأسعار الصرف دلوقتي (موقع الجمارك والبنك المركزي).\n"
                    "اكتب السعر في الرسالة: <code>البند 5000$ @50.5</code> أو سجّله: <code>/setfx USD 50.5</code>")
        lines = ["💱 <b>أسعار الصرف</b> (جنيه)"]
        if customs:
            lines.append(f"🛃 سعر التحويل الجمركي (customs.gov.eg) — {_local_day(cts)}:")
            shown = [c for c in ("USD", "EUR", "GBP", "SAR", "AED", "CNY", "KWD", "CHF", "JPY") if c in customs]
            lines.append(" • ".join(f"{c} <b>{customs[c]:g}</b>" for c in shown))
        if auto:
            any_row = next(iter(auto.values()))
            lines.append(f"🏦 البنك المركزي — {any_row['rate_date'] or _local_day(any_row['fetched_at'])} (شراء / بيع):")
            for cur in ("USD", "EUR", "GBP", "SAR", "AED", "CNY", "KWD", "CHF", "JPY"):
                if cur in auto:
                    r = auto[cur]
                    lines.append(f"• {cur}: {r['buy']:.4f} / <b>{r['sell']:.4f}</b>")
        for cur, r in manual.items():
            lines.append(f"✍️ {cur}: {r['rate']:g} (سعر يدوي من {_local_day(r['updated_at'])} — بيتقدم على الرسمي)")
        lines.append("ℹ️ الحساب بيستخدم سعر التحويل الجمركي، ولو مش متاح سعر بيع البنك المركزي. "
                     "النهائي حسب يوم تسجيل البيان.")
        return "\n".join(lines)

    def about(self) -> str:
        return fmt.about_text(self.ref.meta(), self.ref.counts(), bool(self.live and self.live.available))
