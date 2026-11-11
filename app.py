"""FixList — рабочее приложение: интерфейс из ui_preview.py, подключённый к backend.service."""
import streamlit as st
import html
import time
from urllib.parse import urlencode

from backend import service
from backend.schemas import kzt
from ui.theme import apply_theme, card, deck, heading
from ui.charts import activity_chart, downtime_chart

st.set_page_config(page_title="FixList · обходы, проблемы, решения", page_icon="◆", layout="wide")
apply_theme()


@st.cache_resource
def _boot():
    service.bootstrap()
    return True


_boot()

EXAMPLES = {
    "Станок №3 · ремень (повторяется)": "Линия 2, станок №3 встал в 14:10, порвался ремень. Поставили новый, простой 1,5 часа. Причину пока не установили.",
    "Станок №4 · перегрев": "Станок №4 опять перегрелся к обеду, на радиаторе много пыли. Остановили на 40 минут, продули — запустили.",
    "Два станка в одной записи": "Насос Н-1 потёк, подтянули сальник, простой 25 мин. Станок №5 гудит и вибрирует, подшипник греется — остановили на 1 час, заменили подшипник.",
    "На казахском": "4-станок қызып кетті, 30 минут тұрды. Желдеткішті тазаладық.",
    "Электрика компрессора": "Компрессор: выбило автомат, свет в цеху моргнул. Включили через 20 минут.",
    "Нет данных — проверка вопросов": "Что-то сломалось на линии, разбирались долго.",
    "Станка нет в справочнике": "Станок №9 встал, простой 20 минут.",
}
ROLES = {
    "worker": ["Обход", "Новая запись", "Мои задачи", "Спросить ИИ"],
    "admin": ["Панель задач", "Спросить ИИ", "Состояние оборудования", "Сводка дня", "Аномалии", "План экономии", "История и эффект", "Сотрудники"],
}
ROLE_TITLE = {"worker": "Сотрудник", "admin": "Администратор"}
ROLE_HINT = {
    "worker": "Обходите оборудование и сдавайте отчёт. Найденные проблемы сразу станут карточками у администратора.",
    "admin": "Все данные и изменения в одном месте. ИИ предлагает решения, считает убытки и заранее предупреждает о рисках.",
}
NAV_LABEL = {("worker", "Обход"): "Отчёт об обходе", ("worker", "Новая запись"): "Сообщить о проблеме",
             ("admin", "Аномалии"): "Что ломается чаще", ("admin", "План экономии"): "Деньги и экономия", ("admin", "История и эффект"): "История"}
STEP_OF_PAGE = {"Спросить ИИ": 4, "Обход": 1, "Новая запись": 1, "Мои задачи": 3, "Панель задач": 3, "Состояние оборудования": 4, "Сводка дня": 4,
                "Аномалии": 4, "План экономии": 4, "История и эффект": 2, "Сотрудники": 1}
RISK_CLASS = {"high": "s-esc", "medium": "s-app", "low": "s-res"}
RISK_LABEL = {"high": "Высокий риск", "medium": "Средний риск", "low": "Норма"}
STATUS_CLASS = {"new": "s-new", "approved": "s-app", "escalated": "s-esc", "resolved": "s-res"}
ISSUE_TEXT = {
    "equipment": "Станок не найден в справочнике — выберите вручную.",
    "downtime": "Длительность простоя не подтверждена текстом — укажите вручную.",
    "downtime_unverified": "ИИ назвал длительность, которой нет в тексте, — значение отброшено.",
    "cause_removed": "ИИ предложил причину, которой нет в тексте, — она удалена.",
    "low_confidence": "Низкая уверенность — проверьте поля.",
    "type_from_history": "Симптомы в тексте не описаны — тип поломки подставлен по истории этого станка. Проверьте.",
}
ss = st.session_state
status = service.status()
EQUIPMENT = service.equipment_options()
EQ_LABEL = {e["id"]: f"{e['id']} · {e['name']}" for e in EQUIPMENT}
FAILURES = service.failure_options()


# ---------------- главная и вход ----------------
def brand_link():
    params = st.query_params.to_dict()
    params["_refresh"] = str(time.time_ns())
    url = html.escape("?" + urlencode(params), quote=True)
    return f'<a class="brand brand-link" href="{url}" target="_self" aria-label="FixList — обновить страницу">Fix<span>List</span></a>'


def landing():
    st.markdown('<div class="topbar">' + brand_link() + '<div class="pill">Для заводов, цехов и мастерских</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="hero"><div class="eyebrow">Обходы · проблемы · решения</div>'
                '<h1>Поломки записаны.<br>Решения видны.</h1>'
                '<p class="intro">Сотрудник обходит оборудование и записывает данные. Найденные проблемы становятся карточками, '
                'а ИИ помогает администратору решить их, считает убытки и заранее предупреждает о рисках.</p></div>', unsafe_allow_html=True)
    deck()
    st.markdown('<div class="how"><div><b>01 · ЗАЯВКА</b><p>Компания оставляет заявку и получает кабинет администратора.</p></div>'
                '<div><b>02 · НАСТРОЙКА</b><p>Мы вносим ваше оборудование и стоимость часа простоя — всё под ваш цех.</p></div>'
                '<div><b>03 · КОМАНДА</b><p>Администратор добавляет сотрудников списком. Они сообщают о проблемах с телефона и решают одобренные задачи с ИИ.</p></div></div>',
                unsafe_allow_html=True)
    demo = service.demo_credentials()
    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Войти")
            with st.form("login"):
                code = st.text_input("Код компании", placeholder="например, FIXLIST")
                login_ = st.text_input("Логин", placeholder="например, master")
                pin = st.text_input("PIN-код", type="password", max_chars=8)
                ok = st.form_submit_button("Войти →", type="primary", use_container_width=True)
            if ok:
                user = service.login(code, login_, pin)
                if user:
                    ss.user = user
                    ss.page = ROLES[user["role"]][0]
                    st.rerun()
                st.error("Неверный код компании, логин или PIN.")
            st.markdown("<div class='creds'>" + f"<span>Код <b>{demo['code']}</b></span>" +
                        "".join(f"<span>{u['name'].split()[0]}: <b>{u['login']} / {u['pin']}</b></span>" for u in demo["users"]) + "</div>",
                        unsafe_allow_html=True)
    with right:
        with st.container(border=True):
            st.subheader("Подключить компанию")
            with st.form("register"):
                name = st.text_input("Название компании", placeholder="ТОО «Пример»")
                city = st.text_input("Город", placeholder="Павлодар")
                note = st.text_area("Что производите и сколько оборудования", height=80)
                reg = st.form_submit_button("Оставить заявку", use_container_width=True)
            if reg:
                try:
                    ss.registered = service.register_company(name, city, note)
                except ValueError as e:
                    st.error(str(e))
            if ss.get("registered"):
                r = ss.registered
                st.success(f"Кабинет «{r['company']['name']}» создан. Сохраните данные для входа — PIN показывается один раз.")
                st.markdown(f"Код компании: <span class='cred'>{r['company']['code']}</span> · логин: <span class='cred'>{r['admin']['login']}</span> · "
                            f"PIN: <span class='cred'>{r['admin']['pin']}</span>", unsafe_allow_html=True)
                if st.button("Войти в кабинет администратора →", type="primary"):
                    ss.user = service.login(r["company"]["code"], r["admin"]["login"], r["admin"]["pin"])
                    ss.page = ROLES["admin"][0]
                    ss.pop("registered", None)
                    st.rerun()


