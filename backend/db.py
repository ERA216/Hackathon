"""SQLite: история записей смен, инциденты, рекомендации, внедрённые меры, журнал вызовов ИИ."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, line TEXT, hour_cost_kzt INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shift_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    author_role TEXT DEFAULT 'Мастер смены',
    status TEXT NOT NULL DEFAULT 'processed',   -- processed | pending (ИИ был недоступен)
    mode TEXT,                                  -- ai | rules
    is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL REFERENCES shift_logs(id),
    occurred_at TEXT NOT NULL,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    failure_type TEXT NOT NULL,
    downtime_min INTEGER NOT NULL,
    parts_cost_kzt INTEGER NOT NULL DEFAULT 0,
    parts_json TEXT DEFAULT '[]',
    cause TEXT, action TEXT,
    confidence REAL, needs_review INTEGER DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL, equipment_id TEXT NOT NULL, failure_type TEXT NOT NULL,
    payload_json TEXT NOT NULL, ai_text TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS applied_measures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL, measure_id TEXT NOT NULL, applied_at TEXT NOT NULL,
    is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, purpose TEXT, provider TEXT, model TEXT,
    latency_ms INTEGER, ok INTEGER, attempts INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_inc_eq ON incidents(equipment_id, occurred_at);
"""

# Стоимость одной единицы запчасти для расчёта потерь (ДЕМО-справочник).
PART_PRICES = {"ремень": 8000, "датчик": 15000, "подшипник": 12000, "предохранитель": 1500, "контактор": 9000, "масло": 3000, "смазка": 3000}


def part_price(name: str) -> int:
    n = (name or "").lower()
    for key, price in PART_PRICES.items():
        if key in n:
            return price
    return 0


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(str(path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path=None) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)
        for e in load_equipment():
            c.execute("INSERT OR IGNORE INTO equipment(id, name, line, hour_cost_kzt) VALUES (?,?,?,?)",
                      (e["id"], e["name"], e["line"], e["hour_cost_kzt"]))


def load_equipment() -> list[dict]:
    return json.loads((config.DATA_DIR / "equipment.json").read_text(encoding="utf-8"))


def load_measures() -> list[dict]:
    return json.loads((config.DATA_DIR / "measures.json").read_text(encoding="utf-8"))


def equipment_map() -> dict:
    return {e["id"]: e for e in load_equipment()}


def is_empty(path=None) -> bool:
    with connect(path) as c:
        return c.execute("SELECT COUNT(*) FROM shift_logs").fetchone()[0] == 0


def add_shift_log(raw_text: str, status: str, mode: str, created_at: Optional[str] = None, is_demo: bool = False, conn=None) -> int:
    sql = "INSERT INTO shift_logs(created_at, raw_text, status, mode, is_demo) VALUES (?,?,?,?,?)"
    args = (created_at or now_iso(), raw_text, status, mode, int(is_demo))
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    with connect() as c:
        return c.execute(sql, args).lastrowid


def add_incident(log_id: int, inc: dict, occurred_at: Optional[str] = None, is_demo: bool = False, conn=None) -> int:
    parts = inc.get("parts") or []
    parts_cost = inc.get("parts_cost_kzt")
    if parts_cost is None:
        parts_cost = sum(part_price(p.get("name", "")) * int(p.get("qty", 1)) for p in parts)
    sql = """INSERT INTO incidents(log_id, occurred_at, equipment_id, failure_type, downtime_min, parts_cost_kzt,
             parts_json, cause, action, confidence, needs_review, is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    args = (log_id, occurred_at or now_iso(), inc["equipment_id"], inc["failure_type"], int(inc["downtime_min"]),
            int(parts_cost), json.dumps(parts, ensure_ascii=False), inc.get("cause"), inc.get("action"),
            inc.get("confidence"), int(bool(inc.get("needs_review"))), int(is_demo))
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    with connect() as c:
        return c.execute(sql, args).lastrowid


def incidents(equipment_id: Optional[str] = None) -> list[dict]:
    q = """SELECT i.*, s.raw_text, e.name AS equipment_name, e.hour_cost_kzt
           FROM incidents i JOIN shift_logs s ON s.id = i.log_id JOIN equipment e ON e.id = i.equipment_id"""
    args: tuple = ()
    if equipment_id:
        q += " WHERE i.equipment_id = ?"
        args = (equipment_id,)
    with connect() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY i.occurred_at", args)]


def shift_logs(limit: int = 200) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM shift_logs ORDER BY created_at DESC LIMIT ?", (limit,))]


def save_recommendation(equipment_id: str, failure_type: str, payload: dict, ai_text: str, mode: str) -> int:
    with connect() as c:
        return c.execute("INSERT INTO recommendations(created_at, equipment_id, failure_type, payload_json, ai_text, mode) VALUES (?,?,?,?,?,?)",
                         (now_iso(), equipment_id, failure_type, json.dumps(payload, ensure_ascii=False), ai_text, mode)).lastrowid


def apply_measure(equipment_id: str, measure_id: str, applied_at: Optional[str] = None, is_demo: bool = False, conn=None) -> int:
    sql = "INSERT INTO applied_measures(equipment_id, measure_id, applied_at, is_demo) VALUES (?,?,?,?)"
    args = (equipment_id, measure_id, applied_at or now_iso(), int(is_demo))
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    with connect() as c:
        return c.execute(sql, args).lastrowid


def unapply_measure(equipment_id: str, measure_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM applied_measures WHERE equipment_id=? AND measure_id=? AND is_demo=0", (equipment_id, measure_id))


def applied_measures() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM applied_measures ORDER BY applied_at")]


def log_llm_call(purpose: str, provider: str, model: str, latency_ms: int, ok: bool, attempts: int, error: Optional[str]) -> None:
    try:
        with connect() as c:
            c.execute("INSERT INTO llm_calls(ts, purpose, provider, model, latency_ms, ok, attempts, error) VALUES (?,?,?,?,?,?,?,?)",
                      (now_iso(), purpose, provider, model, latency_ms, int(ok), attempts, (error or "")[:500]))
    except sqlite3.Error:
        pass  # журнал не должен ронять основной сценарий


def llm_stats() -> dict:
    with connect() as c:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok, COALESCE(AVG(latency_ms),0) avg_ms FROM llm_calls").fetchone()
        return {"calls": r["n"], "ok": r["ok"], "avg_latency_ms": int(r["avg_ms"])}
