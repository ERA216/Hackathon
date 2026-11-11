"""A reproducible public-data demo, styled with the existing FixList theme."""
import html
from datetime import date, datetime
import json

import altair as alt
import pandas as pd
import streamlit as st

from backend import config, open_data as data
from ui.charts import _render
from ui.theme import card, heading


def number(value, decimals=0):
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def duration(seconds):
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours} ч {minutes:02d} мин {seconds:02d} с" if hours else f"{minutes} мин {seconds:02d} с"


def timestamp(value):
    return datetime.fromisoformat(value).strftime("%d.%m.%Y · %H:%M:%S")


def interval_label(days):
    if days >= 1:
        return number(days, 1), "дн."
    if days * 24 >= 1:
        return number(days * 24, 1), "ч"
    if days * 1440 >= 1:
        return number(days * 1440), "мин"
    return number(days * 86400), "с"


def _sourcebar(name, detail, license_, url):
    st.markdown('<div class="od-sourcebar"><div><span class="od-live-dot" aria-hidden="true"></span>'
                f'<strong>{html.escape(name)}</strong><span>{html.escape(detail)}</span></div>'
                f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f'{html.escape(license_)} ↗</a></div>', unsafe_allow_html=True)


def _insight(title, text):
    st.markdown(f'<div class="od-insight"><span>Что видно в данных</span><strong>{html.escape(title)}</strong>'
                f'<p>{html.escape(text)}</p></div>', unsafe_allow_html=True)


def _monthly_chart(rows, start, end):
    frame = pd.DataFrame(data.monthly_hours(rows, start, end))
    frame["date"] = pd.to_datetime(frame["month"])
    frame["label"] = frame["hours"].map(lambda value: number(value, 1))
    frame["peak"] = frame["hours"] == frame["hours"].max()
    base = alt.Chart(frame).encode(
        x=alt.X("date:O", timeUnit="yearmonth", title=None, axis=alt.Axis(format="%m.%Y", labelAngle=0), sort="ascending", scale=alt.Scale(padding=0.35)),
        y=alt.Y("hours:Q", title="Часы остановок по агрегатам", scale=alt.Scale(domain=[0, max(1, frame["hours"].max()) * 1.18])),
    )
    bars = base.mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
        color=alt.condition(alt.datum.peak, alt.value("#b94922"), alt.value("#d8b79c")),
        tooltip=[alt.Tooltip("date:T", title="Месяц", format="%m.%Y"), alt.Tooltip("hours:Q", title="Часы остановок", format=".2f")],
    )
    labels = base.mark_text(dy=-12, fontSize=12, fontWeight=600, color="#493e36").encode(text="label:N")
    _render(bars + labels, 310)


def _event_detail(rows):
    preferred = next((row["id"] for row in rows if row["message"] == "Overload gear oil pump"), rows[0]["id"])
    ids = [row["id"] for row in rows]
    lookup = {row["id"]: row for row in rows}
    chosen = st.selectbox("Открыть событие", ids, index=ids.index(preferred), key="od-event",
                          format_func=lambda key: f'{lookup[key]["equipment_name"]} · {timestamp(lookup[key]["started_at"])} · {data.message_ru(lookup[key])}')
    row = lookup[chosen]
    st.markdown('<div class="od-event-card"><div><span class="od-overline">'
                f'{html.escape(row["equipment_name"])} · СИГНАЛ {html.escape(row["code"])}</span>'
                f'<h3>{html.escape(data.message_ru(row))}</h3><p>{html.escape(row["message"])}</p></div>'
                f'<div class="od-event-duration"><span>Период события в журнале</span><strong>{duration(row["duration_seconds"])}</strong>'
                '<small>Длительность из исходной записи</small></div></div>', unsafe_allow_html=True)
    left, right = st.columns(2)
    left.caption(f'Начало · {timestamp(row["started_at"])} UTC')
    right.caption(f'Окончание · {timestamp(row["ended_at"])} UTC')
    with st.expander("Исходная запись и происхождение"):
        st.write(f'**Файл:** {row["source_file"]}')
        st.write(f'**Строка данных:** {row["source_row"]} · **ID:** {row["id"]}')
        st.json({key: row[key] for key in ("started_at", "ended_at", "source_duration", "status", "code", "message", "comment", "service_category", "iec_category")})
        st.caption("Русское название — перевод для интерфейса. Английское сообщение и временные метки сохранены. Расходы на ремонт в источнике не указаны.")


