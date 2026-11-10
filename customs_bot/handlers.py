"""Telegram handlers (python-telegram-bot v21+/v22)."""
from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
from telegram import ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from . import builder, router
from . import formatters as fmt
from .appdb import iso, parse_iso, utcnow
from .official import crawl_tariff
from .service import CustomsService, Reply
from .settings import ROOT

log = logging.getLogger(__name__)

BTN_SEARCH, BTN_CALC, BTN_GS1, BTN_FX, BTN_HELP = (
    "🔎 بحث عن بند", "🧮 حساب الجمارك", "🧱 تصنيف GS1", "💱 أسعار الصرف", "❓ المساعدة",
)
MENU = ReplyKeyboardMarkup([[BTN_SEARCH, BTN_CALC], [BTN_GS1, BTN_FX], [BTN_HELP]], resize_keyboard=True)
MODE_PROMPTS = {
    BTN_SEARCH: ("hs", "🔎 ابعت اسم المنتج (عربي أو إنجليزي) أو رقم البند."),
    BTN_CALC: ("calc", "🧮 ابعت رقم البند + القيمة CIF بالجنيه.\nمثال: <code>0713330090 250000</code>\n"
                       "أو ابعت رقم البند لوحده وبعدين القيمة."),
    BTN_GS1: ("gs1", "🧱 ابعت اسم المنتج (يفضل بالإنجليزي) أو كود الـ Brick.\nمثال: <code>dried beans</code>"),
}
RATE_LIMIT = (30, 60.0)   # requests per seconds window
_hits: dict[int, deque] = defaultdict(deque)


def svc(context: ContextTypes.DEFAULT_TYPE) -> CustomsService:
    return context.application.bot_data["svc"]


def is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> bool:
    return bool(user_id) and user_id in svc(context).admins()


_refused_at: dict[int, float] = {}


async def gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every handler. In private mode only the owner and allowed users get through.
    With no owner yet, whoever sends /start first becomes the owner."""
    s = svc(context)
    user = update.effective_user
    if user is None or not s.s.private_mode:
        return
    allowed = s.allowed()
    msg = update.effective_message
    if not allowed:
        if msg is not None and (msg.text or "").strip().startswith("/start"):
            s.app.kv_set("owner_id", str(user.id))
            log.warning("owner claimed by %s (%s)", user.id, user.username)
            await msg.reply_text("🔐 تمام — اتسجلت كمالك البوت. البوت دلوقتي خاص بيك، ومحدش غيرك يقدر يستخدمه.")
            return
        if msg is not None:
            await msg.reply_text("ابعت /start الأول.")
        raise ApplicationHandlerStop
    if user.id in allowed:
        return
    if update.callback_query is not None:
        await update.callback_query.answer("🔒 البوت ده خاص", show_alert=False)
    elif msg is not None and time.monotonic() - _refused_at.get(user.id, -1e9) > 3600:
        _refused_at[user.id] = time.monotonic()
        await msg.reply_text(f"🔒 البوت ده خاص.\nلو صاحب البوت عايز يضيفك يبعت: /allow {user.id}")
    raise ApplicationHandlerStop


def _limited(user_id: int) -> bool:
    q = _hits[user_id]
    now = time.monotonic()
    while q and now - q[0] > RATE_LIMIT[1]:
        q.popleft()
    q.append(now)
    return len(q) > RATE_LIMIT[0]


def split_message(text: str, limit: int = 3900) -> list[str]:
    """Split on line breaks so HTML tags (always closed within a line) stay valid."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


async def send(update: Update, context: ContextTypes.DEFAULT_TYPE, reply: Reply, *, edit: bool = False) -> None:
    for k, v in reply.state.items():
        context.user_data[k] = v
    if not reply.text:
        return
    chunks = split_message(reply.text)
    if edit and update.callback_query is not None and len(chunks) == 1:
        try:
            await update.callback_query.edit_message_text(
                chunks[0], parse_mode=ParseMode.HTML, reply_markup=reply.keyboard, disable_web_page_preview=True
            )
            return
        except BadRequest as exc:   # "message is not modified" or too old to edit
            if "not modified" in str(exc).lower():
                return
    for i, chunk in enumerate(chunks):
        await update.effective_message.reply_text(
            chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=reply.keyboard if i == len(chunks) - 1 else None,
        )


def _track(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, query: str) -> None:
    u = update.effective_user
    if u is None:
        return
    s = svc(context)
    s.app.touch_user(u.id, u.full_name, u.username)
    s.app.log(u.id, kind, query)


# ------------------------------------------------------------------ commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    _track(update, context, "start", "")
    name = update.effective_user.first_name if update.effective_user else ""
    await update.effective_message.reply_text(
        fmt.welcome(name, svc(context).s.bot_name), parse_mode=ParseMode.HTML, reply_markup=MENU,
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = is_admin(context, update.effective_user.id if update.effective_user else None)
    await send(update, context, Reply(fmt.help_text(admin)))


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, context, Reply(svc(context).about()))


async def cmd_fx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, context, Reply(await svc(context).fx_text()))


async def cmd_hs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args or [])
    if not text:
        context.user_data["mode"] = "hs"
        await send(update, context, Reply(MODE_PROMPTS[BTN_SEARCH][1]))
        return
    await handle_text(update, context, text, forced_mode="hs")


async def cmd_calc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args or [])
    if not text:
        context.user_data["mode"] = "calc"
        await send(update, context, Reply(MODE_PROMPTS[BTN_CALC][1]))
        return
    await handle_text(update, context, text, forced_mode="calc")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args or [])
    if not text:
        await send(update, context, Reply("ابعت رقم البند أو اسم المنتج بعد الأمر — مثال: <code>/export 0713330090</code>"))
        return
    await handle_text(update, context, "تصدير " + text)


async def cmd_gs1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args or [])
    if not text:
        context.user_data["mode"] = "gs1"
        await send(update, context, Reply(MODE_PROMPTS[BTN_GS1][1]))
        return
    await handle_text(update, context, text, forced_mode="gs1")


