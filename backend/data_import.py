"""Append equipment and events from CSV to a company's own workspace."""
import csv
from datetime import datetime
from io import StringIO
import re

from . import db, workspace_data
from .schemas import MAX_DOWNTIME_MIN, FailureType


EQUIPMENT_FIELDS = ("equipment_code", "name", "line", "hour_cost_kzt")
EVENT_FIELDS = ("event_id", "equipment_code", "occurred_at", "failure_type", "description", "downtime_min", "parts_cost_kzt")
MAX_FILE_SIZE = 2 * 1024 * 1024
SOURCE = "company_csv"


def templates():
    equipment = ";".join(EQUIPMENT_FIELDS) + "\r\nP-01;Насос подачи;Участок 1;25000\r\n"
    events = ";".join(EVENT_FIELDS) + "\r\nEV-001;P-01;2026-11-01 10:30;sensor;Датчик давления отключился;45;0\r\n"
    return equipment.encode("utf-8-sig"), events.encode("utf-8-sig")


def _read_csv(content, fields, label, limit):
    if content is None:
        return []
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"{label} — размер CSV должен быть не больше 2 МБ.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(f"{label} — сохраните файл как CSV UTF-8.") from None
    if not text.strip() or "\x00" in text:
        raise ValueError(f"{label} — пустой или некорректный CSV.")
    reader = csv.reader(StringIO(text, newline=""), delimiter=";" if ";" in text.splitlines()[0] else ",", strict=True)
    rows = []
    seen = set()
    try:
        header = [field.strip() for field in next(reader)]
        if len(header) != len(fields) or set(header) != set(fields):
            raise ValueError(f"{label} — используйте столбцы из шаблона: {', '.join(fields)}.")
        while True:
            line = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            if not any(value.strip() for value in values):
                continue
            if len(values) != len(header):
                raise ValueError(f"{label}, строка {line} — число полей не совпадает с заголовком.")
            if len(rows) == limit:
                raise ValueError(f"{label} — за раз можно загрузить не больше {limit} строк.")
            row = dict(zip(header, (value.strip() for value in values)))
            key = row[fields[0]].upper()
            if key in seen:
                raise ValueError(f"{label}, строка {line} — повторяется {fields[0]}.")
            seen.add(key)
            rows.append((f"{label}, строка {line}", row))
    except csv.Error as error:
        raise ValueError(f"{label}, строка {reader.line_num} — некорректный CSV.") from error
    if not rows:
        raise ValueError(f"{label} — добавьте хотя бы одну строку под заголовком.")
    return rows


def _code(value, field, location):
    code = value.upper()
    if not re.fullmatch(r"[\w.-]{1,40}", code):
        raise ValueError(f"{location} — {field}: до 40 букв, цифр, точек, дефисов или подчёркиваний.")
    return code


def _integer(value, field, location, maximum):
    if not re.fullmatch(r"[0-9]{1,10}", value) or int(value) > maximum:
        raise ValueError(f"{location} — {field}: целое число от 0 до {maximum}.")
    return int(value)


def _timestamp(value, location):
    try:
        time = datetime.fromisoformat(value)
        if time.tzinfo is None and 1900 <= time.year <= 2100:
            return time.isoformat(timespec="seconds")
    except ValueError:
        pass
    raise ValueError(f"{location} — occurred_at: укажите местную дату и время в формате ГГГГ-ММ-ДД ЧЧ:ММ.")


def _validated_rows(equipment_csv, events_csv):
    if equipment_csv is None and events_csv is None:
        raise ValueError("Выберите хотя бы один CSV.")

    equipment = _read_csv(equipment_csv, EQUIPMENT_FIELDS, "Оборудование", 500)
    events = _read_csv(events_csv, EVENT_FIELDS, "Журнал событий", 1000)
    for location, row in equipment:
        row["equipment_code"] = _code(row["equipment_code"], "equipment_code", location)
        if not 1 <= len(row["name"]) <= 120 or len(row["line"]) > 120:
            raise ValueError(f"{location} — укажите название оборудования. Название и участок могут содержать до 120 символов.")
        row["hour_cost_kzt"] = _integer(row["hour_cost_kzt"], "hour_cost_kzt", location, 1_000_000_000)
    for location, row in events:
        row["event_id"] = _code(row["event_id"], "event_id", location)
        row["equipment_code"] = _code(row["equipment_code"], "equipment_code", location)
        row["occurred_at"] = _timestamp(row["occurred_at"], location)
        try:
            row["failure_type"] = FailureType(row["failure_type"].lower()).value
        except ValueError:
            raise ValueError(f"{location} — выберите failure_type из списка на экране импорта.") from None
        if not 1 <= len(row["description"]) <= 4000:
            raise ValueError(f"{location} — укажите описание от 1 до 4000 символов.")
        row["downtime_min"] = _integer(row["downtime_min"], "downtime_min", location, MAX_DOWNTIME_MIN)
        row["parts_cost_kzt"] = _integer(row["parts_cost_kzt"], "parts_cost_kzt", location, 1_000_000_000)
    return equipment, events


def _write_import(conn, company, author, equipment, events):
    result = {"equipment_added": 0, "events_added": 0, "equipment_skipped": 0, "events_skipped": 0}
    catalog = {row["code"]: row for row in conn.execute(
        """SELECT ce.code, e.id, e.name, e.line, e.hour_cost_kzt FROM company_equipment ce
           JOIN equipment e ON e.id=ce.equipment_id WHERE ce.company_id=?""", (company,))}
    for location, row in equipment:
        code = row["equipment_code"]
        details = (row["name"], row["line"], row["hour_cost_kzt"])
        existing = catalog.get(code)
        if existing:
            if details != (existing["name"], existing["line"], existing["hour_cost_kzt"]):
                raise ValueError(f"{location} — оборудование с кодом {code} уже существует с другими параметрами.")
            result["equipment_skipped"] += 1
            continue
        equipment_id = workspace_data.insert_equipment(conn, company, code, *details)
        catalog[code] = {"id": equipment_id}
        result["equipment_added"] += 1

    for location, row in events:
        code = row["equipment_code"]
        if code not in catalog:
            raise ValueError(f"{location} — код {code} отсутствует в справочнике компании. Загрузите оборудование вместе с журналом или заранее.")
        if conn.execute("SELECT 1 FROM incidents WHERE company_id=? AND source_dataset=? AND source_id=?",
                        (company, SOURCE, row["event_id"])).fetchone():
            result["events_skipped"] += 1
            continue
        incident_id = workspace_data.insert_event(conn, company, catalog[code]["id"], row["description"], row["occurred_at"],
                                                  row["failure_type"], row["downtime_min"], row["parts_cost_kzt"], author, mode="csv")
        conn.execute("UPDATE incidents SET source_dataset=?, source_id=? WHERE id=?", (SOURCE, row["event_id"], incident_id))
        result["events_added"] += 1
    return result


def import_company_data(user_id, equipment_csv=None, events_csv=None):
    equipment, events = _validated_rows(equipment_csv, events_csv)
    company = db.current_company()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        author = workspace_data.require_admin(conn, user_id, company)
        return _write_import(conn, company, author, equipment, events)


def start_with_import(workspace_name, equipment_csv, events_csv=None):
    if equipment_csv is None:
        raise ValueError("Для нового кабинета загрузите CSV со справочником оборудования.")
    equipment, events = _validated_rows(equipment_csv, events_csv)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        account = workspace_data.create_account(conn, workspace_name)
        account["import_result"] = _write_import(conn, account["company"]["id"], account["admin"]["display_name"], equipment, events)
    return account