def _kelmarsh(use_workspace=False):
    from backend import service
    meta = data.manifest()["kelmarsh"]
    _sourcebar("Kelmarsh · Великобритания", f'{meta["count"]} событий · 6 турбин · архив 2016 года · UTC', meta["license"], meta["url"])
    st.caption("Вынужденные остановки по классификации источника. Среди них есть защитные и ручные остановки; это не число подтверждённых механических поломок.")
    left, right = st.columns([1, 1.2], gap="large")
    with left:
        equipment = st.selectbox("Оборудование", ["all"] + [f"KEL{i:02d}" for i in range(1, 7)], key="od-equipment",
                                 format_func=lambda key: "Все 6 турбин" if key == "all" else f"Kelmarsh {int(key[-2:])} · Senvion MM92")
    with right:
        period = st.date_input("Период архива", value=(date(2016, 1, 1), date(2016, 12, 31)), min_value=date(2016, 1, 1), max_value=date(2016, 12, 31), format="DD.MM.YYYY", key="od-period")
    if len(period) != 2:
        st.info("Выберите дату окончания периода.")
        return
    start, end = period
    ids = [f"KEL{i:02d}" for i in range(1, 7)] if equipment == "all" else [equipment]
    if use_workspace:
        rows = [row for row in service.archive_report(start=start, end=end)["rows"] if row["equipment_id"] in ids]
    else:
        rows = data.select_events(ids, start, end)
    stats = data.summary(rows, start, end)
    with st.container(key="od-metrics"):
        columns = st.columns(4)
        for column, label, value, note in zip(columns,
                ["Записей об остановках", "Периодов остановки", "Часы остановок", "Повторяющихся сигналов"],
                [str(stats["events"]), str(stats["episodes"]), number(stats["downtime_seconds"] / 3600, 1), str(stats["repeat_groups"])],
                ["Исходные строки журнала", "Пересечения объединены", "Сумма по выбранным турбинам", "Один код на одной турбине"]):
            with column:
                card(label, value, note)
    if not rows:
        st.info("В выбранном периоде записей этой категории нет. Выберите другую турбину или расширьте период.")
        return
    repeats = data.repeated_signals(rows)
    if repeats:
        top = repeats[0]
        _insight(f'{top["label"]} — {top["count"]} записей на Kelmarsh {int(top["equipment_id"][-2:])}',
                 f'Повторяется код {top["code"]}. История помогает выбрать, какой сигнал проверить. Причину и необходимость ремонта подтверждает специалист.')
    else:
        _insight("В выбранном периоде повторений одного кода на одной турбине нет", "Это описание выбранного архива, а не прогноз будущих отказов.")
    with st.container(border=True):
        st.subheader("Когда оборудование было остановлено")
        st.caption("По месяцам · пересечения событий на одной турбине объединены · время UTC")
        _monthly_chart(rows, start, end)
        st.caption("Это суммарные часы остановок отдельных агрегатов, а не длительность остановки всего ветропарка. На границах периода учитывается только попавшая в него часть события.")
    st.subheader("История повторяющегося сигнала")
    if repeats:
        keys = [(item["equipment_id"], item["code"]) for item in repeats]
        groups = dict(zip(keys, repeats))
        key = st.selectbox("Сигнал и оборудование", keys, key="od-repeat", format_func=lambda key: f'Kelmarsh {int(key[0][-2:])} · {groups[key]["label"]} · {groups[key]["count"]} записей')
        group = groups[key]
        frame = pd.DataFrame(group["events"])
        frame["when"] = pd.to_datetime(frame["started_at"], utc=True).dt.tz_localize(None)
        frame["hours"] = frame["duration_seconds"] / 3600
        tick_labels = {row["id"]: datetime.fromisoformat(row["started_at"]).strftime("%d.%m|%H:%M") for row in group["events"]}
        label_expr = "split(" + json.dumps(tick_labels) + "[datum.value], '|')"
        chart = alt.Chart(frame).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#b94922").encode(
            x=alt.X("id:O", title="События по порядку · дата и время UTC", sort=frame["id"].tolist(), axis=alt.Axis(labelExpr=label_expr, labelAngle=0, labelOverlap=True), scale=alt.Scale(padding=0.35)),
            y=alt.Y("hours:Q", title="Длительность записи, ч"),
            tooltip=[alt.Tooltip("when:T", title="Начало · UTC", format="%d.%m.%Y %H:%M"), alt.Tooltip("hours:Q", title="Длительность, ч", format=".2f"), alt.Tooltip("message:N", title="Оригинал")],
        )
        _render(chart, 255)
        intervals = group["interval_days"]
        if intervals:
            cells = "".join(f'<div><span>Интервал {index + 1}</span><strong>{interval_label(value)[0]} <small>{interval_label(value)[1]}</small></strong></div>' for index, value in enumerate(intervals))
            st.markdown('<div class="od-intervals">' + cells + '</div>', unsafe_allow_html=True)
            st.caption("Интервалы между началами непересекающихся периодов с этим кодом. Это наблюдаемая история; прогноз следующей поломки по ней не заявляется.")
        st.caption(f'Оригинал: {group["message"]} · код {group["code"]}. Столбцы идут по порядку с равным шагом; реальные интервалы показаны отдельно. Длительность каждой записи полная, включая часть за границей выбранного периода.')
    else:
        st.caption("Для временной шкалы нужны хотя бы две записи с одним кодом на одной турбине.")
    st.subheader("Одно событие подробно")
    _event_detail(rows)
    st.subheader("Журнал событий")
    query = st.text_input("Поиск в открытом архиве", placeholder="Насос, преобразователь, код сигнала…", key="od-search").casefold().strip()
    shown = [row for row in rows if not query or query in " ".join((row["equipment_name"], row["code"], row["message"], data.message_ru(row))).casefold()]
    if shown:
        st.dataframe([{"Оборудование": row["equipment_name"], "Начало · UTC": timestamp(row["started_at"]), "Длительность": duration(row["duration_seconds"]),
                       "Код": row["code"], "Описание · перевод": data.message_ru(row), "Исходное сообщение": row["message"], "ID источника": row["id"]} for row in shown], hide_index=True, use_container_width=True, height=340)
        st.download_button("Скачать выбранные события · CSV", data.export_events(shown), "fixlist_kelmarsh_events.csv", "text/csv", key="od-download")
    else:
        st.info("По запросу ничего не найдено. Попробуйте другой код или описание.")
    st.caption("Источник: Charlie Plumley, Roberta Takeuchi / Cubico Sustainable Investments Ltd. Kelmarsh wind farm data, v4, CC BY 4.0. Выборка и расчёты FixList; оригинальные даты сохранены.")


