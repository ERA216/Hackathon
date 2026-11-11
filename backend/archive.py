"""Install real source events into the primary workspace and query that workspace."""
from collections import Counter
from contextlib import closing
from datetime import date, datetime
import json
import re
import sqlite3

from . import config, db, open_data


def install(company_id: int) -> bool:
    meta = open_data.manifest()["kelmarsh"]
    profile = db.source_profile(company_id)
    if profile and profile["version"] == meta["sha256"]:
        return False
    # Keep a recoverable snapshot before replacing any synthetic records.
    backup_dir = config.ROOT / ".runtime-backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / (config.DB_PATH.stem + ".before-kelmarsh.db")
    if not backup.exists():
        with closing(sqlite3.connect(str(config.DB_PATH))) as source, closing(sqlite3.connect(str(backup))) as target:
            source.backup(target)
    with db.connect() as connection:
        for i in range(1, 7):
            connection.execute("INSERT OR IGNORE INTO equipment(id,name,line,hour_cost_kzt,hour_cost_known) VALUES (?,?,?,0,0)",
                               (f"KEL{i:02d}", f"Kelmarsh {i}", "Ветропарк Kelmarsh"))
        # Only synthetic company records are replaced. Other companies and manual records survive.
        for table in ("incidents", "applied_measures", "inspections", "shift_logs"):
            connection.execute(f"DELETE FROM {table} WHERE company_id=? AND is_demo=1", (company_id,))
        for row in open_data.events():
            existing = connection.execute("SELECT id FROM incidents WHERE company_id=? AND source_dataset='kelmarsh' AND source_id=?", (company_id, row["id"])).fetchone()
            if existing:
                continue
            occurred = datetime.fromisoformat(row["started_at"]).replace(tzinfo=None).isoformat()
            raw = f'{row["equipment_name"]} · {row["message"]}\n{open_data.message_ru(row)}\n{row["started_at"]} → {row["ended_at"]} · UTC'
            log_id = connection.execute("INSERT INTO shift_logs(created_at,raw_text,author_role,status,mode,is_demo,company_id) VALUES (?,?,?,'processed','source',0,?)",
                                        (occurred, raw, "Журнал SCADA · Kelmarsh", company_id)).lastrowid
            connection.execute("""INSERT INTO incidents(log_id,occurred_at,equipment_id,failure_type,downtime_min,
                parts_cost_kzt,parts_json,cause,action,confidence,needs_review,is_demo,company_id,status,
                source_id,source_dataset,source_record_json,downtime_seconds,parts_cost_known)
                VALUES (?,?,?,?,?,0,'[]',NULL,NULL,NULL,0,0,?,'archived',?,'kelmarsh',?,?,0)""",
                (log_id, occurred, row["equipment_id"], "kelmarsh:" + row["code"], row["duration_seconds"] / 60,
                 company_id, row["id"], json.dumps(row, ensure_ascii=False), row["duration_seconds"]))
        connection.execute("UPDATE companies SET name='Kelmarsh · открытый архив',city='Великобритания',is_demo=0 WHERE id=?", (company_id,))
        connection.execute("UPDATE users SET display_name='Администратор FixList' WHERE company_id=? AND login='boss' AND display_name='Администратор цеха'", (company_id,))
        connection.execute("UPDATE users SET display_name='Оператор FixList' WHERE company_id=? AND login='master' AND display_name='Мастер смены 1'", (company_id,))
        connection.execute("INSERT INTO company_sources(company_id,dataset,version,imported_at) VALUES (?,'kelmarsh',?,?) ON CONFLICT(company_id) DO UPDATE SET dataset=excluded.dataset,version=excluded.version,imported_at=excluded.imported_at",
                           (company_id, meta["sha256"], db.now_iso()))
    return True


def records() -> list[dict]:
    return [{**row["source_record"], "incident_id": row["id"], "workflow_status": row["status"]}
            for row in db.incidents() if row.get("source_dataset") == "kelmarsh" and row.get("source_record")]


def report(equipment_id=None, start=None, end=None) -> dict:
    meta = open_data.manifest()["kelmarsh"]
    start = start or date(2016, 1, 1)
    end = end or date(2016, 12, 31)
    lo, hi = open_data.period_bounds(start, end)
    rows = [row for row in records() if (not equipment_id or row["equipment_id"] == equipment_id)
            and open_data._time(row["started_at"]) < hi and open_data._time(row["ended_at"]) > lo]
    return {"rows": rows, "stats": open_data.summary(rows, start, end), "signals": open_data.repeated_signals(rows),
            "monthly": open_data.monthly_hours(rows, start, end), "start": start, "end": end, "source": meta}