# ---------------------------------------------------------------- free text
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_text(update, context, update.effective_message.text or "")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                      forced_mode: str | None = None) -> None:
    s = svc(context)
    ud = context.user_data
    user_id = update.effective_user.id if update.effective_user else 0
    if _limited(user_id):
        await send(update, context, Reply("⏳ طلبات كتير في وقت قصير — استنى دقيقة وجرب تاني."))
        return
    text = text.strip()
    if text in MODE_PROMPTS:
        mode, prompt = MODE_PROMPTS[text]
        ud["mode"] = mode
        if mode != "calc":
            ud.pop("pending_code", None)
        await send(update, context, Reply(prompt))
        return
    if text == BTN_FX:
        await send(update, context, Reply(await s.fx_text()))
        return
    if text == BTN_HELP:
        await cmd_help(update, context)
        return

    mode = forced_mode or ud.get("mode")
    intent = router.parse(text, mode=mode, pending_code=ud.get("pending_code"))
    _track(update, context, intent.kind, text)
    if update.effective_chat:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    if intent.kind == "calc":
        reply = await s.calculate(intent)
    elif intent.kind == "code" and ud.get("pending_code") and not s.ref.by_prefix(intent.code, 1)[1]:
        # a bare number right after a calculation that is not a tariff code -> a new value
        intent = router.parse(text, mode="calc", pending_code=ud.get("pending_code"))
        reply = await s.calculate(intent)
    elif intent.kind == "code":
        if mode == "calc" and len(intent.code) == 10 and s.ref.get(intent.code):
            ud["pending_code"] = intent.code
            item = s.ref.get(intent.code)
            reply = Reply(
                f"تمام ✅ البند <code>{intent.code}</code>\n{fmt.short(item.short_ar, 90)}\n"
                f"{fmt.rates_inline(item)}\n\nابعت القيمة CIF بالجنيه (أو بالدولار مع $)."
            )
        else:
            reply = await s.details(intent.code)
    elif intent.kind == "export":
        reply = await s.export_view(intent.code)
    elif intent.kind == "gpc":
        reply = s.gpc_search(intent.query)
    elif intent.kind == "gpc_code":
        reply = s.gpc_code(intent.code)
    elif intent.kind == "search":
        reply = s.search(intent.query, export=intent.export)
    else:
        reply = Reply("ابعت اسم منتج أو رقم بند. /help للمساعدة.")
    await send(update, context, reply)


# ---------------------------------------------------------------- callbacks
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    s = svc(context)
    ud = context.user_data
    action, _, arg = (q.data or "").partition("|")
    await q.answer()
    if update.effective_user and _limited(update.effective_user.id):
        return
    if action == "d":
        await send(update, context, await s.details(arg))
    elif action == "k":
        ud["pending_code"] = arg
        ud["mode"] = "calc"
        await send(update, context, Reply(f"🧮 ابعت القيمة CIF للبند <code>{arg}</code>\n"
                                          "مثال: <code>250000</code> أو <code>5000$</code>"))
    elif action == "i":
        await send(update, context, await s.instructions(arg))
    elif action == "x":
        await send(update, context, await s.export_view(arg))
    elif action == "sx":
        await send(update, context, s.search(ud.get("last_query", ""), int(arg or 0), export=True), edit=True)
    elif action == "g":
        await send(update, context, await s.gpc_for_hs(arg))
    elif action == "s":
        await send(update, context, s.search(ud.get("last_query", ""), int(arg or 0)), edit=True)
    elif action == "p":
        prefix, _, off = arg.partition("|")
        await send(update, context, await s.prefix(prefix, int(off or 0)), edit=True)
    elif action == "gp":
        await send(update, context, s.gpc_search(ud.get("last_gpc", ""), int(arg or 0)), edit=True)
    elif action == "cv":
        pending = ud.get("pending_calc")
        if not pending:
            await send(update, context, Reply("ابعت رقم البند والقيمة تاني."))
            return
        await send(update, context, await s.calculate_code(
            arg, pending["value"], currency_note=pending.get("currency_note"), qty=pending.get("qty"),
            origin=pending.get("origin"), personal=pending.get("personal", False)))


# ------------------------------------------------------------------- admin
async def _admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if not is_admin(context, uid):
        await send(update, context, Reply("الأمر ده للأدمن بس."))
        return False
    return True


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_only(update, context):
        return
    s = svc(context)
    st = s.app.stats()
    counts = s.ref.counts()
    lines = [
        "📊 <b>إحصائيات</b>",
        f"👥 المستخدمين: {st['users']:,} (نشط آخر 7 أيام: {st['users_7d']:,})",
        f"📨 الطلبات آخر 7 أيام: {st['events_7d']:,}",
        "• " + " | ".join(f"{k}: {v}" for k, v in st["by_kind_7d"].items()),
        f"📦 بنود التعريفة: {counts['tariff']:,} | GS1: {counts['gpc_brick']:,}",
        f"🕒 آخر تحديث للتعريفة: {s.app.kv_get('last_update_at', s.ref.meta().get('tariff_snapshot', '?'))}",
        f"🌐 الاستعلام المباشر: {'شغال' if s.live and s.live.available else 'متوقف مؤقتاً'}",
        f"🔐 الوضع: {'خاص' if s.s.private_mode else 'عام'} — المسموح لهم: {len(s.allowed())}",
    ]
    if st["top_queries_7d"]:
        lines.append("🔝 أكتر طلبات: " + "، ".join(f"{fmt.short(q, 25)} ({n})" for q, n in st["top_queries_7d"]))
    await send(update, context, Reply("\n".join(lines)))


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    role = "المالك/أدمن" if is_admin(context, u.id) else "مستخدم مسموح له"
    await send(update, context, Reply(f"🪪 رقمك على تليجرام: <code>{u.id}</code>\nصلاحيتك: {role}"))


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE, allow: bool = True) -> None:
    if not await _admin_only(update, context):
        return
    s = svc(context)
    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        ids = sorted(s.allowed() - s.admins())
        body = "، ".join(str(i) for i in ids) or "مفيش"
        await send(update, context, Reply(
            f"المسموح لهم غيرك: {body}\nإضافة: <code>/allow 123456789</code> — إلغاء: <code>/deny 123456789</code>\n"
            "(الشخص يعرف رقمه لما يبعت أي رسالة للبوت أو من @userinfobot)"))
        return
    uid = int(args[0])
    s.set_allowed(uid, allow)
    await send(update, context, Reply(f"✅ {'اتضاف' if allow else 'اتشال'} المستخدم {uid}"))