def _entities_html(entities):
    if not entities:
        return '<div class="od-empty">В этом разборе сущности не найдены.</div>'
    return '<div class="od-entity-list">' + "".join(
        f'<div class="od-entity od-kind-{html.escape(item["kind"])}"><span>{html.escape(data.KINDS[item["kind"]])}</span>'
        f'<strong>{html.escape(item["text"])}</strong></div>' for item in entities) + '</div>'


def _maintie():
    meta = data.manifest()["maintie"]
    _sourcebar("MaintIE · технические записи", f'{meta["count"]} примеров · экспертная разметка · английский язык', "MIT", meta["url"])
    st.caption("Отдельная выборка записей о техническом обслуживании. Идентификаторы и числа обезличены авторами; связи с турбинами Kelmarsh у этих текстов нет.")
    examples = data.examples()
    ids = [item["id"] for item in examples]
    lookup = {item["id"]: item for item in examples}
    preferred = next((item["id"] for item in examples if "broken bolt on brake pump" in item["text"]), ids[0])
    chosen = st.selectbox("Выберите техническую запись", ids, index=ids.index(preferred), format_func=lambda key: lookup[key]["text"], key="od-maintie-example")
    example = lookup[chosen]
    st.markdown('<div class="od-raw-text"><span>Оригинальная запись · ' + html.escape(chosen) + '</span>'
                f'<p>{html.escape(example["text"])}</p><small>&lt;id&gt; — скрытый идентификатор, &lt;num&gt; — скрытое число в исходном датасете.</small></div>', unsafe_allow_html=True)
    use_ai = st.checkbox("Использовать подключённый ИИ для разбора", value=False, disabled=not config.ai_enabled(), key="od-maintie-ai")
    if not config.ai_enabled():
        st.caption("Сейчас доступен разбор по правилам. При настроенном ИИ появится возможность проверить его на той же записи.")
    if st.button("Разобрать запись", type="primary", key="od-maintie-run"):
        with st.spinner("Извлекаем сведения из текста…"):
            st.session_state.od_maintie_result = {"id": chosen, "use_ai": use_ai, "result": data.analyze_example(example, use_ai=use_ai)}
    saved = st.session_state.get("od_maintie_result", {})
    result = saved.get("result") if saved.get("id") == chosen and saved.get("use_ai") == use_ai else None
    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Результат FixList")
        if result:
            st.caption("Разбор ИИ · проверены границы фрагментов" if result["mode"] == "ai" else "Независимый разбор по правилам · без ИИ")
            st.markdown(_entities_html(result["entities"]), unsafe_allow_html=True)
        else:
            st.markdown('<div class="od-empty">Нажмите «Разобрать запись», чтобы извлечь сведения из исходного текста.</div>', unsafe_allow_html=True)
    with right:
        st.subheader("Эталон из датасета")
        st.caption("Экспертная разметка MaintIE · показана отдельно от результата FixList")
        st.markdown(_entities_html(data.gold_entities(example)), unsafe_allow_html=True)
    if result:
        if result["note"]:
            st.info(result["note"])
        _insight(f'Совпало {result["matched"]} из {result["expected"]} эталонных фрагментов',
                 f'Найдено FixList: {result["predicted"]}. Дополнительных фрагментов: {result["extra"]}; пропущено: {result["missed"]}. '
                 'Сравниваются точные границы токенов и один из пяти классов. Это результат одной записи, не оценка точности на всём датасете.')
        if result["rejected"]:
            st.caption(f'Отброшено некорректных фрагментов ответа ИИ: {result["rejected"]}.')
    st.markdown('<div class="od-note"><strong>Неизвестные поля остаются неизвестными</strong>'
                '<p>В этих примерах нет подтверждённых дат остановки, длительности простоя и расходов на ремонт. Они используются для проверки извлечения сведений из текста.</p></div>', unsafe_allow_html=True)
    with st.expander("Разметка, связи и источник записи"):
        st.json(example)
        st.markdown(f'[Исходный корпус, зафиксированная версия]({meta["download_url"]}) · MIT')
        st.caption("В интерфейсе полные типы сведены к пяти верхним классам. Исходные типы и связи сохранены в JSON.")
    st.caption("Источник: Tyler K. Bikaun, Tim French, Michael Stewart, Wei Liu, Melinda Hodkiewicz. MaintIE, 2024. MIT. Тексты и разметка сохранены; добавлены ID выборки.")


