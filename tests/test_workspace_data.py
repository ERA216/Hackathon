"""Self-service data entry and workspace isolation with a temporary database."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
from time import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config, db, service


class WorkspaceDataTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.multiple(config, ROOT=root, DB_PATH=root / "test.db", LLM_PROVIDER="mock", SMTP_HOST=""))
        service.bootstrap()
        self.addCleanup(db.set_company, 1)

    def test_first_save_creates_workspace_without_a_lead(self):
        with self.assertRaisesRegex(ValueError, "оборудования"):
            service.start_workspace("Новый кабинет", " ")
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 1)

        account = service.start_workspace("Новый кабинет", "Насос", "P1", "Цех", 30_000)
        company, admin = account["company"], account["admin"]
        service.use_company(company["id"])
        self.assertEqual(service.login(company["code"], admin["login"], admin["pin"])["id"], admin["id"])
        self.assertEqual([item["name"] for item in service.equipment_options()], ["Насос"])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 0)

    def test_events_and_equipment_stay_in_their_workspace(self):
        first = service.start_workspace("Первая компания", "Насос", "P1")
        second = service.start_workspace("Вторая компания", "Компрессор", "P1")
        service.use_company(second["company"]["id"])
        with self.assertRaisesRegex(ValueError, "своего справочника"):
            service.add_workspace_event(second["admin"]["id"], first["equipment_id"], "Выбило автомат", datetime.now(), None)
        with self.assertRaisesRegex(ValueError, "администратору"):
            service.add_workspace_equipment(first["admin"]["id"], "Фильтр")
        self.assertFalse(service.history())

        service.use_company(first["company"]["id"])
        with self.assertRaisesRegex(ValueError, "уже используется"):
            service.add_workspace_equipment(first["admin"]["id"], "Фильтр", "p1")
        event = service.add_workspace_event(first["admin"]["id"], first["equipment_id"], "Выбило автомат", datetime.now(), None, 25)
        self.assertEqual(service.tickets(["new"])[0]["id"], event)
        self.assertEqual(service.history()[0]["Простой, мин"], 25)

        demo = service.login("FIXLIST", "boss", "1111")
        service.use_company(demo["company_id"])
        with self.assertRaisesRegex(ValueError, "собственного кабинета"):
            service.add_workspace_equipment(demo["id"], "Фильтр")
        self.assertEqual(len(service.archive_report()["rows"]), 177)
        self.assertEqual(len(service.equipment_options()), 6)

    def test_event_form_can_create_and_reuse_equipment_by_name(self):
        account = service.start_workspace("Мастерская", "Насос")
        service.use_company(account["company"]["id"])
        admin = account["admin"]["id"]
        with self.assertRaisesRegex(ValueError, "Опишите"):
            service.add_workspace_event(admin, None, "", datetime.now(), None, equipment_name="Компрессор")
        self.assertEqual([item["name"] for item in service.equipment_options()], ["Насос"])

        first = service.add_workspace_event(admin, None, "Отключился датчик", datetime.now(), None, 25,
                                            equipment_name=" Компрессор   цеха ")
        second = service.add_workspace_event(admin, None, "Компрессор перегрелся", datetime.now(), None,
                                             equipment_name="КОМПРЕССОР цеха")
        first_ticket, second_ticket = service.ticket(first), service.ticket(second)
        self.assertEqual(first_ticket["equipment_id"], second_ticket["equipment_id"])
        self.assertEqual(first_ticket["equipment_name"], "Компрессор цеха")
        self.assertEqual(first_ticket["downtime_min"], 25)
        self.assertEqual(len(service.equipment_options()), 2)

    def test_landing_to_first_equipment_and_task(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25).run()
        next(field for field in app.text_input if field.label == "Название кабинета").set_value("Мастерская")
        next(field for field in app.text_input if field.label == "Название оборудования").set_value("Компрессор")
        next(button for button in app.button if button.label == "Сохранить и открыть кабинет").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["page"], "Панель задач")
        self.assertEqual(app.session_state["user"]["company_name"], "Мастерская")
        self.assertFalse(any(button.key == "nav_Свои данные" for button in app.button))
        next(field for field in app.text_area if field.label == "Что произошло?").set_value("Компрессор перегрелся")
        next(button for button in app.button if button.label == "Сохранить событие").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("добавлено в задачи и историю" in msg.value for msg in app.success))
        next(button for button in app.button if button.label == "Открыть задачу →").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["page"], "Панель задач")
        self.assertTrue(any('archive-detail-header' in row.value and "Компрессор" in row.value and "Перегрев" in row.value for row in app.markdown))
        self.assertTrue(any(button.key == "approve" for button in app.button))

    def test_board_hides_warnings_while_entering_data(self):
        from streamlit.testing.v1 import AppTest

        account = service.start_workspace("Моя компания", "Насос цеха", "P1")
        service.use_company(account["company"]["id"])
        for _ in range(4):
            service.add_workspace_event(account["admin"]["id"], account["equipment_id"], "Выбило автомат", datetime.now(), None, 25)
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25)
        app.session_state["user"] = service.login(account["company"]["code"], account["admin"]["login"], account["admin"]["pin"])
        app.session_state["session-expires-at"] = time() + 3600
        app.session_state["show-workspace-forms"] = True
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(any('class="warn-tag"' in row.value for row in app.markdown))

        next(button for button in app.button if button.label == "Скрыть формы").click().run()
        warning = next(row.value for row in app.markdown if 'class="warn-tag"' in row.value)
        self.assertIn("Насос цеха", warning)
        self.assertNotIn("Kelmarsh", warning)

    def test_against_similar_names_and_long_downtime(self):
        account = service.start_workspace("Мастерская", "Насос", "P-01")
        service.use_company(account["company"]["id"])
        admin = account["admin"]["id"]
        feed = service.add_workspace_equipment(admin, "Насос подачи", "P-02")
        draft = service.analyze_text("Насос подачи перегрелся, простой 48 часов.")["drafts"][0]
        self.assertEqual(draft["equipment_id"], feed)
        self.assertEqual(draft["downtime_min"], 2880)
        result = service.save_record("Насос подачи перегрелся, простой 48 часов.", [draft])
        self.assertEqual(service.ticket(result["incident_ids"][0])["downtime_min"], 2880)

        service.add_workspace_equipment(admin, "Насос подачи", "P-03")
        ambiguous = service.analyze_text("Насос подачи перегрелся, простой 30 минут.")["drafts"][0]
        self.assertIsNone(ambiguous["equipment_id"])
        identified = service.analyze_text("Насос подачи P-02 перегрелся, простой 30 минут.")["drafts"][0]
        self.assertEqual(identified["equipment_id"], feed)
        too_long = service.analyze_text("P-02 перегрелся, простой 999999 часов.")["drafts"][0]
        self.assertIn("downtime", too_long["issues"])

    def test_partial_losses_keep_parts_visible_without_zero_total(self):
        account = service.start_workspace("Мастерская", "Насос без тарифа")
        service.use_company(account["company"]["id"])
        admin = account["admin"]["id"]
        for _ in range(3):
            service.add_workspace_event(admin, account["equipment_id"], "Порвался ремень", datetime.now(), "belt_break", 120, 2000)
        known = service.add_workspace_equipment(admin, "Насос с тарифом", hour_cost_kzt=30000)
        service.add_workspace_event(admin, known, "Порвался ремень", datetime.now(), "belt_break", 60, 1000)

        dash = service.dashboard()
        self.assertIsNone(dash["total_loss_kzt"])
        self.assertEqual(dash["parts_kzt"], 7000)
        self.assertEqual(dash["by_equipment"][0]["loss_kzt"], 31000)
        self.assertIsNone(dash["anomalies"][0]["loss_kzt"])
        self.assertIsNone(service.today_summary()["loss_today_kzt"])
        self.assertIn("запчасти 7 000 ₸", service.daily_brief()["headline"])
        self.assertIn("запчасти 7 000 ₸", service.ask_ai("Какие потери?")["answer"])
        plan = service.savings_plan(account["equipment_id"], "belt_break")
        self.assertIsNone(plan["stats"]["loss_kzt"])
        self.assertTrue(any(measure["savings_kzt"] is None for measure in plan["measures"]))

    def test_reanalysis_saves_only_fields_from_current_text(self):
        from streamlit.testing.v1 import AppTest

        account = service.start_workspace("Мастерская", "Насос", "P-01")
        company = account["company"]["id"]
        service.use_company(company)
        compressor = service.add_workspace_equipment(account["admin"]["id"], "Компрессор", "C-01")
        worker = service.add_workers(company, "Мастер")[0]
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25)
        app.session_state["user"] = service.login(account["company"]["code"], worker["login"], worker["pin"])
        app.session_state["session-expires-at"] = time() + 3600
        app.run()
        app.button(key="nav_Новая запись").click().run()

        def button(label):
            return next(item for item in app.button if item.label.strip() == label)

        app.text_area(key="raw").set_value("P-01 перегрелся, простой 15 минут.")
        button("Разобрать запись  →").click().run()
        app.text_area(key="raw").set_value("C-01 гудит, простой 48 часов.").run()
        self.assertTrue(button("Подтвердить и сохранить").proto.disabled)
        button("Разобрать запись  →").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="eq_2_0").value, compressor)
        self.assertEqual(app.number_input(key="dt_2_0").value, 2880)
        button("Подтвердить и сохранить").click().run()
        service.use_company(company)
        self.assertEqual([(row["equipment_id"], row["downtime_min"]) for row in service.tickets()], [(compressor, 2880)])

    def test_url_cannot_restore_session_and_expiry_requires_login(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=25)
        app.query_params["s"] = "legacy-session-token"
        app.run()
        self.assertNotIn("s", app.query_params)
        self.assertFalse(any(item.key == "nav_Панель задач" for item in app.button))
        for label, value in (("Код компании", "FIXLIST"), ("Логин", "boss"), ("PIN-код", "1111")):
            next(item for item in app.text_input if item.label == label).set_value(value)
        next(item for item in app.button if item.label == "Войти →").click().run()
        self.assertTrue(any(item.key == "nav_Панель задач" for item in app.button))
        self.assertNotIn("s", app.query_params)
        app.session_state["session-expires-at"] = time() - 1
        app.run()
        self.assertFalse(any(item.key == "nav_Панель задач" for item in app.button))
        self.assertTrue(any("Сеанс завершён" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
