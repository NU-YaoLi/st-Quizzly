"""
Admin-style usage dashboard: users, per-quiz detail, and cost (UTC).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from bknd.quizzly_analytics import (
    DailyRow,
    fetch_daily_stats,
    fetch_raw_events,
    fetch_usage_detail_rows,
    fetch_user_ip_rows,
    fetch_user_ip_rows_created_between,
    hour_of_day_counts,
    period_bounds,
)

_SESSION_UNLOCK = "quizzly_analytics_unlocked"
_ANALYTICS_REFRESH_NONCE = "_analytics_refresh_nonce"
# Dataframe viewport: grow with data row count, show at most this many rows without inner scroll.
_DA_MAX_VISIBLE_TABLE_ROWS = 10
_DA_TABLE_HEADER_PX = 52
_DA_TABLE_ROW_PX = 36


def _da_table_height_px(num_rows: int) -> int:
    """
    Streamlit ``st.dataframe`` height in pixels: short for few rows, capped so at most
    ``_DA_MAX_VISIBLE_TABLE_ROWS`` rows are visible; additional rows scroll inside the widget.
    """
    n = max(0, int(num_rows))
    if n == 0:
        return _DA_TABLE_HEADER_PX + _DA_TABLE_ROW_PX
    shown = min(n, _DA_MAX_VISIBLE_TABLE_ROWS)
    return _DA_TABLE_HEADER_PX + shown * _DA_TABLE_ROW_PX


def _analytics_password() -> str:
    return "1404"


@st.cache_data(ttl=90, show_spinner="Loading daily aggregates…")
def _cached_daily_stats(
    ts_start: float, ts_end: float, _refresh_nonce: int = 0
) -> tuple[list[DailyRow], str | None]:
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_daily_stats(start, end)


@st.cache_data(ttl=90, show_spinner="Loading hourly distribution…")
def _cached_raw_events(ts_start: float, ts_end: float, _refresh_nonce: int = 0):
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_raw_events(start, end)


@st.cache_data(ttl=90, show_spinner="Loading detailed usage rows…")
def _cached_usage_details(ts_start: float, ts_end: float, _refresh_nonce: int = 0):
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_usage_detail_rows(start, end)


@st.cache_data(ttl=90, show_spinner="Loading user_ip registry…")
def _cached_user_ips_period(ts_start: float, ts_end: float, _refresh_nonce: int = 0):
    start = datetime.fromtimestamp(ts_start, tz=timezone.utc)
    end = datetime.fromtimestamp(ts_end, tz=timezone.utc)
    return fetch_user_ip_rows_created_between(start, end)


def _fmt_location(meta: dict | None, snap_c: str | None, snap_r: str | None, snap_ct: str | None) -> str:
    c = (meta or {}).get("country") or snap_c
    r = (meta or {}).get("region") or snap_r
    ct = (meta or {}).get("city") or snap_ct
    parts = [p for p in (ct, r, c) if p]
    return ", ".join(parts) if parts else "—"


def _latest_snapshot(evs: list[dict], key: str) -> str | None:
    for e in sorted(evs, key=lambda x: str(x.get("created_at") or ""), reverse=True):
        v = e.get(key)
        if v:
            return str(v)
    return None


def _union_visitor_id_order(detail_rows: list[dict], uid_set: set[str]) -> list[str]:
    """Sort visitor ids by generation count (desc), then id."""
    by_uid: dict[str, list[dict]] = defaultdict(list)
    for r in detail_rows:
        uid = r.get("user_ip_id")
        if uid:
            by_uid[str(uid)].append(r)

    def key_fn(u: str) -> tuple[int, str]:
        return (-len(by_uid.get(u, [])), u)

    return sorted(uid_set, key=key_fn)


def _build_visitor_table(
    detail_rows: list[dict],
    ordered_uids: list[str],
    ip_meta: dict[str, dict],
) -> pd.DataFrame:
    """One row per ``ordered_uids``; generations/spend from ``detail_rows`` in range."""
    by_uid: dict[str, list[dict]] = defaultdict(list)
    for r in detail_rows:
        uid = r.get("user_ip_id")
        if uid:
            by_uid[str(uid)].append(r)

    out: list[dict] = []
    for uid in ordered_uids:
        evs = by_uid.get(uid, [])
        meta = ip_meta.get(uid) or {}
        ip_disp = meta.get("ip") or "—"
        n = len(evs)
        costs = []
        for x in evs:
            v = x.get("estimated_cost_usd")
            if v is not None:
                try:
                    costs.append(float(v))
                except (TypeError, ValueError):
                    pass
        total_c = sum(costs)
        sc = _latest_snapshot(evs, "country")
        sr = _latest_snapshot(evs, "region")
        sct = _latest_snapshot(evs, "city")
        if n:
            row_spend_total = round(total_c, 4)
            row_spend_avg = round(total_c / n, 4)
        else:
            row_spend_total = None
            row_spend_avg = None
        out.append(
            {
                "IP": ip_disp,
                "Location": _fmt_location(meta, sc, sr, sct),
                "Country": meta.get("country") or sc or "—",
                "Region": meta.get("region") or sr or "—",
                "City": meta.get("city") or sct or "—",
                "Generations": n,
                "Total est. spend (USD)": row_spend_total,
                "Avg spend / gen (USD)": row_spend_avg,
            }
        )
    return pd.DataFrame(out)


def _token_sum_row(r: dict, pref: str) -> float:
    a = r.get(f"{pref}_input_tokens")
    b = r.get(f"{pref}_cached_input_tokens")
    c = r.get(f"{pref}_output_tokens")
    s = 0.0
    for v in (a, b, c):
        if v is None:
            continue
        try:
            s += float(v)
        except (TypeError, ValueError):
            pass
    return s


def render_data_analysis_view() -> None:
    st.title("Quizzly Data Analysis")

    if not st.session_state.get(_SESSION_UNLOCK):
        st.caption("Admin only — enter password to view aggregate usage and estimated spend.")
        with st.form("quizzly_analytics_auth", clear_on_submit=False):
            pw = st.text_input("Password", type="password", autocomplete="off")
            submit = st.form_submit_button("Unlock", type="primary", width="stretch")
        if submit:
            if (pw or "").strip() == _analytics_password():
                st.session_state[_SESSION_UNLOCK] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        return

    st.caption(
        "Estimated OpenAI spend (from in-app model pricing) and activity across **all** visitors. "
        "Times are **UTC**. Geo uses ip-api.com and ipwho.is (HTTPS fallback). "
        "Numbers are cached briefly — click **Refresh data** to force a new read from Supabase."
    )
    c_lock, c_refresh, _ = st.columns([1, 1, 2])
    with c_lock:
        if st.button("Lock", help="Clear analytics access for this browser session"):
            st.session_state.pop(_SESSION_UNLOCK, None)
            st.rerun()
    with c_refresh:
        if st.button(
            "Refresh data",
            help="Bypass analytics cache and reload from Supabase (new generations or manual SQL inserts).",
        ):
            st.session_state[_ANALYTICS_REFRESH_NONCE] = (
                int(st.session_state.get(_ANALYTICS_REFRESH_NONCE, 0)) + 1
            )
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

    _nonce = int(st.session_state.get(_ANALYTICS_REFRESH_NONCE, 0))
    rows, err = _cached_daily_stats(ts0, ts1, _nonce)
    detail_rows, detail_err = _cached_usage_details(ts0, ts1, _nonce)

    if err:
        st.error(err)
        return

    df_daily: pd.DataFrame | None = None
    has_agg = bool(rows)
    if rows:
        df_daily = pd.DataFrame(
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
        df_daily["Day (UTC)"] = pd.to_datetime(df_daily["Day (UTC)"])

    tab_user, tab_quiz, tab_cost = st.tabs(["User", "Quiz", "Cost"])

    with tab_user:
        st.markdown(
            "**Visitors** — IP, coarse location, and spend. "
            "Rows include every **`user_ip`** first seen in this UTC range **plus** any IP "
            "with **`quiz_generation_usage`** here (even if first seen earlier). "
            "**0** generations means no completed run linked to that IP in this range."
        )
        ip_period_rows, ip_per_err = _cached_user_ips_period(ts0, ts1, _nonce)
        if ip_per_err:
            st.warning(f"Could not load `user_ip` rows for this period: {ip_per_err}")

        dr = detail_rows or []
        usage_uid_set = {str(r.get("user_ip_id")) for r in dr if r.get("user_ip_id")}
        period_uid_set = {str(r.get("id")) for r in (ip_period_rows or []) if r.get("id")}
        union_set = usage_uid_set | period_uid_set

        if detail_err:
            st.warning(f"Could not load usage detail: {detail_err}")

        if not union_set:
            st.info(
                "No visitor IPs match this window — widen the **time range** or generate a quiz "
                "(which creates `user_ip` when a run completes)."
            )
        else:
            ordered = _union_visitor_id_order(dr, union_set)
            ip_meta, meta_err = fetch_user_ip_rows(ordered)
            if meta_err:
                st.warning(meta_err)
            udf = _build_visitor_table(dr, ordered, ip_meta)
            tgen = int(udf["Generations"].sum())
            tspend = float(pd.to_numeric(udf["Total est. spend (USD)"], errors="coerce").fillna(0).sum())
            nu = len(udf)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Visitor rows in table", f"{nu:,}")
            m2.metric("Total generations (in range)", f"{tgen:,}")
            m3.metric("Total est. spend", f"${tspend:,.2f}")
            m4.metric("Avg spend / generation", f"${(tspend / tgen if tgen else 0):,.4f}")

            if "Country" in udf.columns:
                top_c = (
                    udf[udf["Country"] != "—"]["Country"].value_counts().head(12)
                    if len(udf)
                    else pd.Series(dtype=int)
                )
                if len(top_c):
                    fig_cy = go.Figure(
                        data=[go.Bar(x=top_c.index.tolist(), y=top_c.values.tolist(), marker_color="#636efa")]
                    )
                    fig_cy.update_layout(
                        title="Visitors by country (from user_ip / snapshots)",
                        height=380,
                        xaxis_tickangle=-28,
                        yaxis_title="Visitor rows",
                        margin=dict(l=10, r=10, t=48, b=10),
                    )
                    st.plotly_chart(fig_cy, width="stretch")

            st.subheader("Visitor table")
            st.caption(
                "All IPs in this UTC window are listed. Spend columns are empty when there were "
                "**no** completed generations in range for that IP."
            )
            st.dataframe(
                udf,
                width="stretch",
                hide_index=True,
                height=_da_table_height_px(len(udf)),
            )

    with tab_quiz:
        st.markdown(
            "**Each generation** — mode, materials, duration, token buckets, cost, and geo snapshot."
        )
        if detail_err:
            st.warning(f"Could not load usage detail: {detail_err}")
        elif not detail_rows:
            st.info("No generations in this window.")
        else:
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
            ddf_f = ddf[ddf["generation_mode"].isin(mode_pick) & ddf["material_source"].isin(src_pick)].copy()
            n_sel = len(ddf_f)
            n_all = len(ddf)
            st.caption(f"**{n_sel:,}** of **{n_all:,}** generations match filters.")

            for pref in ("ext", "gen", "vrf"):
                in_col = f"{pref}_input_tokens"
                if in_col not in ddf_f.columns:
                    ddf_f[f"{pref}_tokens_total"] = 0.0
                    continue
                ddf_f[f"{pref}_tokens_total"] = ddf_f.apply(lambda r: _token_sum_row(r.to_dict(), pref), axis=1)

            for tn in ("ext_tokens_total", "gen_tokens_total", "vrf_tokens_total"):
                if tn not in ddf_f.columns:
                    ddf_f[tn] = 0.0
            ddf_f["tokens_total"] = ddf_f["ext_tokens_total"] + ddf_f["gen_tokens_total"] + ddf_f["vrf_tokens_total"]

            show_cols = [
                "created_at",
                "country",
                "region",
                "city",
                "generation_mode",
                "material_source",
                "material_quantity",
                "num_questions",
                "upload_total_bytes",
                "web_text_chars",
                "generation_duration_sec",
                "estimated_cost_usd",
                "ext_tokens_total",
                "gen_tokens_total",
                "vrf_tokens_total",
                "tokens_total",
                "user_ip_id",
            ]
            present = [c for c in show_cols if c in ddf_f.columns]
            quiz_show = ddf_f[present].copy()
            quiz_show = quiz_show.rename(
                columns={
                    "created_at": "Created (UTC)",
                    "generation_mode": "Mode",
                    "material_source": "Material",
                    "material_quantity": "Materials #",
                    "num_questions": "Questions",
                    "upload_total_bytes": "Upload bytes",
                    "web_text_chars": "Web chars",
                    "generation_duration_sec": "Duration (s)",
                    "estimated_cost_usd": "Est. cost (USD)",
                    "user_ip_id": "user_ip id",
                }
            )
            st.subheader("All generations (filtered)")
            _quiz_df = quiz_show.sort_values("Created (UTC)", ascending=False)
            st.dataframe(
                _quiz_df,
                width="stretch",
                hide_index=True,
                height=_da_table_height_px(len(_quiz_df)),
            )

            if n_sel > 0:
                st.subheader("Mode × material mix")
                grp = (
                    ddf_f.groupby(["generation_mode", "material_source"], dropna=False)
                    .agg(generations=("created_at", "count"), avg_cost=("estimated_cost_usd", "mean"))
                    .reset_index()
                )
                grp["avg_cost"] = grp["avg_cost"].apply(lambda x: round(float(x), 4) if pd.notna(x) else None)
                grp["share %"] = (100.0 * grp["generations"] / float(n_sel)).round(1)
                _mix_df = grp.rename(
                    columns={"generation_mode": "Mode", "material_source": "Material", "avg_cost": "Avg cost"}
                )
                st.dataframe(
                    _mix_df,
                    width="stretch",
                    hide_index=True,
                    height=_da_table_height_px(len(_mix_df)),
                )
                labels = grp["generation_mode"].astype(str) + " · " + grp["material_source"].astype(str)
                fig_ms = go.Figure(data=[go.Bar(x=labels, y=grp["generations"], marker_color="#00cc96")])
                fig_ms.update_layout(
                    title="Generations by mode × material",
                    height=420,
                    showlegend=False,
                    yaxis_title="Count",
                    xaxis_tickangle=-25,
                )
                st.plotly_chart(fig_ms, width="stretch")

                lat = pd.to_numeric(ddf_f["generation_duration_sec"], errors="coerce").dropna()
                if len(lat):
                    st.subheader("Workflow duration")
                    p50 = float(lat.quantile(0.5))
                    p90 = float(lat.quantile(0.9))
                    lp1, lp2, lp3 = st.columns(3)
                    lp1.metric("Runs (filtered)", f"{len(lat):,}")
                    lp2.metric("P50 duration (s)", f"{p50:.1f}")
                    lp3.metric("P90 duration (s)", f"{p90:.1f}")
                    fig_l = go.Figure()
                    fig_l.add_trace(go.Histogram(x=lat, nbinsx=min(40, max(10, int(len(lat) ** 0.5) * 3))))
                    fig_l.add_vline(x=p50, line_dash="dash", line_color="#636efa", annotation_text="P50")
                    fig_l.add_vline(x=p90, line_dash="dot", line_color="#ef553b", annotation_text="P90")
                    fig_l.update_layout(
                        title="generation_duration_sec distribution",
                        xaxis_title="Seconds",
                        yaxis_title="Count",
                        height=380,
                    )
                    st.plotly_chart(fig_l, width="stretch")

    with tab_cost:
        st.markdown("**Cost & volume** — daily spend, cumulative trend, and hourly activity.")
        if not has_agg or df_daily is None:
            st.info(
                "No quiz generations in this window yet — run **Generate & Verify Quiz** after deploying "
                "tracking, or widen the time range."
            )
        else:
            total_g = int(df_daily["Generations"].sum())
            total_usd = float(df_daily["Est. spend (USD)"].sum())
            days_n = len(df_daily)
            avg_g = total_g / days_n if days_n else 0.0
            avg_usd = total_usd / days_n if days_n else 0.0
            imax = int(df_daily["Generations"].to_numpy().argmax()) if days_n else 0
            peak_day = str(df_daily.iloc[imax]["Day (UTC)"].date()) if days_n else "—"
            peak_n = int(df_daily["Generations"].iloc[imax]) if days_n else 0

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Total generations", f"{total_g:,}")
            m2.metric("Total est. spend", f"${total_usd:,.2f}")
            m3.metric("Avg generations / day", f"{avg_g:,.1f}")
            m4.metric("Avg spend / day", f"${avg_usd:,.2f}")
            m5.metric("Busiest day (UTC)", f"{peak_n} on {peak_day}")

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
                    x=df_daily["Day (UTC)"],
                    y=df_daily["Generations"],
                    name="Generations",
                    marker_color="#636efa",
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Bar(
                    x=df_daily["Day (UTC)"],
                    y=df_daily["Est. spend (USD)"],
                    name="Est. spend",
                    marker_color="#00cc96",
                ),
                row=2,
                col=1,
            )
            fig.update_layout(height=640, showlegend=False, margin=dict(l=10, r=10, t=40, b=10))
            fig.update_yaxes(title_text="Count", row=1, col=1)
            fig.update_yaxes(title_text="USD", row=2, col=1)
            st.plotly_chart(fig, width="stretch")

            df_c = df_daily.copy()
            df_c["Cumulative spend (USD)"] = df_c["Est. spend (USD)"].cumsum()
            fig_c = go.Figure()
            fig_c.add_trace(
                go.Scatter(
                    x=df_c["Day (UTC)"],
                    y=df_c["Cumulative spend (USD)"],
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
            st.plotly_chart(fig_c, width="stretch")

            raw_ev, herr = _cached_raw_events(ts0, ts1, _nonce)
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
                        title="Generations by hour of day (UTC)",
                        height=380,
                        xaxis_title="Hour (UTC)",
                        yaxis_title="Generations",
                        margin=dict(l=10, r=10, t=40, b=10),
                    )
                    st.plotly_chart(fig_h, width="stretch")

            st.subheader("Daily breakdown")
            show_df = df_daily.drop(columns=["Cumulative spend (USD)"], errors="ignore").sort_values(
                "Day (UTC)", ascending=False
            )
            st.dataframe(
                show_df,
                width="stretch",
                hide_index=True,
                height=_da_table_height_px(len(show_df)),
            )

    st.divider()
    st.markdown(
        """
**How this works**

- Each successful **Generate & Verify Quiz** inserts one row into **`quiz_generation_usage`** and ensures a **`user_ip`** row for the client IP (geo snapshot when available).
- Daily limits only **look up** existing **`user_ip`** for counting — they no longer create **`user_ip`** on every button click (avoids orphan IPs without a completed generation).
- **User tab** lists IPs **first seen** in the selected UTC window **union** IPs that have usage in that window.
- Spend is **estimated** from `MODEL_PRICING_USD_PER_1K` in `quizzly_config.py`, not your OpenAI invoice.
- Protect Supabase and this page (password + strong deployment secrets).
        """
    )
