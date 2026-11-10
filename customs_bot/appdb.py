"""Bot state: users, usage log, exchange rates, live-lookup cache, admin overrides."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY, name TEXT, username TEXT,
    first_seen TEXT, last_seen TEXT, requests INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events(ts TEXT, user_id INTEGER, kind TEXT, query TEXT);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS fx(currency TEXT PRIMARY KEY, rate REAL, updated_at TEXT, updated_by INTEGER);
CREATE TABLE IF NOT EXISTS fx_auto(currency TEXT PRIMARY KEY, buy REAL, sell REAL, rate_date TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS live_cache(code TEXT PRIMARY KEY, payload TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS dev_fee(prefix TEXT PRIMARY KEY, pct REAL, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class AppDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    # ------------------------------------------------------------- usage log
    def touch_user(self, user_id: int, name: str, username: str | None) -> None:
        now = iso()
        self._exec(
            """INSERT INTO users(id, name, username, first_seen, last_seen, requests) VALUES(?,?,?,?,?,1)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username,
               last_seen=excluded.last_seen, requests=requests+1""",
            (user_id, name, username, now, now),
        )

    def log(self, user_id: int, kind: str, query: str) -> None:
        self._exec("INSERT INTO events(ts, user_id, kind, query) VALUES(?,?,?,?)", (iso(), user_id, kind, query[:200]))

    def stats(self) -> dict:
        since = iso(utcnow() - timedelta(days=7))
        return {
            "users": self._all("SELECT COUNT(*) n FROM users")[0]["n"],
            "users_7d": self._all("SELECT COUNT(*) n FROM users WHERE last_seen>=?", (since,))[0]["n"],
            "events_7d": self._all("SELECT COUNT(*) n FROM events WHERE ts>=?", (since,))[0]["n"],
            "by_kind_7d": {r["kind"]: r["n"] for r in self._all(
                "SELECT kind, COUNT(*) n FROM events WHERE ts>=? GROUP BY kind", (since,))},
            "top_queries_7d": [(r["query"], r["n"]) for r in self._all(
                "SELECT query, COUNT(*) n FROM events WHERE ts>=? AND kind IN ('search','code','calc') "
                "GROUP BY query ORDER BY n DESC LIMIT 5", (since,))],
        }

    # --------------------------------------------------------------- FX rates
    def set_fx(self, currency: str, rate: float, by: int | None = None) -> None:
        self._exec(
            "INSERT OR REPLACE INTO fx(currency, rate, updated_at, updated_by) VALUES(?,?,?,?)",
            (currency.upper(), rate, iso(), by),
        )

    def get_fx(self, currency: str) -> tuple[float, str] | None:
        rows = self._all("SELECT rate, updated_at FROM fx WHERE currency=?", (currency.upper(),))
        return (rows[0]["rate"], rows[0]["updated_at"]) if rows else None

    def all_fx(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM fx ORDER BY currency")

    def del_fx(self, currency: str) -> None:
        self._exec("DELETE FROM fx WHERE currency=?", (currency.upper(),))

    def put_auto_fx(self, rate_date: str | None, rates: dict[str, tuple[float, float]]) -> None:
        now = iso()
        with self._lock:
            for cur, (buy, sell) in rates.items():
                self.conn.execute(
                    "INSERT OR REPLACE INTO fx_auto(currency, buy, sell, rate_date, fetched_at) VALUES(?,?,?,?,?)",
                    (cur, buy, sell, rate_date, now))
            self.conn.commit()

    def get_auto_fx(self, currency: str) -> sqlite3.Row | None:
        rows = self._all("SELECT * FROM fx_auto WHERE currency=?", (currency.upper(),))
        return rows[0] if rows else None

    def all_auto_fx(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM fx_auto ORDER BY currency")

    def auto_fx_age_hours(self) -> float | None:
        rows = self._all("SELECT MAX(fetched_at) AS t FROM fx_auto")
        t = parse_iso(rows[0]["t"]) if rows else None
        return None if t is None else (utcnow() - t).total_seconds() / 3600

    # ------------------------------------------------------- live data cache
    def cache_get(self, code: str, max_age_hours: int) -> tuple[dict, str] | None:
        rows = self._all("SELECT payload, fetched_at FROM live_cache WHERE code=?", (code,))
        if not rows:
            return None
        fetched = parse_iso(rows[0]["fetched_at"])
        if fetched is None or utcnow() - fetched > timedelta(hours=max_age_hours):
            return None
        return json.loads(rows[0]["payload"]), rows[0]["fetched_at"]

    def cache_put(self, code: str, payload: dict) -> str:
        ts = iso()
        self._exec(
            "INSERT OR REPLACE INTO live_cache(code, payload, fetched_at) VALUES(?,?,?)",
            (code, json.dumps(payload, ensure_ascii=False), ts),
        )
        return ts

    def cache_clear(self) -> None:
        self._exec("DELETE FROM live_cache")

    # ------------------------------------------------ development-fee overrides
    def set_dev_fee(self, prefix: str, pct: float, note: str = "") -> None:
        self._exec(
            "INSERT OR REPLACE INTO dev_fee(prefix, pct, note, updated_at) VALUES(?,?,?,?)",
            (prefix, pct, note, iso()),
        )

    def del_dev_fee(self, prefix: str) -> None:
        self._exec("DELETE FROM dev_fee WHERE prefix=?", (prefix,))

    def dev_fees(self) -> dict[str, float]:
        return {r["prefix"]: r["pct"] for r in self._all("SELECT prefix, pct FROM dev_fee")}

    # -------------------------------------------------------------- key/value
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        rows = self._all("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def kv_set(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv(key, value) VALUES(?,?)", (key, value))
