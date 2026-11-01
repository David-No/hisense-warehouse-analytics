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


kpi = load("SELECT * FROM gold.kpi_monthly ORDER BY month, warehouse")
weekly = load("SELECT * FROM gold.weekly_volume")
exceptions = load("SELECT * FROM gold.exceptions")
recon = load("SELECT * FROM gold.recon")
weekly["week_start"] = pd.to_datetime(weekly.week_start)

st.title("Warehouse operations analytics")
st.caption("Simulated data for four 3PL-run distribution centers shipping TVs and appliances. "
           "Raw extracts flow through bronze, silver, and gold layers in DuckDB.")

all_wh = sorted(kpi.warehouse.unique())
chosen = st.sidebar.multiselect("Warehouses", all_wh, default=all_wh)
if not chosen:
    st.info("Pick at least one warehouse in the sidebar to see results.")
    st.stop()
kpi_f = kpi[kpi.warehouse.isin(chosen)]
st.sidebar.caption("Built by David No as a portfolio project.")

tab_kpi, tab_fc, tab_ai, tab_rec = st.tabs(["KPI scorecard", "Forecast and capacity", "AI insights", "Data reconciliation"])

# KPI scorecard
with tab_kpi:
    months = sorted(kpi_f.month.unique())
    month = st.select_slider("Month", months, value=months[-1])
    prior = months[months.index(month) - 1] if months.index(month) else None
    now = an.rollup(kpi_f[kpi_f.month == month])
    before = an.rollup(kpi_f[kpi_f.month == prior]) if prior else None
    for col, (key, (label, target, _, higher, fmt)) in zip(st.columns(len(an.KPIS)), an.KPIS.items()):
        value, state = now[key], an.status(key, now[key])
        delta = None if before is None else (value - before[key]) * (1 if key == "units_per_labor_hour" else 100)
        col.metric(label, fmt.format(value),
                   None if delta is None else f"{delta:+.2f}{'' if key == 'units_per_labor_hour' else ' pts'}",
                   delta_color="normal" if higher else "inverse")
        col.markdown(f"<span class='badge' style='background:{STATUS_COLOR[state]}'>{state}</span> "
                     f"<small>target {'≥' if higher else '≤'} {fmt.format(target)}</small>", unsafe_allow_html=True)

    st.subheader("Trend by warehouse")
    key = st.selectbox("KPI", list(an.KPIS), format_func=lambda k: an.KPIS[k][0])
    fig = px.line(kpi_f, x="month", y=key, color="warehouse", markers=True)
    fig.add_hline(y=an.KPIS[key][1], line_dash="dash", line_color=STEEL, annotation_text="Target")
    fig.update_layout(yaxis_tickformat=".1f" if key == "units_per_labor_hour" else ".2%", yaxis_title=None,
                      xaxis_title=None, legend_title=None, height=380)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader(f"By warehouse, {month}")
    table = kpi_f[kpi_f.month == month].set_index("warehouse")
    st.dataframe(pd.DataFrame({an.KPIS[k][0]: table[k].map(an.KPIS[k][4].format) for k in an.KPIS}).assign(**{"3PL": table.tpl}),
                 use_container_width=True)

# Forecast and capacity
with tab_fc:
    left, right = st.columns([1, 2])
    wh = left.selectbox("Warehouse", chosen)
    scenario = left.slider("Volume change vs. forecast", -20, 30, 0, step=5, format="%d%%") / 100
    d = (weekly[weekly.warehouse == wh].groupby("week_start")
         [["inbound_units", "outbound_units", "pallets_stored", "labor_hours"]].sum().sort_index())
    capacity = weekly.loc[weekly.warehouse == wh, "pallet_capacity"].iloc[0]
    fc = an.forecast(d, horizon=12, scenario=scenario)
    util = fc.pallets_stored / capacity
    over = util[util > 0.90]
    hours_now = d.labor_hours.tail(8).mean()

    m1, m2, m3 = right.columns(3)
    m1.metric("Peak forecast utilization", f"{util.max():.0%}")
    m2.metric("Weeks above 90% full", len(over))
    m3.metric("Peak extra staff needed", f"{max(0, (fc.labor_hours.max() - hours_now) / 40):.0f} FTE",
              help="Extra 40-hour workers at the peak week vs. the last 8 weeks' average")
    if len(over):
        right.warning(f"{wh} is forecast above 90% of {capacity:,} pallet positions in {len(over)} of the next 12 weeks, "
                      f"starting the week of {over.index[0]:%b %d}. Plan overflow space or move inbound receipts earlier.")
    else:
        right.success(f"{wh} stays under 90% of capacity for the next 12 weeks in this scenario.")

    hist = d.tail(52)
    fig = go.Figure()
    fig.add_scatter(x=hist.index, y=hist.pallets_stored, name="Pallets stored", line_color=BLUE)
    fig.add_scatter(x=fc.index, y=fc.pallets_stored, name="Forecast", line=dict(color=BLUE, dash="dot"))
    fig.add_hline(y=capacity, line_color=RED, annotation_text="Capacity")
    fig.add_hline(y=capacity * 0.9, line_color=AMBER, line_dash="dash", annotation_text="90%")
    fig.update_layout(title="Storage vs. capacity", height=360, yaxis_title="Pallets", legend_title=None)
    st.plotly_chart(fig, use_container_width=True)

    fig = go.Figure()
    for col, color in (("outbound_units", BLUE), ("inbound_units", STEEL)):
        name = col.split("_")[0].title()
        fig.add_scatter(x=hist.index, y=hist[col], name=name, line_color=color)
        fig.add_scatter(x=fc.index, y=fc[col], name=f"{name} forecast", line=dict(color=color, dash="dot"))
    fig.update_layout(title="Units per week", height=340, yaxis_title="Units", legend_title=None)
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Method: each future week starts from the same week last year, scaled by the last 12 weeks' "
               "year-over-year trend, then by the scenario slider.")

