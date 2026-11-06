"""Шаг 2: расчёты по всей истории — только код, без ИИ.

Потери, интервалы между поломками, тренды, аномалии, эффект «до/после».
"""
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from . import config, db
from .schemas import FAILURE_LABELS, kzt, num


def incidents_df(equipment_id: Optional[str] = None) -> pd.DataFrame:
    rows = db.incidents(equipment_id)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["occurred_at"] = pd.to_datetime(df["occurred_at"])
    df["downtime_h"] = df["downtime_min"] / 60
    df["loss_kzt"] = df["downtime_h"] * df["hour_cost_kzt"] + df["parts_cost_kzt"]
    return df


def _window(df: pd.DataFrame, as_of: Optional[datetime], days: int) -> pd.DataFrame:
    as_of = as_of or datetime.now()
    return df[(df["occurred_at"] > as_of - timedelta(days=days)) & (df["occurred_at"] <= as_of + timedelta(days=1))]


def intervals_days(dates: pd.Series) -> list[int]:
    d = sorted(pd.to_datetime(dates).dt.normalize())
    return [int((b - a).days) for a, b in zip(d, d[1:])]


def group_stats(equipment_id: str, failure_type: str, as_of: Optional[datetime] = None, days: Optional[int] = None) -> dict:
    """Статистика пары станок × тип поломки. Это база для плана экономии."""
    days = days or config.ANALYSIS_WINDOW_DAYS
    df = incidents_df(equipment_id)
    if df.empty:
        return {"failures": 0}
    full = df[df["failure_type"] == failure_type]
    win = _window(full, as_of, days)
    if win.empty:
        return {"failures": 0, "equipment_id": equipment_id, "failure_type": failure_type}
    hour_cost = int(win["hour_cost_kzt"].iloc[0])
    downtime_h = float(win["downtime_h"].sum())
    parts = int(win["parts_cost_kzt"].sum())
    return {
        "equipment_id": equipment_id,
        "equipment_name": win["equipment_name"].iloc[0],
        "failure_type": failure_type,
        "failure_label": FAILURE_LABELS.get(failure_type, failure_type),
        "window_days": days,
        "failures": int(len(win)),
        "downtime_h": round(downtime_h, 2),
        "avg_downtime_h": round(downtime_h / len(win), 2),
        "hour_cost_kzt": hour_cost,
        "parts_kzt": parts,
        "loss_kzt": int(round(downtime_h * hour_cost + parts)),
        "intervals_days": intervals_days(full["occurred_at"])[-4:],
        "incident_ids": [int(i) for i in win["id"]],
        "formula": f"{num(round(downtime_h, 2))} ч × {kzt(hour_cost)} ₸/ч + {kzt(parts)} ₸ запчасти = {kzt(downtime_h * hour_cost + parts)} ₸",
    }


def equipment_summary(as_of: Optional[datetime] = None, days: Optional[int] = None) -> list[dict]:
    days = days or config.ANALYSIS_WINDOW_DAYS
    df = incidents_df()
    if df.empty:
        return []
    win = _window(df, as_of, days)
    out = []
    for eq_id, g in win.groupby("equipment_id"):
        out.append({
            "equipment_id": eq_id,
            "equipment_name": g["equipment_name"].iloc[0],
            "failures": int(len(g)),
            "downtime_h": round(float(g["downtime_h"].sum()), 2),
            "loss_kzt": int(round(g["loss_kzt"].sum())),
        })
    return sorted(out, key=lambda r: r["loss_kzt"], reverse=True)


def anomalies(as_of: Optional[datetime] = None, days: Optional[int] = None) -> list[dict]:
    """Правила: повторяемость, сокращение интервалов, всплеск простоя. Каждый сигнал — со ссылками на записи."""
    days = days or config.ANALYSIS_WINDOW_DAYS
    df = incidents_df()
    if df.empty:
        return []
    signals = []
    for (eq_id, ftype), full in df.groupby(["equipment_id", "failure_type"]):
        st = group_stats(eq_id, ftype, as_of, days)
        if not st.get("failures"):
            continue
        reasons = []
        iv = intervals_days(full["occurred_at"])
        recent = iv[-3:]
        if st["failures"] >= 3:
            reasons.append(f"{st['failures']} поломки за {days} дней")
        if len(recent) >= 3 and all(a > b for a, b in zip(recent, recent[1:])):
            reasons.append("интервал между поломками сокращается: " + " → ".join(map(str, iv[-4:] if len(iv) >= 4 else recent)) + " дн")
        if len(full) >= 4:
            last = float(full.sort_values("occurred_at")["downtime_min"].iloc[-1])
            med = float(full["downtime_min"].iloc[:-1].median())
            if med > 0 and last > 1.5 * med:
                reasons.append(f"последний простой {int(last)} мин — в {last / med:.1f} раза дольше обычного")
        if not reasons:
            continue
        severity = "high" if len(reasons) >= 2 or st["loss_kzt"] >= 150000 else "medium"
        signals.append({
            **st,
            "severity": severity,
            "reasons": reasons,
            "title": f"{st['equipment_name']} · {st['failure_label']} повторяется",
            "message": f"{st['equipment_name']}: " + "; ".join(reasons) + f". Потери за период ≈ {kzt(st['loss_kzt'])} ₸.",
        })
    return sorted(signals, key=lambda s: (s["severity"] != "high", -s["loss_kzt"]))


