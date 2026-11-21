"""Existing FixList sections backed by the real Kelmarsh workspace."""
from datetime import date, datetime
import html
import math

import altair as alt
import pandas as pd
import streamlit as st

from backend import service
from ui import sessions
from ui.charts import _render
from ui.open_data import _kelmarsh, _maintie, _sources, duration, interval_label, number, timestamp
from ui.theme import card, heading


def maintenance_examples():
    _maintie()


def source_banner():
    st.markdown('<div class="od-sourcebar"><div><span class="od-live-dot" aria-hidden="true"></span>'
                '<strong>Kelmarsh · реальные события</strong><span>Архив 2016 года · 6 турбин · UTC</span></div>'
                '<a href="https://zenodo.org/records/16807551" target="_blank" rel="noopener noreferrer">Источник · CC BY 4.0 ↗</a></div>', unsafe_allow_html=True)


def _metrics(items):
    with st.container(key="od-metrics"):
        for column, (label, value, note) in zip(st.columns(len(items)), items):
            with column:
                card(label, str(value), note)


def _money_panel(first, second, note, period, *, compact=False):
    title = "" if compact else f'<div class="archive-money-heading">Денежный эффект <span>{html.escape(period)}</span></div>'
    return (f'<section class="archive-money{" archive-money-compact" if compact else ""}" aria-label="Расчётный финансовый эффект">'
            + title + '<div class="archive-money-values"><div>'
            f'<span>{html.escape(first[0])}</span><strong>{html.escape(first[1])}</strong></div>'
            f'<div class="archive-money-saving"><span>{html.escape(second[0])}</span><strong>{html.escape(second[1])}</strong></div>'
            f'</div><p>{html.escape(note)}</p></section>')


def _money_estimate(hours, period="за это событие", *, compact=False):
    """Illustrative economics, kept separate from the archive's unknown costs."""
    hourly_rate = 50_000
    reduction = 0.20
    loss = max(0, hours) * hourly_rate

    def amount(value):
        if value >= 1_000_000:
            return number(value / 1_000_000, 1) + " млн ₸"
        rounded = round(value / 100) * 100 if value >= 100 else round(value)
        return number(rounded) + " ₸"

    note = (period.capitalize() + ". " if compact else "") + (
        "Пример: 50 000 ₸/ч · сокращение простоя на 20%. "
        "Расчётный сценарий, не фактические расходы или достигнутая экономия.")
    return _money_panel(("Потери · оценка", f"≈ {amount(loss)}"), ("Потенциал экономии", f"≈ {amount(loss * reduction)}"),
                        note, period, compact=compact)


def _event_money(row, *, compact=False):
    source = row["source_record"]
    if source:
        return _money_estimate(source["duration_seconds"] / 3600, compact=compact)
    tariff = row["hour_cost_kzt"]
    parts = row["parts_cost_kzt"]
    loss = row["downtime_min"] / 60 * (tariff or 0) if tariff or not row["downtime_min"] else None
    loss_label = "—" if loss is None else f"≈ {number(loss)} ₸"
    parts_label = "—" if parts is None else f"{number(parts)} ₸"
    if tariff:
        note = f"Простой рассчитан по тарифу {number(tariff)} ₸/ч."
    else:
        note = "Тариф простоя не задан." if loss is None else "Простоя не было."
    return _money_panel(("Простой · оценка", loss_label), ("Запчасти · по записи", parts_label), note, "за это событие", compact=compact)


