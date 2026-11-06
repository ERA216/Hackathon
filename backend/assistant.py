"""«Спросить ИИ»: ответы только по данным базы компании (не общий чат-бот).

Факты собирает код: состояние оборудования, убытки, прогнозы, открытые заявки, сводка дня.
Числа в ответе ИИ сверяются с фактами; если не сходятся или ИИ недоступен — ответ по правилам из тех же фактов.
"""
import json

from . import analytics, briefing, config, db, llm
from .advisor import _numbers
from .schemas import BaseModel, Field, kzt

PROMPT = """Ты ИИ-аналитик сервиса FixList на производстве. Отвечай на вопрос пользователя по-русски, коротко и по делу (2–6 предложений или список).
Используй ТОЛЬКО факты из базы ниже: состояние оборудования, убытки, прогнозы поломок, открытые заявки, сводку дня.
Если ответа в фактах нет — так и скажи и подскажи, какие данные нужно записать при обходе.
Числа — только из фактов. Не придумывай станки, суммы и даты. Если уместно — посоветуй, что сделать в первую очередь.
Верни JSON: {"answer": "...", "based_on": ["названия станков или номера заявок, на которые опирался"]}"""


class Answer(BaseModel):
    answer: str
    based_on: list[str] = Field(default_factory=list, max_length=10)


def facts() -> dict:
    from . import service  # поздний импорт, чтобы не было цикла
    health = analytics.equipment_health()
    sm = briefing.today_summary()
    return {
        "equipment": [{k: h[k] for k in ("equipment_name", "risk", "health", "failures", "downtime_h", "loss_kzt", "days_to_next", "explanation")} for h in health],
        "total_loss_30d_kzt": sum(h["loss_kzt"] for h in health),
        "open_tickets": [{"id": t["id"], "equipment": t["equipment_name"], "status": t["status_label"], "text": t["raw_text"][:160],
                          "note": t.get("escalation_note")} for t in service.tickets(["new", "approved", "escalated"], limit=20)],
        "ticket_counts": service.ticket_counts(),
        "today": {k: sm[k] for k in ("logs_today", "incidents_today", "downtime_h_today", "loss_today_kzt")},
    }


def _rules_answer(question: str, f: dict) -> dict:
    q = question.lower()
    hits = [e for e in f["equipment"] if any(part in q for part in e["equipment_name"].lower().replace("(", " ").replace(")", " ").split() if len(part) > 3 or part.startswith("№"))]
    if hits:
        e = hits[0]
        return {"answer": f"{e['equipment_name']}: здоровье {e['health']}%. {e['explanation'][:1].upper()}{e['explanation'][1:]}", "based_on": [e["equipment_name"]]}
    risky = [e for e in f["equipment"] if e["risk"] != "low"][:3]
    lines = [f"Убыток от простоев за 30 дней ≈ {kzt(f['total_loss_30d_kzt'])} ₸. Открытых заявок: {len(f['open_tickets'])}."]
    lines += [f"• {e['equipment_name']} — здоровье {e['health']}%, убыток {kzt(e['loss_kzt'])} ₸" for e in risky]
    return {"answer": "\n".join(lines), "based_on": [e["equipment_name"] for e in risky]}


def ask(question: str, history: list[dict] | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("Напишите вопрос.")
    f = facts()
    try:
        hist = [{"q": h["q"], "a": h["a"]} for h in (history or [])[-3:]]
        user = json.dumps({"facts": f, "previous": hist, "question": question}, ensure_ascii=False)
        out = llm.call_json(PROMPT, user, Answer, "assistant")
        allowed = _numbers(json.dumps(f, ensure_ascii=False)) | {str(i) for i in range(0, 31)}
        for e in f["equipment"]:
            allowed |= {f"{round(e['loss_kzt'] / 1000):g}", f"{round(e['loss_kzt'], -3):g}"}
        bad = _numbers(out.answer) - allowed
        if bad:
            r = _rules_answer(question, f)
            return {**r, "mode": "rules", "warning": "ИИ назвал числа, которых нет в базе, — показан ответ, собранный кодом из данных."}
        return {"answer": out.answer, "based_on": out.based_on, "mode": "ai", "warning": None}
    except llm.LLMError as e:
        return {**_rules_answer(question, f), "mode": "rules", "warning": e.user_message if config.ai_enabled() else None}
