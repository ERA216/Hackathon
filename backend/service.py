"""Фасад для интерфейса: всё, что нужно экранам, в одном месте. UI вызывает только эти функции."""
from typing import Optional

from . import advisor, analytics, auth, briefing, config, db, extract, seed
from .schemas import FAILURE_LABELS, FailureType


def bootstrap() -> None:
    """Создать базу и демо-данные при первом запуске."""
    db.init_db()
    if db.is_empty() or db.get_meta("seed_version") != seed.SEED_VERSION:
        seed.reset_demo()  # первая установка или новая версия тестовых данных: тестовая компания пересоздаётся (другие не трогаются)
        db.set_meta("seed_version", seed.SEED_VERSION)
    else:
        seed.ensure_demo_company()


def status() -> dict:
    return {"ai_enabled": config.ai_enabled(), "mode_label": config.mode_label(), **db.llm_stats()}


def equipment_options() -> list[dict]:
    return db.load_equipment()


def failure_options() -> dict:
    return {t.value: FAILURE_LABELS[t.value] for t in FailureType}


# ---------- экран 1: новая запись ----------

def analyze_text(raw: str) -> dict:
    """Текст смены → черновики для проверки мастером. ValueError — понятная ошибка ввода."""
    return extract.extract(raw)


def save_record(raw: str, drafts: list[dict], mode: str = "ai", author: Optional[str] = None) -> dict:
    """Сохранить подтверждённые мастером инциденты. Возвращает id и сигналы по затронутым станкам."""
    if not (raw or "").strip():
        raise ValueError("Пустой текст смены.")
    if not drafts:
        raise ValueError("Нет инцидентов для сохранения.")
    eq = db.equipment_map()
    for i, d in enumerate(drafts, 1):
        if d.get("equipment_id") not in eq:
            raise ValueError(f"Инцидент {i}: выберите станок из списка.")
        if d.get("failure_type") not in FAILURE_LABELS:
            raise ValueError(f"Инцидент {i}: выберите тип неисправности.")
        if d.get("downtime_min") is None or int(d["downtime_min"]) < 0:
            raise ValueError(f"Инцидент {i}: укажите простой в минутах.")
    with db.connect() as c:
        log_id = db.add_shift_log(raw.strip(), "processed", mode, conn=c, author=author)
        ids = [db.add_incident(log_id, {**d, "parts_cost_kzt": d.get("parts_cost_kzt")}, conn=c) for d in drafts]
    touched = {d["equipment_id"] for d in drafts}
    signals = [s for s in analytics.anomalies() if s["equipment_id"] in touched]
    return {"log_id": log_id, "incident_ids": ids, "signals": signals}


# ---------- экран 2: аномалии ----------

def dashboard() -> dict:
    summary = analytics.equipment_summary()
    return {
        "window_days": config.ANALYSIS_WINDOW_DAYS,
        "total_loss_kzt": sum(r["loss_kzt"] for r in summary),
        "total_downtime_h": round(sum(r["downtime_h"] for r in summary), 1),
        "by_equipment": summary,
        "anomalies": analytics.anomalies(),
    }


def incident_evidence(incident_ids: list[int]) -> list[dict]:
    rows = [r for r in db.incidents() if r["id"] in set(incident_ids)]
    return [{"id": r["id"], "date": r["occurred_at"][:10], "downtime_min": r["downtime_min"], "raw_text": r["raw_text"], "is_demo": bool(r["is_demo"])} for r in rows]


# ---------- экран 3: план экономии ----------

def savings_plan(equipment_id: str, failure_type: str) -> dict:
    return advisor.plan(equipment_id, failure_type)


def mark_applied(equipment_id: str, measure_id: str) -> None:
    db.apply_measure(equipment_id, measure_id)


def unmark_applied(equipment_id: str, measure_id: str) -> None:
    db.unapply_measure(equipment_id, measure_id)


def applied_set() -> set[tuple[str, str]]:
    return {(a["equipment_id"], a["measure_id"]) for a in db.applied_measures()}


# ---------- экран 4: история и эффект ----------

def history(query: str = "", limit: int = 300) -> list[dict]:
    q = (query or "").casefold().strip()
    rows = []
    for r in reversed(db.incidents()):
        row = {"Запись": f"#{r['id']}", "Дата": r["occurred_at"][:16].replace("T", " "), "Станок": r["equipment_name"],
               "Тип": FAILURE_LABELS.get(r["failure_type"], r["failure_type"]), "Простой, мин": r["downtime_min"],
               "Запчасти, ₸": r["parts_cost_kzt"], "Кто записал": r.get("author_role") or "", "Исходный текст": r["raw_text"]}
        if not q or q in " ".join(map(str, row.values())).casefold():
            rows.append(row)
    return rows[:limit]


def effects() -> list[dict]:
    measures = {m["id"]: m for m in db.load_measures()}
    eq = db.equipment_map()
    out = []
    for a in db.applied_measures():
        m = measures.get(a["measure_id"])
        if not m:
            continue
        eff = analytics.measure_effect(a["equipment_id"], m["applies_to"], a["applied_at"])
        out.append({**eff, "measure_id": m["id"], "measure_title": m["title"], "equipment_name": eq[a["equipment_id"]]["name"], "is_demo": bool(a["is_demo"])})
    return out


# ---------- сводка дня (руководитель) ----------

def today_summary() -> dict:
    return briefing.today_summary()


def checklist() -> list[dict]:
    return briefing.checklist()


def daily_brief() -> dict:
    return briefing.daily_brief()


def reset_demo() -> None:
    seed.reset_demo()


def ask_ai(question: str, history=None) -> dict:
    from . import assistant
    return assistant.ask(question, history)


# ---------- обход оборудования (сотрудник-аналитик) ----------

