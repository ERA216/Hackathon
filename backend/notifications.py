"""Уведомления по SMTP: настройки компании, надёжная очередь и фоновая отправка."""
import logging
import re
import smtplib
import ssl
import threading
from datetime import datetime, timedelta
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import config, db
from .schemas import FAILURE_LABELS

_log = logging.getLogger(__name__)
_worker = None
_worker_lock = threading.Lock()
_wake = threading.Event()
MAX_ATTEMPTS = 5


def configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD and config.SMTP_PORT in (465, 587))


def _admin(user_id: int) -> int:
    with db.connect() as c:
        user = c.execute("SELECT company_id, role FROM users WHERE id=? AND company_id=?",
                         (user_id, db.current_company())).fetchone()
    if not user or user["role"] != "admin":
        raise ValueError("Настройки уведомлений доступны только администратору своей компании.")
    return user["company_id"]


def _email(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        address = Address(addr_spec=value)
        if (len(value) > 254 or not value.isascii() or any(ch.isspace() for ch in value)
                or not address.username or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", address.domain)
                or "." not in address.domain or ".." in address.domain):
            raise ValueError()
    except (ValueError, IndexError, HeaderParseError):
        raise ValueError("Укажите один корректный email, например name@gmail.com.") from None
    return value


def settings(user_id: int) -> dict:
    cid = _admin(user_id)
    with db.connect() as c:
        row = c.execute("SELECT recipient, enabled FROM email_notification_settings WHERE company_id=?", (cid,)).fetchone()
        counts = {r["state"]: r["n"] for r in c.execute(
            "SELECT state, COUNT(*) AS n FROM email_outbox WHERE company_id=? GROUP BY state", (cid,))}
        last = c.execute("SELECT last_error FROM email_outbox WHERE company_id=? AND state='failed' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    return {"recipient": row["recipient"] if row else "", "enabled": bool(row and row["enabled"]),
            "configured": configured(), "pending": counts.get("pending", 0) + counts.get("sending", 0),
            "sent": counts.get("sent", 0), "failed": counts.get("failed", 0),
            "last_error": last["last_error"] if last else None}


def save_settings(user_id: int, recipient: str, enabled: bool) -> None:
    cid = _admin(user_id)
    recipient = _email(recipient)
    if enabled and not recipient:
        raise ValueError("Укажите почту, на которую будут приходить уведомления.")
    with db.connect() as c:
        c.execute("""INSERT INTO email_notification_settings(company_id,recipient,enabled,updated_at) VALUES (?,?,?,?)
                     ON CONFLICT(company_id) DO UPDATE SET recipient=excluded.recipient, enabled=excluded.enabled,
                     updated_at=excluded.updated_at""", (cid, recipient, int(enabled), db.now_iso()))
        # Старые письма не перенаправляются на новый адрес; отключённая подписка отменяет очередь.
        c.execute("""UPDATE email_outbox SET state='cancelled' WHERE company_id=? AND state IN ('pending','failed')
                     AND (recipient<>? OR ?=0)""", (cid, recipient, int(enabled)))
    _wake.set()


def _enqueue(c, cid: int, incident_id, event: str, recipient: str, subject: str, body: str):
    now = db.now_iso()
    c.execute("""INSERT OR IGNORE INTO email_outbox(company_id,incident_id,event,recipient,subject,body,message_id,created_at,next_attempt_at)
                 VALUES (?,?,?,?,?,?,?,?,?)""", (cid, incident_id, event, recipient, subject, body,
                                               make_msgid(domain="fixlist.local"), now, now))


def queue_incidents(incident_ids: list[int], conn) -> None:
    """Вызывается в одной транзакции с новой заявкой. Демо и старые записи не рассылаются."""
    cid = db.current_company()
    subscription = conn.execute("SELECT recipient FROM email_notification_settings WHERE company_id=? AND enabled=1", (cid,)).fetchone()
    if not subscription:
        return
    for incident_id in incident_ids:
        row = conn.execute("""SELECT i.*, s.raw_text, s.author_role, e.name AS equipment_name, co.name AS company_name
                              FROM incidents i JOIN shift_logs s ON s.id=i.log_id JOIN equipment e ON e.id=i.equipment_id
                              JOIN companies co ON co.id=i.company_id WHERE i.id=? AND i.company_id=? AND i.is_demo=0""",
                           (incident_id, cid)).fetchone()
        if not row:
            continue
        equipment_name = " ".join(row["equipment_name"].split())
        subject = f"FixList: новая заявка — {equipment_name}"
        body = (f"В компании «{row['company_name']}» зарегистрирована новая заявка #{row['id']}.\n\n"
                f"Оборудование: {row['equipment_name']}\n"
                f"Тип: {FAILURE_LABELS.get(row['failure_type'], row['failure_type'])}\n"
                f"Время: {row['occurred_at'][:16].replace('T', ' ')}\n"
                f"Простой: {row['downtime_min']} мин\n"
                f"Сообщил: {row['author_role'] or 'Сотрудник'}\n\n"
                f"Описание:\n{row['raw_text'][:8000]}\n\n"
                "Откройте FixList → Панель задач, чтобы посмотреть заявку.\n"
                "Управление уведомлениями: Аккаунт → Уведомления о поломках.")
        _enqueue(conn, cid, incident_id, "incident_created", subscription["recipient"], subject, body)


def queue_test(user_id: int) -> None:
    cid = _admin(user_id)
    current = settings(user_id)
    if not current["enabled"] or not current["recipient"]:
        raise ValueError("Сначала укажите почту и включите уведомления.")
    if not configured():
        raise ValueError("Почта отправителя ещё не подключена на сервере.")
    with db.connect() as c:
        _enqueue(c, cid, None, "test", current["recipient"], "FixList: проверка уведомлений",
                 "Это тестовое письмо FixList. На этот адрес будут приходить уведомления о новых заявках вашей компании.")
    _wake.set()


def retry_failed(user_id: int) -> None:
    cid = _admin(user_id)
    with db.connect() as c:
        c.execute("""UPDATE email_outbox SET state='pending',attempts=0,next_attempt_at=?,last_error=NULL
                     WHERE company_id=? AND state='failed' AND EXISTS (
                         SELECT 1 FROM email_notification_settings n WHERE n.company_id=email_outbox.company_id
                         AND n.enabled=1 AND n.recipient=email_outbox.recipient)""", (db.now_iso(), cid))
    _wake.set()


def _claim():
    now = datetime.now()
    stamp = now.isoformat(timespec="seconds")
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        # Возврат зависшей отправки после перезапуска процесса. SMTP-таймаут короче аренды.
        c.execute("""UPDATE email_outbox SET state=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,locked_at=NULL
                     WHERE state='sending' AND locked_at<?""", (MAX_ATTEMPTS, (now - timedelta(minutes=2)).isoformat(timespec="seconds")))
        row = c.execute("""SELECT o.* FROM email_outbox o JOIN email_notification_settings n ON n.company_id=o.company_id
                           WHERE o.state='pending' AND o.next_attempt_at<=? AND o.attempts<? AND n.enabled=1
                           AND n.recipient=o.recipient ORDER BY o.id LIMIT 1""", (stamp, MAX_ATTEMPTS)).fetchone()
        if row:
            c.execute("UPDATE email_outbox SET state='sending',attempts=attempts+1,locked_at=? WHERE id=?", (stamp, row["id"]))
            return {**dict(row), "attempts": row["attempts"] + 1}
    return None


def _send_email(item: dict) -> None:
    message = EmailMessage()
    message["From"] = str(Address("FixList", addr_spec=config.SMTP_USER))
    message["To"] = item["recipient"]
    message["Subject"] = item["subject"]
    message["Message-ID"] = item["message_id"]
    message["Date"] = formatdate(localtime=True)
    message.set_content(item["body"])
    context = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        transport = smtplib.SMTP_SSL(config.SMTP_HOST, 465, timeout=config.SMTP_TIMEOUT, context=context)
    else:
        transport = smtplib.SMTP(config.SMTP_HOST, 587, timeout=config.SMTP_TIMEOUT)
    with transport as smtp:
        if config.SMTP_PORT == 587:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        refused = smtp.send_message(message, from_addr=config.SMTP_USER, to_addrs=[item["recipient"]])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def process_pending(limit: int = 10) -> int:
    """Ограниченная порция очереди; ошибки SMTP не мешают сохранению заявок."""
    if not configured():
        return 0
    delivered = 0
    for _ in range(limit):
        item = _claim()
        if not item:
            break
        try:
            _send_email(item)
        except Exception as exc:
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                error = "Почтовый сервис отклонил вход отправителя."
            elif isinstance(exc, smtplib.SMTPRecipientsRefused):
                error = "Почтовый сервис отклонил адрес получателя."
            else:
                error = "Не удалось отправить письмо. Проверьте подключение почты отправителя."
            attempts = item["attempts"]
            later = (datetime.now() + timedelta(seconds=min(3600, 60 * 2 ** (attempts - 1)))).isoformat(timespec="seconds")
            with db.connect() as c:
                c.execute("UPDATE email_outbox SET state=?,next_attempt_at=?,locked_at=NULL,last_error=? WHERE id=?",
                          ("failed" if attempts >= MAX_ATTEMPTS else "pending", later, error, item["id"]))
            _log.warning("Email delivery deferred (%s)", type(exc).__name__)
        else:
            with db.connect() as c:
                c.execute("UPDATE email_outbox SET state='sent',sent_at=?,locked_at=NULL,last_error=NULL WHERE id=?", (db.now_iso(), item["id"]))
            delivered += 1
    return delivered


def _run_worker():
    while True:
        _wake.clear()
        try:
            process_pending()
        except Exception as exc:
            _log.warning("Email queue unavailable (%s)", type(exc).__name__)
        _wake.wait(timeout=10)


def start_worker() -> None:
    """Один фоновый отправитель на процесс; работает и без открытого браузера."""
    global _worker
    if not configured():
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="fixlist-email", daemon=True)
            _worker.start()
