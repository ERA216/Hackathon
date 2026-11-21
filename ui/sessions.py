"""Keep access to a workspace in its connected Streamlit session."""
import time

import streamlit as st


SESSION_SECONDS = 8 * 60 * 60


def start(user):
    st.session_state.clear()
    st.session_state["user"] = user
    st.session_state["session-expires-at"] = time.time() + SESSION_SECONDS


def current_user():
    if time.time() < st.session_state.get("session-expires-at", 0):
        return st.session_state.get("user")
    return None


def require_user():
    user = current_user()
    if user is None:
        st.rerun()
    return user


def end():
    st.session_state.clear()
    st.query_params.clear()
    st.rerun()
