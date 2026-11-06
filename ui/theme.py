"""Presentation only. Safe to reuse from the production Streamlit entry point."""
from pathlib import Path
import html
import streamlit as st


def apply_theme():
    st.markdown(f"<style>{Path(__file__).with_name('theme.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def heading(kicker, title, description):
    st.markdown(f'<div class="eyebrow">{html.escape(kicker)}</div><h1>{html.escape(title)}</h1><p class="intro">{html.escape(description)}</p>', unsafe_allow_html=True)


def card(label, value, note, accent=False):
    st.markdown(f'<div class="metric-card {"accent" if accent else ""}"><div class="metric-label">{html.escape(label)}</div><div class="metric-value">{html.escape(value)}</div><div class="metric-note">{html.escape(note)}</div></div>', unsafe_allow_html=True)
