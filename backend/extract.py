"""Шаг 1: свободный текст смены → проверенные черновики инцидентов.

ИИ извлекает поля, код проверяет их по справочнику и по исходному тексту.
Если ИИ недоступен, тот же контракт выполняет разбор по правилам (резервный режим).
"""
import json
import re
from typing import Optional

from . import config, db, llm
from .schemas import FAILURE_LABELS, ExtractionResult, FailureType

FAILURE_KEYWORDS = {
    "belt_break": ["ремен", "ремн", "белдік"],
    "overheating": ["перегр", "греет", "горяч", "қызып", "қызды"],
    "electrical": ["электр", "автомат", "предохран", "контакт", "щит", "проводк", "замыкан", "ток "],
    "sensor": ["датчик", "сенсор", "концевик"],
    "lubrication": ["смаз", "масл", "майла"],
    "bearing": ["подшип", "мойынтірек", "гул", "вибрац"],
    "material": ["сырь", "материал", "заготов", "шикізат"],
}
PART_KEYWORDS = {"ремень": ["ремен", "ремн", "белдік"], "датчик": ["датчик"], "подшипник": ["подшип"], "предохранитель": ["предохран"], "контактор": ["контактор"]}
REPLACE_WORDS = ["замен", "поставили нов", "поменял", "ауыстыр"]

SYSTEM_PROMPT = """Ты извлекаешь инциденты оборудования из отчёта мастера смены небольшого производства.
Отчёт может быть на русском, казахском или смеси, с опечатками.

Правила:
1. equipment_id — ТОЛЬКО id из справочника ниже. Если станок не назван или не найден — null и вопрос в needs_clarification.
2. failure_type — одно значение из списка типов.
3. downtime_min — простой в минутах целым числом («1,5 часа» = 90, «полчаса» = 30). Если длительность не указана — null.
4. downtime_quote — ДОСЛОВНЫЙ фрагмент отчёта, из которого взята длительность (скопируй символы как есть). Если длительности нет — null.
5. cause — только если причина прямо названа в тексте. Не придумывай. Иначе null.
6. action — что сделали, по тексту. parts — заменённые детали.
7. confidence — 0..1, насколько уверен в полях этого инцидента.
8. Ничего не выдумывай: числа, детали и причины — только из текста.

Верни JSON строго вида:
{"incidents":[{"equipment_id":"M3","failure_type":"belt_break","downtime_min":90,"downtime_quote":"простой 1,5 часа",
"parts":[{"name":"ремень","qty":1}],"cause":null,"action":"заменили ремень","confidence":0.9}],
"needs_clarification":["..."]}"""


# ---------- разбор длительностей (используется и для проверки ответа ИИ) ----------

_DUR_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>час(?:а|ов)?|ч\b|сағ(?:ат)?|мин(?:ут[ауы]?)?\b|м\b)"
    r"|(?P<half>полчаса|жарты сағат)"
    r"|(?P<hourword>(?<![\d,.]\s)(?<!\d)час\b)",
    re.I,
)


def parse_durations(text: str) -> list[tuple[int, str]]:
    found = []
    for m in _DUR_RE.finditer(text):
        if m.group("half"):
            found.append((30, m.group(0)))
        elif m.group("hourword"):
            found.append((60, m.group(0)))
        else:
            val = float(m.group("num").replace(",", "."))
            unit = m.group("unit").lower()
            minutes = val * 60 if unit.startswith(("ч", "сағ")) else val
            found.append((int(round(minutes)), m.group(0)))
    return found


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def match_equipment(text: str) -> Optional[str]:
    t = _norm(text)
    m = re.search(r"(?:станок|станка|станке|станоке|станокта)\s*(?:№|n|#)?\s*(\d+)", t)
    eq = db.equipment_map()
    if m and f"M{m.group(1)}" in eq:
        return f"M{m.group(1)}"
    for e in eq.values():
        for alias in sorted(e["aliases"], key=len, reverse=True):
            if len(alias) > 2 and _norm(alias) in t:
                return e["id"]
    return None


def detect_failure(text: str) -> str:
    t = _norm(text)
    for ftype, words in FAILURE_KEYWORDS.items():
        if any(w in t for w in words):
            return ftype
    return "other"


# ---------- резервный разбор по правилам ----------

