"""Диагностика причины: гипотезы из базы типовых причин (data/causes.json), ранжированные по симптомам и истории.

ИИ выбирает и объясняет гипотезы, но только из базы. Без ИИ работает ранжирование по правилам.
Гипотеза — не факт: в интерфейсе её подтверждает мастер, и у каждой есть список «что проверить».
"""
import json
from functools import lru_cache

from . import analytics, config, db, llm
from .schemas import FAILURE_LABELS, BaseModel, Field

DIAG_PROMPT = """Ты инженер по надёжности на небольшом производстве. Помоги мастеру найти вероятную причину поломки.
Тебе дают: отчёт мастера, историю этого станка с той же поломкой и список ДОПУСТИМЫХ причин с признаками.

Правила:
1. Выбери до 3 причин ТОЛЬКО из списка (cause_id), от самой вероятной.
2. why — одно-два предложения: какие слова из отчёта или факты истории указывают на эту причину.
3. confidence — 0..1. Если данных мало — ставь низкую уверенность, не выдумывай.
4. Не придумывай фактов, которых нет в отчёте или истории.
Верни JSON: {"hypotheses":[{"cause_id":"C01","why":"...","confidence":0.6}]}"""


class Hypothesis(BaseModel):
    cause_id: str
    why: str
    confidence: float = Field(ge=0, le=1)


class Diagnosis(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=3)


@lru_cache
def load_causes() -> tuple:
    return tuple(json.loads((config.DATA_DIR / "causes.json").read_text(encoding="utf-8")))


def candidates(failure_type: str) -> list[dict]:
    c = [x for x in load_causes() if x["failure_type"] == failure_type]
    return c or [x for x in load_causes() if x["failure_type"] == "other"]


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def history(equipment_id: str, failure_type: str) -> dict:
    st = analytics.group_stats(equipment_id, failure_type)
    rows = [r for r in db.incidents(equipment_id) if r["failure_type"] == failure_type]
    iv = st.get("intervals_days", []) if st.get("failures") else []
    return {
        "failures_in_window": st.get("failures", 0),
        "window_days": config.ANALYSIS_WINDOW_DAYS,
        "intervals_days": iv,
        "shrinking": len(iv) >= 3 and all(a > b for a, b in zip(iv[-3:], iv[-2:])),
        "recent_notes": [r["raw_text"].replace("ДЕМО · ", "") for r in rows[-4:]],
        "past_causes": [r["cause"] for r in rows if r.get("cause")][-3:],
    }


def rule_rank(raw: str, failure_type: str, hist: dict) -> list[dict]:
    """Скоринг: признаки в тексте и прошлых записях + повторяемость для «системных» причин."""
    text = _norm(raw)
    notes = _norm(" ".join(hist["recent_notes"]))
    repeat = hist["failures_in_window"] >= 2 or hist["shrinking"]
    out = []
    for c in candidates(failure_type):
        hits_now = [s for s in c["signs"] if s in text]
        hits_hist = [s for s in c["signs"] if s in notes and s not in hits_now]
        score = 1 + 3 * len(hits_now) + 1 * len(hits_hist)
        reasons = []
        if hits_now:
            reasons.append("в записи: «" + "», «".join(hits_now) + "»")
        if hits_hist:
            reasons.append("в прошлых записях: «" + "», «".join(hits_hist) + "»")
        if c["recurrent"] and repeat:
            score += 3
            reasons.append(f"поломка повторяется ({hist['failures_in_window']} за {hist['window_days']} дн.) — типично для системной причины")
        elif not c["recurrent"] and not repeat:
            score += 1
            reasons.append("первый случай — возможна разовая причина")
        out.append({"cause": c, "score": score, "why": "; ".join(reasons) or "типовая причина для этой поломки"})
    out.sort(key=lambda x: -x["score"])
    total = sum(x["score"] for x in out[:3]) or 1
    return [{"cause_id": x["cause"]["id"], "why": x["why"], "confidence": round(x["score"] / total * 0.8, 2)} for x in out[:3]]


def _present(hyps: list[dict], source: str) -> list[dict]:
    by_id = {c["id"]: c for c in load_causes()}
    return [{"cause_id": h["cause_id"], "title": by_id[h["cause_id"]]["title"], "checks": by_id[h["cause_id"]]["checks"],
             "why": h["why"], "confidence": h["confidence"], "source": source} for h in hyps if h["cause_id"] in by_id]


def diagnose(raw: str, equipment_id: str, failure_type: str) -> dict:
    """Возвращает {hypotheses, mode, history}. Никогда не бросает исключение из-за ИИ."""
    hist = history(equipment_id, failure_type)
    cands = candidates(failure_type)
    allowed = {c["id"] for c in cands}
    try:
        user = json.dumps({
            "report": raw,
            "equipment": db.equipment_map()[equipment_id]["name"],
            "failure": FAILURE_LABELS.get(failure_type, failure_type),
            "history": hist,
            "allowed_causes": [{k: c[k] for k in ("id", "title", "signs", "recurrent")} for c in cands],
        }, ensure_ascii=False)
        out = llm.call_json(DIAG_PROMPT, user, Diagnosis, "diagnose")
        hyps = [h.model_dump() for h in out.hypotheses if h.cause_id in allowed]  # чужие id отбрасываются
        if not hyps:
            raise llm.LLMBadOutput("no valid cause ids")
        return {"hypotheses": _present(hyps, "ai"), "mode": "ai", "history": hist}
    except llm.LLMError:
        return {"hypotheses": _present(rule_rank(raw, failure_type, hist), "rules"), "mode": "rules", "history": hist}