if not ss.get("user") and st.query_params.get("s"):  # вход сохраняется после обновления страницы
    restored = service.user_from_token(st.query_params.get("s"))
    if restored:
        ss.user = restored
    else:
        st.query_params.clear()
if not ss.get("user"):
    landing()
    st.stop()
st.query_params["s"] = service.session_token(ss.user["id"])

USER = ss.user
ROLE = "admin" if USER["role"] == "admin" else "worker"
service.use_company(USER["company_id"])
if ss.get("page") not in ROLES[ROLE]:
    ss.page = ROLES[ROLE][0]


def go(page):
    if page in ROLES[ROLE]:
        ss.page = page
        st.rerun()


def flow():
    """Путь данных: видно, на каком шаге находится пользователь."""
    active = STEP_OF_PAGE.get(ss.page, 1)
    steps = ["Сотрудник обходит и записывает", "Данные в базе", "Проблемы — карточками", "Администратор решает с ИИ: убытки, прогноз"]
    html = '<span>→</span>'.join((f"<b>0{i} &nbsp; {t}</b>" if i == active else f"<span>0{i} &nbsp; {t}</span>") for i, t in enumerate(steps, 1))
    st.markdown(f'<div class="steps">{html}</div>', unsafe_allow_html=True)


def logout():
    for k in list(ss.keys()):
        del ss[k]
    st.query_params.clear()
    st.rerun()


@st.dialog("Аккаунт")
def account_dialog():
    initials = "".join(part[0] for part in USER["display_name"].split()[:2]).upper()
    st.markdown(
        '<div class="account-profile account-dialog-profile">'
        f'<div class="account-avatar" aria-hidden="true">{html.escape(initials)}</div>'
        f'<div class="account-info"><strong>{html.escape(USER["display_name"])}</strong>'
        f'<small>{html.escape(ROLE_TITLE[ROLE])}</small></div></div>',
        unsafe_allow_html=True,
    )
    details = [
        ("Логин", USER.get("login", "—")),
        ("Роль", ROLE_TITLE[ROLE]),
        ("Компания", USER["company_name"]),
        ("Код компании", USER["company_code"]),
    ]
    st.markdown(
        '<dl class="account-details">' + "".join(
            f'<div><dt>{label}</dt><dd>{html.escape(value)}</dd></div>'
            for label, value in details
        ) + '</dl>', unsafe_allow_html=True,
    )
    if st.button("Выйти из аккаунта", key="profile-logout", use_container_width=True):
        logout()


# ---------------- боковая панель ----------------
open_account = False
with st.sidebar:
    st.markdown(brand_link() + '<div class="brand-sub">Обходы · проблемы · решения</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="workspace">{html.escape(USER["company_name"])}<small>Код компании: {html.escape(USER["company_code"])}</small></div>', unsafe_allow_html=True)
    for i, page in enumerate(ROLES[ROLE]):
        label = NAV_LABEL.get((ROLE, page), page)
        if st.button(f"0{i+1}   {label}", type="primary" if ss.page == page else "secondary", use_container_width=True, key=f"nav_{page}"):
            go(page)
    st.divider()
    with st.container(border=True, key="sidebar-account"):
        initials = "".join(part[0] for part in USER["display_name"].split()[:2]).upper()
        account_detail = ROLE_TITLE[ROLE] + (f' · {USER["login"]}' if USER.get("login") else "")
        with st.container(key="account-trigger"):
            st.markdown(
                '<div class="account-profile">'
                f'<div class="account-avatar" aria-hidden="true">{html.escape(initials)}</div>'
                '<div class="account-info"><span>Аккаунт</span>'
                f'<strong>{html.escape(USER["display_name"])}</strong>'
                f'<small>{html.escape(account_detail)}</small></div>'
                '<span class="account-chevron" aria-hidden="true">›</span></div>',
                unsafe_allow_html=True,
            )
            open_account = st.button("Открыть аккаунт", key="open-account", use_container_width=True)
        if st.button("Выйти", use_container_width=True):
            logout()

if open_account:
    account_dialog()