async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_allow(update, context, allow=False)


async def cmd_setfx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_only(update, context):
        return
    args = context.args or []
    try:
        cur, rate = args[0].upper(), float(args[1].replace(",", ""))
        assert re.fullmatch(r"[A-Z]{3}", cur) and rate >= 0
    except (IndexError, ValueError, AssertionError):
        await send(update, context, Reply("الصيغة: <code>/setfx USD 50.5</code> — و <code>/setfx USD 0</code> للرجوع لسعر البنك المركزي"))
        return
    if rate == 0:
        svc(context).app.del_fx(cur)
        await send(update, context, Reply(f"✅ {cur} رجع يتحسب بسعر البنك المركزي"))
        return
    svc(context).app.set_fx(cur, rate, update.effective_user.id)
    await send(update, context, Reply(f"✅ اتسجل سعر يدوي {cur} = {rate:g} ج (بيتقدم على سعر البنك المركزي)"))


async def cmd_devfee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_only(update, context):
        return
    s = svc(context)
    args = context.args or []
    if not args:
        fees = {**s.s.dev_fees, **s.app.dev_fees()}
        body = "\n".join(f"• {k}: {v:g}%" for k, v in sorted(fees.items())) or "مفيش"
        await send(update, context, Reply(f"رسم التنمية المُعرّف:\n{body}\n\nالصيغة: <code>/devfee 8703 3</code>"))
        return
    try:
        prefix = re.sub(r"\D", "", args[0])
        pct = float(args[1])
        assert 2 <= len(prefix) <= 10 and pct >= 0
    except (IndexError, ValueError, AssertionError):
        await send(update, context, Reply("الصيغة: <code>/devfee 8703 3</code> (وصفر للإلغاء)"))
        return
    if pct == 0:
        s.app.del_dev_fee(prefix)
        await send(update, context, Reply(f"✅ اتلغى رسم التنمية لبنود {prefix}"))
    else:
        s.app.set_dev_fee(prefix, pct)
        await send(update, context, Reply(f"✅ رسم تنمية {pct:g}% على كل البنود اللي بتبدأ بـ {prefix}"))