# AI insights
with tab_ai:
    flags = an.find_anomalies(kpi_f)
    st.subheader("What changed")
    if flags.empty:
        st.success("No KPI is outside its normal range for the selected warehouses.")
    for f in flags.itertuples():
        (st.error if f.metric in an.METRIC_EXCEPTION else st.warning)(f.message)

    recent = sorted(exceptions.month.unique())[-3:]
    exc_f = exceptions[exceptions.warehouse.isin(chosen)]
    st.subheader("Root-cause themes in exception notes, last 3 months")
    themes = (exc_f[exc_f.month.isin(recent)].groupby("warehouse").note.apply(an.note_themes)
              .rename("incidents").reset_index().rename(columns={"level_1": "theme"}))
    fig = px.bar(themes, x="incidents", y="theme", color="warehouse", orientation="h", height=380)
    fig.update_layout(yaxis_title=None, legend_title=None, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Briefing")
    context = an.ai_context(flags, kpi_f, exc_f)
    if os.getenv("OPENAI_API_KEY"):
        if st.button("Write AI briefing"):
            with st.spinner("Reading KPIs and exception notes"):
                st.markdown(an.ask_ai("Write a short briefing for the Director of Distribution: the top issues, "
                                      "likely root causes, and recommended actions.", context))
        question = st.text_input("Ask a question about the data", placeholder="Why is damage rising at Cranbury?")
        if question:
            with st.spinner("Thinking it through"):
                st.markdown(an.ask_ai(question, context))
    else:
        st.markdown(an.rule_briefing(flags, exc_f))
        st.caption("Rule-based briefing. Add an OPENAI_API_KEY secret to turn on AI briefings and questions.")
    st.caption("AI output is a starting point for investigation. Check it against the source data before acting.")

# Data reconciliation
with tab_rec:
    r = recon[recon.warehouse.isin(chosen)]
    issues = r[r.recon_status != "Matched"]
    unbacked = r.loc[r.recon_status == "Billed by 3PL, no WMS record", "billed_amount"].sum()
    dup = r[r.recon_status == "Billed more than once"]
    overbilled = unbacked + (dup.billed_amount - dup.billed_amount / dup.line_count).sum()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Orders checked", f"{len(r):,}")
    m2.metric("Three-way match rate", f"{(r.recon_status == 'Matched').mean():.1%}")
    m3.metric("Exceptions to review", f"{len(issues):,}")
    m4.metric("Billing at risk", f"${overbilled:,.0f}", help="3PL charges with no WMS shipment, plus duplicate lines")
    st.caption("Last 8 weeks of orders matched across ERP (ordered), WMS (shipped), and the 3PL report (billed).")

    by_tpl = r.groupby("tpl").recon_status.apply(lambda s: (s == "Matched").mean()).rename("match_rate").reset_index()
    fig = px.bar(by_tpl, x="tpl", y="match_rate", text_auto=".1%", color_discrete_sequence=[BLUE], height=320)
    fig.update_layout(title="Match rate by 3PL", yaxis_tickformat=".0%", yaxis_range=[0.7, 1], xaxis_title=None, yaxis_title=None)
    st.plotly_chart(fig, use_container_width=True)

    counts = issues.recon_status.value_counts().rename_axis("issue").reset_index(name="orders")
    fig = px.bar(counts, x="orders", y="issue", orientation="h", color_discrete_sequence=[AMBER], height=320)
    fig.update_layout(title="Exceptions by type", yaxis_title=None, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)

    pick = st.multiselect("Show exceptions", sorted(issues.recon_status.unique()))
    st.dataframe(issues[issues.recon_status.isin(pick)] if pick else issues, use_container_width=True, hide_index=True)