def measure_effect(equipment_id: str, failure_types: list[str], applied_at: str) -> dict:
    """Сравнение «до/после» внедрения меры по реальным записям. Наблюдение, а не доказательство причинности."""
    df = incidents_df(equipment_id)
    if df.empty:
        return {"status": "no_data"}
    df = df[df["failure_type"].isin(failure_types)]
    t = pd.to_datetime(applied_at)
    before, after = df[df["occurred_at"] < t], df[df["occurred_at"] >= t]
    iv_before = intervals_days(before["occurred_at"])[-5:]
    # интервал после считаем от даты внедрения: так видно, сколько станок прожил без поломки
    after_dates = [t.normalize()] + sorted(after["occurred_at"].dt.normalize())
    iv_after = [int((b - a).days) for a, b in zip(after_dates, after_dates[1:])]
    days_since = int((pd.Timestamp.now().normalize() - t.normalize()).days)
    res = {
        "equipment_id": equipment_id,
        "applied_at": t.date().isoformat(),
        "failures_before": int(len(before)),
        "failures_after": int(len(after)),
        "avg_interval_before": round(sum(iv_before) / len(iv_before), 1) if iv_before else None,
        "avg_interval_after": round(sum(iv_after) / len(iv_after), 1) if iv_after else None,
        "days_since": days_since,
    }
    if res["avg_interval_after"] is None:
        res["status"] = "waiting" if days_since < 14 else "no_failures_since"
    else:
        res["status"] = "improved" if res["avg_interval_before"] and res["avg_interval_after"] > res["avg_interval_before"] else "no_improvement"
        if res["avg_interval_before"]:
            res["ratio"] = round(res["avg_interval_after"] / res["avg_interval_before"], 1)
    return res


STATE_LABELS = {"ok": "Норма", "remark": "Замечание", "problem": "Проблема"}


def equipment_health(today: Optional[datetime] = None) -> list[dict]:
    """Состояние каждого аппарата: поломки и убытки за период, ритм поломок, прогноз следующей и объяснение.

    Прогноз простой и прозрачный: средний из последних интервалов между поломками (если интервалы сокращаются —
    берётся последний). Это предупреждение, а не гарантия.
    """
    today = (today or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    days = config.ANALYSIS_WINDOW_DAYS
    df = incidents_df()
    last_insp = {}
    for r in db.inspections():
        last_insp.setdefault(r["equipment_id"], r)
    out = []
    for e in db.load_equipment():
        g = df[df["equipment_id"] == e["id"]] if not df.empty else df
        win = _window(g, today + timedelta(hours=23, minutes=59), days) if not g.empty else g
        failures = int(len(win))
        downtime_h = round(float(win["downtime_h"].sum()), 1) if failures else 0.0
        loss = int(round(win["loss_kzt"].sum())) if failures else 0
        iv = intervals_days(g["occurred_at"]) if len(g) >= 2 else []
        recent = [x for x in iv[-3:] if x > 0]
        last_date = g["occurred_at"].max().normalize() if not g.empty else None
        days_since = int((today - last_date).days) if last_date is not None else None
        shrinking = len(recent) >= 3 and all(a > b for a, b in zip(recent, recent[1:]))
        expected = (recent[-1] if shrinking else round(sum(recent) / len(recent))) if recent else None
        days_to_next = (expected - days_since) if expected is not None and days_since is not None else None
        insp = last_insp.get(e["id"])
        reasons = []
        risk = "low"
        if shrinking or failures >= 4 or (days_to_next is not None and days_to_next <= 2 and failures >= 2):
            risk = "high"
        elif failures >= 3 or (days_to_next is not None and days_to_next <= 7 and failures >= 2):
            risk = "medium"
        if insp and insp["state"] == "problem":
            risk = "high"
        elif insp and insp["state"] == "remark" and risk == "low":
            risk = "medium"
        if failures:
            reasons.append(f"за {days} дн. поломок: {failures}, простой {num(downtime_h)} ч, убыток ≈ {kzt(loss)} ₸")
        else:
            reasons.append(f"за {days} дн. поломок не было")
        if shrinking:
            reasons.append("интервалы между поломками сокращаются: " + " → ".join(map(str, recent)) + " дн")
        if expected is not None and days_to_next is not None:
            if days_to_next <= 0:
                reasons.append(f"обычно ломается раз в ~{expected} дн., с последней поломки прошло {days_since} — срок уже наступил")
            else:
                reasons.append(f"обычно ломается раз в ~{expected} дн. — следующая возможна через ~{days_to_next} дн.")
        if insp:
            reasons.append(f"последний обход {insp['created_at'][:10]}: {STATE_LABELS[insp['state']].lower()}" + (f" — {insp['note']}" if insp.get("note") else ""))
        health = 100 - failures * 12 - int(downtime_h * 4) - (15 if shrinking else 0) - (20 if insp and insp["state"] == "problem" else 8 if insp and insp["state"] == "remark" else 0)
        out.append({"equipment_id": e["id"], "equipment_name": e["name"], "line": e["line"], "failures": failures, "downtime_h": downtime_h,
                    "loss_kzt": loss, "risk": risk, "health": max(5, min(100, health)), "days_to_next": days_to_next, "expected_interval": expected,
                    "shrinking": shrinking, "last_inspection": insp, "explanation": ("; ".join(reasons) + ".").replace("..", ".")})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda r: (order[r["risk"]], -r["loss_kzt"], r["health"]))