async def run_update(application: Application, notify_chat: int | None) -> str:
    """Download the whole tariff from customs.gov.eg and swap it in. Returns a summary."""
    s: CustomsService = application.bot_data["svc"]
    if application.bot_data.get("update_running"):
        return "تحديث شغال بالفعل."
    application.bot_data["update_running"] = True
    status_msg = None
    last_edit = 0.0

    async def progress(text: str) -> None:
        nonlocal status_msg, last_edit
        if notify_chat is None or time.monotonic() - last_edit < 15:
            return
        last_edit = time.monotonic()
        try:
            if status_msg is None:
                status_msg = await application.bot.send_message(notify_chat, text)
            else:
                await status_msg.edit_text(text)
        except Exception:   # progress is best-effort
            pass

    try:
        old = s.ref.rates_snapshot()
        records = await crawl_tariff(s.s.official_base_url, progress=progress)
        raw_path = s.s.data_dir / "tariff_raw.json"
        raw_path.write_text(json.dumps({"meta": {"fetched_at": iso(), "source": s.s.official_base_url},
                                        "records": records}, ensure_ascii=False), encoding="utf-8")
        try:
            with builder.AtomicDB(s.s.reference_db, before_swap=s.ref.close) as conn:
                n = builder.write_tariff(conn, records, source="customs.gov.eg (Egyptian Customs Authority)",
                                         snapshot=iso()[:10])
        finally:
            s.ref.reload()
        s.app.cache_clear()
        s.app.kv_set("last_update_at", iso())
        new = s.ref.rates_snapshot()
        changed = [c for c in new if c in old and new[c] != old[c]]
        added = [c for c in new if c not in old]
        removed = [c for c in old if c not in new]
        summary = (f"✅ تم تحديث التعريفة من موقع الجمارك: {n:,} بند\n"
                   f"• بنود اتغيرت فئاتها: {len(changed)}\n• بنود جديدة: {len(added)}\n• بنود اتشالت: {len(removed)}")
        if changed:
            summary += "\nأمثلة: " + "، ".join(
                f"{c} ({old[c][0]}→{new[c][0]})" for c in changed[:5])
        return summary
    except Exception as exc:
        log.exception("tariff update failed")
        return (f"❌ التحديث فشل: {exc}\n"
                "لو السيرفر مش قادر يوصل لموقع الجمارك، شغّل tools/update_tariff.py من جهاز في مصر "
                "وابعت ملف reference.db للبوت.")
    finally:
        application.bot_data["update_running"] = False


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_only(update, context):
        return
    chat_id = update.effective_chat.id
    await send(update, context, Reply("⏳ بدأ تحديث التعريفة من موقع الجمارك (ممكن ياخد 20–40 دقيقة). هبعتلك النتيجة."))

    async def job() -> None:
        summary = await run_update(context.application, chat_id)
        await context.application.bot.send_message(chat_id, summary)

    tasks = context.application.bot_data.setdefault("bg_tasks", set())
    task = asyncio.get_running_loop().create_task(job())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin uploads: GPC json/xml, tariff raw json, or a full reference.db."""
    if not is_admin(context, update.effective_user.id if update.effective_user else None):
        await send(update, context, Reply("الملفات بتتقبل من الأدمن بس."))
        return
    s = svc(context)
    doc = update.effective_message.document
    name = (doc.file_name or "upload.bin").lower()
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await send(update, context, Reply("تليجرام بيسمح للبوتات تنزل ملفات لحد 20MB بس — اضغط الملف ZIP وابعته تاني."))
        return
    await send(update, context, Reply("⏳ جاري استيراد الملف..."))
    tg_file = await doc.get_file()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        await tg_file.download_to_drive(path)
        try:
            if name.endswith(".zip"):
                path, name = _unzip_single(path, Path(tmp) / "unzipped")
            if name.endswith(".db"):
                info = builder.validate_reference(path)
                if info.get("tariff", 0) < 1000:
                    raise ValueError(f"الملف مش قاعدة بيانات مرجعية سليمة: {info}")
                try:
                    _copy_into(path, s.s.reference_db, before_swap=s.ref.close)
                finally:
                    s.ref.reload()
                s.app.cache_clear()
                s.app.kv_set("last_update_at", iso())
                msg = f"✅ اتحدثت قاعدة البيانات: {info}"
            elif name.endswith((".json", ".xml")):
                head = path.read_bytes()[:4000].decode("utf-8", "ignore")
                if '"Schema"' in head or name.endswith(".xml"):
                    bricks, lang, version = builder.read_gpc_file(path)
                    if len(bricks) < 1000:
                        raise ValueError(f"عدد الـ Bricks قليل ({len(bricks)}) — الملف مش GPC كامل")
                    try:
                        with builder.AtomicDB(s.s.reference_db, before_swap=s.ref.close) as conn:
                            if lang == "EN":
                                n = builder.write_gpc(conn, bricks, version=f"GPC {version} ({lang})",
                                                      source="GS1 Global Product Classification")
                            else:
                                n = builder.add_gpc_translation(conn, bricks, lang)
                    finally:
                        s.ref.reload()
                    msg = f"✅ GS1 GPC اتحدث ({lang}): {n:,} Brick"
                else:
                    records, meta = builder.load_raw_tariff(path)
                    if len(records) < 1000:
                        raise ValueError(f"عدد البنود في الملف قليل ({len(records)})")
                    try:
                        with builder.AtomicDB(s.s.reference_db, before_swap=s.ref.close) as conn:
                            n = builder.write_tariff(conn, records, source=meta.get("source", "customs.gov.eg"),
                                                     snapshot=(meta.get("fetched_at") or iso())[:10])
                    finally:
                        s.ref.reload()
                    s.app.cache_clear()
                    s.app.kv_set("last_update_at", iso())
                    msg = f"✅ التعريفة اتحدثت من الملف: {n:,} بند"
            else:
                msg = "نوع الملف مش مدعوم. ابعت .json أو .xml (GPC) أو reference.db"
        except Exception as exc:
            log.exception("upload failed")
            msg = f"❌ فشل استيراد الملف: {exc}"
    await send(update, context, Reply(msg))


def _unzip_single(zpath: Path, out_dir: Path) -> tuple[Path, str]:
    """Extract the first .db/.json/.xml from an uploaded zip (no path traversal, size-capped)."""
    import zipfile

    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            inner = Path(info.filename).name.lower()
            if info.is_dir() or not inner.endswith((".db", ".json", ".xml")):
                continue
            if info.file_size > 300 * 1024 * 1024:
                raise ValueError("الملف جوه الـ ZIP كبير جداً")
            target = out_dir / inner
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            return target, inner
    raise ValueError("الـ ZIP مفيهوش ملف .db أو .json أو .xml")


def _copy_into(src: Path, dst: Path, before_swap=None) -> None:
    import shutil

    tmp = dst.with_suffix(".upload")
    shutil.copy2(src, tmp)
    if dst.exists():
        shutil.copy2(dst, dst.with_suffix(".db.bak"))
    if before_swap:
        before_swap()
    tmp.replace(dst)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ حصل خطأ غير متوقع — جرب تاني أو ابعت /start.")
        except Exception:
            pass


# ------------------------------------------------------------- auto update
async def fx_loop(application: Application) -> None:
    """Keep the CBE exchange rates fresh (every 6 hours)."""
    s: CustomsService = application.bot_data["svc"]
    await asyncio.sleep(20)
    while True:
        await s.refresh_cbe(max_age_hours=6)
        await asyncio.sleep(6 * 3600)


async def auto_update_loop(application: Application) -> None:
    s: CustomsService = application.bot_data["svc"]
    days = s.s.auto_update_days
    if days <= 0:
        return
    await asyncio.sleep(600)   # let the bot settle after start
    while True:
        stamps = [parse_iso(s.app.kv_get(k)) for k in ("last_update_at", "last_update_try")]
        stamps = [t for t in stamps if t is not None]
        due = not stamps or (utcnow() - max(stamps)).days >= days
        if due:
            s.app.kv_set("last_update_try", iso())     # failed attempts wait for the next cycle too
            summary = await run_update(application, None)
            for admin in s.admins():
                try:
                    await application.bot.send_message(admin, "🔄 تحديث تلقائي:\n" + summary)
                except Exception:
                    pass
        await asyncio.sleep(3600)


# ------------------------------------------------------------ self update (code from GitHub)
# The bot keeps its own code current. Every 30 minutes it asks GitHub for the newest commit of
# UPDATE_REPO. A new commit is downloaded and checked before anything changes: only known file names
# are taken, every .py file must compile, and the new code must pass its own self-test against a copy
# of the data. Then a small helper process waits for this bot to stop, swaps the files (backup first),
# starts the new version and waits for it to report in (heartbeat). If the new version does not come
# up, the helper puts the old files back and starts the old version. The owner hears about both.
UPDATE_REPO = os.getenv("UPDATE_REPO", "maboheiba911-star/customs-bot").strip()
UPDATE_BRANCH = (os.getenv("UPDATE_BRANCH", "main").strip() or "main")
UPDATE_EVERY = 30 * 60             # seconds between checks
UPDATE_FIRST_CHECK = 3 * 60        # first check a few minutes after start
GITHUB_API = "https://api.github.com"
LOCK_PORT = 47391                  # bot.py holds this port while it runs (one bot per computer)
_UPD_ALLOWED = re.compile(
    r"^(?:bot\.py|requirements\.txt|README\.md|DATA_SOURCES\.md|\.env\.example|[A-Za-z0-9_]+\.bat"
    r"|customs_bot/[A-Za-z0-9_]+\.py|tools/[A-Za-z0-9_]+\.py)$")
_UPD_REQUIRED = ("bot.py", "customs_bot/__init__.py", "customs_bot/handlers.py", "customs_bot/service.py")
_UPD_MAX_BYTES = 25 * 1024 * 1024
_GH_HEADERS = {"User-Agent": "customs-bot-self-update", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
_WIN_DETACHED = 0x00000008 | 0x00000200     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
_WIN_BREAKAWAY = 0x01000000                 # CREATE_BREAKAWAY_FROM_JOB
_WIN_NO_WINDOW = 0x08000000                 # CREATE_NO_WINDOW
upd_transport = None                        # tests inject an httpx MockTransport here


def auto_update_enabled() -> bool:
    v = (os.getenv("AUTO_UPDATE") or "").strip().lower()
    if v:
        return v in {"1", "true", "yes", "on"}
    return os.name == "nt" and bool(UPDATE_REPO)      # on by default on the owner's Windows PC


def _upd_dir(s: CustomsService) -> Path:
    d = s.s.data_dir / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def installed_version(upd: Path) -> dict:
    v = _read_json(upd / "installed.json", {})
    return v if isinstance(v, dict) and v.get("sha") else {"sha": "manual", "message": "نسخة متركبة يدوي"}


def _local_time(ts: str | None) -> str:
    """UTC timestamp (…Z) -> this computer's local time, e.g. 2026-11-11 00:40."""
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts[:16]


