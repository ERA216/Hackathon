"""Presentation-only chart views; source records and business logic stay unchanged."""
import html
import altair as alt
import pandas as pd
import streamlit as st


INK, MUTED, ACCENT, SAND = "#493e36", "#83766b", "#b94922", "#d8b79c"


def _number(value):
    return f"{value:g}".replace(".", ",")


def _header(title, subtitle, stats):
    st.markdown(f'<div class="chart-heading">{html.escape(title)}</div>'
                f'<div class="chart-description">{html.escape(subtitle)}</div>'
                '<div class="chart-stats">' + ''.join(
                    f'<div><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'
                    for label, value in stats) + '</div>', unsafe_allow_html=True)


def _render(chart, height):
    chart = chart.properties(
        height=height, padding={"left": 8, "right": 18, "top": 28, "bottom": 8},
        usermeta={"embedOptions": {"actions": False, "renderer": "svg"}},
    ).configure(
        background="#fffdf9", font="Onest, Segoe UI, sans-serif",
    ).configure_view(stroke=None).configure_axis(
        labelFontSize=12, titleFontSize=12, titleFontWeight=500,
        labelColor=MUTED, titleColor=MUTED, labelPadding=12, titlePadding=16,
        gridColor="#eee7de", domain=False, ticks=False,
    ).configure_axisX(grid=False)
    # No interactive()/scale binding: mouse movement and wheel cannot zoom the plot.
    st.altair_chart(chart, use_container_width=True, theme=None)


def _legend(items):
    st.markdown('<div class="chart-legend">' + ''.join(
        f'<span><i class="legend-{style}"></i>{html.escape(text)}</span>'
        for style, text in items) + '</div>', unsafe_allow_html=True)


def activity_chart(rows):
    if not rows:
        st.info("За выбранный период пока нет данных.")
        return
    frame = pd.DataFrame(rows).rename(columns={"Дата": "date", "Записей": "count"})
    frame["today"] = [False] * (len(frame) - 1) + [True]
    avg = float(frame["count"].mean())
    peak = frame.loc[frame["count"].idxmax()]
    with st.container(border=True):
        _header("Активность по дням", "Инциденты, зарегистрированные за последние 7 дней", [
            ("Всего инцидентов", str(int(frame["count"].sum()))),
            ("В среднем за день", _number(round(avg, 1))),
            ("Максимум за день", f'{int(peak["count"])} · {peak["date"]}'),
        ])
        base = alt.Chart(frame).encode(
            x=alt.X("date:O", title=None, sort=None, axis=alt.Axis(labelAngle=0, labelOverlap=False), scale=alt.Scale(padding=0.55)),
            y=alt.Y("count:Q", title="Инциденты, шт.", scale=alt.Scale(domain=[0, max(1, float(frame["count"].max())) * 1.2]),
                    axis=alt.Axis(tickMinStep=1, tickCount=5, format="d")))
        bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            color=alt.condition(alt.datum.today, alt.value(ACCENT), alt.value(SAND)),
            tooltip=[alt.Tooltip("date:O", title="Дата"), alt.Tooltip("count:Q", title="Инцидентов", format="d")])
        labels = base.mark_text(dy=-12, fontSize=14, fontWeight=600, color=INK).encode(text=alt.Text("count:Q", format="d"))
        mean = alt.Chart(pd.DataFrame({"avg": [avg]})).mark_rule(stroke=MUTED, strokeDash=[5, 5], strokeWidth=1).encode(y="avg:Q")
        _render(bars + mean + labels, 310)
        _legend([("today", "Сегодня"), ("history", "Предыдущие дни"), ("average", f"Среднее: {_number(round(avg, 1))} / день")])
        st.caption("Количество инцидентов отражает записи в базе, а не загрузку или производительность оборудования.")


def downtime_chart(evidence):
    if not evidence:
        st.info("Нет записей для построения истории остановок.")
        return
    frame = pd.DataFrame(evidence).sort_values(["date", "id"]).reset_index(drop=True)
    frame["when"] = pd.to_datetime(frame["date"])
    frame["record"] = frame["id"].map(lambda i: f"#{i}")
    avg = float(frame["downtime_min"].mean())
    maximum = float(frame["downtime_min"].max())
    frame["longest"] = frame["downtime_min"] == frame["downtime_min"].max()
    frame["label"] = frame["downtime_min"].map(lambda m: f"{m} мин")
    dates = frame["when"].drop_duplicates().tolist()
    span = max(1, (dates[-1] - dates[0]).days)
    ticks = [alt.DateTime(year=d.year, month=d.month, date=d.day) for d in dates[::max(1, (len(dates) + 6) // 7)]]
    with st.container(border=True):
        _header("История остановок", "Каждая точка — отдельный инцидент. Расстояние по времени соответствует датам.", [
            ("Суммарный простой", f'{_number(round(float(frame["downtime_min"].sum()) / 60, 1))} ч'),
            ("Средняя остановка", f'{_number(round(avg, 1))} мин'),
            ("Самая долгая", f'{_number(maximum)} мин'),
        ])
        base = alt.Chart(frame).encode(
            x=alt.X("when:T", title=None, scale=alt.Scale(domain=[dates[0] - pd.Timedelta(days=span * 0.08), dates[-1] + pd.Timedelta(days=span * 0.08)]),
                    axis=alt.Axis(values=ticks, format="%d.%m", labelAngle=0, labelOverlap="greedy")),
            y=alt.Y("downtime_min:Q", title="Простой, мин", scale=alt.Scale(domain=[0, max(1, maximum) * 1.3]), axis=alt.Axis(tickCount=5, tickMinStep=1)))
        stems = base.mark_rule(strokeWidth=2, color="#d9c5b4").encode(y2=alt.datum(0))
        points = base.mark_circle(size=110, opacity=1, stroke="#fffdf9", strokeWidth=2).encode(
            color=alt.condition(alt.datum.longest, alt.value(ACCENT), alt.value("#b69276")),
            tooltip=[alt.Tooltip("record:N", title="Запись"), alt.Tooltip("when:T", title="Дата", format="%d.%m.%Y"),
                     alt.Tooltip("downtime_min:Q", title="Простой, мин"), alt.Tooltip("raw_text:N", title="Отчёт мастера")])
        labels = base.mark_text(dy=-19, color=INK, fontSize=13, fontWeight=600).encode(
            text=alt.Text("label:N") if len(frame) <= 10 else alt.condition(alt.datum.longest, alt.Text("label:N"), alt.value("")))
        mean = alt.Chart(pd.DataFrame({"avg": [avg]})).mark_rule(stroke=MUTED, strokeDash=[5, 5], strokeWidth=1).encode(y="avg:Q")
        _render(stems + mean + points + labels, 340)
        _legend([("today", "Максимальный простой"), ("average", f"Среднее: {_number(round(avg, 1))} мин")])
        items = []
        for i, row in frame.iterrows():
            interval = "Первая запись периода" if i == 0 else f'Через {(row["when"] - frame.iloc[i - 1]["when"]).days} дн.'
            items.append(f'<div><span>{row["when"].strftime("%d.%m")} · {html.escape(row["record"])}</span>'
                         f'<strong>{int(row["downtime_min"])} мин</strong><small>{interval}</small></div>')
        st.markdown('<div class="chart-events">' + ''.join(items) + '</div>', unsafe_allow_html=True)
        if frame["when"].duplicated().any():
            st.caption("В один день зарегистрировано несколько инцидентов. Точки могут совпадать; все записи перечислены ниже.")