def _sources():
    meta = data.manifest()
    st.subheader("Откуда взяты данные")
    with st.container(border=True):
        st.markdown("**Kelmarsh wind farm data · v4**")
        st.write("Charlie Plumley, Roberta Takeuchi / Cubico Sustainable Investments Ltd")
        st.markdown(f'[Открыть первоисточник]({meta["kelmarsh"]["url"]}) · [CC BY 4.0]({meta["kelmarsh"]["license_url"]})')
        st.write(f'В проект включены {meta["kelmarsh"]["count"]} полных записей с положительной длительностью: Stop / Forced outage, шесть турбин, 2016 год. Даты не переносились на сегодняшний день.')
        st.caption(f'Преобразования: CSV → JSON, ID исходной строки, время UTC, проверка длительности, перевод описаний для интерфейса. Исключено неполных записей: {len(meta["kelmarsh"]["excluded"])}. Пересечения объединяются при подсчёте часов. Стоимости и подтверждённых результатов ремонта нет.')
    with st.container(border=True):
        st.markdown("**MaintIE · gold corpus**")
        st.write("Tyler K. Bikaun, Tim French, Michael Stewart, Wei Liu, Melinda Hodkiewicz")
        st.markdown(f'[Репозиторий и описание]({meta["maintie"]["url"]}) · MIT')
        st.write(f'Выбраны {meta["maintie"]["count"]} текстов о промышленных узлах с экспертной разметкой. Оригинальные тексты, токены, сущности и связи сохранены.')
        st.caption("Это тематическая демонстрационная выборка. Эталон используется только для сравнения после разбора; он не передаётся модели или резервному алгоритму. Модель на этой выборке не дообучалась.")
        with st.expander("Лицензия MaintIE"):
            st.text((data.DATA / "MaintIE_LICENSE.md").read_text(encoding="utf-8"))
    st.download_button("Скачать сведения об источниках · JSON", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"), "fixlist_data_sources.json", "application/json", key="od-manifest-download")