async def latest_commit() -> dict:
    async with httpx.AsyncClient(headers=_GH_HEADERS, timeout=20, follow_redirects=True,
                                 transport=upd_transport) as client:
        r = await client.get(f"{GITHUB_API}/repos/{UPDATE_REPO}/commits/{UPDATE_BRANCH}")
        r.raise_for_status()
        j = r.json()
    commit = j.get("commit") or {}
    first_line = ((commit.get("message") or "").strip().splitlines() or [""])[0]
    return {"sha": j["sha"], "message": first_line[:150], "date": (commit.get("committer") or {}).get("date", "")}


async def download_zip(sha: str) -> bytes:
    buf = bytearray()
    async with httpx.AsyncClient(headers=_GH_HEADERS, timeout=90, follow_redirects=True,
                                 transport=upd_transport) as client:
        async with client.stream("GET", f"{GITHUB_API}/repos/{UPDATE_REPO}/zipball/{sha}") as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > _UPD_MAX_BYTES:
                    raise ValueError("ملف التحديث أكبر من المسموح")
    return bytes(buf)


def stage_update(data: bytes, stage: Path) -> list[str]:
    """Unpack the allowed files of a GitHub zipball into `stage`. Raises on anything suspicious."""
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    files: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        entries = [i for i in z.infolist() if not i.is_dir()]
        if not entries or len(entries) > 3000:
            raise ValueError("ملف التحديث شكله غلط")
        if len({i.filename.split("/", 1)[0] for i in entries}) != 1:
            raise ValueError("ملف التحديث شكله غلط (أكتر من فولدر رئيسي)")
        for info in entries:
            name = info.filename
            parts = name.split("/")
            if "\\" in name or name.startswith("/") or ".." in parts or ":" in name:
                raise ValueError(f"مسار مش مسموح: {name}")
            rel = "/".join(parts[1:])
            if not _UPD_ALLOWED.match(rel):
                continue
            if info.file_size > 5 * 1024 * 1024:
                raise ValueError(f"ملف كبير زيادة: {rel}")
            body = z.read(info)
            if rel.endswith(".py"):
                compile(body.decode("utf-8"), rel, "exec")       # a syntax error rejects the update
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
            files.append(rel)
    missing = [f for f in _UPD_REQUIRED if f not in files]
    if missing:
        raise ValueError("ملفات ناقصة: " + "، ".join(missing))
    return sorted(files)


_BASIC_SELFTEST = r"""
import os, shutil, sys
sys.path.insert(0, os.getcwd())
ref = os.environ.get("SELFTEST_REFERENCE_DB", "")
if ref and os.path.exists(ref):
    shutil.copy2(ref, os.path.join(os.environ["DATA_DIR"], "reference.db"))
import bot
from customs_bot.settings import load_settings
app = bot.build_application(load_settings())
assert "svc" in app.bot_data
print("basic selftest ok")
"""


def _selftest_sync(stage: Path, s: CustomsService, work: Path) -> tuple[bool, str]:
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    env = dict(os.environ)
    env.update({"TELEGRAM_BOT_TOKEN": "123456:SELFTEST", "AUTO_UPDATE": "0", "LIVE_LOOKUP": "0",
                "DATA_DIR": str(work), "SELFTEST_REFERENCE_DB": str(s.s.reference_db),
                "PYTHONIOENCODING": "utf-8"})
    env.pop("CUSTOMS_BOT_BOOT", None)
    bot_py = (stage / "bot.py").read_text(encoding="utf-8")
    cmd = ([sys.executable, str(stage / "bot.py"), "--selftest"] if "--selftest" in bot_py
           else [sys.executable, "-c", _BASIC_SELFTEST])
    try:
        r = subprocess.run(cmd, cwd=str(stage), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=240,
                           creationflags=_WIN_NO_WINDOW if os.name == "nt" else 0)
        out = (r.stdout or b"").decode("utf-8", "replace")
        return r.returncode == 0, out[-1500:]
    except subprocess.TimeoutExpired:
        return False, "self-test timed out"
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def run_selftest(stage: Path, s: CustomsService) -> tuple[bool, str]:
    return await asyncio.to_thread(_selftest_sync, stage, s, _upd_dir(s) / "selftest")


