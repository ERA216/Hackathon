"""Проверки уведомлений: изолированная SQLite и подменённый SMTP, без настоящих писем."""
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import auth, config, db, notifications, service


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_patch = patch.multiple(
            config, DB_PATH=Path(self.temp.name) / "notifications.db", LLM_PROVIDER="mock",
            SMTP_HOST="smtp.gmail.com", SMTP_PORT=465, SMTP_USER="sender@gmail.com", SMTP_PASSWORD="test-only-password",
        )
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        service.reset_demo()
        demo = service.demo_credentials()
        admin = next(u for u in demo["users"] if u["role"] == "admin")
        worker = next(u for u in demo["users"] if u["role"] == "worker")
        self.admin = service.login(demo["code"], admin["login"], admin["pin"])
        self.worker = service.login(demo["code"], worker["login"], worker["pin"])
        db.set_company(self.admin["company_id"])
        self.addCleanup(db.set_company, 1)

    def subscribe(self, recipient="owner@gmail.com"):
        service.save_notification_settings(self.admin["id"], recipient, True)

    def record(self):
        return service.save_record("Станок №3 перегрелся. Остановили на 15 минут.", [
            {"equipment_id": "M3", "failure_type": "overheating", "downtime_min": 15, "parts": [], "confidence": 1.0},
        ], mode="rules", author="Мастер смены 1")

    def outbox(self):
        with db.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM email_outbox ORDER BY id")]

    def test_email_required_and_invalid_headers_rejected(self):
        for value in ("", "not-an-email", "a@gmail.com\r\nBcc: evil@gmail.com", "a@gmail.com,b@gmail.com", "a@localhost", "a b@gmail.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.save_notification_settings(self.admin["id"], value, True)
        service.save_notification_settings(self.admin["id"], "", False)
        self.assertFalse(service.notification_settings(self.admin["id"])["enabled"])

    def test_worker_cannot_read_or_change_company_subscription(self):
        with self.assertRaises(ValueError):
            service.save_notification_settings(self.worker["id"], "worker@gmail.com", True)
        with self.assertRaises(ValueError):
            service.notification_settings(self.worker["id"])

    def test_new_record_is_queued_once_with_problem_details(self):
        self.subscribe()
        result = self.record()
        with db.connect() as c:
            notifications.queue_incidents(result["incident_ids"], conn=c)
        messages = self.outbox()
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["recipient"], "owner@gmail.com")
        self.assertEqual(message["incident_id"], result["incident_ids"][0])
        for text in ("Станок №3", "15 мин", "Мастер смены 1", "перегрелся"):
            self.assertIn(text, message["body"])
        service.dashboard()
        service.ticket_counts()
        self.assertEqual(len(self.outbox()), 1)

    def test_queue_is_atomic_with_the_incident(self):
        self.subscribe()
        before = len(service.tickets())
        original = notifications.queue_incidents

        def fail_after_insert(ids, conn):
            original(ids, conn)
            raise RuntimeError("interrupted transaction")

        with patch.object(notifications, "queue_incidents", side_effect=fail_after_insert), self.assertRaises(RuntimeError):
            self.record()
        self.assertEqual(len(service.tickets()), before)
        self.assertEqual(self.outbox(), [])

    def test_manual_board_event_queues_mail_for_its_company(self):
        account = service.start_workspace("Мой кабинет", "Насос")
        service.use_company(account["company"]["id"])
        admin = account["admin"]["id"]
        service.save_notification_settings(admin, "owner@example.com", True)
        event_id = service.add_workspace_event(admin, account["equipment_id"], "Насос остановился", datetime.now(), "other", 15)
        messages = self.outbox()
        self.assertEqual([(row["incident_id"], row["recipient"]) for row in messages], [(event_id, "owner@example.com")])
        self.assertIn("15 мин", messages[0]["body"])

    def test_inspection_problems_and_remarks_notify_but_ok_does_not(self):
        self.subscribe()
        result = service.save_inspection([
            {"equipment_id": "M1", "state": "ok", "note": ""},
            {"equipment_id": "M2", "state": "remark", "note": "Вибрация", "downtime_min": 0},
            {"equipment_id": "M3", "state": "problem", "note": "Перегрев", "downtime_min": 20},
        ], "Мастер смены 1")
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(len(self.outbox()), 2)
        self.assertEqual({row["incident_id"] for row in self.outbox()}, set(result["cards"]))

    def test_demo_and_existing_records_are_not_backfilled(self):
        ids = [t["id"] for t in service.tickets()]
        self.subscribe()
        service.bootstrap()
        with db.connect() as c:
            notifications.queue_incidents(ids, conn=c)
        self.assertEqual(self.outbox(), [])

    def test_company_recipients_and_access_remain_isolated(self):
        self.subscribe()
        first = self.record()
        other = service.register_company("Другая компания")
        other_id = other["company"]["id"]
        db.set_company(other_id)
        with self.assertRaises(ValueError):
            service.notification_settings(self.admin["id"])
        service.save_notification_settings(other["admin"]["id"], "other@gmail.com", True)
        second = self.record()
        with db.connect() as c:
            notifications.queue_incidents(first["incident_ids"], conn=c)
        messages = self.outbox()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["recipient"], "owner@gmail.com")
        self.assertEqual(messages[1]["recipient"], "other@gmail.com")
        self.assertEqual(messages[1]["incident_id"], second["incident_ids"][0])
        self.assertNotIn("Другая компания", messages[0]["body"])
        self.assertIn("Другая компания", messages[1]["body"])
        self.assertEqual(service.notification_settings(other["admin"]["id"])["pending"], 1)

    def test_disabled_notifications_and_changed_recipient_cancel_pending(self):
        self.subscribe()
        self.record()
        self.subscribe("new@gmail.com")
        self.assertEqual(self.outbox()[0]["state"], "cancelled")
        self.record()
        self.assertEqual(self.outbox()[1]["recipient"], "new@gmail.com")
        service.save_notification_settings(self.admin["id"], "new@gmail.com", False)
        self.assertTrue(all(row["state"] == "cancelled" for row in self.outbox()))
        self.record()
        self.assertEqual(len(self.outbox()), 2)

    def test_no_sender_configuration_leaves_queue_intact(self):
        self.subscribe()
        self.record()
        with patch.object(config, "SMTP_PASSWORD", ""), patch.object(notifications, "_send_email") as send:
            self.assertEqual(notifications.process_pending(), 0)
            send.assert_not_called()
        self.assertEqual(self.outbox()[0]["attempts"], 0)
        self.assertEqual(self.outbox()[0]["state"], "pending")

    def test_smtp_success_is_not_repeated(self):
        self.subscribe()
        self.record()
        with patch.object(notifications, "_send_email") as send:
            self.assertEqual(notifications.process_pending(), 1)
            self.assertEqual(notifications.process_pending(), 0)
            send.assert_called_once()
        row = self.outbox()[0]
        self.assertEqual(row["state"], "sent")
        self.assertTrue(row["sent_at"])

    def test_failure_retries_without_losing_incident_and_hides_credentials(self):
        self.subscribe()
        result = self.record()
        error = notifications.smtplib.SMTPAuthenticationError(535, b"test-only-password")
        message_id = self.outbox()[0]["message_id"]
        with patch.object(notifications, "_send_email", side_effect=error):
            for attempt in range(notifications.MAX_ATTEMPTS):
                with db.connect() as c:
                    c.execute("UPDATE email_outbox SET next_attempt_at='2000-01-01T00:00:00'")
                notifications.process_pending()
        row = self.outbox()[0]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], 5)
        self.assertNotIn("test-only-password", row["last_error"])
        self.assertIn(result["incident_ids"][0], [t["id"] for t in service.tickets()])
        self.assertEqual(service.notification_settings(self.admin["id"])["failed"], 1)
        service.retry_notifications(self.admin["id"])
        with patch.object(notifications, "_send_email") as send:
            notifications.process_pending()
        self.assertEqual(send.call_args.args[0]["message_id"], message_id)
        self.assertEqual(self.outbox()[0]["state"], "sent")

    def test_two_workers_do_not_claim_the_same_message(self):
        self.subscribe()
        self.record()
        with patch.object(notifications, "_send_email") as send, ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: notifications.process_pending(limit=1), range(2)))
        self.assertEqual(sum(results), 1)
        send.assert_called_once()

    def test_restart_recovers_stale_lease_with_same_message_id(self):
        self.subscribe()
        self.record()
        claim = notifications._claim()
        with db.connect() as c:
            c.execute("UPDATE email_outbox SET locked_at=?", ((datetime.now() - timedelta(minutes=3)).isoformat(timespec="seconds"),))
        with patch.object(notifications, "_send_email") as send:
            self.assertEqual(notifications.process_pending(), 1)
        self.assertEqual(send.call_args.args[0]["message_id"], claim["message_id"])

    def test_smtp_ssl_transport_receives_russian_message(self):
        self.subscribe()
        self.record()
        with patch.object(notifications.smtplib, "SMTP_SSL") as transport:
            smtp = transport.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            self.assertEqual(notifications.process_pending(), 1)
            smtp.login.assert_called_once_with("sender@gmail.com", "test-only-password")
            message = smtp.send_message.call_args.args[0]
            self.assertEqual(str(message["To"]), "owner@gmail.com")
            self.assertIn("Станок №3", str(message["Subject"]))
            self.assertIn("перегрелся", message.get_content())
            self.assertNotIn("test-only-password", message.as_string())
            context = transport.call_args.kwargs["context"]
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, notifications.ssl.CERT_REQUIRED)

    def test_starttls_precedes_authentication_on_port_587(self):
        self.subscribe()
        self.record()
        with patch.object(config, "SMTP_PORT", 587), patch.object(notifications.smtplib, "SMTP") as transport:
            smtp = transport.return_value.__enter__.return_value
            smtp.send_message.return_value = {}
            self.assertEqual(notifications.process_pending(), 1)
            names = [call[0] for call in smtp.method_calls]
            self.assertLess(names.index("starttls"), names.index("login"))

    def test_test_email_requires_saved_recipient_and_sender(self):
        with self.assertRaises(ValueError):
            service.test_notification(self.admin["id"])
        self.subscribe()
        with patch.object(config, "SMTP_PASSWORD", ""), self.assertRaises(ValueError):
            service.test_notification(self.admin["id"])
        service.test_notification(self.admin["id"])
        row = self.outbox()[0]
        self.assertEqual(row["event"], "test")
        self.assertIsNone(row["incident_id"])
        self.assertEqual(row["recipient"], "owner@gmail.com")


if __name__ == "__main__":
    unittest.main()
