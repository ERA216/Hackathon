"""Сводка дня для руководителя: что загружено сегодня, что тревожит, что проверить.

Факты и числа собирает код. ИИ пишет короткое объяснение; числа и названия станков в его тексте
сверяются с фактами, иначе показывается шаблон.
"""
import json
from datetime import date

from . import analytics, db, diagnose, llm
from .advisor import _numbers
from .schemas import FAILURE_LABELS, BaseModel, Field, kzt

BRIEF_PROMPT = """Ты помощник руководителя небольшого производства. По фактам ниже напиши короткую сводку дня.
Факты: записи за сегодня, сигналы (повторяющиеся поломки с потерями), гипотезы причин с проверками и риски по аппаратам
(здоровье, прогноз следующей поломки). Обязательно предупреди о самых рискованных аппаратах заранее и объясни почему.

Правила:
1. headline — одно предложение: главное за сегодня.
2. points — 2–4 коротких пункта: что произошло и почему это важно. Опирайся только на факты.
3. priorities — до 3 действий на сегодня, от важного к менее важному: equipment (название станка как в фактах) и action (что сделать/проверить).
4. Используй только числа из фактов. Не придумывай станки, суммы, причины.
Верни JSON: {"headline":"...","points":["..."],"priorities":[{"equipment":"...","action":"..."}]}"""


class Priority(BaseModel):
    equipment: str
    action: str


class Brief(BaseModel):
    headline: str
    points: list[str] = Field(default_factory=list, max_length=5)
    priorities: list[Priority] = Field(default_factory=list, max_length=3)


def today_records() -> list[dict]:
    today = date.today().isoformat()
    rows = [r for r in db.incidents() if r["occurred_at"][:10] == today]
    return [{"id": r["id"], "time": r["occurred_at"][11:16], "equipment_id": r["equipment_id"], "equipment_name": r["equipment_name"],
             "failure_type": r["failure_type"], "failure_label": FAILURE_LABELS.get(r["failure_type"], r["failure_type"]),
             "downtime_min": r["downtime_min"], "loss_kzt": int(round(r["downtime_min"] / 60 * r["hour_cost_kzt"] + r["parts_cost_kzt"])),
             "cause": r["cause"], "raw_text": r["raw_text"], "author": r.get("author_role") or "", "is_demo": bool(r["is_demo"])} for r in reversed(rows)]


def today_summary() -> dict:
    recs = today_records()
    with db.connect() as c:
        cid = db.current_company()
        logs_today = c.execute("SELECT COUNT(*) FROM shift_logs WHERE company_id=? AND substr(created_at,1,10)=?", (cid, date.today().isoformat())).fetchone()[0]
        total_logs = c.execute("SELECT COUNT(*) FROM shift_logs WHERE company_id=?", (cid,)).fetchone()[0]
        first = c.execute("SELECT MIN(created_at) FROM shift_logs WHERE company_id=?", (cid,)).fetchone()[0]
    df = analytics.incidents_df()
    per_day = []
    if not df.empty:
        start = date.fromordinal(date.today().toordinal() - 6)
        last7 = df[df["occurred_at"].dt.date >= start]
        counts = last7.groupby(last7["occurred_at"].dt.date).size()
        for i in range(6, -1, -1):
            d = date.fromordinal(date.today().toordinal() - i)
            per_day.append({"Дата": d.strftime("%d.%m"), "Записей": int(counts.get(d, 0))})
    return {
        "logs_today": logs_today, "incidents_today": len(recs), "records": recs,
        "downtime_h_today": round(sum(r["downtime_min"] for r in recs) / 60, 1),
        "loss_today_kzt": sum(r["loss_kzt"] for r in recs),
        "total_logs": total_logs, "history_since": (first or "")[:10], "per_day": per_day,
    }


def checklist(limit: int = 4) -> list[dict]:
    """Что проверить сегодня: по каждому сигналу — самая вероятная причина и её проверки (из базы причин)."""
    items = []
    for s in analytics.anomalies()[:limit]:
        notes = " ".join(r["raw_text"] for r in db.incidents(s["equipment_id"]) if r["id"] in s["incident_ids"])
        hist = diagnose.history(s["equipment_id"], s["failure_type"])
        top = diagnose._present(diagnose.rule_rank(notes, s["failure_type"], hist)[:1], "rules")
        if top:
            h = top[0]
            items.append({"equipment_id": s["equipment_id"], "failure_type": s["failure_type"], "equipment_name": s["equipment_name"],
                          "signal": s["title"], "loss_kzt": s["loss_kzt"], "severity": s["severity"], "cause": h["title"],
                          "why": h["why"], "checks": h["checks"]})
    return items


def _template(summary: dict, checks: list[dict]) -> dict:
    if summary["incidents_today"]:
        head = (f"Сегодня записано инцидентов: {summary['incidents_today']}, простой {str(summary['downtime_h_today']).replace('.', ',')} ч, "
                f"потери ≈ {kzt(summary['loss_today_kzt'])} ₸.")
    else:
        head = "Сегодня новых инцидентов не записано."
    points = [f"{c['equipment_name']}: {c['signal'].split(' · ')[-1].lower()}, потери за период ≈ {kzt(c['loss_kzt'])} ₸." for c in checks[:3]]
    prios = [{"equipment": c["equipment_name"], "action": f"Проверить: {c['cause'].lower()} — {c['checks'][0].lower()}"} for c in checks[:3]]
    return {"headline": head, "points": points, "priorities": prios}


def daily_brief() -> dict:
    summary, checks = today_summary(), checklist()
    facts = {
        "today": [{k: r[k] for k in ("time", "equipment_name", "failure_label", "downtime_min", "loss_kzt", "cause")} for r in summary["records"]],
        "today_totals": {k: summary[k] for k in ("incidents_today", "downtime_h_today", "loss_today_kzt")},
        "signals": [{k: c[k] for k in ("equipment_name", "signal", "loss_kzt", "cause", "checks")} for c in checks],
        "risks": [{k: h[k] for k in ("equipment_name", "risk", "health", "days_to_next", "loss_kzt", "explanation")}
                  for h in analytics.equipment_health() if h["risk"] != "low"][:5],
    }
    try:
        out = llm.call_json(BRIEF_PROMPT, json.dumps(facts, ensure_ascii=False), Brief, "brief")
        allowed = _numbers(json.dumps(facts, ensure_ascii=False)) | {str(i) for i in range(0, 11)}
        for c in checks:
            allowed |= {f"{round(c['loss_kzt'] / 1000):g}", f"{round(c['loss_kzt'], -3):g}"}
        names = {e["name"] for e in db.load_equipment()}
        text = " ".join([out.headline, *out.points, *(p.action for p in out.priorities)])
        bad = _numbers(text) - allowed
        unknown = [p.equipment for p in out.priorities if not any(p.equipment in n or n in p.equipment for n in names)]
        if bad or unknown:
            return {**_template(summary, checks), "mode": "rules",
                    "warning": "ИИ упомянул числа или станки, которых нет в данных, — показана проверенная сводка по шаблону."}
        return {"headline": out.headline, "points": out.points, "priorities": [p.model_dump() for p in out.priorities], "mode": "ai", "warning": None}
    except llm.LLMError as e:
        return {**_template(summary, checks), "mode": "rules", "warning": e.user_message if llm.config.ai_enabled() else None}