def _event_card(row, open_event):
    source = row.get("source_record")
    state = row["status"] if row["status"] in {"archived", "new", "approved", "escalated", "resolved"} else "archived"
    started = datetime.fromisoformat(source["started_at"] if source else row["occurred_at"])
    seconds = source["duration_seconds"] if source else row["downtime_min"] * 60
    elapsed = duration(seconds)
    text = " ".join(row["raw_text"].split())
    label = row["failure_label"] if source else text[:150] + ("…" if len(text) > 150 else "")
    origin = "SCADA · Код " + source["code"] if source else ("CSV" if row["source_id"] else "FIXLIST")
    with st.container(key=f'archive-card-{row["id"]}'):
        st.markdown(
            f'<article class="archive-ticket archive-state-{state}">'
            f'<div class="archive-ticket-top"><span class="archive-status">{html.escape(row["status_label"])}</span>'
            f'<span class="archive-origin">{html.escape(origin)}</span></div>'
            f'<h3>{html.escape(row["equipment_name"])}</h3>'
            f'<p class="archive-ticket-description">{html.escape(label)}</p>'
            '<div class="archive-ticket-facts"><div><span>Длительность остановки</span>'
            f'<strong>{html.escape(elapsed)}</strong></div><div><span>{"Начало · UTC" if source else "Дата записи"}</span>'
            f'<strong>{started:%d.%m.%Y} · {started:%H:%M:%S}</strong></div></div>'
            + _event_money(row, compact=True) + '</article>', unsafe_allow_html=True)
        if st.button("Открыть карточку →", key=f'archive-open-{row["id"]}', use_container_width=True):
            open_event(row["id"])


def event_grid(rows, open_event):
    with st.container(key="archive-event-grid"):
        for offset in range(0, len(rows), 3):
            columns = st.columns(3, gap="medium")
            for column, row in zip(columns, rows[offset:offset + 3]):
                with column:
                    _event_card(row, open_event)


def render_event_details(row):
    source = row["source_record"]
    origin = "АРХИВ 2016" if source else ("CSV" if row["source_id"] else "FIXLIST")
    st.markdown(f'<div class="archive-detail-header"><span>СОБЫТИЕ ОБОРУДОВАНИЯ · {origin}</span>'
                f'<h3>{html.escape(row["equipment_name"])}</h3><p>{html.escape(row["failure_label"])}</p></div>', unsafe_allow_html=True)
    if source:
        facts = [("Начало · UTC", timestamp(source["started_at"])), ("Окончание · UTC", timestamp(source["ended_at"])),
                 ("Длительность", duration(source["duration_seconds"])), ("Исходный код", source["code"])]
    else:
        reference = ("Номер события в CSV", row["source_id"]) if row["source_id"] else ("Кто внёс", row["author"] or "—")
        facts = [("Дата и время", timestamp(row["occurred_at"])), ("Длительность", duration(row["downtime_min"] * 60)),
                 ("Статус", row["status_label"]), reference]
    st.markdown('<dl class="archive-detail-facts">' + "".join(f'<div><dt>{html.escape(name)}</dt><dd>{html.escape(value)}</dd></div>' for name, value in facts) + '</dl>', unsafe_allow_html=True)
    st.markdown(_event_money(row), unsafe_allow_html=True)
    if source:
        st.caption("Состояние ремонта и расходы в источнике не указаны. Результат проверки можно добавить в FixList.")
        with st.expander("Первоисточник и технические сведения"):
            st.write(source["message"])
            st.caption(f'ID: {source["id"]} · {source["source_file"]}, строка {source["source_row"]}')
            st.json(source)
    else:
        with st.expander("Исходная запись", expanded=True):
            st.text(row["raw_text"])


@st.dialog("Событие оборудования", width="large")
def event_dialog(incident_id, admin_name):
    service.use_company(sessions.require_user()["company_id"])
    record = service.ticket(incident_id)
    if not record:
        st.error("Запись не найдена.")
        return
    render_event_details(record)
    status = record["status"]
    if status == "archived":
        if st.button("Создать задачу по проверке сигнала", type="primary", key="archive-create-task"):
            service.reopen_ticket(incident_id)
            st.rerun()
    elif status == "new":
        st.caption("Задача создана в FixList для проверки архивного сигнала.")
        if st.button("Поручить проверку сотрудникам", type="primary", key="archive-approve-task"):
            service.approve_ticket(incident_id, admin_name)
            st.rerun()
    elif status in ("approved", "escalated"):
        st.caption("Результат проверки заполняет пользователь FixList.")
        resolution = st.text_area("Что проверили и что установили", key="archive-resolution")
        if st.button("Сохранить результат проверки", type="primary", key="archive-resolve-task"):
            try:
                service.resolve_ticket(incident_id, admin_name, resolution)
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    elif status == "resolved":
        st.write("**Результат проверки в FixList:** " + (record.get("resolution") or "—"))
        st.caption("Дата записи результата: " + (record.get("resolved_at") or "—"))