def equipment() -> list[dict]:
    output = []
    for item in db.load_equipment():
        view = report(item["id"])
        rows = view["rows"]
        output.append({**item, "events": len(rows), "hours": view["stats"]["downtime_seconds"] / 3600,
                       "repeat_groups": view["stats"]["repeat_groups"], "last_event": max((row["started_at"] for row in rows), default=None),
                       "top_signal": view["signals"][0] if view["signals"] else None})
    return output


def daily(day: date) -> dict:
    view = report(start=day, end=day)
    began = [row for row in view["rows"] if row["started_at"][:10] == day.isoformat()]
    return {**view, "day": day, "started_events": len(began)}


def dashboard() -> dict:
    view = report()
    return {"window_days": 366, "total_loss_kzt": None, "total_downtime_h": round(view["stats"]["downtime_seconds"] / 3600, 2),
            "by_equipment": [{"equipment_id": row["id"], "equipment_name": row["name"], "failures": row["events"], "downtime_h": round(row["hours"], 2), "loss_kzt": None} for row in equipment()],
            "anomalies": [{"equipment_id": row["equipment_id"], "failure_type": "kelmarsh:" + row["code"], "failure_label": row["label"],
                           "equipment_name": f'Kelmarsh {int(row["equipment_id"][-2:])}', "severity": "medium", "loss_kzt": None,
                           "title": f'Kelmarsh {int(row["equipment_id"][-2:])} · {row["label"]}', "count": row["count"]} for row in view["signals"]]}


def ask(question: str, history=None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("Напишите вопрос.")
    q = question.casefold()
    match = re.search(r"(?:kelmarsh|турбин[аы]?)\s*(?:№|#)?\s*([1-6])\b", q)
    equipment_id = f"KEL{int(match[1]):02d}" if match else None
    view = report(equipment_id)
    stats, signals = view["stats"], view["signals"]
    names = [f'Kelmarsh {int(equipment_id[-2:])}'] if equipment_id else [row["name"] for row in equipment()]
    facts = {"period": "2016-01-01 — 2016-12-31 UTC", "equipment": names, "event_records": stats["events"],
             "stop_periods": stats["episodes"], "turbine_hours": round(stats["downtime_seconds"] / 3600, 2),
             "repeated_codes": [{"equipment": f'Kelmarsh {int(row["equipment_id"][-2:])}', "code": row["code"], "message": row["label"], "records": row["count"]} for row in signals[:5]],
             "repair_cost": None, "parts_cost": None, "confirmed_cause": None, "future_failure_date": None}
    if any(word in q for word in ("цен", "стоим", "убыт", "дороже", "эконом", "потер")):
        text = "В источнике нет тарифов простоя и стоимости ремонта. В разделе «Деньги и экономия» можно рассчитать сценарий по введённому вами тарифу."
    elif any(word in q for word in ("когда слом", "сломается", "прогноз", "здоров")):
        text = "Архив содержит реальные остановки 2016 года. Подтверждённых данных о текущем состоянии, будущем отказе и результате ремонта в нём нет. Можно изучить повторения сигнала на конкретной турбине."
    else:
        text = f'{", ".join(names) if equipment_id else "Ветропарк Kelmarsh"}: за 2016 год в базе {stats["events"]} записей об остановках; суммарно {facts["turbine_hours"]:g} агрегато-часов.'
        if signals:
            top = signals[0]
            text += f' Чаще всего повторяется сигнал «{top["label"]}» на Kelmarsh {int(top["equipment_id"][-2:])}: {top["count"]} записей, код {top["code"]}. Это основание изучить историю сигнала; причина поломки в источнике не установлена.'
    # Missing values are answered deterministically rather than guessed by a model.
    if any(word in q for word in ("цен", "стоим", "убыт", "дороже", "эконом", "потер", "сломается", "прогноз", "здоров")):
        return {"answer": text, "based_on": names, "mode": "rules", "warning": None}
    from . import llm
    from .assistant import Answer
    from .advisor import _numbers
    try:
        output = llm.call_json(
            "Ты аналитик FixList. Ответь кратко по-русски только по переданным фактам реального архива Kelmarsh за 2016 год. "
            "Это архив, не текущие аварии. Нет сведений о причине, стоимости, выполненном ремонте или следующей поломке. "
            "Не добавляй неизвестные факты и числа. Верни JSON {answer, based_on}.",
            json.dumps({"facts": facts, "question": question}, ensure_ascii=False), Answer, "archive_assistant")
        if _numbers(output.answer) - _numbers(json.dumps(facts, ensure_ascii=False)):
            raise llm.LLMBadOutput("unsupported numbers")
        return {"answer": output.answer, "based_on": names, "mode": "ai", "warning": None}
    except llm.LLMError:
        return {"answer": text, "based_on": names, "mode": "rules", "warning": None}
