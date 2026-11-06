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


def test_gemini_key_rotation_and_model_fallback():
    import requests
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, json.dumps(body)

        def json(self):
            return self._b

    ok = {"candidates": [{"content": {"parts": [{"text": json.dumps({"incidents": [], "needs_clarification": []})}]}}]}

    def fake_post(url, headers, json, timeout):
        key, model = headers["x-goog-api-key"], url.split("/models/")[1].split(":")[0]
        calls.append((key, model))
        if key == "k1":
            return R(429, {"error": "quota"})          # первый ключ упёрся в лимит
        if model == "gemini-flash-latest":
            return R(404, {"error": "model not found"})  # модель недоступна
        return R(200, ok)

    saved = (config.LLM_PROVIDER, config.GEMINI_API_KEY, config.GEMINI_API_KEY_2, config.LLM_MODEL, requests.post)
    config.LLM_PROVIDER, config.GEMINI_API_KEY, config.GEMINI_API_KEY_2, config.LLM_MODEL = "gemini", "k1", "k2", "gemini-flash-latest"
    saved_fb = config.GEMINI_FALLBACK_MODELS
    config.GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]
    requests.post = fake_post
    llm._working.clear()
    try:
        from backend.schemas import ExtractionResult
        llm.call_json("s", "u", ExtractionResult, "test")
    finally:
        config.LLM_PROVIDER, config.GEMINI_API_KEY, config.GEMINI_API_KEY_2, config.LLM_MODEL, requests.post = saved
        config.GEMINI_FALLBACK_MODELS = saved_fb
        llm._working.clear()
    assert calls == [("k1", "gemini-flash-latest"), ("k2", "gemini-flash-latest"), ("k2", "gemini-2.5-flash")]


def test_gemini_overload_switches_model():
    import requests
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, json.dumps(body)

        def json(self):
            return self._b

    ok = {"candidates": [{"content": {"parts": [{"text": json.dumps({"incidents": [], "needs_clarification": []})}]}}]}

    def fake_post(url, headers, json, timeout):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return R(503, {"error": "high demand"}) if model == "gemini-flash-latest" else R(200, ok)

    saved = (config.LLM_PROVIDER, config.GEMINI_API_KEY, config.GEMINI_API_KEY_2, config.LLM_MODEL, requests.post)
    config.LLM_PROVIDER, config.GEMINI_API_KEY, config.GEMINI_API_KEY_2, config.LLM_MODEL = "gemini", "k1", "", "gemini-flash-latest"
    saved_fb = config.GEMINI_FALLBACK_MODELS
    config.GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]
    requests.post = fake_post
    llm._working.clear()
    try:
        from backend.schemas import ExtractionResult
        llm.call_json("s", "u", ExtractionResult, "test")
    finally:
        config.LLM_PROVIDER, config.GEMINI_API_KEY, config.GEMINI_API_KEY_2, config.LLM_MODEL, requests.post = saved
        config.GEMINI_FALLBACK_MODELS = saved_fb
        llm._working.clear()
    assert calls == ["gemini-flash-latest", "gemini-2.5-flash"]


def test_new_machines_and_diagnosis_rules():
    r = service.analyze_text("Станок №4 опять перегрелся к обеду, на радиаторе много пыли. Остановили на 40 минут, продули — запустили.")
    d = r["drafts"][0]
    assert d["equipment_id"] == "M4" and d["failure_type"] == "overheating" and d["downtime_min"] == 40
    titles = [h["title"] for h in d["hypotheses"]]
    assert "Загрязнение радиатора и вентиляции" in titles
    assert all(h["checks"] for h in d["hypotheses"])
    assert service.analyze_text("4-станок қызып кетті, 30 минут тұрды.")["drafts"][0]["equipment_id"] == "M4"
    q = service.analyze_text("Станок №9 встал, простой 20 минут.")["questions"]
    assert any("№9" in x for x in q)


def test_diagnosis_ai_rejects_unknown_cause():
    from backend import diagnose
    out = json.dumps({"hypotheses": [{"cause_id": "C99", "why": "выдумка", "confidence": 0.9},
                                     {"cause_id": "C06", "why": "в записи упомянута пыль на радиаторе", "confidence": 0.7}]})
    orig_enabled, orig_call = config.ai_enabled, llm._call_provider
    config.ai_enabled = lambda: True
    llm._call_provider = _fake_provider([out])
    try:
        dg = diagnose.diagnose("Станок №4 перегрелся, пыль на радиаторе", "M4", "overheating")
    finally:
        config.ai_enabled, llm._call_provider = orig_enabled, orig_call
    assert dg["mode"] == "ai" and [h["cause_id"] for h in dg["hypotheses"]] == ["C06"]


def test_type_from_history_when_no_symptoms():
    d = service.analyze_text("сломался станок номер 3 найди причину")["drafts"][0]
    assert d["equipment_id"] == "M3" and d["failure_type"] == "belt_break"
    assert "type_from_history" in d["issues"] and d["hypotheses"]


def test_timeout_switches_model():
    import requests
    calls = []

    class R:
        status_code, text = 200, ""

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({"incidents": [], "needs_clarification": []})}]}}]}

    def fake_post(url, headers, json, timeout):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        if model == "gemini-flash-lite-latest":
            raise requests.Timeout("read timed out")
        return R()

    saved = (config.LLM_PROVIDER, config.GEMINI_API_KEY, config.LLM_MODEL, requests.post)
    config.LLM_PROVIDER, config.GEMINI_API_KEY, config.LLM_MODEL = "gemini", "k1", "gemini-flash-lite-latest"
    requests.post = fake_post
    llm._working.clear()
    try:
        from backend.schemas import ExtractionResult
        llm.call_json("s", "u", ExtractionResult, "test")
    finally:
        config.LLM_PROVIDER, config.GEMINI_API_KEY, config.LLM_MODEL, requests.post = saved
        llm._working.clear()
    assert calls == ["gemini-flash-lite-latest", "gemini-2.5-flash-lite"]


