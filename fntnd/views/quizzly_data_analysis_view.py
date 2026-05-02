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
    fetch_usage_detail_rows,
    fetch_raw_events,
    hour_of_day_counts,
    period_bounds,
)

_SESSION_UNLOCK = "quizzly_analytics_unlocked"


def _analytics_password() -> str:
    # Simple, lightweight access gate (non-critical admin page).
    return "1404"


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


@st.cache_data(ttl=90, show_spinner="Loading detailed usage rows…")
def _cached_usage_details(ts_start: float, ts_end: float):
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_usage_detail_rows(start, end)


def render_data_analysis_view() -> None:
    st.title("Usage & cost analytics")

    if not st.session_state.get(_SESSION_UNLOCK):
        st.caption("Admin only — enter password to view aggregate usage and estimated spend.")
        with st.form("quizzly_analytics_auth", clear_on_submit=False):
            pw = st.text_input("Password", type="password", autocomplete="off")
            submit = st.form_submit_button("Unlock", type="primary", use_container_width=True)
        if submit:
            if (pw or "").strip() == _analytics_password():
                st.session_state[_SESSION_UNLOCK] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        return

    st.caption(
        "Estimated OpenAI spend (from in-app model pricing) and quiz-generation counts, "
        "aggregated across **all** visitors. Times are **UTC**."
    )
    c_lock, _ = st.columns([1, 3])
    with c_lock:
        if st.button("Lock", help="Clear analytics access for this browser session"):
            st.session_state.pop(_SESSION_UNLOCK, None)
            st.rerun()

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
    detail_rows = []
    detail_err = None

    if err:
        st.error(err)
        return

    if not rows:
        st.info(
            "No quiz generations in this window yet — run **Generate & Verify Quiz** after deploying "
            "tracking, or widen the time range."
        )
        st.divider()
        st.markdown(
            "**Tip:** migrate `quizzly_sql.txt` in Supabase and generate at least once so daily and "
            "detail analytics populate."
        )
        return

    if rows:
        df = pd.DataFrame(
            [
                {
                    "Day (UTC)": r.day.isoformat(),
                    "Generations": r.generations,
                    "Est. spend (USD)": round(r.total_cost_usd, 4),
                    "Distinct visitors": r.distinct_visitors,
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

        with st.expander("Hourly distribution", expanded=False):
            raw_ev, herr = _cached_raw_events(ts0, ts1)
            if herr:
                st.warning(herr)
            elif raw_ev:
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
                else:
                    st.info("No hourly data in this window.")
            else:
                st.info("No hourly data in this window.")

    with st.expander("Mode/source + tokens + latency (detailed)", expanded=False):
        detail_rows, detail_err = _cached_usage_details(ts0, ts1)
        if detail_err:
            st.warning(f"Detailed usage breakdown unavailable: {detail_err}")
        elif not detail_rows:
            st.info("No detailed usage rows in this window yet.")
        else:
            st.subheader("Mode & material source")
            ddf = pd.DataFrame(detail_rows)
            ddf["generation_mode"] = ddf["generation_mode"].fillna("unknown").replace("", "unknown")
            ddf["material_source"] = ddf["material_source"].fillna("unknown").replace("", "unknown")
            fm1, fm2 = st.columns(2)
            with fm1:
                mode_pick = st.multiselect(
                    "Generation modes",
                    options=sorted(ddf["generation_mode"].unique().tolist()),
                    default=sorted(ddf["generation_mode"].unique().tolist()),
                )
            with fm2:
                src_pick = st.multiselect(
                    "Material sources",
                    options=sorted(ddf["material_source"].unique().tolist()),
                    default=sorted(ddf["material_source"].unique().tolist()),
                )
            ddf_f = ddf[
                ddf["generation_mode"].isin(mode_pick) & ddf["material_source"].isin(src_pick)
            ].copy()
            n_sel = len(ddf_f)
            n_all = len(ddf)
            if n_sel == 0:
                st.info("No rows match the selected filters.")
            else:
                st.caption(
                    f"Showing **{n_sel:,}** of **{n_all:,}** generations in range after filters "
                    "(Share % is within the filtered set)."
                )
                for pref in ("ext", "gen", "vrf"):
                    in_col = f"{pref}_input_tokens"
                    ca_col = f"{pref}_cached_input_tokens"
                    ou_col = f"{pref}_output_tokens"
                    if in_col not in ddf_f.columns:
                        ddf_f[f"{pref}_tokens_sum"] = 0.0
                        continue
                    ddf_f[f"{pref}_tokens_sum"] = (
                        pd.to_numeric(ddf_f[in_col], errors="coerce").fillna(0)
                        + pd.to_numeric(ddf_f[ca_col], errors="coerce").fillna(0)
                        + pd.to_numeric(ddf_f[ou_col], errors="coerce").fillna(0)
                    )

                grp = (
                    ddf_f.groupby(["generation_mode", "material_source"], dropna=False)
                    .agg(
                        generations=("created_at", "count"),
                        avg_est_cost_usd=("estimated_cost_usd", "mean"),
                        avg_ext_tokens=("ext_tokens_sum", "mean"),
                        avg_gen_tokens=("gen_tokens_sum", "mean"),
                        avg_vrf_tokens=("vrf_tokens_sum", "mean"),
                    )
                    .reset_index()
                )
                grp["share_pct"] = 100.0 * grp["generations"] / float(n_sel)
                for col in (
                    "avg_est_cost_usd",
                    "avg_ext_tokens",
                    "avg_gen_tokens",
                    "avg_vrf_tokens",
                    "share_pct",
                ):
                    if col in grp.columns:
                        grp[col] = grp[col].apply(
                            lambda x: round(float(x), 2) if pd.notna(x) else None
                        )

                show_grp = grp.rename(
                    columns={
                        "generation_mode": "Mode",
                        "material_source": "Material",
                        "generations": "Generations",
                        "share_pct": "Share %",
                        "avg_est_cost_usd": "Avg est. cost (USD)",
                        "avg_ext_tokens": "Avg ext tokens / run",
                        "avg_gen_tokens": "Avg gen tokens / run",
                        "avg_vrf_tokens": "Avg vrf tokens / run",
                    }
                )
                st.dataframe(show_grp, use_container_width=True, hide_index=True)

                labels = grp["generation_mode"].astype(str) + " · " + grp["material_source"].astype(
                    str
                )
                fig_ms = go.Figure(data=[go.Bar(x=labels, y=grp["generations"])])
                fig_ms.update_layout(
                    title="Generations by mode × material (filtered)",
                    height=420,
                    showlegend=False,
                    yaxis_title="Count",
                    xaxis_tickangle=-25,
                )
                st.plotly_chart(fig_ms, use_container_width=True)

            lat_basis = ddf_f if n_sel > 0 else ddf
            st.subheader("Workflow duration (generate → verify complete)")
            if n_sel > 0:
                st.caption("Based on rows matching the mode/source filters above.")
            else:
                st.caption(
                    "Filters excluded all rows — duration uses **all** generations in this window."
                )

            lat = pd.to_numeric(lat_basis["generation_duration_sec"], errors="coerce").dropna()
            if len(lat) == 0:
                st.info("No `generation_duration_sec` in range (run DB migration and new generations).")
            else:
                p50 = float(lat.quantile(0.5))
                p90 = float(lat.quantile(0.9))
                lp1, lp2, lp3 = st.columns(3)
                lp1.metric("Runs (filtered)", f"{len(lat):,}")
                lp2.metric("P50 duration (s)", f"{p50:.1f}")
                lp3.metric("P90 duration (s)", f"{p90:.1f}")
                fig_l = go.Figure()
                fig_l.add_trace(
                    go.Histogram(x=lat, nbinsx=min(40, max(10, int(len(lat) ** 0.5) * 3)))
                )
                fig_l.add_vline(x=p50, line_dash="dash", line_color="#636efa", annotation_text="P50")
                fig_l.add_vline(x=p90, line_dash="dot", line_color="#ef553b", annotation_text="P90")
                fig_l.update_layout(
                    title="Distribution of generation_duration_sec (seconds)",
                    xaxis_title="Seconds",
                    yaxis_title="Count",
                    height=380,
                )
                st.plotly_chart(fig_l, use_container_width=True)

    if rows:
        st.subheader("Daily breakdown")
        show_df = df.drop(columns=["Cumulative spend (USD)"], errors="ignore").sort_values(
            "Day (UTC)", ascending=False
        )
        st.dataframe(show_df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown(
        """
**How this works**

- Each successful **Generate & Verify Quiz** inserts one row linked to **`user_ip`** (client IP + best-effort city/region/country via ip-api.com).
- **Personal data:** you are storing real IPs and coarse location for operations — protect Supabase and this dashboard (password + strong `ANALYTICS_PASSWORD` in secrets).
- Spend is **estimated** from `MODEL_PRICING_USD_PER_1K` in `quizzly_config.py`, not your OpenAI invoice.
        """
    )