def rule_based(raw: str) -> ExtractionResult:
    segments = [s for s in re.split(r"[\n;]+|(?<=[.!?])\s+(?=[А-ЯA-ZӘҒҚҢӨҰҮҺІ])", raw) if s.strip()]
    incidents, questions, current = [], [], None
    for seg in segments:
        eq = match_equipment(seg)
        if eq or current is None:
            current = {"equipment_id": eq, "text": seg}
            incidents.append(current)
        else:
            current["text"] += " " + seg
    out = []
    for item in incidents:
        t = item["text"]
        durs = parse_durations(t)
        if not item["equipment_id"] and not durs and detect_failure(t) == "other":
            continue  # нет ни станка, ни поломки, ни простоя — это не инцидент
        parts = [{"name": name, "qty": 1} for name, words in PART_KEYWORDS.items()
                 if any(w in _norm(t) for w in words) and any(w in _norm(t) for w in REPLACE_WORDS)]
        out.append({
            "equipment_id": item["equipment_id"],
            "failure_type": detect_failure(t),
            "downtime_min": durs[0][0] if durs else None,
            "downtime_quote": durs[0][1] if durs else None,
            "parts": parts,
            "cause": None,
            "action": "замена детали" if parts else None,
            "confidence": 0.6,
        })
    if not out:
        questions.append("Не удалось найти инцидент. Опишите, какой станок остановился и на сколько.")
    return ExtractionResult.model_validate({"incidents": out, "needs_clarification": questions})


# ---------- проверка результата кодом ----------

def validate(raw: str, result: ExtractionResult) -> tuple[list[dict], list[str]]:
    eq_map = db.equipment_map()
    raw_n = _norm(raw)
    text_durations = parse_durations(raw)
    drafts, questions = [], list(result.needs_clarification)
    for inc in result.incidents:
        d = inc.model_dump(mode="json")
        issues = []
        # 1. Станок только из справочника
        if d["equipment_id"] not in eq_map:
            guess = match_equipment(raw) if len(result.incidents) == 1 else None
            if guess:
                d["equipment_id"] = guess
            else:
                d["equipment_id"] = None
                issues.append("equipment")
                questions.append("Какой станок остановился? Выберите из списка.")
        # 2. Простой: число должно подтверждаться текстом
        mins, quote = d.get("downtime_min"), d.get("downtime_quote")
        verified = False
        if mins is not None and quote and _norm(quote) in raw_n:
            q = parse_durations(quote)
            verified = bool(q) and abs(q[0][0] - mins) <= 1
        if mins is not None and not verified:
            same = [t for t in text_durations if abs(t[0] - mins) <= 1]
            if same:
                d["downtime_quote"], verified = same[0][1], True
        if not verified:
            if mins is not None:
                issues.append("downtime_unverified")
            d["downtime_min"] = None if mins is None or not verified else mins
            issues.append("downtime")
            questions.append("Сколько минут длился простой? В тексте не нашлось подтверждения.")
        # 3. Причина не должна быть выдумана: хотя бы одно слово причины есть в тексте
        if d.get("cause"):
            words = [w for w in re.findall(r"\w{4,}", _norm(d["cause"]))]
            if words and not any(w[:5] in raw_n for w in words):
                d["cause"] = None
                issues.append("cause_removed")
        # 4. Уверенность
        if d["confidence"] < config.CONFIDENCE_THRESHOLD:
            issues.append("low_confidence")
        d["issues"] = sorted(set(issues))
        d["needs_review"] = bool(d["issues"])
        d["failure_label"] = FAILURE_LABELS.get(d["failure_type"], d["failure_type"])
        d["equipment_name"] = eq_map[d["equipment_id"]]["name"] if d["equipment_id"] else None
        d["parts_cost_kzt"] = sum(db.part_price(p["name"]) * p["qty"] for p in d.get("parts", []))
        drafts.append(d)
    return drafts, list(dict.fromkeys(questions))


def extract(raw: str) -> dict:
    """Возвращает {drafts, questions, mode, warning}. Никогда не бросает исключение из-за ИИ."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Пустой текст смены. Опишите, что произошло, или вставьте демо-пример.")
    if len(raw) > 4000:
        raise ValueError("Текст слишком длинный (больше 4000 символов). Разбейте отчёт на несколько записей.")
    warning, mode = None, "ai"
    try:
        eq_list = json.dumps([{k: e[k] for k in ("id", "name", "line", "aliases")} for e in db.load_equipment()], ensure_ascii=False)
        types = ", ".join(f"{t.value} ({FAILURE_LABELS[t.value]})" for t in FailureType)
        user = f"Справочник оборудования:\n{eq_list}\n\nТипы неисправностей: {types}\n\nОтчёт мастера:\n\"\"\"{raw}\"\"\""
        result = llm.call_json(SYSTEM_PROMPT, user, ExtractionResult, "extract")
    except llm.LLMError as e:
        result, mode = rule_based(raw), "rules"
        warning = None if not config.ai_enabled() else e.user_message
    drafts, questions = validate(raw, result)
    return {"drafts": drafts, "questions": questions, "mode": mode, "warning": warning}
