"""SQLite: история записей смен, инциденты, рекомендации, внедрённые меры, журнал вызовов ИИ."""
import json
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE,
    city TEXT, created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL REFERENCES companies(id),
    login TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin', 'mechanic', 'worker')),
    pin_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(company_id, login)
);
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, author TEXT,
    equipment_id TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('ok', 'remark', 'problem')), note TEXT, incident_id INTEGER, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, company_name TEXT NOT NULL,
    city TEXT, note TEXT, company_id INTEGER
);
"""
SCOPED_TABLES = ("shift_logs", "incidents", "recommendations", "applied_measures")
INCIDENT_STATUS_COLUMNS = {"status": "TEXT NOT NULL DEFAULT 'new'", "assignee": "TEXT", "taken_at": "TEXT", "resolved_at": "TEXT",
                           "resolution": "TEXT", "approved_by": "TEXT", "escalation_note": "TEXT"}
# путь заявки: сотрудник сообщил → администратор одобрил → сотрудники решают с ИИ → (нужна помощь → администратор) → решена
STATUS_LABELS = {"new": "Ждёт одобрения", "approved": "Решают сотрудники", "escalated": "Нужна помощь администратора", "resolved": "Решена"}
USERS_REBUILD = """
ALTER TABLE users RENAME TO users_old;
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL REFERENCES companies(id),
    login TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin', 'mechanic', 'worker')),
    pin_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(company_id, login)
);
INSERT INTO users SELECT id, company_id, login, display_name, role, pin_hash, created_at FROM users_old;
DROP TABLE users_old;
"""

_ctx = threading.local()


def set_company(company_id: int) -> None:
    """Компания текущего пользователя. Все чтения и записи данных ограничены ею."""
    _ctx.company_id = int(company_id)


def current_company() -> int:
    return getattr(_ctx, "company_id", 1)

# Стоимость одной единицы запчасти для расчёта потерь (ДЕМО-справочник).
PART_PRICES = {"сальник": 4000, "ремень": 8000, "датчик": 15000, "подшипник": 12000, "предохранитель": 1500, "контактор": 9000, "масло": 3000, "смазка": 3000}


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
        for t in SCOPED_TABLES:  # миграция старых баз: добавить company_id
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            if "company_id" not in cols:
                c.execute(f"ALTER TABLE {t} ADD COLUMN company_id INTEGER NOT NULL DEFAULT 1")
        inc_cols = [r[1] for r in c.execute("PRAGMA table_info(incidents)")]
        for col, ddl in INCIDENT_STATUS_COLUMNS.items():  # жизненный цикл заявки
            if col not in inc_cols:
                c.execute(f"ALTER TABLE incidents ADD COLUMN {col} {ddl}")
        if "status" not in inc_cols and inc_cols:  # прошлые записи старой базы считаем закрытыми
            c.execute("UPDATE incidents SET status='resolved', resolution=COALESCE(action, 'Устранено') WHERE date(occurred_at) < date('now','localtime')")
        c.execute("UPDATE incidents SET status='approved' WHERE status='in_progress'")  # статус прошлой версии
        c.execute("UPDATE users SET role='worker' WHERE role='mechanic'")  # в продукте две роли
        users_sql = c.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
        if "mechanic" not in users_sql:  # старая база: роль «механик» не разрешена — пересоздаём таблицу
            c.executescript(USERS_REBUILD)
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
        return c.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0


def add_shift_log(raw_text: str, status: str, mode: str, created_at: Optional[str] = None, is_demo: bool = False, conn=None, author: Optional[str] = None) -> int:
    sql = "INSERT INTO shift_logs(created_at, raw_text, status, mode, is_demo, author_role, company_id) VALUES (?,?,?,?,?,?,?)"
    args = (created_at or now_iso(), raw_text, status, mode, int(is_demo), author or "Мастер смены", current_company())
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
             parts_json, cause, action, confidence, needs_review, is_demo, company_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    args = (log_id, occurred_at or now_iso(), inc["equipment_id"], inc["failure_type"], int(inc["downtime_min"]),
            int(parts_cost), json.dumps(parts, ensure_ascii=False), inc.get("cause"), inc.get("action"),
            inc.get("confidence"), int(bool(inc.get("needs_review"))), int(is_demo), current_company())
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    with connect() as c:
        return c.execute(sql, args).lastrowid


def incidents(equipment_id: Optional[str] = None) -> list[dict]:
    q = """SELECT i.*, s.raw_text, s.author_role, e.name AS equipment_name, e.hour_cost_kzt
           FROM incidents i JOIN shift_logs s ON s.id = i.log_id JOIN equipment e ON e.id = i.equipment_id
           WHERE i.company_id = ?"""
    args: tuple = (current_company(),)
    if equipment_id:
        q += " AND i.equipment_id = ?"
        args += (equipment_id,)
    with connect() as c:
        rows = [dict(r) for r in c.execute(q + " ORDER BY i.occurred_at", args)]
    for r in rows:
        r["raw_text"] = (r.get("raw_text") or "").replace("ДЕМО · ", "")
    return rows


def shift_logs(limit: int = 200) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM shift_logs WHERE company_id=? ORDER BY created_at DESC LIMIT ?", (current_company(), limit))]


def save_recommendation(equipment_id: str, failure_type: str, payload: dict, ai_text: str, mode: str) -> int:
    with connect() as c:
        return c.execute("INSERT INTO recommendations(created_at, equipment_id, failure_type, payload_json, ai_text, mode, company_id) VALUES (?,?,?,?,?,?,?)",
                         (now_iso(), equipment_id, failure_type, json.dumps(payload, ensure_ascii=False), ai_text, mode, current_company())).lastrowid


def apply_measure(equipment_id: str, measure_id: str, applied_at: Optional[str] = None, is_demo: bool = False, conn=None) -> int:
    sql = "INSERT INTO applied_measures(equipment_id, measure_id, applied_at, is_demo, company_id) VALUES (?,?,?,?,?)"
    args = (equipment_id, measure_id, applied_at or now_iso(), int(is_demo), current_company())
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    with connect() as c:
        return c.execute(sql, args).lastrowid


def unapply_measure(equipment_id: str, measure_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM applied_measures WHERE equipment_id=? AND measure_id=? AND is_demo=0 AND company_id=?", (equipment_id, measure_id, current_company()))


def applied_measures() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM applied_measures WHERE company_id=? ORDER BY applied_at", (current_company(),))]


def add_inspection(equipment_id: str, state: str, note: str, author: str, incident_id: Optional[int] = None,
                   created_at: Optional[str] = None, is_demo: bool = False, conn=None) -> int:
    sql = "INSERT INTO inspections(company_id, created_at, author, equipment_id, state, note, incident_id, is_demo) VALUES (?,?,?,?,?,?,?,?)"
    args = (current_company(), created_at or now_iso(), author, equipment_id, state, note, incident_id, int(is_demo))
    if conn is not None:
        return conn.execute(sql, args).lastrowid
    with connect() as c:
        return c.execute(sql, args).lastrowid


def inspections(limit: int = 500) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM inspections WHERE company_id=? ORDER BY created_at DESC LIMIT ?", (current_company(), limit))]


def get_meta(key: str) -> Optional[str]:
    with connect() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_meta(key: str, value: str) -> None:
    with connect() as c:
        c.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def set_status(incident_id: int, status: str, who: Optional[str] = None, resolution: Optional[str] = None,
               cause: Optional[str] = None, note: Optional[str] = None) -> None:
    if status not in STATUS_LABELS:
        raise ValueError("Неизвестный статус")
    with connect() as c:
        row = c.execute("SELECT id FROM incidents WHERE id=? AND company_id=?", (incident_id, current_company())).fetchone()
        if not row:
            raise ValueError("Заявка не найдена")
        if status == "approved":
            c.execute("UPDATE incidents SET status='approved', approved_by=?, taken_at=? WHERE id=?", (who, now_iso(), incident_id))
        elif status == "escalated":
            c.execute("UPDATE incidents SET status='escalated', escalation_note=?, assignee=? WHERE id=?", (note, who, incident_id))
        elif status == "resolved":
            c.execute("UPDATE incidents SET status='resolved', resolved_at=?, resolution=?, cause=COALESCE(?, cause), assignee=? WHERE id=?",
                      (now_iso(), resolution, cause, who, incident_id))
        else:
            c.execute("UPDATE incidents SET status='new', approved_by=NULL, escalation_note=NULL, resolved_at=NULL WHERE id=?", (incident_id,))


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
