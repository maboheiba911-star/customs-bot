#!/data/data/com.termux/files/usr/bin/bash
# تسطيب بوت الجمارك على تليفون أندرويد (Termux) — بيشتغل على الداتا أو الواي فاي.
# الاستخدام (جوه Termux):
#   curl -fsSLo i.sh https://raw.githubusercontent.com/maboheiba911-star/customs-bot/main/termux_install.sh && bash i.sh
# آمن تعيد تشغيله: بيحدّث الكود وبيسيب التوكن والبيانات زي ما هم.
set -euo pipefail

REPO="maboheiba911-star/customs-bot"
DIR="$HOME/customs-bot"
BOOT="$HOME/.termux/boot/start-customs-bot"

say()  { printf '\n\033[1;32m▶ %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31m✖ %s\033[0m\n' "$1"; exit 1; }

[ -d /data/data/com.termux ] || fail "السكريبت ده لـ Termux على أندرويد بس."

# ------------------------------------------------------------------ 1) packages
say "1/6 تجهيز Termux (أول مرة بياخد كام دقيقة — سيبه يكمّل)"
case "${TERMUX_VERSION:-}" in
  googleplay*) fail "دي نسخة Termux بتاعة جوجل بلاي: مش بتشتغل مع Termux:Boot وسيرفرها ممكن يبقى مقفول. امسحها ونزّل نسخة F-Droid: https://f-droid.org/repo/com.termux_1022.apk" ;;
esac

