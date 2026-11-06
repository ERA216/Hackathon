"""Шаг 3: план экономии. Меры — только из базы (мини-RAG), деньги — только кодом, ИИ — выбор и объяснение."""
import json
import re

from . import analytics, config, db, llm
from .schemas import AdvisorChoice, kzt, num

ADVISOR_PROMPT = """Ты инженер по надёжности оборудования на небольшом производстве.
Тебе дают статистику поломок одного станка, исходные записи мастеров и список ДОПУСТИМЫХ мер с уже посчитанной экономикой.

Задача:
1. Выбери до 3 мер, которые лучше всего подходят к этой поломке по смыслу записей. Используй ТОЛЬКО measure_id из списка.
2. Для каждой меры в поле why — одно предложение, почему она подходит именно здесь (опирайся на записи мастеров).
3. В поле summary — 2–3 предложения для владельца простым языком: что происходит и с чего начать.

Числа: используй только числа из входных данных. Не придумывай новые суммы, проценты и сроки.
Верни JSON: {"measures":[{"measure_id":"MS02","why":"..."}],"summary":"..."}"""


class AdvisorOutput(AdvisorChoice):
    summary: str


def candidates(failure_type: str) -> list[dict]:
    return [m for m in db.load_measures() if failure_type in m["applies_to"]]


def economics(measure: dict, st: dict) -> dict:
    """Формулы из описания проекта. Меры считаются по отдельности, их экономия не суммируется."""
    loss, n, avg_h, hour = st["loss_kzt"], st["failures"], st["avg_downtime_h"], st["hour_cost_kzt"]
    eff, monthly, one_time = measure["effect"], measure["monthly_kzt"], measure["one_time_kzt"]
    scale = 30 / st["window_days"]  # приводим к месяцу
    if eff["type"] == "reduce_failures":
        gross = loss * scale * eff["value"]
        formula = f"{kzt(loss * scale)} × {eff['value']:.0%}"
    else:  # reduce_downtime: простой одного случая сокращается до value часов
        new_h = min(eff["value"], avg_h)
        gross = n * scale * (avg_h - new_h) * hour
        formula = f"{num(n * scale)} × ({num(avg_h)} − {num(new_h)}) ч × {kzt(hour)} ₸"
    savings = gross - monthly
    if monthly:
        formula += f" − {kzt(monthly)} ₸ ежемесячных затрат"
    if savings <= 0:
        payback, payback_label = None, "не окупается"
    elif one_time == 0:
        payback, payback_label = 0.0, "без разовых вложений"
    else:
        payback = one_time / (savings / 30)
        payback_label = f"~{max(1, round(payback))} дн."
    out = {
        "measure_id": measure["id"], "title": measure["title"], "note": measure["note"], "demo": measure.get("demo", False),
        "one_time_kzt": one_time, "monthly_kzt": monthly,
        "savings_kzt": int(round(savings)), "payback_days": None if payback is None else round(payback, 1),
        "payback_label": payback_label,
        "formula": formula + f" = {kzt(savings)} ₸/мес",
    }
    if one_time and savings > 0:
        out["payback_formula"] = f"{kzt(one_time)} / ({kzt(savings)} / 30) ≈ {num(round(payback, 1))} дн."
    return out


def _rank_key(e: dict):
    return (e["payback_days"] is None, e["payback_days"] if e["payback_days"] is not None else 1e9, -e["savings_kzt"])


_NUM_RE = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM_RE.findall(text):
        v = re.sub(r"[\s ]", "", m).replace(",", ".")
        try:
            f = float(v)
        except ValueError:
            continue
        out.add(f"{f:g}")
    return out


def _allowed_numbers(st: dict, econ: list[dict]) -> set[str]:
    blob = json.dumps({"st": st, "econ": econ}, ensure_ascii=False)
    allowed = _numbers(blob) | {str(i) for i in range(0, 11)}
    for e in econ:  # округлённые варианты, которые модель может написать естественно
        for k in ("savings_kzt", "one_time_kzt", "monthly_kzt"):
            v = e[k]
            allowed |= {f"{round(v, -3):g}", f"{round(v / 1000):g}", f"{round(v / 1000, 1):g}"}
        if e["payback_days"]:
            allowed |= {f"{round(e['payback_days']):g}", f"{max(1, round(e['payback_days'])):g}"}
    allowed |= {f"{st['loss_kzt'] / 1000:g}", f"{round(st['loss_kzt'] / 1000):g}", f"{round(st['loss_kzt'], -3):g}"}
    return allowed


def _template_summary(st: dict, best: dict) -> str:
    txt = (f"{st['equipment_name']}: «{st['failure_label'].lower()}» — случаев за {st['window_days']} дней: {st['failures']}, "
           f"потери ≈ {kzt(st['loss_kzt'])} ₸. ")
    if best:
        txt += (f"Начните с меры «{best['title']}»: экономия ≈ {kzt(best['savings_kzt'])} ₸ в месяц, "
                f"окупаемость — {best['payback_label']}.")
    return txt


def plan(equipment_id: str, failure_type: str, save: bool = True) -> dict:
    st = analytics.group_stats(equipment_id, failure_type)
    if not st.get("failures"):
        return {"stats": st, "measures": [], "summary": "За период поломок такого типа нет — мер не требуется.", "mode": "rules", "ai_generated": False}
    econ = sorted((economics(m, st) for m in candidates(failure_type)), key=_rank_key)
    mode, warning, summary = "ai", None, None
    by_id = {e["measure_id"]: e for e in econ}
    chosen = []
    try:
        if not econ:
            raise llm.LLMBadOutput("no candidates")
        notes = [r["raw_text"] for r in db.incidents(equipment_id) if r["id"] in st["incident_ids"]][-6:]
        user = json.dumps({"stats": {k: st[k] for k in ("equipment_name", "failure_label", "window_days", "failures", "avg_downtime_h", "loss_kzt", "intervals_days")},
                           "shift_notes": notes,
                           "allowed_measures": [{k: e[k] for k in ("measure_id", "title", "note", "one_time_kzt", "savings_kzt", "payback_label")} for e in econ]},
                          ensure_ascii=False)
        out = llm.call_json(ADVISOR_PROMPT, user, AdvisorOutput, "advisor")
        allowed = _allowed_numbers(st, econ)
        for c in out.measures:
            if c.measure_id in by_id and c.measure_id not in [x["measure_id"] for x in chosen]:
                why = c.why if _numbers(c.why) <= allowed else None
                chosen.append({**by_id[c.measure_id], "why": why, "why_checked": why is not None})
        bad_numbers = _numbers(out.summary) - allowed
        summary = out.summary if not bad_numbers else None
        if bad_numbers:
            warning = "ИИ упомянул числа, которых нет в расчёте, — показан проверенный шаблонный текст."
        if not chosen:
            raise llm.LLMBadOutput("no valid measure ids")
        chosen.sort(key=_rank_key)
    except llm.LLMError as e:
        mode = "rules"
        warning = warning or (e.user_message if config.ai_enabled() else None)
        chosen = [{**e2, "why": None, "why_checked": False} for e2 in econ[:3]]
    summary = summary or _template_summary(st, chosen[0] if chosen else None)
    result = {"stats": st, "measures": chosen, "summary": summary, "mode": mode, "warning": warning,
              "ai_generated": mode == "ai", "all_candidates": econ}
    if save:
        db.save_recommendation(equipment_id, failure_type, {"measures": chosen, "stats": st}, summary, mode)
    return result