def _spawn_helper(upd: Path, args: dict) -> None:
    """Start the detached helper that swaps the files after this bot has stopped."""
    script = upd / "apply_update.py"
    script.write_text(_HELPER_SRC, encoding="utf-8")
    args_path = upd / "helper_args.json"
    _write_json(args_path, args)
    cmd = [sys.executable, str(script), str(args_path)]
    kw = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True)
    if os.name == "nt":
        try:
            subprocess.Popen(cmd, creationflags=_WIN_DETACHED | _WIN_BREAKAWAY, **kw)
        except OSError:        # not allowed to leave the job: the launcher's job lets children go anyway
            subprocess.Popen(cmd, creationflags=_WIN_DETACHED, **kw)
    else:
        subprocess.Popen(cmd, start_new_session=True, **kw)


def _stop_application(application: Application) -> None:
    application.stop_running()


async def _notify_admins(application: Application, text: str) -> None:
    s: CustomsService = application.bot_data["svc"]
    for admin in s.admins():
        try:
            await application.bot.send_message(admin, text, parse_mode=ParseMode.HTML)
        except Exception:
            log.warning("could not notify admin %s", admin)


def _result_text(res: dict) -> str | None:
    sha = html.escape((res.get("sha") or "")[:7])
    msg = html.escape(res.get("message") or "")
    status = res.get("status")
    if status == "ok":
        return f"✅ البوت حدّث نفسه للنسخة <code>{sha}</code>" + (f"\n📝 {msg}" if msg else "")
    if status == "rolled_back":
        return (f"⚠️ النسخة الجديدة <code>{sha}</code> مشتغلتش، فرجعت للنسخة اللي كانت شغالة لوحدي."
                f"\nالسبب: {html.escape(res.get('reason') or '')}")
    if status == "restarted":
        return "🔄 البوت اشتغل تاني."
    if status == "failed":
        return f"⚠️ التحديث <code>{sha}</code> متركبش: {html.escape(res.get('reason') or '')}"
    return None


async def _announce_result(application: Application) -> None:
    """After a start by the helper: tell the owner how the update / restart ended."""
    s: CustomsService = application.bot_data["svc"]
    path = _upd_dir(s) / "result.json"
    for _ in range(72):              # the helper writes the result within a few minutes
        if path.exists():
            res = _read_json(path, {})
            path.unlink(missing_ok=True)
            text = _result_text(res) if isinstance(res, dict) else None
            if text:
                await _notify_admins(application, text)
            return
        await asyncio.sleep(5)


async def _updater_on_start(application: Application) -> None:
    try:
        s: CustomsService = application.bot_data["svc"]
        upd = _upd_dir(s)
        _write_json(upd / "heartbeat.json", {"sha": installed_version(upd)["sha"], "pid": os.getpid(),
                                             "boot": os.environ.get("CUSTOMS_BOT_BOOT", ""), "at": iso()})
        loop = asyncio.get_running_loop()
        tasks = application.bot_data.setdefault("bg_tasks", set())
        if os.environ.get("CUSTOMS_BOT_BOOT"):          # started by the update/restart helper
            tasks.add(loop.create_task(_announce_result(application)))
        if auto_update_enabled():
            tasks.add(loop.create_task(self_update_loop(application)))
    except Exception:
        log.exception("self-update start hook failed")


async def check_and_apply(application: Application, *, force: bool = False,
                          chat_id: int | None = None) -> tuple[str, bool]:
    """Look for a newer version on GitHub and install it. Returns (status text, restarting)."""
    s: CustomsService = application.bot_data["svc"]
    if application.bot_data.get("upd_running"):
        return "⏳ فحص التحديث شغال بالفعل.", False
    application.bot_data["upd_running"] = True
    upd = _upd_dir(s)
    try:
        s.app.kv_set("upd_last_check", iso())
        try:
            latest = await latest_commit()
        except Exception as exc:
            s.app.kv_set("upd_last_error", f"GitHub: {type(exc).__name__}"[:200])
            return f"⚠️ مقدرتش أوصل لـ GitHub دلوقتي ({html.escape(type(exc).__name__)}). هجرب تاني لوحدي.", False
        s.app.kv_set("upd_last_error", "")
        s.app.kv_set("upd_remote", json.dumps(latest, ensure_ascii=False))
        sha7 = html.escape(latest["sha"][:7])
        inst = installed_version(upd)
        if latest["sha"] == inst["sha"]:
            return f"✅ البوت على آخر نسخة (<code>{sha7}</code>).", False
        bad = _read_json(upd / "bad.json", [])
        if latest["sha"] in bad and not force:
            return f"⚠️ النسخة <code>{sha7}</code> فشلت قبل كده — مستني نسخة أحدث.", False
        stage = upd / "stage"
        try:
            data = await download_zip(latest["sha"])
        except Exception as exc:
            s.app.kv_set("upd_last_error", f"download: {type(exc).__name__}"[:200])
            return f"⚠️ مقدرتش أنزّل النسخة الجديدة دلوقتي ({html.escape(type(exc).__name__)}).", False

        def reject(why: str) -> tuple[str, bool]:
            bad.append(latest["sha"])
            _write_json(upd / "bad.json", bad[-20:])
            s.app.kv_set("upd_last_error", f"rejected {latest['sha'][:7]}: {why}"[:200])
            shutil.rmtree(stage, ignore_errors=True)
            return f"❌ النسخة الجديدة <code>{sha7}</code> متركبتش: {html.escape(why)}", False

        try:
            files = await asyncio.to_thread(stage_update, data, stage)
        except Exception as exc:
            return reject(f"{type(exc).__name__}: {exc}"[:300])
        ok, out = await run_selftest(stage, s)
        if not ok:
            log.error("self-test of %s failed:\n%s", latest["sha"][:7], out)
            text, _ = reject("فشلت في الاختبار قبل التركيب")
            if chat_id is None:
                await _notify_admins(application, text)
            return text + f"\n<code>{html.escape(out[-400:])}</code>", False
        args = {"mode": "update", "sha": latest["sha"], "message": latest["message"], "date": latest["date"],
                "from_sha": inst["sha"], "stage": str(stage), "files": files, "bot_dir": str(ROOT),
                "upd_dir": str(upd), "python": sys.executable, "port": LOCK_PORT, "boot": secrets.token_hex(8)}
        text = (f"⬆️ نسخة جديدة <code>{sha7}</code>: {html.escape(latest['message'])}\n"
                "البوت هيعيد التشغيل دلوقتي (ثواني) وهيبلغك لما يخلص.")
        if chat_id is not None:
            try:
                await application.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            except Exception:
                pass
        _spawn_helper(upd, args)
        log.info("self-update: installing %s, restarting", latest["sha"][:7])
        _stop_application(application)
        return text, True
    finally:
        application.bot_data["upd_running"] = False


