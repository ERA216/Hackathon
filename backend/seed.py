"""Демо-данные (помечены is_demo=1). Даты считаются от сегодняшнего дня, чтобы демо всегда было свежим.

Заложенные закономерности:
- Станок №3, ремень: 4 поломки за 30 дней, интервалы 12 → 8 → 5 дней, потери 224 000 ₸.
- Станок №2, ремень: мера MS02 внедрена 40 дней назад, интервал вырос с 5 до 18 дней.
"""
from datetime import datetime, timedelta

from . import auth, db

DEMO_CODE = "FIXLIST"
OLD_CODES = ("DEMO",)
COMPANY_NAME = "Цех №1 · Павлодар"
DEMO_USERS = [("Администратор цеха", "admin", "boss", "1111"), ("Мастер смены 1", "worker", "master", "2222")]

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
    # Станок №4: перегрев повторяется, интервалы 10 → 7 → 4, в записях — пыль на радиаторе
    ("M4", 24, "overheating", 35, [], "Станок №4 перегрелся к обеду, остановили на {m} мин, дали остыть."),
    ("M4", 14, "overheating", 40, [], "Токарный №4 опять горячий, на радиаторе пыль. Простой {m} мин."),
    ("M4", 7, "overheating", 45, [], "Станок 4 снова перегрев, продули радиатор, стояли {m} мин."),
    ("M4", 3, "overheating", 50, [], "Ст. №4 перегрелся, работали без остановки вторую смену. Простой {m} мин."),
    # Станок №5: подшипник гудит
    ("M5", 20, "bearing", 30, [], "Фрезерный №5 гудит, остановили на {m} мин, подшипник смазали."),
    ("M5", 5, "bearing", 70, [{"name": "подшипник", "qty": 1}], "Станок №5 сильная вибрация и гул, заменили подшипник, простой {m} мин."),
    # Станок №6: разовый случай с датчиком
    ("M6", 11, "sensor", 15, [], "Станок №6 не видит заготовку, датчик в стружке — почистили, {m} мин."),
    # Насос Н-1: течь сальника
    ("N1", 18, "lubrication", 25, [], "Насос Н-1 подтекает, подтянули сальник, простой {m} мин."),
    ("N1", 4, "lubrication", 35, [{"name": "сальник", "qty": 1}], "Насос Н-1 опять течёт, лужа под насосом, заменили сальник, простой {m} мин."),
]
# записи текущего дня (утренняя смена): время раньше полудня, чтобы не оказаться «в будущем»
TODAY = [
    ("M6", "sensor", 20, [], "Станок №6 снова не видит заготовку, датчик в стружке. Почистили, простой {m} мин.", 8, 40),
    ("C1", "electrical", 15, [], "Компрессор отключился, свет в цеху моргнул. Включили через {m} минут.", 10, 5),
]


def _ts(days_ago: int, hour: int = 14, minute: int = 10) -> str:
    d = datetime.now() - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(timespec="seconds")


def ensure_demo_company() -> int:
    with db.connect() as c:
        for old in OLD_CODES:  # базы прошлых версий
            c.execute("UPDATE companies SET code=?, name=? WHERE code=? AND NOT EXISTS (SELECT 1 FROM companies WHERE code=?)",
                      (DEMO_CODE, COMPANY_NAME, old, DEMO_CODE))
        r = c.execute("SELECT id FROM companies WHERE code=?", (DEMO_CODE,)).fetchone()
        if r:
            have = {x["login"] for x in c.execute("SELECT login FROM users WHERE company_id=?", (r["id"],))}
    if r:
        for name, role, login, pin in DEMO_USERS:  # базы прошлых версий: добавить недостающих демо-пользователей
            if login not in have:
                auth.add_user(r["id"], name, role, login=login, pin=pin)
        return r["id"]
    comp = auth.create_company(COMPANY_NAME, "Павлодар", is_demo=True, code=DEMO_CODE)
    for name, role, login, pin in DEMO_USERS:
        auth.add_user(comp["id"], name, role, login=login, pin=pin)
    return comp["id"]


def seed_demo() -> None:
    db.set_company(ensure_demo_company())
    with db.connect() as c:
        def add(eq, days_ago, ftype, mins, parts, text, action=None):
            ts = _ts(days_ago)
            log_id = db.add_shift_log(text.format(m=mins), "processed", "demo", ts, True, conn=c, author="Мастер смены 1")
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
        for eq, ftype, mins, parts, text, hh, mm in TODAY:
            now = datetime.now()
            if (hh, mm) >= (now.hour, now.minute):  # ранним утром не создаём записи «из будущего»
                continue
            ts = _ts(0, hh, mm)
            log_id = db.add_shift_log(text.format(m=mins), "processed", "demo", ts, True, conn=c, author="Мастер смены 1")
            db.add_incident(log_id, {"equipment_id": eq, "failure_type": ftype, "downtime_min": mins, "parts": parts,
                                     "cause": None, "action": None, "confidence": 1.0}, ts, True, conn=c)
        # утренний обход (без карточек): большинство станков в норме, у станка №5 замечание
        now = datetime.now()
        if (now.hour, now.minute) > (9, 0):
            for eq in ("M1", "M2", "M6", "P1", "K1", "C1", "N1"):
                db.add_inspection(eq, "ok", None, "Мастер смены 1", None, _ts(0, 9, 0), True, conn=c)
            db.add_inspection("M5", "remark", "Подшипник шумит сильнее, чем вчера", "Мастер смены 1", None, _ts(0, 9, 0), True, conn=c)
        # статусы: прошлые заявки решены, сегодняшние ждут одобрения; станок №4 решают сотрудники; станок №3 — нужна помощь
        cid = db.current_company()
        c.execute("""UPDATE incidents SET status='resolved', assignee='Мастер смены 1', approved_by='Администратор цеха',
                     resolution=COALESCE(action, 'Устранено'), resolved_at=datetime(occurred_at, '+2 hours')
                     WHERE company_id=? AND date(occurred_at) < date('now','localtime')""", (cid,))
        for eq, status, note in (("M4", "approved", None),
                                 ("M3", "escalated", "Ремень меняем уже 4-й раз за месяц. Похоже на натяжитель — такой запчасти на складе нет, нужна закупка.")):
            last = c.execute("SELECT MAX(id) FROM incidents WHERE company_id=? AND equipment_id=?", (cid, eq)).fetchone()[0]
            if last:
                c.execute("""UPDATE incidents SET status=?, resolution=NULL, resolved_at=NULL, approved_by='Администратор цеха',
                             escalation_note=?, assignee=CASE WHEN ?='escalated' THEN 'Мастер смены 1' ELSE NULL END WHERE id=?""",
                          (status, note, status, last))


def reset_demo() -> None:
    """Пересоздать данные демо-компании. Данные других компаний не трогаются."""
    db.init_db()
    cid = ensure_demo_company()
    with db.connect() as c:
        for t in ("incidents", "recommendations", "applied_measures", "shift_logs", "inspections"):
            c.execute(f"DELETE FROM {t} WHERE company_id=?", (cid,))
    seed_demo()
