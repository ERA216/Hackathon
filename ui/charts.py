"""Stable, presentation-only charts for the app's light palette."""
import altair as alt
import pandas as pd
import streamlit as st


def bar_chart(data, x, y, *, y_title, height=380, integer=False):
    frame = pd.DataFrame(data)
    axis = alt.Axis(labelAngle=0, labelPadding=12, labelOverlap="greedy", titlePadding=18)
    base = alt.Chart(frame).encode(
        x=alt.X(f"{x}:O", title=None, sort=None, axis=axis, scale=alt.Scale(padding=0.45)),
        y=alt.Y(f"{y}:Q", title=y_title, scale=alt.Scale(zero=True),
                axis=alt.Axis(tickMinStep=1 if integer else 0.25, tickCount=5)),
    )
    bars = base.mark_bar(color="#cd7048", cornerRadiusTopLeft=7, cornerRadiusTopRight=7,
                         tooltip=False)
    labels = base.mark_text(dy=-12, color="#594537", fontSize=14, fontWeight=600).encode(
        text=alt.Text(f"{y}:Q", format=".0f" if integer else ".2f")
    )
    chart = (bars + labels).properties(
        height=height, padding={"left": 16, "right": 20, "top": 32, "bottom": 14},
        usermeta={"embedOptions": {"actions": False, "renderer": "svg"}},
    ).configure(
        background="#fffaf5", font="Onest, Segoe UI, sans-serif",
    ).configure_view(stroke=None).configure_axis(
        labelFontSize=13, titleFontSize=14, titleFontWeight=500,
        labelColor="#665849", titleColor="#594537",
        gridColor="#eadecf", gridDash=[3, 5], domain=False, ticks=False,
    ).configure_axisX(grid=False)
    # No interactive()/scale binding: mouse movement and wheel cannot zoom the plot.
    st.altair_chart(chart, use_container_width=True, theme=None)