async def self_update_loop(application: Application) -> None:
    await asyncio.sleep(UPDATE_FIRST_CHECK)
    while True:
        try:
            _, restarting = await check_and_apply(application)
            if restarting:
                return
        except Exception:
            log.exception("self-update check failed")
        await asyncio.sleep(UPDATE_EVERY)


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = svc(context)
    upd = _upd_dir(s)
    inst = installed_version(upd)
    head = f"🤖 <b>نسخة البوت:</b> <code>{html.escape(inst['sha'][:7])}</code>"
    if inst.get("message"):
        head += f" — {html.escape(inst['message'])}"
    lines = [head]
    if inst.get("installed_at"):
        lines.append(f"📅 اتركبت: {html.escape(inst['installed_at'].replace('T', ' ')[:16])}")
    if auto_update_enabled():
        lines.append(f"🔄 التحديث التلقائي: شغال — كل {UPDATE_EVERY // 60} دقيقة من github.com/{UPDATE_REPO}")
    else:
        lines.append("🔄 التحديث التلقائي: متوقف على الجهاز ده")
    last = s.app.kv_get("upd_last_check")
    if last:
        err = s.app.kv_get("upd_last_error")
        lines.append(f"🕒 آخر فحص: {_local_time(last)}" + (f" — ⚠️ {html.escape(err)}" if err else ""))
    remote = _read_json_text(s.app.kv_get("upd_remote"))
    if remote.get("sha") and remote["sha"] != inst["sha"]:
        lines.append(f"⬆️ متاح نسخة أحدث: <code>{html.escape(remote['sha'][:7])}</code>")
    if is_admin(context, update.effective_user.id if update.effective_user else None):
        lines.append("أوامر: /upgrade تحديث دلوقتي • /restart إعادة تشغيل")
    await send(update, context, Reply("\n".join(lines)))


def _read_json_text(raw: str | None) -> dict:
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_only(update, context):
        return
    await send(update, context, Reply("🔎 بدوّر على نسخة جديدة…"))
    text, restarting = await check_and_apply(context.application, force=True, chat_id=update.effective_chat.id)
    if not restarting:
        await send(update, context, Reply(text))


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin_only(update, context):
        return
    s = svc(context)
    upd = _upd_dir(s)
    _spawn_helper(upd, {"mode": "restart", "sha": installed_version(upd)["sha"], "bot_dir": str(ROOT),
                        "upd_dir": str(upd), "python": sys.executable, "port": LOCK_PORT,
                        "boot": secrets.token_hex(8)})
    await send(update, context, Reply("🔄 البوت بيعيد التشغيل… ثواني وهيبلغك."))
    _stop_application(context.application)


