"""Check CSV import against an isolated SQLite database."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
from time import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config, db, service


class DataImportTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temp)
        self.enterContext(patch.multiple(config, ROOT=root, DB_PATH=root / "test.db", LLM_PROVIDER="mock"))
        service.bootstrap()
        account = service.register_company("Компания А")
        self.company = account["company"]["id"]
        self.admin = account["admin"]["id"]
        self.user = service.login(account["company"]["code"], account["admin"]["login"], account["admin"]["pin"])
        service.use_company(self.company)
        self.addCleanup(db.set_company, 1)
        self.equipment, events = service.data_import_templates()
        self.events = events.replace(b"2026-11-01 10:30", datetime.now().isoformat(timespec="minutes").encode())

    def test_append_is_idempotent_and_feeds_company_workflow(self):
        service.save_record("Старая запись", [{"equipment_id": "M1", "failure_type": "other", "downtime_min": 20}])
        first = service.import_company_data(self.admin, self.equipment, self.events)
        self.assertEqual((first["equipment_added"], first["events_added"]), (1, 1))
        catalog = service.equipment_options()
        self.assertEqual({item.get("code", item["id"]) for item in catalog}, {"M1", "P-01"})
        ticket = next(ticket for ticket in service.tickets(["new"]) if ticket["source_id"] == "EV-001")
        service.approve_ticket(ticket["id"], "Администратор")
        self.assertIn("Датчик давления", service.history("Насос подачи")[0]["Исходный текст"])
        self.assertTrue(service.history("Старая запись"))
        self.assertEqual(service.today_summary()["incidents_today"], 2)
        self.assertTrue(any(row["equipment_name"] == "Насос подачи" for row in service.dashboard()["by_equipment"]))
        self.assertEqual(service.analyze_text("P-01: датчик отключился, простой 45 мин.")["drafts"][0]["equipment_id"], ticket["equipment_id"])

        repeated = service.import_company_data(self.admin, self.equipment, self.events)
        self.assertEqual((repeated["equipment_skipped"], repeated["events_skipped"]), (1, 1))
        self.assertEqual(service.tickets(["approved"])[0]["id"], ticket["id"])
        self.assertEqual(len(db.shift_logs()), 2)

    def test_unknown_equipment_rolls_back_new_equipment_and_prior_events(self):
        invalid = self.events + self.events.splitlines()[1].replace(b"EV-001;P-01", b"EV-002;UNKNOWN") + b"\n"
        with self.assertRaisesRegex(ValueError, "строка 3.*UNKNOWN"):
            service.import_company_data(self.admin, self.equipment, invalid)
        self.assertFalse(any(item.get("code") == "P-01" for item in service.equipment_options()))
        self.assertEqual(db.incidents(), [])
        self.assertEqual(db.shift_logs(), [])

    def test_company_isolation_and_protected_public_archive(self):
        service.import_company_data(self.admin, self.equipment, self.events)
        own_id = service.equipment_options()[0]["id"]
        other = service.register_company("Компания Б")
        service.use_company(other["company"]["id"])
        with self.assertRaisesRegex(ValueError, "администратору"):
            service.import_company_data(self.admin, self.equipment, self.events)
        self.assertEqual(service.tickets(), [])
        self.assertFalse(any(item["id"] == own_id for item in service.equipment_options()))
        result = service.import_company_data(other["admin"]["id"], self.equipment, self.events)
        self.assertEqual(result["events_added"], 1)
        self.assertNotEqual(service.equipment_options()[0]["id"], own_id)

        demo = service.login("FIXLIST", "boss", "1111")
        service.use_company(demo["company_id"])
        with self.assertRaisesRegex(ValueError, "собственного кабинета"):
            service.import_company_data(demo["id"], self.equipment)
        self.assertEqual(len(service.archive_report()["rows"]), 177)
        self.assertEqual(len(service.equipment_options()), 6)

    def test_worker_cannot_import(self):
        worker = service.add_workers(self.company, "Сотрудник")[0]
        with self.assertRaisesRegex(ValueError, "администратору"):
            service.import_company_data(worker["id"], self.equipment)

    def test_first_csv_creates_workspace_and_keeps_demo_separate(self):
        demo = service.login("FIXLIST", "boss", "1111")
        service.use_company(demo["company_id"])
        invalid = self.events.replace(b"EV-001;P-01", b"EV-001;UNKNOWN")
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            service.start_workspace_with_import("Ошибочный кабинет", self.equipment, invalid)
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 2)

        account = service.start_workspace_with_import("Кабинет из CSV", self.equipment, self.events)
        self.assertEqual(account["import_result"]["events_added"], 1)
        self.assertEqual(len(service.archive_report()["rows"]), 177)
        service.use_company(account["company"]["id"])
        result = service.tickets(["new"])
        self.assertEqual([row["source_id"] for row in result], ["EV-001"])
        self.assertEqual(result[0]["hour_cost_kzt"], 25_000)
        self.assertEqual(len(db.shift_logs()), 1)

    def test_board_can_open_events_beyond_the_first_page(self):
        from streamlit.testing.v1 import AppTest

        heading, example = self.events.splitlines()
        events = b"\n".join([heading, *(example.replace(b"EV-001", f"EV-{i:03}".encode()) for i in range(1, 14))])
        service.import_company_data(self.admin, self.equipment, events)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25)
        app.session_state["user"] = self.user
        app.session_state["session-expires-at"] = time() + 3600
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(sum((button.key or "").startswith("archive-open-") for button in app.button), 12)
        app.number_input(key="board-page-new").set_value(2).run()
        remaining = [button for button in app.button if (button.key or "").startswith("archive-open-")]
        self.assertEqual(len(remaining), 1)
        remaining[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('archive-detail-header' in row.value and 'Насос подачи' in row.value for row in app.markdown))
        self.assertTrue(any('archive-detail-facts' in row.value and 'Номер события в CSV' in row.value for row in app.markdown))
        self.assertTrue(any('archive-money' in row.value and '18 750 ₸' in row.value for row in app.markdown))


if __name__ == "__main__":
    unittest.main()