# أول ما Termux يتفتح بيحدّث نفسه في الخلفية وبيقفل الـ apt لحد ما يخلص — نستنى بدل ما نفشل
update_lists() {
  for i in $(seq 1 18); do
    rc=0; out="$(pkg update -y -o Dpkg::Options::=--force-confnew 2>&1)" || rc=$?
    if printf '%s' "$out" | grep -q "Could not get lock"; then
      echo "⏳ Termux لسه بيجهّز نفسه — هستنى 10 ثواني وأجرب تاني ($i/18)"; sleep 10; continue
    fi
    printf '%s\n' "$out" | tail -n 2
    return $rc
  done
  return 1
}
has_python() { apt-cache show python >/dev/null 2>&1; }      # بدون pipe: مع pipefail الـ grep -q ممكن يدي نتيجة غلط
# سيرفر الحزم الافتراضي ممكن يبقى مقفول على بعض الشبكات — نجرّب السيرفرات التانية لحد ما واحد يرد
pick_mirror() {
  for f in "$PREFIX"/etc/termux/mirrors/*/*; do
    [ -f "$f" ] || continue
    main="$(sed -n 's/^MAIN="\(.*\)"$/\1/p' "$f")"
    [ -n "$main" ] || continue
    echo "… بجرب سيرفر: $main"
    if curl -fsS --max-time 15 -o /dev/null "$main/dists/stable/Release"; then
      echo "deb $main stable main" > "$PREFIX/etc/apt/sources.list"
      ln -sfn "$f" "$PREFIX/etc/termux/chosen_mirrors"
      echo "✅ هنستخدم السيرفر ده"
      return 0
    fi
  done
  return 1
}
update_lists || true
if ! has_python; then
  warn "سيرفر الحزم مش بيرد — هدوّر على سيرفر تاني"
  pick_mirror && update_lists || true
fi
has_python || fail "مقدرتش أوصل لأي سيرفر حزم من الشبكة دي. جرّب تبدّل (واي فاي ⇄ داتا) وشغّل الأمر تاني."
pkg upgrade -y -o Dpkg::Options::=--force-confnew >/dev/null 2>&1 || true
pkg install -y python curl termux-tools || fail "تسطيب بايثون فشل — صوّر الشاشة وابعتها لـ Claude."
python -m pip --version >/dev/null 2>&1 || pkg install -y python-pip

# ------------------------------------------------------------------ 2) storage permission (optional)
say "2/6 إذن الملفات (لو ظهرت رسالة اضغط «سماح») — لو مظهرتش مش مشكلة"
can_read() { ls "$HOME/storage/shared/" >/dev/null 2>&1; }
if ! can_read; then
  termux-setup-storage >/dev/null 2>&1 || true
  for _ in $(seq 1 15); do can_read && break; sleep 1; done
fi

# ------------------------------------------------------------------ 3) code
say "3/6 تنزيل البوت من GitHub"
mkdir -p "$DIR/data/updates"
SHA="$(curl -fsSL -H 'Accept: application/vnd.github.sha' "https://api.github.com/repos/$REPO/commits/main" || true)"
TMP="${TMPDIR:-$PREFIX/tmp}"
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.zip" -o "$TMP/customs-bot.zip" \
  || fail "مقدرتش أنزّل البوت — اتأكد إن الداتا شغالة وجرب تاني."
python - "$TMP/customs-bot.zip" "$DIR" <<'PY'
import sys, zipfile, pathlib
src, dst = sys.argv[1], pathlib.Path(sys.argv[2])
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
        if not rel or info.is_dir() or rel.startswith(("data/", ".env")) or ".." in rel.split("/"):
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(z.read(info))
PY
rm -f "$TMP/customs-bot.zip"
if [ ${#SHA} -eq 40 ]; then
  printf '{"sha": "%s", "message": "تسطيب Termux", "date": ""}\n' "$SHA" > "$DIR/data/updates/installed.json"
fi
cd "$DIR"

# ------------------------------------------------------------------ 4) python libraries
say "4/6 تسطيب مكتبات البوت"
python -m pip install --disable-pip-version-check -q -r requirements.txt \
  || python -m pip install --disable-pip-version-check -q --break-system-packages -r requirements.txt \
  || fail "تسطيب المكتبات فشل — ابعت لـ Claude آخر سطور ظهرت فوق."
python -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')" \
  || fail "قاعدة البيانات في Termux مش بتدعم البحث (FTS5) — ابعت لـ Claude."

# ------------------------------------------------------------------ 5) data
ask_token() {
  touch .env
  if grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env; then
    python tools/setup_wizard.py --check-saved && return 0
  fi
  echo ""
  echo "فحص الوصول لتليجرام:"
  python tools/net_check.py || warn "هكمّل، والبوت هيفضل يحاول يوصل لتليجرام لوحده. لو فضل كده: شغّل تطبيق VPN (زي 1.1.1.1) وشغّل الأمر تاني."
  echo ""
  echo "الصق توكن البوت هنا ودوس Enter"
  echo "(من BotFather ← /mybots ← اختار البوت ← API Token ← انسخه):"
  python tools/setup_wizard.py --token-only < /dev/tty || fail "التوكن ما اتحفظش — شغّل الأمر تاني."
}
say "5/6 بيانات البوت"
if [ -f "$DIR/data/reference.db" ]; then
  echo "البيانات موجودة من قبل — هسيبها."
else
  python - "$DIR/data" <<'PY' 2>/dev/null || true
import glob, os, sys, zipfile
home = os.path.expanduser("~/storage/shared")
places = ["Download", "Download/Telegram", "Telegram/Telegram Documents",
          "Android/media/org.telegram.messenger/Telegram/Telegram Documents"]
found = [f for p in places for f in glob.glob(os.path.join(home, p, "customs-bot-data*.zip"))]
if found:
    src = max(found, key=os.path.getmtime)
    with zipfile.ZipFile(src) as z:
        for name in ("reference.db", "app.db", "config.json"):
            if name in z.namelist():
                with open(os.path.join(sys.argv[1], name), "wb") as f:
                    f.write(z.read(name))
    print("اتنقلت البيانات من:", src)
PY
  if [ ! -f "$DIR/data/reference.db" ]; then
    ask_token
    python tools/fetch_backup.py "$DIR/data" || fail "الملف موصلش. شغّل الأمر تاني وابعت الملف (Forward) للبوت."
  fi
fi
[ -f "$DIR/data/reference.db" ] || fail "ملف reference.db مش موجود."

# ------------------------------------------------------------------ settings for the phone
touch .env
grep -q '^AUTO_UPDATE=' .env || echo 'AUTO_UPDATE=1' >> .env            # يحدّث نفسه من GitHub زي الكمبيوتر
grep -q '^POLL_TIMEOUT=' .env || echo 'POLL_TIMEOUT=30' >> .env         # أقل استهلاك داتا وهو مستني
python bot.py --selftest || fail "اختبار البوت فشل — ابعت لـ Claude آخر سطور ظهرت فوق."

# ------------------------------------------------------------------ start on boot (Termux:Boot)
mkdir -p "$(dirname "$BOOT")"
cat > "$BOOT" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd "$DIR" && exec python bot.py >> data/boot.log 2>&1
EOF
chmod +x "$BOOT"

# ------------------------------------------------------------------ 6) token + start
termux-wake-lock || true          # يمنع أندرويد ينيّم البوت
ask_token
if grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env; then
  say "6/6 تشغيل البوت"
  pkill -f 'customs-bot/bot[.]py|python bot[.]py' 2>/dev/null || true; sleep 2   # إعادة تشغيل بالكود الجديد
  python - <<'PY'
import sys
sys.path.insert(0, "tools")
import setup_wizard as w
w.start_bot()
ok, tail = w.wait_for_start()
print("✅ البوت شغال" if ok else "⚠️ البوت مقامش:\n" + tail[-400:])
PY
else
  say "6/6 توكن البوت — من BotFather: /mybots ← البوت ← API Token ← انسخه والصقه هنا"
  python tools/setup_wizard.py --console
fi

cat <<'TXT'

✅ خلصنا. البوت شغال على التليفون.
عشان يفضل شغال على طول:
  1) الإعدادات ← التطبيقات ← Termux ← البطارية ← «غير مقيّد» (Unrestricted)
  2) افتح تطبيق Termux:Boot مرة واحدة (علشان يشتغل لوحده بعد ما التليفون يعيد التشغيل)
  3) سيب Termux مفتوح في الخلفية — متكتبش exit
جرّب: ابعت /version للبوت في تليجرام.
TXT
