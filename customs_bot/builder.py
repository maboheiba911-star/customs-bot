"""Build / rebuild the tables of the reference database (data/reference.db).

The reference DB holds three independent datasets, each rebuilt on its own:
  * tariff + instruction + tariff_fts   <- official customs.gov.eg records
  * hs + hs_fts                          <- WCO HS nomenclature in English (6-digit)
  * gpc_brick + gpc_fts                  <- GS1 Global Product Classification

Updates are done on a copy of the database which then atomically replaces the
live file, so the bot never reads a half-written database.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .arabic import index_text, normalize
from .taxparse import parse_taxes, summary_rates

_INSTR_RE = re.compile(r"^\s*([^\s\d\-]{1,3}\s?\d{2,6})\s*-\s*(.*)$", re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digits(code: str) -> str:
    return re.sub(r"\D", "", code or "")


def set_meta(conn: sqlite3.Connection, **kv) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    for k, v in kv.items():
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (k, str(v)))


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------
TARIFF_SCHEMA = """
DROP TABLE IF EXISTS tariff;
DROP TABLE IF EXISTS instruction;
DROP TABLE IF EXISTS tariff_fts;
CREATE TABLE tariff(
    code TEXT PRIMARY KEY,
    chapter TEXT, heading TEXT, sub6 TEXT,
    desc_ar TEXT, short_ar TEXT,
    taxes_json TEXT,
    instr_codes TEXT,          -- import instructions (official view trfType=1 "وارد")
    export_codes TEXT,         -- export instructions (official view trfType=2 "صادر")
    duty_pct REAL, duty_text TEXT, vat_pct REAL, vat_text TEXT,
    has_table INTEGER, has_agreements INTEGER
);
CREATE INDEX idx_tariff_sub6 ON tariff(sub6);
CREATE INDEX idx_tariff_heading ON tariff(heading);
CREATE TABLE instruction(code TEXT PRIMARY KEY, text TEXT, kind TEXT);
CREATE VIRTUAL TABLE tariff_fts USING fts5(code UNINDEXED, body, tokenize='unicode61 remove_diacritics 2');
"""


def split_instruction(text: str, fallback_code: str | None) -> tuple[str, str]:
    t = (text or "").strip()
    m = _INSTR_RE.match(t)
    if m:
        return m.group(1).replace(" ", ""), m.group(2).strip()
    return (fallback_code or f"x{abs(hash(t)) % 10**8}"), t


def parse_instructions(texts: list | None, codes: list | None, kind: str) -> list[dict]:
    """Official instruction strings -> [{code, text, kind}] (the code is read from the text itself,
    because the site's InstructionCodes list is not always in the same order)."""
    out: list[dict] = []
    seen: set[str] = set()
    codes = codes or []
    for i, text in enumerate(texts or []):
        if not text or not str(text).strip():
            continue
        icode, body = split_instruction(str(text), codes[i] if i < len(codes) else None)
        if icode not in seen:
            seen.add(icode)
            out.append({"code": icode, "text": body, "kind": kind})
    return out


def write_tariff(conn: sqlite3.Connection, records: list[dict], *, source: str, snapshot: str) -> int:
    conn.executescript(TARIFF_SCHEMA)
    n = 0
    seen: set[str] = set()
    for rec in records:
        code = digits(rec.get("Number") or rec.get("code") or "")
        if len(code) != 10 or code in seen:
            continue
        seen.add(code)
        taxes = [t for t in (rec.get("Taxes") or []) if isinstance(t, str)]
        parsed = parse_taxes(taxes)
        rates = summary_rates(parsed)
        imp = parse_instructions(rec.get("Instructions"), rec.get("InstructionCodes"), "import")
        exp = parse_instructions(rec.get("ExportInstructions"), rec.get("ExportInstructionCodes"), "export")
        for ins in imp + exp:
            conn.execute("INSERT OR IGNORE INTO instruction(code, text, kind) VALUES(?,?,?)",
                         (ins["code"], ins["text"], ins["kind"]))
        desc = (rec.get("Desc") or rec.get("ShortDesc") or "").strip()
        short = (rec.get("ShortDesc") or desc).strip()
        conn.execute(
            """INSERT INTO tariff(code, chapter, heading, sub6, desc_ar, short_ar, taxes_json, instr_codes,
                   export_codes, duty_pct, duty_text, vat_pct, vat_text, has_table, has_agreements)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                code, code[:2], code[:4], code[:6], desc, short,
                json.dumps(taxes, ensure_ascii=False),
                json.dumps([i["code"] for i in imp], ensure_ascii=False),
                json.dumps([i["code"] for i in exp], ensure_ascii=False),
                rates["duty_pct"], rates["duty_text"], rates["vat_pct"], rates["vat_text"],
                rates["has_table"], rates["has_agreements"],
            ),
        )
        conn.execute("INSERT INTO tariff_fts(code, body) VALUES(?, ?)", (code, index_text(desc + " " + short)))
        n += 1
    set_meta(conn, tariff_source=source, tariff_snapshot=snapshot, tariff_count=n, tariff_built_at=now_iso())
    return n


def load_raw_tariff(path: Path) -> tuple[list[dict], dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("records") or [], data.get("meta") or {}
    return data, {}


# ---------------------------------------------------------------------------
# HS nomenclature (English)
# ---------------------------------------------------------------------------
HS_SCHEMA = """
DROP TABLE IF EXISTS hs;
DROP TABLE IF EXISTS hs_fts;
CREATE TABLE hs(code TEXT PRIMARY KEY, level INTEGER, parent TEXT, desc_en TEXT);
CREATE VIRTUAL TABLE hs_fts USING fts5(code UNINDEXED, body, tokenize='unicode61 remove_diacritics 2');
"""


def write_hs(conn: sqlite3.Connection, csv_path: Path, *, source: str) -> int:
    conn.executescript(HS_SCHEMA)
    n = 0
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = digits(r.get("hscode", ""))
            if len(code) not in (2, 4, 6):
                continue
            desc = (r.get("description") or "").strip()
            conn.execute(
                "INSERT OR REPLACE INTO hs(code, level, parent, desc_en) VALUES(?,?,?,?)",
                (code, len(code), r.get("parent"), desc),
            )
            if len(code) in (4, 6):
                conn.execute("INSERT INTO hs_fts(code, body) VALUES(?, ?)", (code, index_text(desc)))
            n += 1
    set_meta(conn, hs_source=source, hs_count=n)
    return n


# ---------------------------------------------------------------------------
# GS1 GPC
# ---------------------------------------------------------------------------
GPC_SCHEMA = """
DROP TABLE IF EXISTS gpc_brick;
DROP TABLE IF EXISTS gpc_fts;
CREATE TABLE gpc_brick(
    code TEXT PRIMARY KEY, title TEXT, definition TEXT, excludes TEXT,
    class_code TEXT, class_title TEXT, family_code TEXT, family_title TEXT,
    segment_code TEXT, segment_title TEXT, title_ar TEXT, path_ar TEXT
);
CREATE VIRTUAL TABLE gpc_fts USING fts5(code UNINDEXED, title, path, definition, tokenize='unicode61 remove_diacritics 2');
"""


def _bricks_from_json(data: dict) -> tuple[list[dict], str, str]:
    lang = (data.get("LanguageCode") or "EN").upper()
    version = str(data.get("DateUtc") or "")
    out: list[dict] = []

    def walk(node: dict, path: list[dict]) -> None:
        level = node.get("Level")
        here = path + [node]
        if level == 4:
            seg, fam, cls = (here + [{}, {}, {}])[:3]
            out.append({
                "code": str(node.get("Code")), "title": node.get("Title") or "",
                "definition": node.get("Definition") or "", "excludes": node.get("DefinitionExcludes") or "",
                "class_code": str(cls.get("Code", "")), "class_title": cls.get("Title", ""),
                "family_code": str(fam.get("Code", "")), "family_title": fam.get("Title", ""),
                "segment_code": str(seg.get("Code", "")), "segment_title": seg.get("Title", ""),
                "active": node.get("Active", True),
            })
            return
        for child in node.get("Childs") or []:
            walk(child, here)

    for seg in data.get("Schema") or []:
        walk(seg, [])
    return out, lang, version


def _bricks_from_xml(path: Path) -> tuple[list[dict], str, str]:
    out: list[dict] = []
    tree = ET.parse(path)
    root = tree.getroot()

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    lang = "EN"
    version = ""
    for el in root.iter():
        t = local(el.tag)
        if t == "CreationDateAndTime" and not version:
            version = (el.text or "")[:10]
        if t == "segment":
            seg = el
            for fam in seg:
                if local(fam.tag) != "family":
                    continue
                for cls in fam:
                    if local(cls.tag) != "class":
                        continue
                    for brick in cls:
                        if local(brick.tag) != "brick":
                            continue
                        out.append({
                            "code": brick.get("code", ""), "title": brick.get("text", ""),
                            "definition": brick.get("definition", ""), "excludes": "",
                            "class_code": cls.get("code", ""), "class_title": cls.get("text", ""),
                            "family_code": fam.get("code", ""), "family_title": fam.get("text", ""),
                            "segment_code": seg.get("code", ""), "segment_title": seg.get("text", ""),
                            "active": True,
                        })
    return out, lang, version


def read_gpc_file(path: Path) -> tuple[list[dict], str, str]:
    path = Path(path)
    head = path.read_bytes()[:200].lstrip(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<"):
        bricks, lang, _ = _bricks_from_xml(path)
        m = re.search(r"\b([A-Z]{2})\b(?!.*\b[A-Z]{2}\b)", path.stem)
        return bricks, (m.group(1) if m else lang), path.stem   # header dates in GS1 XML are not the release
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return _bricks_from_json(data)


def write_gpc(conn: sqlite3.Connection, bricks: list[dict], *, version: str, source: str) -> int:
    conn.executescript(GPC_SCHEMA)
    n = 0
    for b in bricks:
        if not b.get("code") or b.get("active") is False:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO gpc_brick(code, title, definition, excludes, class_code, class_title,
                   family_code, family_title, segment_code, segment_title)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (b["code"], b["title"], b["definition"], b["excludes"], b["class_code"], b["class_title"],
             b["family_code"], b["family_title"], b["segment_code"], b["segment_title"]),
        )
        path = " ".join([b["segment_title"], b["family_title"], b["class_title"]])
        conn.execute(
            "INSERT INTO gpc_fts(code, title, path, definition) VALUES(?,?,?,?)",
            (b["code"], index_text(b["title"]), index_text(path), index_text(b["definition"])),
        )
        n += 1
    set_meta(conn, gpc_version=version, gpc_count=n, gpc_source=source)
    return n


def add_gpc_translation(conn: sqlite3.Connection, bricks: list[dict], lang: str) -> int:
    """Attach a non-English GPC file (same codes) as extra searchable titles."""
    n = 0
    for b in bricks:
        path = " / ".join(x for x in [b["segment_title"], b["family_title"], b["class_title"]] if x)
        cur = conn.execute(
            "UPDATE gpc_brick SET title_ar=?, path_ar=? WHERE code=?", (b["title"], path, b["code"])
        )
        if cur.rowcount:
            row = conn.execute("SELECT title, definition FROM gpc_brick WHERE code=?", (b["code"],)).fetchone()
            conn.execute("DELETE FROM gpc_fts WHERE code=?", (b["code"],))
            conn.execute(
                "INSERT INTO gpc_fts(code, title, path, definition) VALUES(?,?,?,?)",
                (b["code"], index_text(row[0] + " " + b["title"]), index_text(path), index_text(row[1])),
            )
            n += 1
    set_meta(conn, **{f"gpc_lang_{lang.lower()}": n})
    return n


# ---------------------------------------------------------------------------
# Safe, atomic update of the live database file
# ---------------------------------------------------------------------------
class AtomicDB:
    """Copy the live DB, let the caller modify the copy, then swap it in."""

    def __init__(self, live_path: Path, before_swap=None):
        self.live_path = Path(live_path)
        self.before_swap = before_swap      # e.g. close readers (needed on Windows)
        self.tmp_path: Path | None = None
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.live_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="reference-", suffix=".db", dir=self.live_path.parent)
        os.close(fd)
        self.tmp_path = Path(tmp)
        if self.live_path.exists():
            src = sqlite3.connect(self.live_path)
            dst = sqlite3.connect(self.tmp_path)
            src.backup(dst)
            src.close()
            dst.close()
        self.conn = sqlite3.connect(self.tmp_path)
        self.conn.execute("PRAGMA journal_mode=DELETE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self.conn is not None and self.tmp_path is not None
        if exc_type is None:
            self.conn.commit()
            self.conn.execute("VACUUM")
            self.conn.close()
            if self.live_path.exists():
                shutil.copy2(self.live_path, self.live_path.with_suffix(".db.bak"))
            os.chmod(self.tmp_path, 0o644)
            if self.before_swap:
                self.before_swap()
            os.replace(self.tmp_path, self.live_path)
        else:
            self.conn.close()
            self.tmp_path.unlink(missing_ok=True)


def validate_reference(path: Path) -> dict:
    """Sanity check of a reference DB file (used before accepting an upload)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        info = {}
        for table in ("tariff", "gpc_brick", "hs"):
            try:
                info[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                info[table] = 0
        return info
    finally:
        conn.close()


def normalize_query_code(text: str) -> str:
    return digits(normalize(text))