def board(user, add_data):
    heading("01 / Панель задач", "Сигналы записаны.\nПроверки под контролем.", "Реальные остановки оборудования Kelmarsh. Откройте исходное событие и создайте задачу по его проверке.")
    add_data()
    counts = service.ticket_counts()
    _metrics([("Событий в архиве", len(service.archive_report()["rows"]), "Исходные записи Kelmarsh"),
              ("Новых задач", counts["new"], "Проверки, созданные в FixList"),
              ("В работе", counts["approved"] + counts["escalated"], "Текущий статус задач FixList")])
    statuses = {"Архив событий": "archived", "Новые задачи": "new", "В работе": "approved", "Нужна помощь": "escalated", "Проверены": "resolved"}
    selected = st.radio("Показать", list(statuses), horizontal=True, key="archive-board-filter")
    rows = service.tickets([statuses[selected]], limit=10000)
    query = st.text_input("Поиск по оборудованию и сигналу", placeholder="Kelmarsh 1, насос, код…", key="archive-board-search").casefold().strip()
    rows = [row for row in rows if not query or query in (row["equipment_name"] + " " + row["raw_text"] + " " + (row.get("source_record") or {}).get("code", "")).casefold()]
    if not rows:
        st.info("В этом разделе пока нет записей. Задачи на проверку создаются из архивного события.")
        return
    pages = max(1, math.ceil(len(rows) / 12))
    if st.session_state.get("archive-board-page", 1) > pages:
        st.session_state["archive-board-page"] = 1
    page = st.number_input("Страница", min_value=1, max_value=pages, value=1, step=1, key="archive-board-page")
    st.caption(f"Найдено: {len(rows)} · страница {page} из {pages} · сначала новые события")
    event_grid(rows[(page - 1) * 12:page * 12], lambda incident_id: event_dialog(incident_id, user["display_name"]))


def health():
    heading("03 / Состояние оборудования", "История оборудования —\nв одном месте.", "Реальные записи остановок и повторения сигналов за 2016 год. Текущее техническое состояние требует отдельной диагностики.")
    rows = service.archive_equipment()
    view = service.archive_report()
    _metrics([("Оборудования", len(rows), "Senvion MM92 · Kelmarsh"), ("Событий", view["stats"]["events"], "Остановки по классификации источника"),
              ("Часы остановок", number(view["stats"]["downtime_seconds"] / 3600, 1), "Сумма по отдельным агрегатам")])
    with st.container(key="archive-equipment-grid"):
        for offset in range(0, len(rows), 3):
            for column, row in zip(st.columns(3, gap="medium"), rows[offset:offset + 3]):
                with column:
                    with st.container(key=f'archive-equipment-card-{row["id"]}'):
                        top = row["top_signal"]
                        description = top["label"] if top else "Повторений одного сигнала нет"
                        last = datetime.fromisoformat(row["last_event"]).strftime("%d.%m.%Y") if row["last_event"] else "—"
                        st.markdown('<article class="archive-equipment-ticket"><div class="archive-ticket-top">'
                                    '<span class="archive-status">История оборудования</span><span class="archive-origin">2016</span></div>'
                                    f'<h3>{html.escape(row["name"])}</h3><p class="archive-equipment-model">Senvion MM92 · ветроустановка</p>'
                                    '<div class="archive-equipment-numbers">'
                                    f'<div><strong>{row["events"]}</strong><span>событий</span></div>'
                                    f'<div><strong>{number(row["hours"], 1)} ч</strong><span>остановок</span></div></div>'
                                    '<p class="archive-ticket-description"><span class="archive-recurring-label">Чаще повторяется: </span>'
                                    f'{html.escape(description)}</p>'
                                    f'<div class="archive-ticket-reference">Последнее событие · {last}</div>'
                                    + _money_estimate(row["hours"], "за 2016 год", compact=True) + '</article>', unsafe_allow_html=True)
                        if st.button("Посмотреть историю →", key=f'archive-equipment-open-{row["id"]}', use_container_width=True):
                            st.session_state["od-equipment"] = row["id"]
                            st.session_state["page"] = "История и эффект"
                            st.rerun()


