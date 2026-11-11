"""Public-data regression checks: no network, production DB, API or email required."""
import copy
import csv
from datetime import date
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import open_data as data


def event(equipment, start, end, code="10"):
    return {"equipment_id": equipment, "started_at": start + "+00:00", "ended_at": end + "+00:00", "code": code}


class PublicDataTests(unittest.TestCase):
    def test_bundled_sources_and_provenance(self):
        rows, examples, manifest = data.events(), data.examples(), data.manifest()
        self.assertEqual(len(rows), manifest["kelmarsh"]["count"])
        self.assertEqual(len(examples), 80)
        self.assertEqual(len({row["equipment_id"] for row in rows}), 6)
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        for row in rows:
            self.assertEqual(row["status"], "Stop")
            self.assertEqual(row["iec_category"], "Forced outage")
            self.assertEqual(row["duration_seconds"], (data._time(row["ended_at"]) - data._time(row["started_at"])).total_seconds())
            self.assertTrue(row["started_at"].startswith("2016-"))
            self.assertIn(row["message"], data.MESSAGES_RU)
            self.assertNotIn("parts_cost_kzt", row)
        for item in examples:
            self.assertTrue(all(0 <= entity["start"] < entity["end"] <= len(item["tokens"]) for entity in item["entities"]))
            self.assertNotIn("downtime_min", item)
            self.assertNotIn("occurred_at", item)

    def test_known_oil_pump_event_is_unshifted(self):
        row = next(row for row in data.events() if row["message"] == "Overload gear oil pump")
        self.assertEqual(row["equipment_name"], "Kelmarsh 1")
        self.assertEqual(row["started_at"], "2016-05-26T08:34:30+00:00")
        self.assertEqual(row["duration_seconds"], 9 * 3600 + 27 * 60 + 40)

    def test_overlap_is_merged_per_turbine(self):
        rows = [event("KEL01", "2016-01-05T10:00:00", "2016-01-05T12:00:00"),
                event("KEL01", "2016-01-05T11:00:00", "2016-01-05T13:00:00"),
                event("KEL02", "2016-01-05T10:00:00", "2016-01-05T11:00:00")]
        result = data.summary(rows, date(2016, 1, 1), date(2016, 1, 31))
        self.assertEqual(result["events"], 3)
        self.assertEqual(result["episodes"], 2)
        self.assertEqual(result["downtime_seconds"], 4 * 3600)

    def test_period_and_month_clipping(self):
        rows = [event("KEL01", "2016-01-31T23:00:00", "2016-02-01T02:00:00")]
        months = data.monthly_hours(rows, date(2016, 1, 1), date(2016, 2, 29))
        self.assertEqual([item["hours"] for item in months], [1, 2])
        result = data.summary(rows, date(2016, 2, 1), date(2016, 2, 1))
        self.assertEqual(result["downtime_seconds"], 7200)

    def test_event_selection_is_overlap_based(self):
        rows = [event("KEL01", "2016-01-31T23:00:00", "2016-02-01T02:00:00"),
                event("KEL01", "2016-01-31T20:00:00", "2016-02-01T00:00:00")]
        with patch.object(data, "events", return_value=rows):
            self.assertEqual(len(data.select_events(["KEL01"], date(2016, 2, 1), date(2016, 2, 1))), 1)
            self.assertEqual(data.select_events([], date(2016, 2, 1), date(2016, 2, 1)), [])

    def test_repeat_intervals_use_unioned_periods(self):
        rows = [event("KEL01", "2016-01-01T10:00:00", "2016-01-01T12:00:00"),
                event("KEL01", "2016-01-01T11:00:00", "2016-01-01T13:00:00"),
                event("KEL01", "2016-01-04T10:00:00", "2016-01-04T12:00:00")]
        for index, row in enumerate(rows):
            row.update(id=str(index), message="Frequency converter error")
        groups = data.repeated_signals(rows)
        self.assertEqual(groups[0]["interval_days"], [3])
        self.assertEqual(groups[0]["count"], 3)

    def test_rules_do_not_read_gold_labels(self):
        example = next(item for item in data.examples() if "broken bolt on brake pump" in item["text"])
        altered = copy.deepcopy(example)
        altered["entities"] = []
        first = data.analyze_example(example)
        second = data.analyze_example(altered)
        self.assertEqual(first["entities"], second["entities"])
        self.assertEqual(first["mode"], "rules")
        self.assertGreater(first["matched"], 0)
        self.assertEqual(second["expected"], 0)

    def test_ai_receives_only_tokens_and_rejects_invalid_spans(self):
        example = data.examples()[0]
        def fake_call(system, user, schema, purpose):
            import json
            payload = json.loads(user)
            self.assertEqual(set(payload), {"tokens"})
            self.assertNotIn("entities", payload)
            return schema(entities=[{"start": 0, "end": 1, "kind": "PhysicalObject"},
                                    {"start": 1, "end": 999, "kind": "PhysicalObject"},
                                    {"start": 2, "end": 1, "kind": "Process"},
                                    {"start": 1, "end": 2, "kind": "PhysicalObject"}])
        with patch("backend.llm.call_json", side_effect=fake_call):
            result = data.analyze_example(example, use_ai=True)
        self.assertEqual(result["mode"], "ai")
        self.assertEqual(result["rejected"], 3)
        self.assertEqual(result["predicted"], 1)

    def test_ai_failure_is_explicit_rules_fallback(self):
        from backend.llm import LLMUnavailable
        with patch("backend.llm.call_json", side_effect=LLMUnavailable("offline")):
            result = data.analyze_example(data.examples()[0], use_ai=True)
        self.assertEqual(result["mode"], "rules")
        self.assertTrue(result["note"])

    def test_csv_export_keeps_original_and_provenance(self):
        row = next(csv.DictReader(io.StringIO(data.export_events(data.events()[:1]).decode("utf-8-sig"))))
        self.assertEqual(row["message"], data.events()[0]["message"])
        self.assertTrue(row["source_file"].startswith("Status_Kelmarsh_"))
        self.assertTrue(row["started_at"].endswith("+00:00"))
        self.assertEqual(row["dataset_license"], "CC BY 4.0")
        self.assertIn("zenodo.org", row["dataset_url"])

    def test_streamlit_page_filters_and_text_analysis_offline(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("from ui.theme import apply_theme\nfrom ui.open_data import page_open_data\napply_theme()\npage_open_data()\n", default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertEqual([tab.label for tab in app.tabs], ["Остановки Kelmarsh", "Разбор записи MaintIE", "Источники и лицензии"])
        app.selectbox(key="od-equipment").select("KEL01").run()
        self.assertFalse(app.exception)
        app.button(key="od-maintie-run").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.od_maintie_result["result"]["mode"], "rules")
        app.text_input(key="od-search").set_value("no-matching-signal-123").run()
        self.assertFalse(app.exception)
        self.assertTrue(any("По запросу ничего не найдено" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