def save_inspection(rows: list[dict], author: str) -> dict:
    """rows: [{equipment_id, state: ok|remark|problem, note, downtime_min}]. Замечания и проблемы становятся карточками."""
    eq = db.equipment_map()
    rows = [r for r in rows if r.get("equipment_id") in eq]
    if not rows:
        raise ValueError("Отчёт пустой.")
    for r in rows:
        if r["state"] not in ("ok", "remark", "problem"):
            raise ValueError("Неизвестное состояние.")
        if r["state"] != "ok" and not (r.get("note") or "").strip():
            raise ValueError(f"{eq[r['equipment_id']]['name']}: опишите замечание или проблему в комментарии.")
    cards = []
    for r in rows:
        incident_id = None
        if r["state"] != "ok":
            note = r["note"].strip()
            label = "Проблема" if r["state"] == "problem" else "Замечание"
            text = f"Обход · {eq[r['equipment_id']]['name']} · {label}: {note}"
            with db.connect() as c:
                log_id = db.add_shift_log(text, "processed", "inspection", conn=c, author=author)
                incident_id = db.add_incident(log_id, {"equipment_id": r["equipment_id"], "failure_type": extract.detect_failure(note),
                                                       "downtime_min": int(r.get("downtime_min") or 0), "parts": [], "cause": None,
                                                       "action": None, "confidence": 1.0}, conn=c)
            cards.append(incident_id)
        db.add_inspection(r["equipment_id"], r["state"], (r.get("note") or "").strip() or None, author, incident_id)
    return {"checked": len(rows), "cards": cards}


def inspections_today() -> list[dict]:
    from datetime import date
    return [r for r in db.inspections() if r["created_at"][:10] == date.today().isoformat()]


def equipment_health() -> list[dict]:
    return analytics.equipment_health()


# ---------- заявки: новая → в работе → решена ----------

def _ticket(r: dict) -> dict:
    return {"id": r["id"], "time": r["occurred_at"][:16].replace("T", " "), "equipment_id": r["equipment_id"], "equipment_name": r["equipment_name"],
            "failure_type": r["failure_type"], "failure_label": FAILURE_LABELS.get(r["failure_type"], r["failure_type"]),
            "downtime_min": r["downtime_min"], "raw_text": r["raw_text"], "author": r.get("author_role") or "", "cause": r["cause"],
            "status": r.get("status") or "new", "status_label": db.STATUS_LABELS.get(r.get("status") or "new"), "assignee": r.get("assignee"),
            "resolution": r.get("resolution"), "resolved_at": r.get("resolved_at"), "approved_by": r.get("approved_by"),
            "escalation_note": r.get("escalation_note"), "is_demo": bool(r["is_demo"])}


def tickets(statuses: Optional[list[str]] = None, author: Optional[str] = None, limit: int = 100) -> list[dict]:
    rows = [_ticket(r) for r in reversed(db.incidents())]
    if statuses:
        rows = [t for t in rows if t["status"] in statuses]
    if author:
        rows = [t for t in rows if t["author"] == author]
    return rows[:limit]


def ticket_counts() -> dict:
    from datetime import date
    all_ = [_ticket(r) for r in db.incidents()]
    today = date.today().isoformat()
    out = {k: sum(t["status"] == k for t in all_) for k in ("new", "approved", "escalated")}
    out["resolved_today"] = sum(t["status"] == "resolved" and (t["resolved_at"] or "")[:10] == today for t in all_)
    return out


def ticket_hypotheses(t: dict) -> list[dict]:
    """Быстрые гипотезы причины для механика (по правилам, без вызова ИИ)."""
    from . import diagnose
    hist = diagnose.history(t["equipment_id"], t["failure_type"])
    return diagnose._present(diagnose.rule_rank(t["raw_text"], t["failure_type"], hist), "rules")


def approve_ticket(incident_id: int, admin: str) -> None:
    """Администратор одобряет: заявку решают сотрудники с подсказками ИИ."""
    db.set_status(incident_id, "approved", who=admin)


def escalate_ticket(incident_id: int, who: str, note: str) -> None:
    """Сотрудникам не хватает сил — передать администратору (запчасти, деньги, подрядчик)."""
    if not (note or "").strip():
        raise ValueError("Напишите, чего не хватает: запчасти, инструмента, знаний — администратору будет проще решить.")
    db.set_status(incident_id, "escalated", who=who, note=note.strip())


def resolve_ticket(incident_id: int, who: str, resolution: str, cause: Optional[str] = None) -> None:
    if not (resolution or "").strip():
        raise ValueError("Опишите, что сделали, — это попадёт в историю станка.")
    db.set_status(incident_id, "resolved", who=who, resolution=resolution.strip(), cause=(cause or None))


def reopen_ticket(incident_id: int) -> None:
    db.set_status(incident_id, "new")


# ---------- компании и сотрудники ----------

def use_company(company_id: int) -> None:
    db.set_company(company_id)


def login(company_code: str, login_: str, pin: str):
    return auth.login(company_code, login_, pin)


def session_token(user_id: int) -> str:
    return auth.make_token(user_id)


def user_from_token(token: str):
    return auth.user_from_token(token)


def register_company(name: str, city: str = "", note: str = "") -> dict:
    return auth.register_company(name, city, note)


def add_workers(company_id: int, names_text: str, role: str = "worker") -> list[dict]:
    return auth.add_workers(company_id, names_text, role)


def list_users(company_id: int) -> list[dict]:
    return auth.list_users(company_id)


def demo_credentials() -> dict:
    return {"code": seed.DEMO_CODE, "users": [{"name": n, "role": r, "login": l, "pin": p} for n, r, l, p in seed.DEMO_USERS]}
