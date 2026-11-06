"""Компании и сотрудники: заявка на подключение, вход по коду компании + логину + PIN, добавление сотрудников.

PIN хранится только в виде хеша (PBKDF2-SHA256 с солью). Персональных данных не требуется:
сотрудник может быть «Мастер смены 1», а логин генерируется автоматически.
"""
import hashlib
import hmac
import re
import secrets
from typing import Optional

from . import db

ROLE_LABELS = {"admin": "Администратор", "worker": "Сотрудник"}


def hash_pin(pin: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def check_pin(pin: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pin(pin, salt), stored)


def _new_pin() -> str:
    return f"{secrets.randbelow(9000) + 1000}"


def _company_code(name: str) -> str:
    letters = re.sub(r"[^A-ZА-ЯЁӘҒҚҢӨҰҮҺІ]", "", name.upper())[:3]
    translit = str.maketrans("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЫЭЮЯӘҒҚҢӨҰҮҺІ", "ABVGDEEJZIIKLMNOPRSTUFHCCSSYEUAAGKNOUUHI")
    base = (letters.translate(translit) or "CO")[:3]
    return f"{base}{secrets.randbelow(900) + 100}"


def create_company(name: str, city: str = "", is_demo: bool = False, code: Optional[str] = None) -> dict:
    name = (name or "").strip()
    if len(name) < 2:
        raise ValueError("Укажите название компании.")
    with db.connect() as c:
        for _ in range(10):
            code_ = code or _company_code(name)
            if not c.execute("SELECT 1 FROM companies WHERE code=?", (code_,)).fetchone():
                break
        cid = c.execute("INSERT INTO companies(name, code, city, created_at, is_demo) VALUES (?,?,?,?,?)",
                        (name, code_, city.strip(), db.now_iso(), int(is_demo))).lastrowid
    return {"id": cid, "name": name, "code": code_, "city": city}


def add_user(company_id: int, display_name: str, role: str, login: Optional[str] = None, pin: Optional[str] = None) -> dict:
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Пустое имя сотрудника.")
    if role not in ROLE_LABELS:
        raise ValueError("Неизвестная роль.")
    pin = pin or _new_pin()
    with db.connect() as c:
        if not login:
            n = c.execute("SELECT COUNT(*) FROM users WHERE company_id=?", (company_id,)).fetchone()[0] + 1
            prefix = {"admin": "admin", "worker": "w"}[role]
            login = f"{prefix}{n}"
            while c.execute("SELECT 1 FROM users WHERE company_id=? AND login=?", (company_id, login)).fetchone():
                n += 1
                login = f"{prefix}{n}"
        uid = c.execute("INSERT INTO users(company_id, login, display_name, role, pin_hash, created_at) VALUES (?,?,?,?,?,?)",
                        (company_id, login, display_name, role, hash_pin(pin), db.now_iso())).lastrowid
    return {"id": uid, "login": login, "pin": pin, "display_name": display_name, "role": role}


def register_company(name: str, city: str = "", note: str = "") -> dict:
    """Заявка на подключение. В прототипе кабинет создаётся сразу; в жизни мы связываемся и настраиваем оборудование."""
    comp = create_company(name, city)
    admin = add_user(comp["id"], "Администратор", "admin", login="admin")
    with db.connect() as c:
        c.execute("INSERT INTO leads(created_at, company_name, city, note, company_id) VALUES (?,?,?,?,?)",
                  (db.now_iso(), comp["name"], city.strip(), (note or "").strip()[:500], comp["id"]))
    return {"company": comp, "admin": admin}


def add_workers(company_id: int, names_text: str, role: str = "worker") -> list[dict]:
    names = [n.strip() for n in (names_text or "").splitlines() if n.strip()]
    if not names:
        raise ValueError("Впишите сотрудников — по одному на строку.")
    if len(names) > 50:
        raise ValueError("За раз можно добавить не больше 50 сотрудников.")
    return [add_user(company_id, n, role) for n in names]


def login(company_code: str, login_: str, pin: str) -> Optional[dict]:
    with db.connect() as c:
        r = c.execute("""SELECT u.*, c.name AS company_name, c.code AS company_code, c.is_demo FROM users u
                         JOIN companies c ON c.id = u.company_id WHERE upper(c.code)=upper(?) AND lower(u.login)=lower(?)""",
                      ((company_code or "").strip(), (login_ or "").strip())).fetchone()
    if not r or not check_pin((pin or "").strip(), r["pin_hash"]):
        return None
    u = dict(r)
    u.pop("pin_hash")
    return u


def list_users(company_id: int) -> list[dict]:
    with db.connect() as c:
        return [dict(r) for r in c.execute("SELECT id, login, display_name, role, created_at FROM users WHERE company_id=? ORDER BY id", (company_id,))]


# ---------- сессия: подписанный токен в адресе страницы ----------
def _secret() -> bytes:
    from . import config
    path = config.ROOT / ".session_secret"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


def make_token(user_id: int) -> str:
    sig = hmac.new(_secret(), str(user_id).encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{sig}"


def user_from_token(token: str) -> Optional[dict]:
    try:
        uid, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(make_token(int(uid)), token):
            return None
    except (ValueError, TypeError):
        return None
    with db.connect() as c:
        r = c.execute("""SELECT u.*, c.name AS company_name, c.code AS company_code, c.is_demo FROM users u
                         JOIN companies c ON c.id = u.company_id WHERE u.id=?""", (int(uid),)).fetchone()
    if not r:
        return None
    u = dict(r)
    u.pop("pin_hash")
    return u
