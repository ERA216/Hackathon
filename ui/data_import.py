"""Administrator screen for bringing company data into FixList."""
import streamlit as st

from backend import service


def render(user=None):
    equipment_template, events_template = service.data_import_templates()
    equipment_column, events_column = st.columns(2)
    with equipment_column:
        st.subheader("Справочник оборудования")
        st.caption("Укажите уникальный код, название, участок и стоимость часа простоя в тенге. Если вы не рассчитываете стоимость простоя, укажите 0.")
        st.download_button("Скачать шаблон оборудования", equipment_template, "fixlist-equipment.csv", "text/csv")
    with events_column:
        st.subheader("Журнал событий")
        st.caption("Укажите номер события, код оборудования, местное время, тип поломки, описание, минуты простоя и стоимость запчастей (0 при отсутствии расходов).")
        st.download_button("Скачать шаблон журнала", events_template, "fixlist-events.csv", "text/csv")

    st.markdown("Замените примеры в шаблонах и сохраните файлы как **CSV UTF-8** с разделителем `;` или `,`. "
                "Дата и время события — `ГГГГ-ММ-ДД ЧЧ:ММ`. Справочник можно загрузить заранее или вместе с журналом.")
    st.caption("До 2 МБ на файл, до 500 единиц оборудования и 1000 событий. Уже загруженные номера событий пропускаются.")
    st.caption("Типы `failure_type`: " + ", ".join(f"{code} — {label}" for code, label in service.failure_options(include_archive=False).items()))

    with st.form("company-data-import"):
        if user is None:
            name = st.text_input("Название кабинета", placeholder="Например, Мастерская Сарыарка", max_chars=120, key="csv-workspace-name")
        left, right = st.columns(2)
        equipment = left.file_uploader("CSV оборудования", type=["csv"])
        events = right.file_uploader("CSV событий", type=["csv"])
        submit_label = "Загрузить и открыть кабинет" if user is None else "Импортировать данные"
        submitted = st.form_submit_button(submit_label, type="primary")
    if submitted:
        st.session_state.pop("data_import_result", None)
        try:
            files = (equipment.getvalue() if equipment is not None else None, events.getvalue() if events is not None else None)
            if user is None:
                return service.start_workspace_with_import(name, *files)
            result = service.import_company_data(user["id"], *files)
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state["data_import_result"] = result
            if result["events_added"]:
                st.session_state["company-board-status"] = "new"
                st.session_state["board-page-new"] = 1
            st.session_state.pop("plans", None)
            st.session_state.pop("brief_key", None)
            st.rerun()


def show_result():
    if result := st.session_state.pop("data_import_result", None):
        st.success(f"Добавлено оборудования: {result['equipment_added']}. Событий: {result['events_added']}.")
        if result["equipment_skipped"] or result["events_skipped"]:
            st.caption(f"Уже в базе — оборудование: {result['equipment_skipped']}, события: {result['events_skipped']}.")
