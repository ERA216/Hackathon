"""Presentation only. Safe to reuse from the production Streamlit entry point."""
from pathlib import Path
import html
import streamlit as st


def apply_theme():
    st.markdown(f"<style>{Path(__file__).with_name('theme.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


BANNERS = ["peach", "lilac", "lime", "sky", "mint"]
ART = ('<svg viewBox="0 0 220 160" class="art"><circle cx="150" cy="70" r="52"/><circle cx="150" cy="70" r="30"/>'
       '<path d="M20 140h180M40 140V96h26v44M80 140V70h26v70M120 140v-30h26v30"/><path d="M178 22l14-8M190 46l16 2"/></svg>')


def heading(kicker, title, description):
    tone = BANNERS[sum(map(ord, kicker)) % len(BANNERS)]
    st.markdown(f'<div class="pagehead {tone}"><div class="ph-text"><div class="eyebrow">{html.escape(kicker)}</div>'
                f'<h1>{html.escape(title)}</h1><p class="intro">{html.escape(description)}</p></div>{ART}</div>', unsafe_allow_html=True)


def card(label, value, note, accent=False, *, tone=None):
    tone = tone or ("danger" if accent else "neutral")
    if tone not in {"neutral", "danger", "warning", "success"}:
        tone = "neutral"
    st.markdown(f'<div class="metric-card metric-{tone}"><div class="metric-label">{html.escape(label)}</div><div class="metric-value">{html.escape(value)}</div><div class="metric-note">{html.escape(note)}</div></div>', unsafe_allow_html=True)


PROBLEMS = [
    {"g": "g1", "icon": '<svg viewBox="0 0 64 64"><rect x="14" y="8" width="36" height="48" rx="5"/><path d="M22 20h20M22 28h20M22 36h12"/><path d="M14 16h-4M14 28h-4M14 40h-4"/></svg>', "problem": "Поломки пишут в тетрадь", "label": "Сотрудник · с телефона",
     "solution": "Сотрудник пишет с телефона своими словами — заявка сразу в базе у администратора."},
    {"g": "g2", "icon": '<svg viewBox="0 0 64 64"><path d="M14 26a18 18 0 0 1 32-8l4 5"/><path d="M50 13v10H40"/><path d="M50 38a18 18 0 0 1-32 8l-4-5"/><path d="M14 51V41h10"/></svg>', "problem": "Ремень рвётся каждую неделю", "label": "ИИ · поиск закономерностей",
     "solution": "Код видит, что интервалы сокращаются 12 → 8 → 5 дней, и поднимает сигнал раньше следующей аварии."},
    {"g": "g3", "icon": '<svg viewBox="0 0 64 64"><circle cx="28" cy="28" r="15"/><path d="M39 39l14 14"/><path d="M22 28h12M28 22v12"/></svg>', "problem": "Непонятно, почему сломалось", "label": "Сотрудник · решает с ИИ",
     "solution": "ИИ предлагает причины из базы типовых и говорит, что проверить. Сотрудник подтверждает и закрывает задачу."},
    {"g": "g4", "icon": '<svg viewBox="0 0 64 64"><ellipse cx="32" cy="18" rx="18" ry="7"/><path d="M14 18v10c0 4 8 7 18 7s18-3 18-7V18"/><path d="M14 28v10c0 4 8 7 18 7s18-3 18-7V28"/><path d="M14 38v8c0 4 8 7 18 7s18-3 18-7v-8"/></svg>', "problem": "Не ясно, во что вложиться", "label": "Администратор · деньги",
     "solution": "Потери в тенге и окупаемость каждой меры: «натяжитель за 45 000 ₸ окупится за 9 дней»."},
    {"g": "g5", "icon": '<svg viewBox="0 0 64 64"><circle cx="14" cy="32" r="6"/><circle cx="50" cy="16" r="6"/><circle cx="50" cy="48" r="6"/><path d="M20 30l24-12M20 34l24 12"/></svg>', "problem": "Проблема теряется по пути", "label": "Администратор · карточки",
     "solution": "Проблема с обхода сразу становится карточкой у администратора. Можно поручить сотрудникам, а не хватит сил — кнопка «Нужна помощь»."},
    {"g": "g6", "icon": '<svg viewBox="0 0 64 64"><path d="M10 52h44"/><rect x="14" y="34" width="8" height="18" rx="2"/><rect x="28" y="24" width="8" height="28" rx="2"/><rect x="42" y="14" width="8" height="38" rx="2"/></svg>', "problem": "Нет картины дня", "label": "Администратор · анализ ИИ",
     "solution": "Сводка ИИ: что загружено сегодня, что повторяется и что проверить в первую очередь. Числа сверены с базой."},
]


def deck(height: int = 470):
    """Анимированный веер карточек-проблем: покачиваются, листаются сами, по клику выбранная выходит вперёд и показывает решение."""
    import json
    from streamlit.components.v1 import html as st_html
    tpl = Path(__file__).with_name("deck.html").read_text(encoding="utf-8")
    safe = [{k: (v if k == "icon" else html.escape(v)) for k, v in c.items()} for c in PROBLEMS]  # иконки — наши SVG
    st_html(tpl.replace("__CARDS__", json.dumps(safe, ensure_ascii=False)), height=height)
