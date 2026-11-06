"""UI prototype only: no backend imports, API calls or production data writes."""
import streamlit as st
from ui.theme import apply_theme, heading, card

st.set_page_config(page_title="Смена.AI · Производство под контролем", page_icon="◈", layout="wide")
apply_theme()
EXAMPLE = "Линия 2, станок №3 встал в 14:10, порвался ремень. Поставили новый, простой 1,5 часа. Причину пока не установили."
PAGES = ["Новая запись", "Аномалии", "План экономии", "История и эффект"]
if "page" not in st.session_state:
    st.session_state.page = PAGES[0]

with st.sidebar:
    st.markdown('<div class="brand">смена<span>.AI</span></div><div class="brand-sub">Меньше простоев. Больше дела.</div>', unsafe_allow_html=True)
    st.markdown('<div class="workspace">◈ &nbsp; Павлодар · Цех №1<small>Демонстрационное производство</small></div>', unsafe_allow_html=True)
    st.caption("РАБОЧЕЕ ПРОСТРАНСТВО")
    for i, page in enumerate(PAGES):
        if st.button(f"0{i+1}   {page}", type="primary" if st.session_state.page == page else "secondary", use_container_width=True):
            st.session_state.page = page
            st.rerun()
    st.divider()
    st.markdown('<span class="demo-badge">ДЕМО · ВИЗУАЛЬНЫЙ ПРОТОТИП</span>', unsafe_allow_html=True)
    st.caption("Данные вымышлены. ИИ и база данных ещё не подключены. Изменения живут только в текущем сеансе.")

top, badge = st.columns([4, 1])
top.caption("ПРОИЗВОДСТВО / " + st.session_state.page.upper())
badge.caption("●  Демо-пространство")

