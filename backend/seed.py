"""Демо-данные (помечены is_demo=1). Даты считаются от сегодняшнего дня, чтобы демо всегда было свежим.

Заложенные закономерности:
- Станок №3, ремень: 4 поломки за 30 дней, интервалы 12 → 8 → 5 дней, потери 224 000 ₸.
- Станок №2, ремень: мера MS02 внедрена 40 дней назад, интервал вырос с 5 до 18 дней.
"""
from datetime import datetime, timedelta

from . import db

M3_BELT = [(27, 84, "Линия 2, станок №3 встал в 14:10, порвался ремень. Поставили новый, простой {m} мин."),
           (15, 108, "Станок №3 — опять ремень, лопнул на запуске. Заменили ремень, стояли {m} мин."),
           (7, 90, "Ст. №3: разрыв ремня, замена ремня. Простой 1,5 часа."),
           (2, 102, "Станок 3 стоял {m} минут, ремень порвался. Белдік ауыстырылды (ремень заменили).")]
M2_BELT_BEFORE = [70, 65, 60, 55, 50, 45]
M2_BELT_AFTER = [22, 4]
OTHER = [
    ("P1", 25, "overheating", 45, [], "Пресс П-1 перегрелся, остановили на {m} мин, остыл — запустили."),
    ("P1", 9, "overheating", 35, [], "Пресс П-1 снова греется, простой {m} мин."),
    ("K1", 26, "sensor", 30, [{"name": "датчик", "qty": 1}], "Конвейер К-1: датчик ленты сбоит, заменили датчик, простой {m} мин."),
    ("K1", 16, "sensor", 25, [], "Конвейер К-1 встал из-за датчика, почистили, простой {m} мин."),
    ("K1", 6, "sensor", 20, [], "Конвейер: опять датчик, перезапуск, {m} мин простоя."),
    ("C1", 19, "electrical", 50, [{"name": "предохранитель", "qty": 1}], "Компрессор: выбило автомат, заменили предохранитель, простой {m} мин."),
    ("M1", 12, "lubrication", 40, [], "Станок №1 — не хватило смазки, шумел, остановили на {m} мин, смазали."),
]


def _ts(days_ago: int, hour: int = 14, minute: int = 10) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(timespec="seconds")


def seed_demo() -> None:
    with db.connect() as c:
        def add(eq, days_ago, ftype, mins, parts, text, action=None):
            ts = _ts(days_ago)
            log_id = db.add_shift_log("ДЕМО · " + text.format(m=mins), "processed", "demo", ts, True, conn=c)
            db.add_incident(log_id, {"equipment_id": eq, "failure_type": ftype, "downtime_min": mins, "parts": parts,
                                     "cause": None, "action": action, "confidence": 1.0}, ts, True, conn=c)

        belt = [{"name": "ремень", "qty": 1}]
        for days_ago, mins, text in M3_BELT:
            add("M3", days_ago, "belt_break", mins, belt, text, "замена ремня")
        for d in M2_BELT_BEFORE:
            add("M2", d, "belt_break", 60, belt, "Станок №2: порвался ремень, заменили, простой {m} мин.", "замена ремня")
        db.apply_measure("M2", "MS02", _ts(40, 9, 0), True, conn=c)
        for d in M2_BELT_AFTER:
            add("M2", d, "belt_break", 45, belt, "Станок №2: ремень износился, заменили, простой {m} мин.", "замена ремня")
        for eq, d, ftype, mins, parts, text in OTHER:
            add(eq, d, ftype, mins, parts, text)


def reset_demo() -> None:
    """Пересоздать базу с нуля (для репетиции демо)."""
    if db.config.DB_PATH.exists():
        db.config.DB_PATH.unlink()
    db.init_db()
    seed_demo()
