"""Write equipment and events to a private FixList workspace."""
from datetime import datetime
import re
import secrets

from . import auth, db, extract, notifications
from .schemas import MAX_DOWNTIME_MIN, FailureType


def require_admin(conn, user_id, company_id):
    user = conn.execute("""SELECT u.display_name FROM users u JOIN companies c ON c.id=u.company_id
                           WHERE u.id=? AND u.company_id=? AND u.role='admin'
                           AND c.is_demo=0 AND c.code<>'FIXLIST'
                           AND NOT EXISTS (SELECT 1 FROM company_sources WHERE company_id=c.id)""",
                        (user_id, company_id)).fetchone()
    if not user:
        raise ValueError("Добавление данных доступно администратору собственного кабинета.")
    return user["display_name"]


def insert_equipment(conn, company_id, code, name, line, hour_cost_kzt):
    equipment_id = f"company:{company_id}:{code}"
    conn.execute("INSERT INTO equipment(id,name,line,hour_cost_kzt) VALUES (?,?,?,?)", (equipment_id, name, line, hour_cost_kzt))
    conn.execute("INSERT INTO company_equipment(company_id,code,equipment_id) VALUES (?,?,?)", (company_id, code, equipment_id))
    return equipment_id


def insert_event(conn, company_id, equipment_id, description, occurred_at, failure_type, downtime_min, parts_cost_kzt, author, *, mode):
    log_id = db.add_shift_log(description, "processed", mode, occurred_at, conn=conn, author=author, company_id=company_id)
    return db.add_incident(log_id, {"equipment_id": equipment_id, "failure_type": failure_type,
                                  "downtime_min": downtime_min, "parts_cost_kzt": parts_cost_kzt}, occurred_at, conn=conn, company_id=company_id)


def _number(value, maximum, label):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label}: укажите целое число от 0 до {maximum}.")
    return value


def _equipment_fields(name, code, line, hour_cost_kzt):
    name, code, line = name.strip(), code.strip().upper(), line.strip()
    if not name or len(name) > 120 or len(line) > 120:
        raise ValueError("Укажите название оборудования. Название и участок могут содержать до 120 символов.")
    if code and not re.fullmatch(r"[\w.-]{1,40}", code):
        raise ValueError("Код может содержать до 40 букв, цифр, точек, дефисов или подчёркиваний.")
    tariff = _number(hour_cost_kzt, 1_000_000_000, "Стоимость часа простоя")
    return code or f"EQ-{secrets.token_hex(4).upper()}", name, line, tariff


def create_account(conn, workspace_name):
    workspace_name = workspace_name.strip()
    if not 2 <= len(workspace_name) <= 120:
        raise ValueError("Укажите название кабинета из 2–120 символов.")
    company = auth.create_company(workspace_name, conn=conn)
    pin = f"{secrets.randbelow(90_000_000) + 10_000_000}"
    admin = auth.add_user(company["id"], "Администратор", "admin", login="admin", pin=pin, conn=conn)
    return {"company": company, "admin": admin}


def start_workspace(workspace_name, name, code="", line="", hour_cost_kzt=0):
    fields = _equipment_fields(name, code, line, hour_cost_kzt)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = create_account(conn, workspace_name)
        account["equipment_id"] = insert_equipment(conn, account["company"]["id"], *fields)
    return account


def add_equipment(user_id, name, code="", line="", hour_cost_kzt=0):
    fields = _equipment_fields(name, code, line, hour_cost_kzt)
    company = db.current_company()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        require_admin(conn, user_id, company)
        if conn.execute("SELECT 1 FROM company_equipment WHERE company_id=? AND code=?", (company, fields[0])).fetchone():
            raise ValueError(f"Код {fields[0]} уже используется. Укажите другой код или оставьте поле пустым.")
        return insert_equipment(conn, company, *fields)


def _equipment_by_name(conn, company, name):
    name = " ".join(name.split())
    if not name:
        raise ValueError("Выберите оборудование или введите его название.")
    catalog = conn.execute("""SELECT e.id, e.name, ce.code FROM company_equipment ce
                              JOIN equipment e ON e.id=ce.equipment_id WHERE ce.company_id=?""", (company,))
    key = name.casefold()
    matches = [row["id"] for row in catalog if key in (" ".join(row["name"].split()).casefold(), row["code"].casefold())]
    if len(matches) > 1:
        raise ValueError("У нескольких объектов такое название. Выберите нужный объект по коду в списке.")
    if matches:
        return matches[0]
    return insert_equipment(conn, company, *_equipment_fields(name, "", "", 0))


def add_event(user_id, equipment_id, description, occurred_at, failure_type, downtime_min=0, parts_cost_kzt=0, *, equipment_name=None):
    description = description.strip()
    if not 1 <= len(description) <= 4000:
        raise ValueError("Опишите, что произошло (до 4000 символов).")
    if occurred_at.tzinfo is not None or not 1900 <= occurred_at.year <= 2100 or occurred_at.date() > datetime.now().date():
        raise ValueError("Укажите местную дату и время события. Дата не должна быть в будущем.")
    try:
        kind = FailureType(failure_type or extract.detect_failure(description)).value
    except ValueError:
        raise ValueError("Выберите тип события из списка.") from None
    downtime = _number(downtime_min, MAX_DOWNTIME_MIN, "Длительность простоя")
    cost = _number(parts_cost_kzt, 1_000_000_000, "Стоимость запчастей")
    company = db.current_company()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        author = require_admin(conn, user_id, company)
        if equipment_name is not None:
            equipment_id = _equipment_by_name(conn, company, equipment_name)
        elif not equipment_id:
            raise ValueError("Выберите оборудование или введите его название.")
        elif not conn.execute("SELECT 1 FROM company_equipment WHERE company_id=? AND equipment_id=?", (company, equipment_id)).fetchone():
            raise ValueError("Выберите оборудование из своего справочника.")
        event_id = insert_event(conn, company, equipment_id, description, occurred_at.isoformat(timespec="seconds"),
                                kind, downtime, cost, author, mode="manual")
        notifications.queue_incidents([event_id], conn=conn)
        return event_id