def daily():
    heading("04 / Сводка дня", "Один день.\nВсе события на виду.", "Выберите день в реальном архиве Kelmarsh. Длительность считается только в пределах выбранных суток, по времени UTC.")
    archive = service.archive_report()
    latest = max(date.fromisoformat(row["started_at"][:10]) for row in archive["rows"])
    day = st.date_input("День архива", value=latest, min_value=date(2016, 1, 1), max_value=date(2016, 12, 31), format="DD.MM.YYYY", key="archive-summary-day")
    view = service.archive_daily(day)
    stats = view["stats"]
    _metrics([("Началось событий", view["started_events"], day.strftime("%d.%m.%Y") + " · UTC"),
              ("Часы остановок", number(stats["downtime_seconds"] / 3600, 2), "Только внутри выбранных суток"),
              ("Турбин с событиями", len(stats["equipment"]), "В том числе события с предыдущего дня"),
              ("В архиве записей", len(archive["rows"]), "Исходные события 2016 года")])
    with st.container(border=True):
        st.subheader("Сводка по данным журнала")
        st.write(f'За {day:%d.%m.%Y} началось событий: {view["started_events"]}. В выбранные сутки попали {stats["events"]} записей об остановках.')
        st.write(f'Объединённая длительность по агрегатам: {number(stats["downtime_seconds"] / 3600, 2)} ч. Пересекающиеся интервалы на одной турбине не суммируются повторно.')
    if view["rows"]:
        st.subheader("События выбранного дня")
        st.caption("Карточка показывает полную длительность исходного события; в сводке выше учитывается только часть внутри выбранных суток.")
        tickets = {row["id"]: row for row in service.tickets(limit=10000)}
        event_grid([tickets[row["incident_id"]] for row in view["rows"]],
                   lambda incident_id: event_dialog(incident_id, st.session_state["user"]["display_name"]))
    else:
        st.info("В этот день в выбранном архиве остановок этой категории нет.")


def signals():
    heading("05 / Повторяющиеся сигналы", "Повторение видно.\nИсторию можно проверить.", "Повторяющиеся сигналы по одному объекту или оборудованию. Интервалы рассчитаны по реальным датам; причина и прогноз следующего отказа не подменяются догадкой.")
    view = service.archive_report()
    groups = view["signals"]
    _metrics([("Повторяющихся кодов", len(groups), "Пары «турбина × код»"), ("Событий в базе", len(view["rows"]), "Полные исходные записи"),
              ("Турбин", 6, "Период: 2016 год")])
    keys = list(range(len(groups)))
    selected = st.selectbox("Оборудование и сигнал", keys, format_func=lambda index: f'Kelmarsh {int(groups[index]["equipment_id"][-2:])} · {groups[index]["label"]} · {groups[index]["count"]} записей', key="archive-signal")
    group = groups[selected]
    st.subheader(group["label"])
    st.caption(f'Оригинал: {group["message"]} · код {group["code"]}')
    frame = pd.DataFrame(group["events"])
    frame["when"] = pd.to_datetime(frame["started_at"], utc=True).dt.tz_localize(None)
    frame["hours"] = frame["duration_seconds"] / 3600
    frame["label"] = frame["when"].dt.strftime("%d.%m %H:%M")
    chart = alt.Chart(frame).mark_bar(color="#b94922", cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("label:O", sort=frame["label"].tolist(), title="События по порядку · UTC", axis=alt.Axis(labelAngle=-25), scale=alt.Scale(padding=0.35)),
        y=alt.Y("hours:Q", title="Длительность записи, ч"),
        tooltip=[alt.Tooltip("when:T", title="Дата · UTC", format="%d.%m.%Y %H:%M:%S"), alt.Tooltip("hours:Q", title="Часы", format=".2f")])
    _render(chart, 310)
    if group["interval_days"]:
        cells = "".join(f'<div><span>Интервал {index + 1}</span><strong>{interval_label(value)[0]} <small>{interval_label(value)[1]}</small></strong></div>' for index, value in enumerate(group["interval_days"]))
        st.markdown('<div class="od-intervals">' + cells + '</div>', unsafe_allow_html=True)
    st.caption("Столбцы расположены с равным шагом по порядку событий. Фактические интервалы показаны отдельно.")
    st.dataframe([{"Оборудование": f'Kelmarsh {int(row["equipment_id"][-2:])}', "Сигнал": row["label"], "Код": row["code"], "Записей": row["count"]} for row in groups], hide_index=True, use_container_width=True)