def test_daily_brief_checks_ai_numbers():
    from backend import briefing
    from backend.schemas import kzt
    loss = [c for c in service.checklist() if c["equipment_id"] == "M3"][0]["loss_kzt"]
    good = json.dumps({"headline": "Главный риск — Станок №3, ремень рвётся всё чаще.",
                       "points": [f"Станок №3 теряет около {kzt(loss)} ₸ за период."],
                       "priorities": [{"equipment": "Станок №3", "action": "Проверить натяжение ремня"}]})
    bad = json.dumps({"headline": "Потери 9 999 999 ₸", "points": [], "priorities": [{"equipment": "Станок №77", "action": "x"}]})
    orig_enabled, orig_call = config.ai_enabled, llm._call_provider
    config.ai_enabled = lambda: True
    try:
        llm._call_provider = _fake_provider([good])
        b1 = briefing.daily_brief()
        llm._call_provider = _fake_provider([bad])
        b2 = briefing.daily_brief()
    finally:
        config.ai_enabled, llm._call_provider = orig_enabled, orig_call
    assert b1["mode"] == "ai" and b1["priorities"][0]["equipment"] == "Станок №3"
    assert b2["mode"] == "rules" and b2["warning"] and "9 999 999" not in b2["headline"]
    assert service.checklist() and service.today_summary()["total_logs"] > 0


def test_auth_and_company_isolation():
    from backend import db
    demo = service.login("fixlist", "boss", "1111")
    assert demo and demo["role"] == "admin"
    assert service.login("FIXLIST", "boss", "0000") is None
    reg = service.register_company("ТОО Ромашка", "Павлодар")
    code, admin = reg["company"]["code"], reg["admin"]
    me = service.login(code, "admin", admin["pin"])
    assert me and me["company_id"] == reg["company"]["id"]
    workers = service.add_workers(me["company_id"], "Мастер смены 1\nМастер смены 2")
    assert len(workers) == 2 and service.login(code, workers[0]["login"], workers[0]["pin"])["role"] == "worker"
    # новая компания не видит демо-данные и наоборот
    service.use_company(me["company_id"])
    assert db.incidents() == [] and service.dashboard()["anomalies"] == []
    r = service.analyze_text("Станок №3: ремень порвался, простой 30 мин.")
    service.save_record("Станок №3: ремень порвался, простой 30 мин.", r["drafts"], r["mode"], author="Мастер смены 1")
    assert len(db.incidents()) == 1
    service.use_company(demo["company_id"])
    assert all(x["author_role"] != "Мастер смены 1" or x["is_demo"] for x in db.incidents())
    with db.connect() as c:
        assert "$" in c.execute("SELECT pin_hash FROM users LIMIT 1").fetchone()[0]  # PIN только в виде хеша


def test_ticket_lifecycle_two_roles():
    admin = service.login("FIXLIST", "boss", "1111")
    service.use_company(admin["company_id"])
    assert admin["role"] == "admin" and service.login("FIXLIST", "master", "2222")["role"] == "worker"
    r = service.analyze_text("Станок №5 гудит, простой 15 минут.")
    tid = service.save_record("Станок №5 гудит, простой 15 минут.", r["drafts"], r["mode"], author="Мастер смены 1")["incident_ids"][0]
    t = [x for x in service.tickets(["new"]) if x["id"] == tid][0]
    assert service.ticket_hypotheses(t)
    service.approve_ticket(tid, "Администратор цеха")
    try:
        service.escalate_ticket(tid, "Мастер смены 1", " ")
        raise AssertionError("empty note must fail")
    except ValueError:
        pass
    service.escalate_ticket(tid, "Мастер смены 1", "Нет подшипника на складе")
    t = [x for x in service.tickets(["escalated"]) if x["id"] == tid][0]
    assert t["escalation_note"] == "Нет подшипника на складе" and t["approved_by"] == "Администратор цеха"
    service.resolve_ticket(tid, "Администратор цеха", "Закупили и заменили подшипник", "Выработка ресурса подшипника")
    t = [x for x in service.tickets(author="Мастер смены 1") if x["id"] == tid][0]
    assert t["status"] == "resolved" and t["assignee"] == "Администратор цеха"
    c = service.ticket_counts()
    assert c["escalated"] >= 1 and c["approved"] >= 1  # демо: станок №3 — нужна помощь, станок №4 — решают сотрудники


def test_inspection_creates_cards_and_health_forecast():
    service.use_company(service.login("FIXLIST", "boss", "1111")["company_id"])
    before = service.ticket_counts()["new"]
    try:
        service.save_inspection([{"equipment_id": "M6", "state": "problem", "note": " ", "downtime_min": 0}], "Мастер смены 1")
        raise AssertionError("problem without note must fail")
    except ValueError:
        pass
    res = service.save_inspection([{"equipment_id": "M1", "state": "ok", "note": "", "downtime_min": 0},
                                   {"equipment_id": "M6", "state": "problem", "note": "Датчик не видит заготовку", "downtime_min": 10}], "Мастер смены 1")
    assert res["checked"] == 2 and len(res["cards"]) == 1 and service.ticket_counts()["new"] == before + 1
    hs = {h["equipment_id"]: h for h in service.equipment_health()}
    assert hs["M3"]["risk"] == "high" and hs["M3"]["shrinking"] and "сокращаются" in hs["M3"]["explanation"]
    assert hs["M1"]["last_inspection"]["state"] == "ok"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