if st.session_state.page == PAGES[0]:
    heading("01 / Журнал смены", "Хорошая смена начинается\nс ясной картины.", "Расскажите, что произошло. Смена.AI поможет превратить запись мастера в понятную историю оборудования.")
    st.markdown('<div class="steps"><b>01 &nbsp; Опишите смену</b><span>→</span><span>02 &nbsp; Проверьте поля</span><span>→</span><span>03 &nbsp; Сохраните запись</span></div>', unsafe_allow_html=True)
    left, right = st.columns([1.9, 1], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Что произошло на смене?")
            st.caption("Пишите привычными словами — на русском или казахском.")
            if st.button("↗  Вставить демо-пример"):
                st.session_state.raw = EXAMPLE
                st.session_state.pop("review", None)
            raw = st.text_area("Отчёт мастера", key="raw", height=190, placeholder="Какой станок? Что случилось? Как долго стоял?", label_visibility="collapsed")
            st.caption(f"{len(raw)} символов · Без имён, телефонов и других персональных данных")
            if st.button("Показать демо-разбор  →", type="primary", use_container_width=True):
                if not raw.strip():
                    st.error("Сначала добавьте текст смены или вставьте демо-пример.")
                elif raw.strip() != EXAMPLE:
                    st.info("Визуальный прототип разбирает только демо-пример. Ваш текст сохранён в поле; для произвольного текста нужно подключить ИИ.")
                else:
                    st.session_state.review = True
            if st.session_state.get("review"):
                st.divider()
                st.subheader("Проверьте перед сохранением")
                st.caption("ДЕМО · Готовый пример результата ИИ, реальный вызов не выполнялся")
                machine = st.selectbox("Оборудование", ["M3 · Станок №3", "M2 · Станок №2"])
                minutes = st.number_input("Простой, минут", min_value=0, value=90, step=5)
                st.caption('Источник: «простой 1,5 часа» → 90 минут')
                st.warning("Причина не установлена. Уточните у механика — модель не должна её придумывать.")
                cause = st.text_input("Причина", value="Не установлена")
                if st.button("Подтвердить демо-запись", type="primary"):
                    st.session_state.setdefault("saved", []).append({"Запись": f"D-{len(st.session_state.get('saved', []))+1}", "Станок": machine, "Простой, мин": minutes, "Причина": cause, "Тип": "ДЕМО"})
                    st.session_state.review = False
                    st.success("Демо-запись добавлена в историю текущего сеанса.")
    with right:
        st.markdown('<div class="section-label">Из записей — в решения</div>', unsafe_allow_html=True)
        card("Потери станка №3 / месяц", "224 000 ₸", "ДЕМО · 4 поломки ремня", True)
        st.subheader("Каждая деталь на месте")
        st.markdown("**01 · Оборудование**\n\nК какому станку относится запись.\n\n**02 · Простой**\n\nСколько времени потеряла смена.\n\n**03 · Действия**\n\nЧто заменили и что ещё нужно проверить.")
        st.caption("Сомнительные поля подтверждает мастер. Финансовые показатели рассчитывает код.")

elif st.session_state.page == PAGES[1]:
    heading("02 / Сигналы производства", "Заметьте повторение.\nОстановите потери.", "Посмотрите, где поломки повторяются чаще. Каждый сигнал можно проследить до исходных записей смен.")
    cols = st.columns(3)
    for c, args in zip(cols, [("Потери станка №3", "224 000 ₸", "Октябрь · демонстрационные данные", True), ("Аварийный простой", "6,4 ч", "4 остановки за месяц", False), ("Последние интервалы", "12 → 8 → 5", "Дней между поломками · ДЕМО", False)]):
        with c: card(*args)
    st.markdown('<div class="signal"><div class="signal-title">↗ &nbsp; Станок №3 · Разрыв ремня повторяется</div><p>В демонстрационной истории интервалы между остановками сокращаются. Проверьте записи и обсудите возможные меры с механиком.</p></div>', unsafe_allow_html=True)
    a, b = st.columns([1.7, 1])
    with a:
        st.subheader("Длительность остановок")
        st.caption("ДЕМО · часы простоя по четырём инцидентам")
        st.bar_chart({"Дата": ["05 окт", "17 окт", "25 окт", "30 окт"], "Часы": [1.4, 1.8, 1.5, 1.7]}, x="Дата", y="Часы", color="#fa7946")
    with b:
        st.subheader("На чём основан сигнал")
        for ident, date, mins in [(12,"05 октября",84),(24,"17 октября",108),(32,"25 октября",90),(37,"30 октября",102)]:
            with st.expander(f"#{ident} · {date} · {mins} мин"):
                st.write(f"ДЕМО: станок №3, разрыв ремня. Выполнена замена. Простой {mins} минут.")
        if st.button("Посмотреть план экономии →", type="primary"):
            st.session_state.page = PAGES[2]
            st.rerun()

elif st.session_state.page == PAGES[2]:
    heading("03 / План экономии", "Исправляйте то,\nчто меняет результат.", "Сравните меры для станка №3 по вложениям и ожидаемой окупаемости. Эффекты — демонстрационные оценки, а не гарантии.")
    st.info("Меры сравниваются отдельно. Их экономия не суммируется. Перед внедрением решение проверяет механик.")
    measures = [("Запасной ремень на складе",8000,144000,"~2 дня","Сокращение времени ожидания запчасти"),("Замена натяжителя ремня",45000,156800,"~9 дней","Демо-гипотеза: на 70% меньше поломок"),("Плановая замена ремня",0,133200,"Без разовых вложений","Демо-гипотеза: на 80% меньше аварий")]
    for i, (name,cost,savings,payback,note) in enumerate(measures):
        with st.container(border=True):
            main, amount, action = st.columns([2.4,1.4,1.2])
            with main:
                st.caption(f"МЕРА 0{i+1} · ДЕМО")
                st.subheader(name)
                st.caption(note)
            with amount:
                st.metric("Потенциал / месяц", f"{savings:,.0f} ₸".replace(",", " "))
                st.caption(f"Окупаемость: {payback}")
            with action:
                st.caption(f"Вложения: {cost:,} ₸".replace(",", " "))
                applied = st.session_state.get(f"applied_{i}", False)
                if st.button("Отменить отметку" if applied else "Отметить внедрение", key=f"apply_{i}", use_container_width=True):
                    st.session_state[f"applied_{i}"] = not applied
                    st.rerun()
                if applied: st.caption("✓ Демо-отметка сохранена в сеансе")
            with st.expander("Как получена оценка"):
                st.write("Базовые потери: 6,4 ч × 30 000 ₸/ч + 32 000 ₸ запчасти = 224 000 ₸/мес.")
                st.write(["Экономия: 4 × (1,6 − 0,4) ч × 30 000 ₸ = 144 000 ₸/мес. Окупаемость: 8 000 / (144 000 / 30) ≈ 1,7 дня.","Экономия: 224 000 × 70% = 156 800 ₸/мес. Окупаемость: 45 000 / (156 800 / 30) ≈ 8,6 дня.","Экономия: 224 000 × 80% − 46 000 = 133 200 ₸/мес. Регулярные расходы 46 000 ₸/мес уже учтены."][i])
                st.caption("Источники: демо-записи #12, #24, #32, #37. Коэффициенты требуют проверки на реальном производстве.")
else:
    heading("04 / История и эффект", "Знания остаются.\nДаже когда смена уходит.", "Проверяйте исходные записи и наблюдайте результат внедрённых мер.")
    st.subheader("Эффект меры · Станок №2")
    st.caption("ДЕМО · Пример заранее подготовленного сравнения, не результат действий в этом интерфейсе")
    a,b,c = st.columns(3)
    with a: card("Интервал до внедрения", "5 дней", "Демонстрационный период «до»")
    with b: card("Интервал после", "18 дней", "Демонстрационный период «после»", True)
    with c: card("Изменение интервала", "×3,6", "Наблюдение, а не доказательство причинности")
    st.subheader("Журнал записей")
    query = st.text_input("Поиск в истории", placeholder="Номер записи, станок или причина…")
    rows = [{"Запись":"#12","Станок":"M3 · Станок №3","Простой, мин":84,"Причина":"Не установлена","Тип":"ДЕМО"},{"Запись":"#24","Станок":"M3 · Станок №3","Простой, мин":108,"Причина":"Не установлена","Тип":"ДЕМО"}] + st.session_state.get("saved", [])
    filtered = [r for r in rows if query.casefold() in " ".join(map(str,r.values())).casefold()]
    if filtered: st.dataframe(filtered, use_container_width=True, hide_index=True)
    else: st.info("Записей по этому запросу нет. Попробуйте номер станка или очистите поиск.")
st.divider()
st.caption("Смена.AI / Павлодар     ·     Демо-интерфейс     ·     Решения подтверждает человек")