# ---------------- 01 · новая запись ----------------
def page_new_record():
    if ROLE == "worker":
        heading("02 / Сообщить о проблеме", "Что случилось?\nНапишите как есть.", "Своими словами, можно с телефона. ИИ разберёт запись, а администратор сразу её увидит.")
    else:
        heading("06 / Запись смены", "Хорошая смена начинается\nс ясной картины.", "Расскажите, что произошло. FixList превратит запись в историю оборудования и сам заметит повторяющиеся поломки.")
    left, right = st.columns([1.9, 1], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Что произошло на смене?")
            st.caption("Пишите привычными словами — на русском или казахском. Можно несколько станков в одной записи.")
            ex_col, btn_col = st.columns([2.2, 1])
            ex_name = ex_col.selectbox("Пример", list(EXAMPLES), label_visibility="collapsed")
            if btn_col.button("Вставить пример", use_container_width=True):
                ss.raw = EXAMPLES[ex_name]
                ss.pop("analysis", None)
            raw = st.text_area("Отчёт мастера", key="raw", height=170, placeholder="Какой станок? Что случилось? Как долго стоял?", label_visibility="collapsed")
            st.caption(f"{len(raw)} / 4000 символов · Без имён, телефонов и других персональных данных")
            if st.button("Разобрать запись  →", type="primary", use_container_width=True):
                try:
                    with st.spinner("ИИ разбирает запись и ищет причину, код проверяет поля…" if status["ai_enabled"] else "Разбираю по правилам…"):
                        ss.analysis = {"raw": raw, **service.analyze_text(raw)}
                        ss.analysis_id = ss.get("analysis_id", 0) + 1
                    ss.pop("saved_signals", None)
                except ValueError as e:
                    st.error(str(e))
                except Exception as e:  # приложение не должно падать
                    st.error(f"Не удалось разобрать запись: {e}. Попробуйте ещё раз.")
            review(raw)
            saved_signals()
    with right:
        if ROLE == "worker":
            st.markdown('<div class="section-label">Как писать</div>', unsafe_allow_html=True)
            st.markdown("**Что** сломалось — номер станка.\n\n**Что** случилось — шум, перегрев, порвался ремень.\n\n**Сколько** стояли — «40 минут», «полтора часа».")
            st.caption("Заявку сразу увидит администратор. После одобрения её можно будет решить с анализом ИИ.")
            mine = service.tickets(author=USER["display_name"], limit=3)
            if mine:
                st.markdown('<div class="section-label">Ваши последние заявки</div>', unsafe_allow_html=True)
                for t in mine:
                    st.caption(f"{t['status_label']} · {t['equipment_name']} · {t['time'][5:]}")
            return
        st.markdown('<div class="section-label">Из записей — в решения</div>', unsafe_allow_html=True)
        dash = service.dashboard()
        top_sig = dash["anomalies"][0] if dash["anomalies"] else None
        if top_sig:
            card(f"Потери · {top_sig['equipment_name']} / {dash['window_days']} дн.", f"{kzt(top_sig['loss_kzt'])} ₸", top_sig["failure_label"], True)
        card("Все потери от простоев", f"{kzt(dash['total_loss_kzt'])} ₸", f"За {dash['window_days']} дней · {dash['total_downtime_h']} ч простоя")
        st.subheader("Как это работает")
        st.markdown("**01 · ИИ извлекает поля**\n\nСтанок, поломка, простой, запчасти.\n\n**02 · Код проверяет**\n\nСтанок — из справочника, минуты — из текста, причины не выдумываются.\n\n**03 · Код считает**\n\nПотери, интервалы, окупаемость мер.")


def review(raw):
    a = ss.get("analysis")
    if not a:
        return
    st.divider()
    if a["mode"] == "ai":
        st.caption("Поля извлечены ИИ и проверены кодом. Подтвердите перед сохранением.")
    else:
        st.caption("Резервный режим: поля найдены по правилам, без ИИ. Проверьте их внимательно.")
    if a.get("warning"):
        st.warning(a["warning"])
    if a["raw"] != raw:
        st.info("Текст изменён после разбора — нажмите «Разобрать запись» ещё раз.")
    for q in a["questions"]:
        st.info("" + q)
    if not a["drafts"]:
        return
    st.subheader("Проверьте перед сохранением")
    eq_ids = [e["id"] for e in EQUIPMENT]
    edited = []
    for i, d in enumerate(a["drafts"]):
        with st.container(border=True):
            st.caption(f"ИНЦИДЕНТ {i+1} · уверенность {d['confidence']:.0%}")
            for issue in d["issues"]:
                if issue in ISSUE_TEXT:
                    st.warning(ISSUE_TEXT[issue])
            c1, c2, c3 = st.columns([1.4, 1.4, 1])
            eq_opts = [None] + eq_ids
            eq = c1.selectbox("Оборудование", eq_opts, index=eq_opts.index(d["equipment_id"]) if d["equipment_id"] in eq_ids else 0,
                              format_func=lambda x: "— выберите станок —" if x is None else EQ_LABEL[x], key=f"eq_{i}")
            ft_keys = list(FAILURES)
            ft = c2.selectbox("Тип неисправности", ft_keys, index=ft_keys.index(d["failure_type"]), format_func=FAILURES.get, key=f"ft_{i}")
            mins = c3.number_input("Простой, мин", min_value=0, max_value=1440, value=int(d["downtime_min"] or 0), step=5, key=f"dt_{i}")
            if d.get("downtime_quote"):
                st.caption(f"Источник: «{d['downtime_quote']}» → {d['downtime_min']} мин")
            c4, c5 = st.columns(2)
            ckey = f"cause_{ss.get('analysis_id', 0)}_{i}"
            ss.setdefault(ckey, d.get("cause") or "")
            cause = c4.text_input("Причина (подтверждает мастер)", placeholder="Не установлена — выберите гипотезу ниже или впишите", key=ckey)
            action = c5.text_input("Что сделали", value=d.get("action") or "", key=f"act_{i}")
            parts = ", ".join(f"{p['name']} × {p['qty']}" for p in d.get("parts", [])) or "нет"
            st.caption(f"Запчасти: {parts} · стоимость по справочнику: {kzt(d['parts_cost_kzt'])} ₸")
            render_hypotheses(d, ckey)
            edited.append({**d, "equipment_id": eq, "failure_type": ft, "downtime_min": None if ("downtime" in d["issues"] and mins == 0) else mins,
                           "cause": cause or None, "action": action or None})
    if st.button("Подтвердить и сохранить", type="primary"):
        try:
            res = service.save_record(a["raw"], edited, a["mode"], author=USER["display_name"])
            ss.saved_signals = res["signals"]
            ss.pop("analysis", None)
            ss.pop("plans", None)
            st.success(f"Запись #{res['log_id']} сохранена: инцидентов — {len(res['incident_ids'])}. Заявка ушла администратору на одобрение.")
        except ValueError as e:
            st.error(str(e))


def _accept(key, text):
    ss[key] = text


def render_hypotheses(d, ckey):
    hyps = d.get("hypotheses") or []
    if not hyps:
        return
    h = d.get("history") or {}
    src = "Гипотезы ИИ" if d.get("diagnosis_mode") == "ai" else "Гипотезы по правилам"
    hist_note = f" · похожих поломок за {h.get('window_days', 30)} дн.: {h.get('failures_in_window', 0)}" if h else ""
    if h.get("intervals_days"):
        hist_note += " · интервалы: " + " → ".join(map(str, h["intervals_days"])) + " дн."
    with st.expander(f"Возможные причины — {src}{hist_note}", expanded=True):
        st.caption("Причины выбраны только из базы типовых причин. Это гипотезы: проверьте их перед тем, как принять.")
        for j, hy in enumerate(hyps):
            a, b = st.columns([4, 1.3])
            with a:
                st.markdown(f"**{j+1}. {hy['title']}** · вероятность {hy['confidence']:.0%}")
                st.caption(hy["why"])
                st.markdown("Проверить: " + "; ".join(hy["checks"]))
            b.button("Принять", key=f"acc_{ckey}_{j}", on_click=_accept, args=(ckey, hy["title"]), use_container_width=True)


def saved_signals():
    """Сигналы после сохранения: рисуются при каждой перерисовке, чтобы кнопка перехода работала."""
    if ROLE != "admin":
        if ss.get("saved_signals") is not None:
            st.caption("Заявка ушла администратору на одобрение. Статус — в разделе «Мои задачи».")
        return
    for s in ss.get("saved_signals", []):
        st.markdown(f'<div class="signal"><div class="signal-title">↗ &nbsp; {s["title"]}</div><p>{s["message"]}</p></div>', unsafe_allow_html=True)
        if st.button("Посмотреть план экономии →", key=f"go_{s['equipment_id']}_{s['failure_type']}", type="primary"):
            ss.plan_target = (s["equipment_id"], s["failure_type"])
            go("План экономии")


# ---------------- руководитель: сводка дня ----------------
def page_brief():
    heading("01 / Сводка дня", "Что произошло сегодня\nи что проверить.", "Код собирает факты из базы: новые записи, повторяющиеся поломки, потери. ИИ объясняет главное простыми словами. Числа в тексте ИИ сверяются с базой.")
    sm = service.today_summary()
    with st.container(key="brief-metrics"):
        c = st.columns(4)
        with c[0]:
            card("Загружено сегодня", str(sm["logs_today"]), f"Инцидентов: {sm['incidents_today']}", True)
        with c[1]:
            card("Простой сегодня", f"{sm['downtime_h_today']} ч".replace(".", ","), "По записям мастеров")
        with c[2]:
            card("Потери сегодня", f"{kzt(sm['loss_today_kzt'])} ₸", "Простой × стоимость часа + запчасти")
        with c[3]:
            card("В базе записей", str(sm["total_logs"]), f"История с {sm['history_since']}")

    key = (sm["total_logs"], service.status()["ai_enabled"])
    if ss.get("brief_key") != key or st.button("Обновить сводку"):
        with st.spinner("ИИ готовит сводку по данным из базы…"):
            ss.brief, ss.brief_key = service.daily_brief(), key
    b = ss.brief
    with st.container(border=True, key="daily-brief"):
        st.caption("СВОДКА ИИ · числа сверены с базой" if b["mode"] == "ai" else "СВОДКА ПО ШАБЛОНУ · без ИИ")
        st.subheader(b["headline"])
        for pt in b["points"]:
            st.markdown(f"- {pt}")
        if b.get("warning"):
            st.caption("" + b["warning"])
        if b["priorities"]:
            st.markdown("**Приоритеты на сегодня**")
            for i, pr in enumerate(b["priorities"], 1):
                st.markdown(f"{i}. **{pr['equipment']}** — {pr['action']}")

    st.subheader("Загружено сегодня")
    if sm["records"]:
        record_columns = st.columns(2, gap="large")
        for i, r in enumerate(sm["records"]):
            with record_columns[i % 2]:
                with st.expander(f"{r['time']} · {r['equipment_name']} · {r['downtime_min']} мин"):
                    st.write(r["raw_text"])
                    st.caption(f"{r['failure_label']} · причина: {r['cause'] or 'не установлена'} · потери ≈ {kzt(r['loss_kzt'])} ₸")
    else:
        st.info("Сегодня мастера ещё ничего не записали.")

    if sm["per_day"]:
        activity_chart(sm["per_day"])

    cnt = service.ticket_counts()
    st.markdown(f"**Заявки:** нужна помощь — {cnt['escalated']} · ждут одобрения — {cnt['new']} · решают сотрудники — {cnt['approved']} · решено сегодня — {cnt['resolved_today']}")
    if (cnt["new"] or cnt["escalated"]) and st.button("Открыть панель задач →"):
        go("Панель задач")
    st.subheader("Что проверить сегодня")
    st.caption("По каждой повторяющейся поломке — самая вероятная причина из базы типовых причин и конкретные проверки. Отметки живут в текущем сеансе.")
    render_checklist()


# ---------------- общие элементы заявок ----------------
def ticket_card(t):
    """Компактная пастельная карточка заявки."""
    import html as _h
    text = _h.escape(t["raw_text"].replace("ДЕМО · ", ""))
    st.markdown(f'''<div class="tcard {STATUS_CLASS[t["status"]]}"><div class="tc-top">{_h.escape(t["status_label"])}</div>
        <div class="tc-title">{_h.escape(t["equipment_name"])}</div><div class="tc-text">{text}</div>
        <div class="tc-foot">{_h.escape(t["failure_label"])} · {t["downtime_min"]} мин · {t["time"][5:]}</div></div>''', unsafe_allow_html=True)


def ai_hints(t):
    hyps = service.ticket_hypotheses(t)
    st.markdown("**Анализ ИИ: вероятные причины и что проверить**")
    for h in hyps:
        st.markdown(f"- **{h['title']}** · {h['confidence']:.0%} — " + "; ".join(h["checks"]))
    return hyps


def resolve_form(t, hyps, who, key):
    opts = ["Не установлена"] + [h["title"] for h in hyps] + ["Другая"]
    cause = st.selectbox("Причина", opts, key=f"cause_sel_{key}")
    if cause == "Другая":
        cause = st.text_input("Своя причина", key=f"cause_own_{key}")
    done = st.text_input("Что сделали", placeholder="Например: заменили ремень, отрегулировали натяжитель", key=f"done_{key}")
    if st.button("Решено", key=f"close_{key}", type="primary"):
        try:
            service.resolve_ticket(t["id"], who, done, None if cause == "Не установлена" else cause)
            ss.pop("sel_ticket", None)
            st.rerun()
        except ValueError as e:
            st.error(str(e))


# ---------------- сотрудник: мои задачи ----------------
def page_tasks():
    heading("03 / Мои задачи", "Поручено — значит,\nможно решать.", "Задачи, которые одобрил администратор. ИИ подсказывает причины и проверки. Если сил не хватает — одна кнопка, и задача уходит администратору.")
    tasks = service.tickets(["approved"])
    st.subheader(f"Задачи к решению · {len(tasks)}")
    if not tasks:
        st.info("Одобренных задач нет. Новые появятся, когда администратор одобрит заявку.")
    for t in tasks:
        with st.container(border=True):
            a, b = st.columns([1, 1.6], gap="large")
            with a:
                ticket_card(t)
            with b:
                hyps = ai_hints(t)
                tab1, tab2 = st.tabs(["Решили сами", "Нужна помощь администратора"])
                with tab1:
                    resolve_form(t, hyps, USER["display_name"], f"w{t['id']}")
                with tab2:
                    note = st.text_area("Чего не хватает?", placeholder="Например: нужна запчасть, которой нет на складе; нужен электрик; нужна закупка",
                                        height=80, key=f"esc_{t['id']}")
                    if st.button("Передать администратору", key=f"escb_{t['id']}"):
                        try:
                            service.escalate_ticket(t["id"], USER["display_name"], note)
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
    st.subheader("Мои заявки")
    mine = service.tickets(author=USER["display_name"], limit=12)
    if not mine:
        st.caption("Вы ещё не отправляли заявок.")
    cols = st.columns(3)
    for i, t in enumerate(mine):
        with cols[i % 3]:
            ticket_card(t)
            if t["status"] == "resolved":
                st.caption(f"Сделано: {t['resolution'] or '—'}")
    if st.button("Сообщить о новой проблеме", type="primary", use_container_width=True):
        go("Новая запись")


# ---------------- сотрудник: отчёт об обходе ----------------
STATE_OPTS = {"Норма": "ok", "Замечание": "remark", "Проблема": "problem"}


def page_inspection():
    import pandas as pd
    heading("01 / Отчёт об обходе", "Проверили — записали.\nВсё уходит в базу.", "Отметьте состояние каждого аппарата. Замечания и проблемы сразу станут карточками у администратора, а ИИ подскажет, как их решить.")
    done = service.inspections_today()
    if done:
        st.caption(f"Сегодня уже записано проверок: {len(done)}. Можно сдать ещё один отчёт — он добавится к истории.")
    df = pd.DataFrame([{"Оборудование": e["name"], "Линия": e["line"], "Состояние": "Норма", "Комментарий": "", "Простой, мин": 0} for e in EQUIPMENT])
    edited = st.data_editor(df, hide_index=True, use_container_width=True, key="insp_editor", disabled=["Оборудование", "Линия"],
                            column_config={"Состояние": st.column_config.SelectboxColumn("Состояние", options=list(STATE_OPTS), required=True),
                                           "Комментарий": st.column_config.TextColumn("Комментарий", help="Обязателен для замечания и проблемы"),
                                           "Простой, мин": st.column_config.NumberColumn("Простой, мин", min_value=0, max_value=1440, step=5)})
    rows = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
    issues = sum(r["Состояние"] != "Норма" for r in rows)
    st.caption(f"Проверено аппаратов: {len(rows)} · с замечаниями и проблемами: {issues}")
    if st.button("Сдать отчёт об обходе", type="primary", use_container_width=True):
        name_to_id = {e["name"]: e["id"] for e in EQUIPMENT}
        try:
            res = service.save_inspection([{"equipment_id": name_to_id[r["Оборудование"]], "state": STATE_OPTS[r["Состояние"]], "note": r["Комментарий"] or "",
                                            "downtime_min": int(r["Простой, мин"] or 0)} for r in rows], USER["display_name"])
            st.success(f"Отчёт сохранён: проверено {res['checked']}, новых карточек для администратора — {len(res['cards'])}.")
        except ValueError as e:
            st.error(str(e))


# ---------------- администратор: состояние оборудования ----------------
def page_health():
    heading("02 / Состояние оборудования", "Что может сломаться —\nвидно заранее.", "Для каждого аппарата код считает поломки, убытки и ритм отказов и прогнозирует следующую поломку. Это предупреждение по истории, а не гарантия.")
    hs = service.equipment_health()
    c = st.columns(3)
    with c[0]:
        card("Высокий риск", str(sum(h["risk"] == "high" for h in hs)), "аппаратов требуют внимания", True)
    with c[1]:
        card("Убытки за 30 дней", f"{kzt(sum(h['loss_kzt'] for h in hs))} ₸", "простой × стоимость часа + запчасти")
    with c[2]:
        card("Среднее здоровье парка", f"{round(sum(h['health'] for h in hs) / max(1, len(hs)))}%", f"аппаратов: {len(hs)}")
    cols = st.columns(3)
    for i, h in enumerate(hs):
        with cols[i % 3]:
            nxt = ("срок уже наступил" if h["days_to_next"] is not None and h["days_to_next"] <= 0 else
                   f"следующая поломка через ~{h['days_to_next']} дн." if h["days_to_next"] is not None else "прогноза нет — мало данных")
            st.markdown(f'''<div class="tcard {RISK_CLASS[h["risk"]]}"><div class="tc-top">{RISK_LABEL[h["risk"]]} · здоровье {h["health"]}%</div>
                <div class="tc-title">{h["equipment_name"]}</div><div class="tc-text">{nxt}. Убыток за 30 дн.: {kzt(h["loss_kzt"])} ₸</div>
                <div class="tc-foot">{h["line"]} · поломок за 30 дн.: {h["failures"]}</div></div>''', unsafe_allow_html=True)
            with st.expander("Почему так"):
                st.write(h["explanation"])
                if h["risk"] != "low" and h["failures"]:
                    if st.button("Что сделать и сколько стоит", key=f"hplan_{h['equipment_id']}", use_container_width=True):
                        types = [t["failure_type"] for t in service.tickets(limit=999) if t["equipment_id"] == h["equipment_id"]]
                        ss.plan_target = (h["equipment_id"], max(set(types), key=types.count) if types else "other")
                        go("План экономии")


def _live(fn):
    """Блок, который сам обновляется каждые 15 секунд (если версия Streamlit это умеет)."""
    frag = getattr(st, "fragment", None)
    return frag(run_every=15)(fn) if frag else fn


@_live
def ai_warnings():
    import html as _h
    from datetime import datetime
    risky = [h for h in service.equipment_health() if h["risk"] != "low"][:3]
    if not risky:
        return
    rows = ""
    for h in risky:
        nxt = (f"следующая поломка возможна через ~{h['days_to_next']} дн." if h["days_to_next"] is not None and h["days_to_next"] > 0
               else "срок поломки уже наступил" if h["days_to_next"] is not None else "мало данных для прогноза")
        rows += (f'<div class="warn-row"><span class="dot {h["risk"]}"></span><b>{_h.escape(h["equipment_name"])}</b>'
                 f'<span class="warn-meta">здоровье {h["health"]}% · убыток {kzt(h["loss_kzt"])} ₸ за 30 дн. · {nxt}</span></div>')
    cnt = service.ticket_counts()
    st.markdown(f'<div class="warn"><div class="warn-head"><span class="warn-tag">ИИ предупреждает</span>'
                f'<span class="warn-sub">обновлено в {datetime.now():%H:%M:%S} · пересчёт каждые 15 сек · открытых заявок: {cnt["new"] + cnt["approved"] + cnt["escalated"]}</span></div>{rows}</div>',
                unsafe_allow_html=True)
    if st.button("Состояние всего оборудования →", key="to_health"):
        go("Состояние оборудования")


# ---------------- спросить ИИ ----------------
EXAMPLE_QUESTIONS = ["Что сломается первым?", "Какой станок обходится дороже всего?", "Что сделать сегодня в первую очередь?", "Что со станком №3?"]


def page_ask():
    heading("Спросить ИИ", "Спросите — ИИ ответит\nпо данным вашего цеха.", "ИИ видит состояние оборудования, убытки, прогнозы и открытые заявки. Отвечает только по базе: числа в ответе сверяются с данными.")
    chat = ss.setdefault("chat", [])
    cols = st.columns(len(EXAMPLE_QUESTIONS))
    picked = None
    for c, q in zip(cols, EXAMPLE_QUESTIONS):
        if c.button(q, use_container_width=True, key=f"exq_{q}"):
            picked = q
    for m in chat:
        with st.chat_message("user"):
            st.write(m["q"])
        with st.chat_message("assistant"):
            st.write(m["a"])
            st.caption(("Анализ ИИ" if m["mode"] == "ai" else "Ответ собран кодом из базы") + (" · опирается на: " + ", ".join(m["based_on"]) if m["based_on"] else ""))
    st.markdown('<div class="ask-label">Спросить ИИ ↓ напишите вопрос в поле внизу экрана</div>', unsafe_allow_html=True)
    q = st.chat_input("Спросите ИИ: почему станок №4 перегревается и что делать?") or picked
    if q:
        with st.spinner("ИИ анализирует базу…"):
            try:
                r = service.ask_ai(q, chat)
                chat.append({"q": q, "a": r["answer"], "mode": r["mode"], "based_on": r["based_on"]})
                if r.get("warning"):
                    st.warning(r["warning"])
            except ValueError as e:
                st.error(str(e))
        st.rerun()


# ---------------- администратор: панель задач ----------------
def page_board():
    heading("01 / Панель задач", "Выберите, что решить\nв первую очередь.", "Каждая карточка — проблема, найденная сотрудником на обходе или в работе. Откройте её: ИИ предложит причину и решение, посчитает убытки. Можно поручить сотрудникам или решить самому.")
    ai_warnings()
    cnt = service.ticket_counts()
    tabs = [("escalated", f"Нужна помощь · {cnt['escalated']}"), ("new", f"Ждут одобрения · {cnt['new']}"),
            ("approved", f"Решают сотрудники · {cnt['approved']}"), ("resolved", "Решённые")]
    default = 0 if cnt["escalated"] else 1
    labels = [l for _, l in tabs]
    choice = st.radio("Фильтр", labels, index=labels.index(ss.get("board_tab", labels[default])) if ss.get("board_tab") in labels else default,
                      horizontal=True, label_visibility="collapsed")
    ss.board_tab = choice
    status = tabs[labels.index(choice)][0]
    items = service.tickets([status], limit=30 if status == "resolved" else 100)
    sel = next((t for t in service.tickets(limit=999) if t["id"] == ss.get("sel_ticket")), None)
    if sel:
        ticket_detail(sel)
    if not items:
        st.info("Здесь пусто — всё решено.")
    cols = st.columns(3)
    for i, t in enumerate(items):
        with cols[i % 3]:
            ticket_card(t)
            if st.button("Открыть" if not sel or sel["id"] != t["id"] else "Открыта", key=f"open_{t['id']}", use_container_width=True):
                ss.sel_ticket = t["id"]
                st.rerun()


def ticket_detail(t):
    with st.container(border=True):
        top, close = st.columns([6, 1])
        top.markdown(f"### {t['equipment_name']} · {t['failure_label']}")
        if close.button("Свернуть", key="close_detail"):
            ss.pop("sel_ticket", None)
            st.rerun()
        st.caption(f"{t['status_label']} · {t['time']} · сообщил: {t['author'] or 'сотрудник'} · простой {t['downtime_min']} мин")
        st.write(t["raw_text"])
        if t["status"] == "escalated":
            st.warning(f"{t['assignee'] or 'Сотрудник'}: {t['escalation_note']}")
        if t["status"] == "resolved":
            st.success(f"Решено: {t['resolution'] or '—'}" + (f" · причина: {t['cause']}" if t["cause"] else "") + (f" · {t['assignee']}" if t["assignee"] else ""))
            return
        a, b = st.columns([1.3, 1], gap="large")
        with a:
            hyps = ai_hints(t)
        with b:
            if t["status"] == "new":
                if st.button("Поручить сотрудникам — решают с анализом ИИ", type="primary", use_container_width=True, key="approve"):
                    service.approve_ticket(t["id"], USER["display_name"])
                    ss.pop("sel_ticket", None)
                    st.rerun()
            if st.button("Деньги и экономия по этой поломке", use_container_width=True, key="to_plan"):
                ss.plan_target = (t["equipment_id"], t["failure_type"])
                go("План экономии")
            if hyps:
                top = hyps[0]
                st.markdown(f"**Решение ИИ:** {top['title'].lower()} — {top['checks'][0].lower()}.")
                if st.button("Применить решение ИИ", type="primary", use_container_width=True, key=f"ai_fix_{t['id']}"):
                    service.resolve_ticket(t["id"], USER["display_name"], "По анализу ИИ: " + "; ".join(top["checks"]), top["title"])
                    ss.pop("sel_ticket", None)
                    st.rerun()


def render_checklist():
    items = service.checklist()
    if not items:
        st.info("Повторяющихся поломок нет — проверять нечего.")
    for i, it in enumerate(items):
        with st.container(border=True):
            a, b = st.columns([3, 1])
            with a:
                st.markdown(f"<span class='dot {'high' if it['severity'] == 'high' else 'medium'}'></span>**{it['equipment_name']}** · вероятная причина: **{it['cause']}**", unsafe_allow_html=True)
                st.caption(f"{it['why']} · потери за период ≈ {kzt(it['loss_kzt'])} ₸")
                for j, chk in enumerate(it["checks"]):
                    st.checkbox(chk, key=f"chk_{it['equipment_id']}_{it['failure_type']}_{j}")
            with b:
                if ROLE == "admin" and st.button("План экономии →", key=f"plan_{i}", use_container_width=True):
                    ss.plan_target = (it["equipment_id"], it["failure_type"])
                    go("План экономии")


# ---------------- руководитель: сотрудники ----------------
def page_staff():
    heading("05 / Сотрудники", "Добавьте людей —\nи они начнут писать.", "Вставьте список сотрудников, по одному на строку. Каждый получит логин и PIN для входа с телефона. Персональные данные не обязательны: можно «Мастер смены 2».")
    st.markdown(f"Сотрудники входят на главной странице: код компании <span class='cred'>{USER['company_code']}</span>, свой логин и PIN.", unsafe_allow_html=True)
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        with st.container(border=True):
            st.subheader("Добавить списком")
            with st.form("add_staff"):
                names = st.text_area("Сотрудники", placeholder="Мастер смены 2\nМеханик\nОператор пресса", height=140)
                role = st.selectbox("Роль", ["worker", "admin"], format_func={"worker": "Сотрудник — сообщает и решает задачи", "admin": "Администратор — одобряет, деньги, анализ"}.get)
                add = st.form_submit_button("Добавить и выдать доступы", type="primary", use_container_width=True)
            if add:
                try:
                    ss.new_staff = service.add_workers(USER["company_id"], names, role)
                except ValueError as e:
                    st.error(str(e))
            if ss.get("new_staff"):
                st.success("Доступы созданы. Передайте их сотрудникам — PIN показывается только сейчас.")
                st.dataframe([{"Сотрудник": u["display_name"], "Логин": u["login"], "PIN": u["pin"]} for u in ss.new_staff], use_container_width=True, hide_index=True)
    with right:
        st.subheader("Кто уже в системе")
        users = service.list_users(USER["company_id"])
        st.dataframe([{"Сотрудник": u["display_name"], "Логин": u["login"], "Роль": ROLE_TITLE[u["role"]], "Добавлен": u["created_at"][:10]} for u in users],
                     use_container_width=True, hide_index=True)


# ---------------- 02 · аномалии ----------------
def page_anomalies():
    heading("02 / Сигналы производства", "Заметьте повторение.\nОстановите потери.", "Код проверяет всю историю: повторяемость, сокращение интервалов, всплески простоя. Каждый сигнал — со ссылками на записи смен.")
    dash = service.dashboard()
    sigs = dash["anomalies"]
    c = st.columns(3)
    with c[0]:
        card("Потери от простоев", f"{kzt(dash['total_loss_kzt'])} ₸", f"За {dash['window_days']} дней · все станки", True)
    with c[1]:
        card("Аварийный простой", f"{dash['total_downtime_h']} ч".replace(".", ","), f"За {dash['window_days']} дней")
    with c[2]:
        card("Сигналов", str(len(sigs)), "Требуют внимания")
    if not sigs:
        st.info("Повторяющихся поломок за период нет.")
        return
    labels = [f"{'Высокий' if s['severity'] == 'high' else 'Средний'} · {s['title']} · {kzt(s['loss_kzt'])} ₸" for s in sigs]
    idx = st.radio("Сигналы", range(len(sigs)), format_func=lambda i: labels[i], label_visibility="collapsed")
    s = sigs[idx]
    st.markdown(f'<div class="signal"><div class="signal-title">↗ &nbsp; {s["title"]}</div><p>{s["message"]}</p></div>', unsafe_allow_html=True)
    st.caption(f"Расчёт потерь: {s['formula']}")
    ev = service.incident_evidence(s["incident_ids"])
    downtime_chart(ev)
    st.subheader("На чём основан сигнал")
    for e in ev:
        with st.expander(f"#{e['id']} · {e['date']} · {e['downtime_min']} мин"):
            st.write(e["raw_text"])
    if st.button("Посмотреть план экономии →", type="primary"):
        ss.plan_target = (s["equipment_id"], s["failure_type"])
        go("План экономии")
    with st.expander("Потери по всем станкам"):
        st.dataframe([{"Станок": r["equipment_name"], "Поломок": r["failures"], "Простой, ч": r["downtime_h"], "Потери, ₸": r["loss_kzt"]} for r in dash["by_equipment"]],
                     use_container_width=True, hide_index=True)


# ---------------- 03 · план экономии ----------------
def page_plan():
    heading("03 / План экономии", "Исправляйте то,\nчто меняет результат.", "Меры — из базы, деньги — по формулам, выбор и объяснение — от ИИ. Эффект мер — оценка, а не гарантия.")
    dash = service.dashboard()
    targets = [(s["equipment_id"], s["failure_type"]) for s in dash["anomalies"]]
    names = {(s["equipment_id"], s["failure_type"]): s["title"] for s in dash["anomalies"]}
    cur = ss.get("plan_target")
    if cur and cur not in targets:
        targets.insert(0, cur)
    if not targets:
        st.info("Нет сигналов для расчёта. Выберите станок и тип поломки вручную.")
        c1, c2 = st.columns(2)
        eq = c1.selectbox("Станок", list(EQ_LABEL), format_func=EQ_LABEL.get)
        ft = c2.selectbox("Тип", list(FAILURES), format_func=FAILURES.get)
        targets = [(eq, ft)]
    t = st.selectbox("Для какой проблемы", targets, index=targets.index(cur) if cur in targets else 0,
                     format_func=lambda x: names.get(x, f"{EQ_LABEL[x[0]]} · {FAILURES[x[1]]}"))
    ss.plan_target = t
    plans = ss.setdefault("plans", {})
    if t not in plans or st.button("Пересчитать"):
        with st.spinner("Подбираю меры из базы и считаю окупаемость…"):
            try:
                plans[t] = service.savings_plan(*t)
            except Exception as e:
                st.error(f"Не удалось построить план: {e}")
                return
    p = plans[t]
    st_ = p["stats"]
    if not p["measures"]:
        st.info(p["summary"])
        return
    with st.container(border=True):
        st.caption("СФОРМИРОВАНО ИИ · числа проверены кодом" if p["ai_generated"] else "ШАБЛОН БЕЗ ИИ · расчёт кодом")
        st.write(p["summary"])
        if p.get("warning"):
            st.caption("" + p["warning"])
        st.caption(f"Базовые потери: {st_['formula']} · источники: " + ", ".join(f"#{i}" for i in st_["incident_ids"]))
    applied = service.applied_set()
    for i, m in enumerate(p["measures"]):
        with st.container(border=True):
            main, amount, action = st.columns([2.4, 1.4, 1.2])
            with main:
                st.caption(f"МЕРА 0{i+1} · {m['measure_id']}")
                st.subheader(m["title"])
                st.caption(m["note"])
                if m.get("why"):
                    st.write("ИИ: " + m["why"])
            with amount:
                st.metric("Потенциал / месяц", f"{kzt(m['savings_kzt'])} ₸")
                st.caption(f"Окупаемость: {m['payback_label']}")
            with action:
                st.caption(f"Вложения: {kzt(m['one_time_kzt'])} ₸" + (f" + {kzt(m['monthly_kzt'])} ₸/мес" if m["monthly_kzt"] else ""))
                key = (t[0], m["measure_id"])
                is_on = key in applied
                if st.button("Отменить отметку" if is_on else "Отметить внедрение", key=f"apply_{m['measure_id']}", use_container_width=True):
                    (service.unmark_applied if is_on else service.mark_applied)(*key)
                    st.rerun()
                if is_on:
                    st.caption("Внедрено · эффект появится в «Истории»")
            with st.expander("Как получена оценка"):
                st.write("Экономия: " + m["formula"])
                if m.get("payback_formula"):
                    st.write("Окупаемость: " + m["payback_formula"])


# ---------------- 04 · история и эффект ----------------
def page_history():
    heading("04 / История и эффект", "Знания остаются.\nДаже когда смена уходит.", "Исходные записи смен и результат внедрённых мер по реальным данным «до/после».")
    effs = service.effects()
    if effs:
        for e in effs:
            st.subheader(f"Эффект меры · {e['equipment_name']} · {e['measure_title']}")
            a, b, c = st.columns(3)
            before = f"{e['avg_interval_before']:g} дн." if e.get("avg_interval_before") else "—"
            after = f"{e['avg_interval_after']:g} дн." if e.get("avg_interval_after") else "ещё нет поломок"
            with a:
                card("Интервал до внедрения", before, f"Поломок до: {e['failures_before']}")
            with b:
                card("Интервал после", after, f"Внедрено {e['applied_at']} · {e['days_since']} дн. назад", True)
            with c:
                verdict = {"improved": f"×{e.get('ratio', 0):g}".replace(".", ","), "no_improvement": "без улучшения",
                           "waiting": "ждём данных", "no_failures_since": "поломок нет"}.get(e["status"], "—")
                card("Изменение", verdict, "Наблюдение, а не доказательство причинности")
    else:
        st.info("Внедрённых мер пока нет. Отметьте меру на экране «План экономии».")
    st.subheader("Журнал записей")
    query = st.text_input("Поиск в истории", placeholder="Номер записи, станок или причина…")
    rows = service.history(query)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("Записей по этому запросу нет. Попробуйте номер станка или очистите поиск.")


{"Спросить ИИ": page_ask, "Обход": page_inspection, "Новая запись": page_new_record, "Мои задачи": page_tasks, "Панель задач": page_board, "Состояние оборудования": page_health,
 "Сводка дня": page_brief, "Аномалии": page_anomalies, "План экономии": page_plan, "История и эффект": page_history,
 "Сотрудники": page_staff}[ss.page]()

