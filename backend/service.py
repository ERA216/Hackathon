"""Фасад для интерфейса: всё, что нужно экранам, в одном месте. UI вызывает только эти функции."""
from typing import Optional

from . import advisor, analytics, config, db, extract, seed
from .schemas import FAILURE_LABELS, FailureType


def bootstrap() -> None:
    """Создать базу и демо-данные при первом запуске."""
    db.init_db()
    if db.is_empty():
        seed.seed_demo()


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


def save_record(raw: str, drafts: list[dict], mode: str = "ai") -> dict:
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
        log_id = db.add_shift_log(raw.strip(), "processed", mode, conn=c)
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
               "Запчасти, ₸": r["parts_cost_kzt"], "Исходный текст": r["raw_text"], "Данные": "ДЕМО" if r["is_demo"] else "Реальные"}
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


def reset_demo() -> None:
    seed.reset_demo()