_HELPER_SRC = r'''"""customs-bot update helper (written by customs_bot/handlers.py; safe to delete).

Waits for the running bot to stop, swaps in the new files (backup first), starts the new version and
waits for its heartbeat. If the new version does not come up it restores the old files and starts the
old version again. Standard library only: it must not depend on the files it replaces.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

DETACHED = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000                      # CREATE_BREAKAWAY_FROM_JOB
NO_WINDOW = 0x08000000                      # CREATE_NO_WINDOW


class Helper:
    def __init__(self, args):
        self.a = args
        self.bot_dir = Path(args["bot_dir"])
        self.upd = Path(args["upd_dir"])
        self.python = args["python"]
        self.port = int(args.get("port", 47391))
        self.crash_wait = float(args.get("crash_wait", 150))     # seconds for the new bot to report in
        self.slow_wait = float(args.get("slow_wait", 420))       # still running but slow (no internet?)
        self.stop_wait = float(args.get("stop_wait", 180))       # for the old bot to stop

    # ------------------------------------------------------------ helpers
    def log(self, msg):
        try:
            with open(self.upd / "update.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except Exception:
            pass

    def read_json(self, name, default):
        try:
            return json.loads((self.upd / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def write_json(self, name, data):
        path = self.upd / name
        tmp = path.with_name(name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def port_free(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", self.port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def wait_port_free(self, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if self.port_free():
                return True
            time.sleep(1)
        return False

    def start_bot(self, boot):
        env = dict(os.environ)
        env["CUSTOMS_BOT_BOOT"] = boot
        cmd = [self.python, str(self.bot_dir / "bot.py")]
        kw = dict(cwd=str(self.bot_dir), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
        if os.name == "nt":
            try:
                return subprocess.Popen(cmd, creationflags=DETACHED | BREAKAWAY, **kw)
            except OSError:
                return subprocess.Popen(cmd, creationflags=DETACHED, **kw)
        return subprocess.Popen(cmd, start_new_session=True, **kw)

    def wait_healthy(self, proc, boot):
        start = time.time()
        while True:
            if proc.poll() is not None:
                return False, "اتقفل بعد ما اشتغل (exit code %s)" % proc.returncode
            hb = self.read_json("heartbeat.json", {})
            if isinstance(hb, dict) and hb.get("boot") == boot:
                return True, ""
            waited = time.time() - start
            if waited > self.slow_wait or (waited > self.crash_wait and self.port_free()):
                return False, "مبعتش إشارة إنه اشتغل خلال %d ثانية" % int(waited)
            time.sleep(2)

    def kill(self, proc):
        try:
            if os.name == "nt":       # the venv launcher starts the real python as a child: kill the tree
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                               creationflags=NO_WINDOW)
            else:
                proc.kill()
            proc.wait(20)
        except Exception:
            pass

    def copy_in(self, src, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".upd-tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)

    def apply(self, stage, files, backup):
        created = []
        for rel in files:
            dst = self.bot_dir / rel
            if dst.exists():
                b = backup / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, b)
            else:
                created.append(rel)
        self.write_json("applying.json", {"backup": str(backup), "files": files, "created": created})
        for rel in files:
            self.copy_in(stage / rel, self.bot_dir / rel)
        return created

    def restore(self, files, backup, created):
        for rel in files:
            b = backup / rel
            if b.exists():
                self.copy_in(b, self.bot_dir / rel)
        for rel in created:
            try:
                (self.bot_dir / rel).unlink()
            except FileNotFoundError:
                pass

    def pip_install(self):
        py = self.python
        if py.lower().endswith("pythonw.exe"):
            py = py[: -len("pythonw.exe")] + "python.exe"
        with open(self.upd / "update.log", "a", encoding="utf-8") as logf:
            r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-r",
                                str(self.bot_dir / "requirements.txt")], cwd=str(self.bot_dir),
                               stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT, timeout=900,
                               creationflags=NO_WINDOW if os.name == "nt" else 0)
        if r.returncode != 0:
            raise RuntimeError("pip install failed (%s)" % r.returncode)

    def prune_backups(self, keep=3):
        root = self.upd / "backups"
        if root.exists():
            for d in sorted(p for p in root.iterdir() if p.is_dir())[:-keep]:
                shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------ main flow
    def run(self):
        a = self.a
        mode = a.get("mode", "update")
        sha = a.get("sha", "")
        boot = a.get("boot") or str(time.time())
        self.log("helper %s %s" % (mode, sha[:7]))
        if not self.wait_port_free(self.stop_wait):
            self.log("the running bot did not stop - nothing changed")
            self.write_json("result.json", {"status": "failed", "sha": sha, "reason": "البوت القديم مقفلش"})
            return 1
        if mode == "restart":
            proc = self.start_bot(boot)
            ok, why = self.wait_healthy(proc, boot)
            self.log("restart: %s %s" % (ok, why))
            if ok:
                self.write_json("result.json", {"status": "restarted", "sha": sha})
            return 0 if ok else 1

        stage = Path(a["stage"])
        files = list(a["files"])
        backup = self.upd / "backups" / time.strftime("%Y%m%d-%H%M%S")
        old_installed = self.read_json("installed.json", None)
        created, proc = [], None
        try:
            req_new, req_old = stage / "requirements.txt", self.bot_dir / "requirements.txt"
            req_changed = req_new.exists() and (not req_old.exists() or req_new.read_bytes() != req_old.read_bytes())
            created = self.apply(stage, files, backup)
            if req_changed:
                self.log("requirements.txt changed - pip install")
                self.pip_install()
            self.write_json("installed.json", {"sha": sha, "message": a.get("message", ""),
                                               "date": a.get("date", ""),
                                               "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            try:
                (self.upd / "heartbeat.json").unlink()
            except FileNotFoundError:
                pass
            proc = self.start_bot(boot)
            ok, why = self.wait_healthy(proc, boot)
        except Exception as exc:
            ok, why = False, "%s: %s" % (type(exc).__name__, exc)
            self.log(traceback.format_exc())
        if ok:
            self.log("update %s OK" % sha[:7])
            self.write_json("result.json", {"status": "ok", "sha": sha, "message": a.get("message", ""),
                                            "from_sha": a.get("from_sha", "")})
            for name in ("applying.json",):
                try:
                    (self.upd / name).unlink()
                except FileNotFoundError:
                    pass
            shutil.rmtree(stage, ignore_errors=True)
            self.prune_backups()
            return 0

        self.log("update %s failed (%s) - rolling back" % (sha[:7], why))
        if proc is not None:
            self.kill(proc)
            self.wait_port_free(60)
        try:
            self.restore(files, backup, created)
        except Exception:
            self.log(traceback.format_exc())
        if old_installed:
            self.write_json("installed.json", old_installed)
        else:
            try:
                (self.upd / "installed.json").unlink()
            except FileNotFoundError:
                pass
        bad = self.read_json("bad.json", [])
        if sha not in bad:
            bad.append(sha)
        self.write_json("bad.json", bad[-20:])
        self.write_json("result.json", {"status": "rolled_back", "sha": sha, "message": a.get("message", ""),
                                        "reason": why})
        try:
            (self.upd / "applying.json").unlink()
        except FileNotFoundError:
            pass
        boot2 = boot + "-rollback"
        proc2 = self.start_bot(boot2)
        ok2, why2 = self.wait_healthy(proc2, boot2)
        self.log("old version back: %s %s" % (ok2, why2))
        return 1


def main(argv):
    args = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    helper = Helper(args)
    try:
        return helper.run()
    except Exception:
        helper.log(traceback.format_exc())
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def register(app: Application) -> None:
    original_post_init = app.post_init

    async def post_init_with_updater(application: Application) -> None:
        if original_post_init:
            await original_post_init(application)
        await _updater_on_start(application)      # heartbeat + self-update loop

    app.post_init = post_init_with_updater
    app.add_handler(TypeHandler(Update, gatekeeper), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("allow", cmd_allow))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("fx", cmd_fx))
    app.add_handler(CommandHandler("hs", cmd_hs))
    app.add_handler(CommandHandler("calc", cmd_calc))
    app.add_handler(CommandHandler("gs1", cmd_gs1))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("setfx", cmd_setfx))
    app.add_handler(CommandHandler("devfee", cmd_devfee))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