def finance():
    heading("06 / Деньги и экономия", "Реальные часы.\nПонятный расчёт.", "В основе — остановки Kelmarsh. Стоимость часа, планируемое сокращение простоев и затраты на меру задаются отдельно.")
    view = service.archive_report()
    hours = view["stats"]["downtime_seconds"] / 3600
    _metrics([("Часы остановок", number(hours, 1), "По данным 2016 года"), ("Стоимость ремонта", "—", "В источнике не указана"), ("Подтверждённая экономия", "—", "Нет данных о результатах мер")])
    st.subheader("Сценарий стоимости остановок")
    st.caption("Расчёт по введённым параметрам. Полученные суммы не являются фактическими расходами ветропарка.")
    rate = st.number_input("Стоимость часа остановки, ₸/час", min_value=0, value=None, step=1000, key="archive-hour-rate", placeholder="Укажите ваш тариф")
    if rate is None:
        st.info("Введите стоимость часа, чтобы получить расчёт по реальной длительности остановок.")
        return
    annual_cost = hours * rate
    card("Стоимость остановок по введённому тарифу", f"{number(annual_cost)} ₸", f"{number(hours, 2)} ч × {number(rate)} ₸/ч · период 2016 года")
    left, right = st.columns(2)
    reduction = left.number_input("Планируемое сокращение часов, %", min_value=0, max_value=100, value=None, step=1, key="archive-reduction")
    investment = right.number_input("Затраты на выбранную меру, ₸", min_value=0, value=None, step=1000, key="archive-investment")
    if reduction is not None:
        monthly = annual_cost / 12 * reduction / 100
        card("Экономия в месяц по сценарию", f"{number(monthly)} ₸", "Среднемесячная стоимость за год × планируемое сокращение")
        if investment is not None and monthly > 0:
            card("Окупаемость по сценарию", number(investment / monthly, 1) + " мес.", "Затраты на меру / расчётная месячная экономия")


def history():
    heading("07 / История", "Каждое событие —\nс первоисточником.", "Исходные даты и описания Kelmarsh сохранены. Историю можно отфильтровать по оборудованию, периоду и коду сигнала.")
    _kelmarsh(use_workspace=True)
    with st.expander("Происхождение данных и лицензии"):
        _sources()


def ask():
    heading("02 / Спросить ИИ", "Спросите по делу.\nОтвет — по истории.", "Реальные события Kelmarsh за 2016 год: оборудование, повторяющиеся коды и длительность остановок. Ответ опирается на записи основной базы.")
    questions = ["Какие сигналы повторяются чаще?", "Что происходило на Kelmarsh 1?", "Сколько часов остановок в архиве?"]
    picked = None
    for column, question in zip(st.columns(3), questions):
        if column.button(question, use_container_width=True, key="archive-question-" + question):
            picked = question
    chat = st.session_state.setdefault("archive-chat", [])
    for message in chat:
        with st.chat_message("user"):
            st.write(message["q"])
        with st.chat_message("assistant"):
            st.write(message["answer"])
            st.caption("Ответ ИИ · по данным архива" if message["mode"] == "ai" else "Ответ собран кодом из реальных записей")
    with st.expander("Проверить разбор технической записи · MaintIE"):
        _maintie()
    question = st.chat_input("Спросите об оборудовании или сигнале Kelmarsh") or picked
    if question:
        with st.spinner("Проверяем историю оборудования…"):
            result = service.ask_ai(question, chat)
            chat.append({"q": question, **result})
        st.rerun()
