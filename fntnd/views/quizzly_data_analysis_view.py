"""
Admin-style usage dashboard: generations and estimated spend across all visitors (UTC).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from bknd.quizzly_analytics import (
    DailyRow,
    fetch_daily_stats,
    fetch_raw_events,
    hour_of_day_counts,
    period_bounds,
)


@st.cache_data(ttl=90, show_spinner="Loading daily aggregates…")
def _cached_daily_stats(ts_start: float, ts_end: float) -> tuple[list[DailyRow], str | None]:
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_daily_stats(start, end)


@st.cache_data(ttl=90, show_spinner="Loading hourly distribution…")
def _cached_raw_events(ts_start: float, ts_end: float):
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_raw_events(start, end)


def render_data_analysis_view() -> None:
    st.title("Usage & cost analytics")
    st.caption(
        "Estimated OpenAI spend (from in-app model pricing) and quiz-generation counts, "
        "aggregated across **all** visitors. Times are **UTC**."
    )

    period = st.selectbox(
        "Time range",
        ["Last 7 days", "Last 30 days", "Last 90 days", "All time", "Custom"],
        index=1,
    )
    custom_start: date | None = None
    custom_end: date | None = None
    if period == "Custom":
        c1, c2 = st.columns(2)
        today_utc = datetime.now(timezone.utc).date()
        with c1:
            custom_start = st.date_input("Start date (UTC)", value=today_utc.replace(day=1))
        with c2:
            custom_end = st.date_input("End date (UTC)", value=today_utc)
        if custom_start and custom_end and custom_start > custom_end:
            st.error("Start date must be on or before end date.")
            return

    start_dt, end_ex = period_bounds(period, custom_start, custom_end)
    ts0 = start_dt.timestamp()
    ts1 = end_ex.timestamp()

    rows, err = _cached_daily_stats(ts0, ts1)

    if err:
        st.error(err)
        return

    if not rows:
        st.info(
            "No quiz generations in this window yet — run **Generate & Verify Quiz** after deploying "
            "cost tracking, or widen the time range."
        )
        st.divider()
        st.markdown(
            "**Tip:** older rows may have **blank cost** if they were recorded before estimated "
            "spend was saved to the database. Generation counts are still accurate."
        )
        return

    df = pd.DataFrame(
        [
            {
                "Day (UTC)": r.day.isoformat(),
                "Generations": r.generations,
                "Est. spend (USD)": round(r.total_cost_usd, 4),
                "Distinct visitors (hash)": r.distinct_visitors,
            }
            for r in rows
        ]
    )
    df["Day (UTC)"] = pd.to_datetime(df["Day (UTC)"])

    total_g = int(df["Generations"].sum())
    total_usd = float(df["Est. spend (USD)"].sum())
    days_n = len(df)
    avg_g = total_g / days_n if days_n else 0.0
    avg_usd = total_usd / days_n if days_n else 0.0
    imax = int(df["Generations"].to_numpy().argmax()) if days_n else 0
    peak_day = str(df.iloc[imax]["Day (UTC)"].date()) if days_n else "—"
    peak_n = int(df["Generations"].iloc[imax]) if days_n else 0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total generations", f"{total_g:,}")
    m2.metric("Total est. spend", f"${total_usd:,.2f}")
    m3.metric("Avg generations / day", f"{avg_g:,.1f}")
    m4.metric("Avg spend / day", f"${avg_usd:,.2f}")
    m5.metric("Busiest day (UTC)", f"{peak_n} on {peak_day}")

    st.divider()

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Quiz generations per day", "Estimated spend per day (USD)"),
        row_heights=[0.55, 0.45],
    )
    fig.add_trace(
        go.Bar(
            x=df["Day (UTC)"],
            y=df["Generations"],
            name="Generations",
            marker_color="#636efa",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=df["Day (UTC)"],
            y=df["Est. spend (USD)"],
            name="Est. spend",
            marker_color="#00cc96",
        ),
        row=2,
        col=1,
    )
    fig.update_layout(height=640, showlegend=False, margin=dict(l=10, r=10, t=40, b=10))
    fig.update_yaxes(title_text="Count", row=1, col=1)
    fig.update_yaxes(title_text="USD", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

    df["Cumulative spend (USD)"] = df["Est. spend (USD)"].cumsum()
    fig_c = go.Figure()
    fig_c.add_trace(
        go.Scatter(
            x=df["Day (UTC)"],
            y=df["Cumulative spend (USD)"],
            fill="tozeroy",
            mode="lines",
            line=dict(color="#ab63fa", width=2),
            fillcolor="rgba(171, 99, 250, 0.25)",
            name="Cumulative spend",
        )
    )
    fig_c.update_layout(
        title="Cumulative estimated spend (USD)",
        height=360,
        margin=dict(l=10, r=10, t=40, b=10),
        yaxis_title="USD",
        xaxis_title="Day (UTC)",
    )
    st.plotly_chart(fig_c, use_container_width=True)

    raw_ev, herr = _cached_raw_events(ts0, ts1)
    if not herr and raw_ev:
        hc = hour_of_day_counts(raw_ev)
        if hc:
            hx = list(range(24))
            hy = [hc.get(h, 0) for h in hx]
            fig_h = go.Figure(
                data=[
                    go.Bar(
                        x=[f"{h:02d}:00" for h in hx],
                        y=hy,
                        marker_color="#ef553b",
                        name="Generations",
                    )
                ]
            )
            fig_h.update_layout(
                title="Generations by hour of day (UTC, whole period)",
                height=380,
                xaxis_title="Hour (UTC)",
                yaxis_title="Generations",
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig_h, use_container_width=True)

    st.subheader("Daily breakdown")
    show_df = df.drop(columns=["Cumulative spend (USD)"], errors="ignore").sort_values(
        "Day (UTC)", ascending=False
    )
    st.dataframe(show_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown(
        """
**How this works**

- Each successful **Generate & Verify Quiz** inserts one row (rate-limit table) with optional **estimated_cost_usd**.
- **Visitors** are anonymous salted IP hashes — not personally identifiable.
- Spend is **estimated** from `MODEL_PRICING_USD_PER_1K` in `quizzly_config.py`, not your OpenAI invoice.
        """
    )
