import os
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analytics as an

DB = Path(__file__).with_name("warehouse.duckdb")
BLUE, AMBER, RED, GREEN, STEEL = "#1F5FA8", "#E0A800", "#C0392B", "#2E7D4F", "#5B6770"
STATUS_COLOR = {"On target": GREEN, "Watch": AMBER, "Off target": RED}

st.set_page_config(page_title="Warehouse operations analytics", page_icon="📦", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@500;600&display=swap');
html, body, .stApp, .stMarkdown, button, input, select, textarea { font-family: 'Barlow', 'Segoe UI', sans-serif; }
h1, h2, h3, [data-testid="stMetricValue"] { font-family: 'Barlow Condensed', 'Arial Narrow', sans-serif; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 3px; color: #fff; font-size: 0.8rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def connect():
    if not DB.exists():
        import build_data
        build_data.build(DB)
    return duckdb.connect(str(DB), read_only=True)


@st.cache_data
def load(sql):
    return connect().execute(sql).df()


kpi = load("SELECT * FROM gold.kpi_monthly ORDER BY month, region")
top_wh = load("SELECT * FROM gold.warehouse_top ORDER BY units_sold DESC")
weekly = load("SELECT * FROM gold.forecast_weekly")
capacity = load("SELECT * FROM gold.capacity").set_index("warehouse")
exceptions = load("SELECT * FROM gold.exceptions")
recon = load("SELECT * FROM gold.recon")
sources = load("SELECT * FROM gold.data_sources")
weekly["week_start"] = pd.to_datetime(weekly.week_start)


def provenance(tab):
    """Which data on this tab is from Kaggle and which is simulated (from gold.data_sources)."""
    d = sources[sources.tab == tab]
    line = lambda o: "<br>".join(f"&nbsp;&nbsp;• {r.item} <i>({r.dataset})</i>" for r in d[d.origin == o].itertuples())  # noqa: E731
    st.markdown(f"<div style='font-size:0.85rem;border-left:4px solid {BLUE};padding:4px 10px;background:#fff'>"
                f"<b style='color:{GREEN}'>Real data from Kaggle</b><br>{line('Kaggle')}<br>"
                f"<b style='color:{AMBER}'>Simulated to fill gaps</b><br>{line('Simulated')}</div>", unsafe_allow_html=True)


st.title("Warehouse operations analytics")
st.caption("Real Kaggle datasets, with simulated data only where no Kaggle file covers a need. "
           "Each tab says which is which. Raw files flow through bronze, silver, and gold layers in DuckDB.")
st.sidebar.caption("Built by David No as a portfolio project.")

tab_kpi, tab_fc, tab_ai, tab_rec = st.tabs(["KPI scorecard", "Forecast and capacity", "AI insights", "Data reconciliation"])

# KPI scorecard
with tab_kpi:
    provenance("kpi")
    st.caption("Heads up: the Kaggle retail file looks synthetic (most columns are evenly spread), so utilization and "
               "on-time sit near 50% everywhere. Because of that, status compares each KPI with its own baseline: the 12 months "
               "ending 3 months earlier (same regions, pooled from summed inputs). On target = within 2 standard deviations "
               "or better, Watch = 2 to 3, Off target = more than 3 (the same test the AI tab uses). "
               "Utilization uses a fixed 85% / 90% capacity limit instead.")
    all_regions = sorted(kpi.region.unique())
    regions = st.multiselect("Regions", all_regions, default=all_regions)
    if not regions:
        st.info("Pick at least one region to see results.")
    else:
        kpi_f = kpi[kpi.region.isin(regions)]
        months = sorted(kpi_f.month.unique())
        month = st.select_slider("Month", months, value=months[-1])
        prior = months[months.index(month) - 1] if months.index(month) else None
        now = an.rollup(kpi_f[kpi_f.month == month])
        before = an.rollup(kpi_f[kpi_f.month == prior]) if prior else None
        base = an.baseline(kpi_f, month)
        tiles = list(an.KPIS.items()) + [("units_sold", ("Units shipped", None, None, True, "{:,.0f}"))]
        for row in (tiles[:4], tiles[4:]):
            for col, (key, (label, target, _, higher, fmt)) in zip(st.columns(4), row):
                value, delta = now[key], None
                if before:
                    delta = f"{(value - before[key]) * 100:+.2f} pts" if fmt.endswith("%}") else (
                        f"{value / before[key] - 1:+.1%}" if key == "units_sold" else f"{value - before[key]:+.4f}")
                col.metric(label, fmt.format(value), delta, delta_color="normal" if higher else "inverse")
                if key == "units_sold":
                    col.caption("Outbound throughput (no status)")
                    continue
                b = base[key] if base else (None, None)
                state = an.status(key, value, *b)
                note = (f"limit ≤ {fmt.format(target)}" if target is not None
                        else f"12-mo baseline {fmt.format(b[0])}" if base else "needs 6+ months of history")
                col.markdown(f"<span class='badge' style='background:{STATUS_COLOR.get(state, STEEL)}'>{state}</span> "
                             f"<small>{note}</small>", unsafe_allow_html=True)

        st.subheader("Trend by region")
        key = st.selectbox("KPI", list(an.KPIS), format_func=lambda k: an.KPIS[k][0])
        fig = px.line(kpi_f, x="month", y=key, color="region", markers=True)
        if an.KPIS[key][1] is not None:
            fig.add_hline(y=an.KPIS[key][1], line_dash="dash", line_color=STEEL, annotation_text="Limit")
        fig.update_layout(yaxis_title=None, xaxis_title=None, legend_title=None, height=380,
                          yaxis_tickformat=".2%" if an.KPIS[key][4].endswith("%}") else "$.3f")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader(f"By region, {month}")
        t = kpi_f[kpi_f.month == month].set_index("region")
        st.dataframe(pd.DataFrame({an.KPIS[k][0]: t[k].map(an.KPIS[k][4].format) for k in an.KPIS})
                     .assign(**{"Units shipped": t.units_sold.map("{:,.0f}".format)}), use_container_width=True)

        st.subheader("10 largest warehouses, all dates")
        st.caption("Kaggle KPIs plus simulated damage. Warehouse IDs in the source appear under several regions, "
                   "so this view does not split by region. Inventory accuracy is only counted by region.")
        w = top_wh.set_index("warehouse_id")
        st.dataframe(pd.DataFrame({an.KPIS[k][0]: w.apply(lambda r, k=k: an.rollup(r.to_frame().T)[k], axis=1).map(an.KPIS[k][4].format)
                                   for k in an.KPIS if k != "inventory_accuracy"})
                     .assign(**{"Units shipped": w.units_sold.map("{:,.0f}".format)}), use_container_width=True)

# Forecast and capacity
with tab_fc:
    provenance("forecast")
    left, right = st.columns([1, 2])
    wh = left.selectbox("Warehouse", sorted(capacity.index))
    scenario = left.slider("Volume change vs. forecast", -20, 30, 0, step=5, format="%d%%") / 100
    d = weekly[weekly.warehouse == wh].set_index("week_start").sort_index()
    cap_week = capacity.loc[wh, "daily_capacity"] * 7
    fc = an.forecast(d[["outbound_units", "inbound_units"]], horizon=12, scenario=scenario)
    fc["labor_hours"] = (fc.outbound_units + fc.inbound_units) / an.UNITS_PER_LABOR_HOUR
    util = fc.outbound_units / cap_week
    over = util[util > 0.90]
    hours_now = d.labor_hours.tail(8).mean()
    low = d.sku_days_below_reorder.tail(4).sum() / d.sku_days.tail(4).sum()

    m1, m2, m3, m4 = right.columns(4)
    m1.metric("Peak forecast utilization", f"{util.max():.0%}", help="Weekly outbound units vs. weekly throughput capacity")
    m2.metric("Weeks above 90% of capacity", len(over))
    m3.metric("Peak extra staff needed", f"{max(0, (fc.labor_hours.max() - hours_now) / 40):.0f} FTE",
              help="Extra 40-hour workers at the peak week vs. the last 8 weeks' average. Labor uses 22 units per labor hour (assumption).")
    m4.metric("SKU-days below reorder point", f"{low:.1%}", help="Last 4 weeks of history. The source records no actual stockouts.")
    if len(over):
        right.warning(f"{wh} is forecast above 90% of its {cap_week:,.0f} units/week capacity in {len(over)} of the next 12 weeks, "
                      f"starting the week of {over.index[0]:%b %d}. Plan overflow space, extra shifts, or earlier inbound receipts.")
    else:
        right.success(f"{wh} stays under 90% of capacity for the next 12 weeks in this scenario.")

    fig = go.Figure()
    fig.add_scatter(x=d.index, y=d.outbound_units, name="Outbound (history)", line_color=BLUE)
    fig.add_scatter(x=fc.index, y=fc.outbound_units, name="Outbound (forecast)", line=dict(color=BLUE, dash="dot"))
    fig.add_scatter(x=d.index, y=d.dataset_forecast_units, name="Forecast in the Kaggle file", line=dict(color=STEEL, width=1, dash="dash"))
    fig.add_hline(y=cap_week, line_color=RED, annotation_text="Capacity (assumed)")
    fig.add_hline(y=cap_week * 0.9, line_color=AMBER, line_dash="dash", annotation_text="90%")
    fig.update_layout(title="Weekly outbound vs. capacity", height=360, yaxis_title="Units", legend_title=None)
    st.plotly_chart(fig, use_container_width=True)

    fig = go.Figure()
    for col, color in (("inbound_units", STEEL), ("labor_hours", AMBER)):
        name = {"inbound_units": "Replenishment orders (units)", "labor_hours": "Labor hours needed"}[col]
        fig.add_scatter(x=d.index, y=d[col], name=name, line_color=color, yaxis="y2" if col == "labor_hours" else "y")
        fig.add_scatter(x=fc.index, y=fc[col], name=f"{name}, forecast", line=dict(color=color, dash="dot"),
                        yaxis="y2" if col == "labor_hours" else "y")
    fig.update_layout(title="Replenishment and labor", height=340, yaxis_title="Units", legend_title=None,
                      yaxis2=dict(title="Labor hours", overlaying="y", side="right"))
    st.plotly_chart(fig, use_container_width=True)
    wape = d.forecast_abs_error.sum() / d.outbound_units.sum()
    st.caption(f"The Kaggle file's own forecast is off by {wape:.1%} of weekly units (summed absolute error / summed sales). "
               "That is so close to actual sales that it was probably built from them, so it is not a fair benchmark for ours. "
               "Our method cannot be back-tested yet: it needs a second year of data.")
    st.caption("Method: each future week repeats the same week one year earlier (the source has one year, 2024, so no growth trend), "
               "then the scenario slider. Capacity is the real peak week times an assumed headroom, not a measured figure.")

# AI insights
with tab_ai:
    provenance("ai")
    flags = an.find_anomalies(kpi)
    st.subheader("What changed")
    if flags.empty:
        st.success("No KPI is outside its normal range.")
    for f in flags.itertuples():
        (st.error if f.metric in an.METRIC_EXCEPTION else st.warning)(f.message)

    recent = sorted(exceptions.month.unique())[-3:]
    st.subheader("Root-cause themes in exception notes, last 3 months")
    themes = (exceptions[exceptions.month.isin(recent)].groupby("region").note.apply(an.note_themes)
              .rename("incidents").reset_index().rename(columns={"level_1": "theme"}))
    fig = px.bar(themes, x="incidents", y="theme", color="region", orientation="h", height=380)
    fig.update_layout(yaxis_title=None, legend_title=None, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Briefing")
    context = an.ai_context(flags, kpi, exceptions)
    if os.getenv("OPENAI_API_KEY"):
        history = st.session_state.setdefault("qa", [])  # newest first, so answers survive reruns
        briefing = st.button("Write AI briefing")
        with st.form("ask", clear_on_submit=True):
            question = st.text_input("Ask a question about the data", placeholder="Why is damage rising in the East region?")
            asked = st.form_submit_button("Ask")
        title, prompt = (("AI briefing", "Write a short briefing for the Director of Distribution: the top issues, "
                          "likely root causes, and recommended actions.") if briefing
                         else (question.strip(), question.strip()) if asked else (None, None))
        if prompt:
            with st.spinner("Reading KPIs and exception notes"):
                try:
                    history.insert(0, (title, an.ask_ai(prompt, context)))
                except Exception as e:  # bad key, network, quota: fall back to the rule-based briefing
                    st.error(f"The AI request failed ({type(e).__name__}). Showing the rule-based briefing instead.")
                    history.insert(0, (title, an.rule_briefing(flags, exceptions)))
        for q, a in history:
            st.markdown(f"**{q}**")
            st.markdown(a)
    else:
        st.markdown(an.rule_briefing(flags, exceptions))
        st.caption("Rule-based briefing. Add an OPENAI_API_KEY secret to turn on AI briefings and questions.")
    st.caption("AI output is a starting point for investigation. Verify it against the source data before acting.")

# Data reconciliation
with tab_rec:
    provenance("recon")
    carriers = sorted(recon.carrier.unique())
    pick_c = st.multiselect("Carriers", carriers, default=carriers)
    r = recon[recon.carrier.isin(pick_c)]
    if r.empty:
        st.info("Pick at least one carrier to see results.")
        st.stop()
    issues = r[r.recon_status != "Matched"]
    unbacked = r.loc[r.recon_status == "Billed by 3PL, no WMS record", "billed_amount"].sum()
    dup = r[r.recon_status == "Billed more than once"]
    at_risk = unbacked + (dup.billed_amount - dup.billed_amount / dup.line_count).sum()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Orders checked", f"{len(r):,}")
    m2.metric("Three-way match rate", f"{(r.recon_status == 'Matched').mean():.1%}")
    m3.metric("Exceptions to review", f"{len(issues):,}")
    m4.metric("Billing at risk", f"${at_risk:,.0f}", help="3PL charges with no WMS shipment, plus duplicate lines")
    nr = (r.rate_source == "no rate card (median used)").sum()
    st.caption(f"{nr:,} of {len(r):,} orders have no rate on the Kaggle freight rate card (carrier V44_3, service CRF, or V444_1 "
               "with DTD). Their billed amounts are priced at the median rate in this demo, so amounts on those orders are not a real check.")
    st.caption("Every order matched across ERP (ordered), WMS (shipped), and the 3PL report (billed). "
               "All orders in the source are dated 2013-05-26, so there is no trend to show.")

    by_c = r.groupby("carrier").recon_status.apply(lambda s: (s == "Matched").mean()).rename("match_rate").reset_index()
    fig = px.bar(by_c, x="carrier", y="match_rate", text_auto=".1%", color_discrete_sequence=[BLUE], height=320)
    fig.update_layout(title="Match rate by carrier", yaxis_tickformat=".0%", yaxis_range=[0.7, 1], xaxis_title=None, yaxis_title=None)
    st.plotly_chart(fig, use_container_width=True)

    counts = issues.recon_status.value_counts().rename_axis("issue").reset_index(name="orders")
    fig = px.bar(counts, x="orders", y="issue", orientation="h", color_discrete_sequence=[AMBER], height=320)
    fig.update_layout(title="Exceptions by type", yaxis_title=None, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    pick = st.multiselect("Show exceptions", sorted(issues.recon_status.unique()))
    st.dataframe(issues[issues.recon_status.isin(pick)] if pick else issues, use_container_width=True, hide_index=True)
