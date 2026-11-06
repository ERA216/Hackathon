"""Проверки бэкенда без сети: python -m pytest -q  (или python tests/test_backend.py)."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["LLM_PROVIDER"] = "mock"

from backend import advisor, analytics, config, extract, llm, service  # noqa: E402

service.reset_demo()


def test_demo_story_numbers():
    st = analytics.group_stats("M3", "belt_break")
    assert st["failures"] == 4
    assert st["downtime_h"] == 6.4
    assert st["loss_kzt"] == 224000
    assert st["intervals_days"][-3:] == [12, 8, 5]


def test_anomaly_found_for_m3():
    sig = [s for s in analytics.anomalies() if s["equipment_id"] == "M3"]
    assert sig and sig[0]["severity"] == "high"
    assert any("сокращается" in r for r in sig[0]["reasons"])


def test_measure_economics_match_doc():
    st = analytics.group_stats("M3", "belt_break")
    econ = {m["id"]: advisor.economics(m, st) for m in advisor.candidates("belt_break")}
    assert econ["MS01"]["savings_kzt"] == 144000 and round(econ["MS01"]["payback_days"]) == 2
    assert econ["MS02"]["savings_kzt"] == 156800 and round(econ["MS02"]["payback_days"]) == 9
    assert econ["MS03"]["savings_kzt"] == 133200 and econ["MS03"]["payback_label"] == "без разовых вложений"


def test_effect_before_after_m2():
    eff = [e for e in service.effects() if e["equipment_id"] == "M2"][0]
    assert eff["avg_interval_before"] == 5 and eff["avg_interval_after"] == 18
    assert eff["status"] == "improved"


def test_rule_based_extraction():
    res = service.analyze_text("Линия 2, станок №3 встал в 14:10, порвался ремень. Поставили новый, простой 1,5 часа.")
    d = res["drafts"][0]
    assert res["mode"] == "rules"
    assert d["equipment_id"] == "M3" and d["failure_type"] == "belt_break" and d["downtime_min"] == 90
    assert d["parts_cost_kzt"] == 8000


def test_empty_input_raises():
    try:
        service.analyze_text("   ")
    except ValueError:
        return
    raise AssertionError("empty input must raise ValueError")


def _fake_provider(payloads):
    it = iter(payloads)
    return lambda system, user: next(it)


def test_ai_hallucinations_are_caught(monkeypatch=None):
    raw = "Станок №3 встал, порвался ремень, заменили. Простой 40 минут."
    bad = json.dumps({"incidents": [{"equipment_id": "M9", "failure_type": "belt_break", "downtime_min": 120,
                                      "downtime_quote": "простой 2 часа", "parts": [], "cause": "перегрузка двигателя",
                                      "action": "замена", "confidence": 0.95}], "needs_clarification": []})
    orig_enabled, orig_call = config.ai_enabled, llm._call_provider
    config.ai_enabled = lambda: True
    llm._call_provider = _fake_provider(["не json", bad])  # первый ответ битый → повтор
    try:
        res = extract.extract(raw)
    finally:
        config.ai_enabled, llm._call_provider = orig_enabled, orig_call
    d = res["drafts"][0]
    assert res["mode"] == "ai"
    assert d["equipment_id"] == "M3"            # несуществующий M9 заменён по тексту
    assert d["downtime_min"] is None            # 120 мин не подтверждаются текстом
    assert "downtime" in d["issues"]
    assert d["cause"] is None                   # выдуманная причина удалена


def test_ai_down_falls_back():
    orig_enabled, orig_call = config.ai_enabled, llm._call_provider
    config.ai_enabled = lambda: True

    def boom(system, user):
        raise llm.LLMRateLimit("429")
    llm._call_provider = boom
    try:
        res = extract.extract("Станок №3: ремень порвался, простой 30 мин.")
        plan = advisor.plan("M3", "belt_break", save=False)
    finally:
        config.ai_enabled, llm._call_provider = orig_enabled, orig_call
    assert res["mode"] == "rules" and res["warning"]
    assert plan["mode"] == "rules" and plan["measures"]


def test_advisor_rejects_unknown_measure_and_numbers():
    out = json.dumps({"measures": [{"measure_id": "MS99", "why": "x"}, {"measure_id": "MS02", "why": "Ремень рвётся из-за натяжения."}],
                      "summary": "Станок теряет 999 999 ₸, начните с натяжителя."})
    orig_enabled, orig_call = config.ai_enabled, llm._call_provider
    config.ai_enabled = lambda: True
    llm._call_provider = _fake_provider([out])
    try:
        p = advisor.plan("M3", "belt_break", save=False)
    finally:
        config.ai_enabled, llm._call_provider = orig_enabled, orig_call
    assert [m["measure_id"] for m in p["measures"]] == ["MS02"]
    assert "999" not in p["summary"] and p["warning"]


def test_save_record_and_signal():
    res = service.analyze_text("Станок №3, ремень опять порвался, заменили ремень. Простой 95 мин.")
    saved = service.save_record("Станок №3, ремень опять порвался, заменили ремень. Простой 95 мин.", res["drafts"], res["mode"])
    assert saved["incident_ids"] and any(s["equipment_id"] == "M3" for s in saved["signals"])
    assert analytics.group_stats("M3", "belt_break")["failures"] == 5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
