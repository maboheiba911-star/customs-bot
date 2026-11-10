"""Read-side access to data/reference.db (tariff, HS English, GS1 GPC)."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from .arabic import expand_query, fts_query, is_arabic, normalize
from .taxparse import ParsedTaxes, parse_taxes


@dataclass
class TariffItem:
    code: str
    desc_ar: str
    short_ar: str
    taxes: list[str]
    instr_codes: list[str]                       # import instructions
    duty_text: str | None = None
    vat_text: str | None = None
    source: str = "local"            # local | live
    fetched_at: str | None = None
    export_codes: list[str] = field(default_factory=list)
    _parsed: ParsedTaxes | None = field(default=None, repr=False)

    @property
    def parsed(self) -> ParsedTaxes:
        if self._parsed is None:
            self._parsed = parse_taxes(self.taxes)
        return self._parsed

    @property
    def sub6(self) -> str:
        return self.code[:6]


def fmt_code(code: str) -> str:
    return code


class ReferenceDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.conn: sqlite3.Connection | None = None
        self.open()

    # ------------------------------------------------------------------ infra
    def open(self) -> None:
        with self._lock:
            if self.conn is not None:
                self.conn.close()
            if not self.path.exists():
                raise FileNotFoundError(f"reference database not found: {self.path}")
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            try:  # parent/heading lines carry no rates; keep them out of search results
                self._parents = {
                    r[0] for r in self.conn.execute("SELECT code FROM tariff WHERE taxes_json='[]'")
                }
            except sqlite3.Error:
                self._parents = set()

    reload = open

    def close(self) -> None:
        with self._lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def rates_snapshot(self) -> dict[str, tuple]:
        return {r["code"]: (r["duty_text"], r["vat_text"]) for r in
                self._q("SELECT code, duty_text, vat_text FROM tariff")}

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            assert self.conn is not None
            return self.conn.execute(sql, args).fetchall()

    def meta(self) -> dict:
        try:
            return {r["key"]: r["value"] for r in self._q("SELECT key, value FROM meta")}
        except sqlite3.Error:
            return {}

    def counts(self) -> dict:
        out = {}
        for t in ("tariff", "gpc_brick", "hs", "instruction"):
            try:
                out[t] = self._q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            except sqlite3.Error:
                out[t] = 0
        return out

    # ----------------------------------------------------------------- tariff
    def _item(self, r: sqlite3.Row) -> TariffItem:
        keys = r.keys()
        return TariffItem(
            code=r["code"], desc_ar=r["desc_ar"] or "", short_ar=r["short_ar"] or "",
            taxes=json.loads(r["taxes_json"] or "[]"), instr_codes=json.loads(r["instr_codes"] or "[]"),
            duty_text=r["duty_text"], vat_text=r["vat_text"],
            export_codes=json.loads(r["export_codes"] or "[]") if "export_codes" in keys else [],
        )

    def get(self, code: str) -> TariffItem | None:
        rows = self._q("SELECT * FROM tariff WHERE code=?", (code,))
        return self._item(rows[0]) if rows else None

    def by_prefix(self, prefix: str, limit: int = 10, offset: int = 0) -> tuple[list[TariffItem], int]:
        total = self._q("SELECT COUNT(*) AS n FROM tariff WHERE code LIKE ?", (prefix + "%",))[0]["n"]
        rows = self._q(
            "SELECT * FROM tariff WHERE code LIKE ? ORDER BY code LIMIT ? OFFSET ?", (prefix + "%", limit, offset)
        )
        return [self._item(r) for r in rows], total

    def is_parent(self, code: str) -> bool:
        return code in self._parents

    def children(self, code: str, limit: int = 12) -> list[TariffItem]:
        """Detail lines under a parent line (same 8- or 6-digit stem)."""
        for n in (8, 6):
            rows = self._q(
                "SELECT * FROM tariff WHERE code LIKE ? AND code<>? AND taxes_json<>'[]' ORDER BY code LIMIT ?",
                (code[:n] + "%", code, limit),
            )
            if rows:
                return [self._item(r) for r in rows]
        return []

    def siblings(self, code: str, limit: int = 4) -> list[TariffItem]:
        rows = self._q(
            "SELECT * FROM tariff WHERE sub6=? AND code<>? ORDER BY code LIMIT ?", (code[:6], code, limit)
        )
        if len(rows) < limit:
            more = self._q(
                "SELECT * FROM tariff WHERE heading=? AND sub6<>? ORDER BY code LIMIT ?",
                (code[:4], code[:6], limit - len(rows)),
            )
            rows = list(rows) + list(more)
        return [self._item(r) for r in rows]

    def instructions(self, codes: list[str]) -> list[dict]:
        if not codes:
            return []
        marks = ",".join("?" * len(codes))
        rows = {r["code"]: r for r in self._q(f"SELECT * FROM instruction WHERE code IN ({marks})", tuple(codes))}
        return [dict(rows[c]) for c in codes if c in rows]

    def hs_desc(self, code: str) -> str | None:
        for n in (6, 4, 2):
            rows = self._q("SELECT desc_en FROM hs WHERE code=?", (code[:n],))
            if rows:
                return rows[0]["desc_en"]
        return None

    @staticmethod
    def _coverage(body: str, groups) -> int:
        words = body.split()
        n = 0
        for alts in groups:
            if any(all(any(w.startswith(s) for w in words) for s in stems) for stems in alts):
                n += 1
        return n

    def _fts(self, table: str, groups, limit: int) -> list[sqlite3.Row]:
        """AND search first; if nothing, OR search keeping only the best-covered rows."""
        if not groups:
            return []
        expr = fts_query(groups, mode="AND")
        if not expr:
            return []
        try:
            rows = self._q(
                f"SELECT code, bm25({table}) AS score FROM {table} WHERE {table} MATCH ? ORDER BY score LIMIT ?",
                (expr, limit),
            )
        except sqlite3.Error:
            rows = []
        if rows or len(groups) == 1:
            return rows
        try:
            rows = self._q(
                f"SELECT code, body, bm25({table}) AS score FROM {table} WHERE {table} MATCH ? "
                f"ORDER BY score LIMIT 400", (fts_query(groups, mode="OR"),)
            )
        except sqlite3.Error:
            return []
        if not rows:
            return []
        cov = [(self._coverage(r["body"], groups), r) for r in rows]
        best = max(c for c, _ in cov)
        if best * 2 < len(groups):      # fewer than half of the words matched -> not useful
            return []
        return [r for c, r in cov if c == best][:limit]

    def _descs(self, codes: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i: i + 400]
            marks = ",".join("?" * len(chunk))
            for r in self._q(f"SELECT code, desc_ar FROM tariff WHERE code IN ({marks})", tuple(chunk)):
                out[r["code"]] = r["desc_ar"] or ""
        return out

    def search(self, query: str, limit: int = 8, offset: int = 0) -> tuple[list[TariffItem], int]:
        """Search the tariff by Arabic or English text. Returns (page, total_found)."""
        codes = self._search_codes(query)
        page = codes[offset: offset + limit]
        items = [self.get(c) for c in page]
        return [i for i in items if i], len(codes)

    def _search_codes(self, query: str, cap: int = 120) -> list[str]:
        groups = expand_query(query)
        if not groups:
            return []
        qn = normalize(query)
        ranked: list[tuple[float, str]] = []
        if is_arabic(query):
            rows = self._fts("tariff_fts", groups, 300)
            descs = self._descs([r["code"] for r in rows])
            for i, r in enumerate(rows):
                dn = normalize(descs.get(r["code"], ""))
                sim = fuzz.token_set_ratio(qn, dn) / 100.0
                head = 2.0 if dn.startswith(qn) else (1.0 if qn in dn[: len(qn) + 25] else 0.0)
                ranked.append((-(r["score"]) + 4.0 * sim + head - 0.002 * i, r["code"]))
        else:
            hs_rows = self._fts("hs_fts", groups, 60)
            seen: set[str] = set()
            # 6-digit subheadings first, whole headings (4-digit) only after them
            hs_rows = sorted(hs_rows, key=lambda r: (len(r["code"]) != 6, r["score"]))
            for i, r in enumerate(hs_rows):
                hs_code = r["code"]
                col = "sub6" if len(hs_code) == 6 else "heading"
                for t in self._q(f"SELECT code FROM tariff WHERE {col}=? ORDER BY code", (hs_code,)):
                    if t["code"] not in seen:
                        seen.add(t["code"])
                        ranked.append((1000.0 - i, t["code"]))
            # Latin words inside Arabic descriptions, e.g. [Phaseolus vulgaris]
            for i, r in enumerate(self._fts("tariff_fts", groups, 60)):
                if r["code"] not in seen:
                    ranked.append((500.0 - i, r["code"]))
        ranked.sort(key=lambda x: -x[0])
        out: list[str] = []
        for _, c in ranked:
            if c not in out and c not in self._parents:
                out.append(c)
            if len(out) >= cap:
                break
        return out

    # -------------------------------------------------------------------- GPC
    def gpc_get(self, code: str) -> dict | None:
        rows = self._q("SELECT * FROM gpc_brick WHERE code=?", (code,))
        return dict(rows[0]) if rows else None

    def gpc_search(self, query_en: str, limit: int = 5, offset: int = 0) -> tuple[list[dict], int]:
        """Rank bricks by how much of the query hits the title, then the class path, then the definition."""
        groups = expand_query(query_en)
        if not groups:
            return [], 0
        try:
            rows = self._q(
                "SELECT f.code, f.title, f.path, f.definition, b.title AS raw_title, "
                "bm25(gpc_fts, 0.0, 10.0, 2.5, 0.6) AS score "
                "FROM gpc_fts f JOIN gpc_brick b ON b.code = f.code "
                "WHERE gpc_fts MATCH ? ORDER BY score LIMIT 400", (fts_query(groups, mode="OR"),)
            )
        except sqlite3.Error:
            rows = []
        n = len(groups)
        qn = normalize(query_en)
        scored = []
        for r in rows:
            ct = self._coverage(r["title"], groups) / n
            cp = self._coverage(r["path"], groups) / n
            cd = self._coverage(r["definition"], groups) / n
            best = max(ct, cp, cd, (self._coverage(" ".join([r["title"], r["path"]]), groups) / n))
            if best < 0.5 and n > 1:
                continue
            sim = fuzz.token_set_ratio(qn, normalize(r["raw_title"])) / 100.0
            score = 4.0 * ct + 1.5 * cp + 1.0 * cd + 2.0 * sim - 0.01 * r["score"]   # bm25: lower is better
            scored.append((score, r["code"]))
        scored.sort(key=lambda x: -x[0])
        codes = [c for _, c in scored]
        page = codes[offset: offset + limit]
        return [self.gpc_get(c) for c in page if c], len(codes)

    @staticmethod
    def looks_like_brick(text: str) -> bool:
        # GPC bricks are 1000xxxx; no HS heading starts with 1000, so no clash with HS codes.
        return bool(re.fullmatch(r"1000\d{4}", text or ""))
