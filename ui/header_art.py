"""Local, decorative illustrations for each FixList section."""
from pathlib import Path


_SECTIONS = {
    "панель задач": "tasks",
    "спросить ии": "assistant",
    "состояние оборудования": "equipment",
    "сводка дня": "daily",
    "повторяющиеся сигналы": "signals",
    "сигналы производства": "signals",
    "деньги и экономия": "savings",
    "план экономии": "savings",
    "история": "history",
    "история и эффект": "history",
    "сотрудники": "team",
    "мои задачи": "assigned",
    "сообщить о проблеме": "report",
    "запись смены": "shift",
    "отчёт об обходе": "rounds",
}


def get_header_art(kicker):
    section = kicker.rsplit("/", 1)[-1].strip().casefold()
    name = _SECTIONS.get(section, "tasks")
    return (Path(__file__).with_name("illustrations") / f"{name}.svg").read_text(encoding="utf-8")
