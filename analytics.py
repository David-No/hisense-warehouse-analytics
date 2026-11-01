"""Analytics used by the app and the self-check. Pure pandas, so it can be tested without Streamlit.

Run `python analytics.py` after `python build_data.py` to self-check the built database.
"""
import os

import pandas as pd

# key: (label, target, watch, higher_is_better, format).
# Only utilization has a fixed limit (a capacity guard). The others are judged against their own trailing baseline (see status).
KPIS = {
    "logistics_expense_rate": ("Logistics expense rate", None, None, False, "{:.3%}"),
    "cost_per_unit": ("Cost per unit shipped", None, None, False, "${:.3f}"),
    "inventory_accuracy": ("Inventory accuracy", None, None, True, "{:.1%}"),
    "damage_rate": ("Damage rate", None, None, False, "{:.2%}"),
    "utilization": ("Warehouse utilization", 0.85, 0.90, False, "{:.1%}"),
    "on_time_rate": ("On-time delivery", None, None, True, "{:.1%}"),
}
UNITS_PER_LABOR_HOUR = 22  # same assumption as build_data.py


def rollup(df):
    """KPIs from summed inputs (never average the rates). Works for any slice of gold.kpi_monthly or gold.warehouse_top."""
    s = df.sum(numeric_only=True)
    out = {
        "logistics_expense_rate": s.shipping_cost / s.revenue,
        "cost_per_unit": s.shipping_cost / s.units_sold,
        "damage_rate": s.units_damaged / s.units_sold,
        "utilization": s.occupied / s.capacity,
        "on_time_rate": s.units_on_time / s.units_sold,
        "units_sold": s.units_sold,
    }
    if "locations_counted" in s:  # cycle counts exist by region-month only
        out["inventory_accuracy"] = s.locations_accurate / s.locations_counted
    return out


def baseline(kpi, month, n=12, gap=3):
    """The n months ending `gap` months before `month`, so a problem that is still developing does not hide in its own baseline.
    kpi is a slice of gold.kpi_monthly. Returns {kpi: (pooled rate from summed inputs, std dev of the monthly rates)},
    or None with under 6 months of history."""
    months = sorted(kpi.month.unique())
    i = months.index(month)
    win = months[max(0, i - gap - n):max(0, i - gap)]
    if len(win) < 6:
        return None
    monthly = pd.DataFrame([rollup(kpi[kpi.month == m]) for m in win])
    pooled = rollup(kpi[kpi.month.isin(win)])
    return {k: (pooled[k], monthly[k].std()) for k in KPIS}


# smallest worsening (in the KPI's own units) that matters, so a tiny wobble is never flagged just because the data is steady
MATERIAL = {"logistics_expense_rate": 0.00005, "cost_per_unit": 0.005, "inventory_accuracy": 0.005, "damage_rate": 0.0005, "on_time_rate": 0.01}


def status(key, value, base=None, sd=None):
    """Utilization: fixed 85%/90% limits. Everything else: vs. its own baseline.
    On target = no worse than 2 std devs from baseline, Watch = 2 to 3, Off target = more than 3 (the AI tab's flag test)."""
    _, target, watch, higher, _ = KPIS[key]
    if target is not None:
        return "On target" if value <= target else "Watch" if value <= watch else "Off target"
    if base is None or not sd:
        return "No baseline"
    worse = (base - value) if higher else (value - base)
    z = worse / sd if worse >= MATERIAL[key] else 0
    return "On target" if z <= 2 else "Watch" if z <= 3 else "Off target"


def forecast(weekly, horizon=12, scenario=0.0):
    """Seasonal baseline: each future week repeats the same week one year earlier, scaled by the scenario.

    weekly: DataFrame indexed by week_start (Timestamp), numeric columns, at least 52 weeks.
    shortcut: no growth trend (the source has one year, so year-over-year growth is unknown); add it once there are 2+ years.
    """
    week = pd.Timedelta(weeks=1)
    future = [weekly.index.max() + k * week for k in range(1, horizon + 1)]
    out = weekly.reindex([f - 52 * week for f in future]) * (1 + scenario)
    out.index = pd.DatetimeIndex(future, name=weekly.index.name)
    return out


# metric: (higher_is_bad, minimum change worth flagging)
WATCHED = {"damage_rate": (True, 0.0005), "inventory_accuracy": (False, 0.01), "on_time_rate": (False, 0.015)}


def find_anomalies(kpi, z_limit=3.0):
    """Compare each region's last 3 months with the 6 months before the last 6 (a clean baseline)."""
    flags = []
    for region, d in kpi.sort_values("month").groupby("region"):
        recent, base = d.tail(3), d.iloc[-12:-6]
        for metric, (higher_bad, min_change) in WATCHED.items():
            r, b, sd = recent[metric].mean(), base[metric].mean(), base[metric].std() or 1e-9
            change = r - b if higher_bad else b - r
            z = change / sd
            if z >= z_limit and change >= min_change:
                flags.append({"region": region, "metric": metric, "recent": r, "baseline": b, "z": round(z, 1),
                              "message": f"{region} region: {KPIS[metric][0].lower()} is {KPIS[metric][4].format(r)} "
                                         f"over the last 3 months vs. {KPIS[metric][4].format(b)} baseline"})
        util = d.utilization.iloc[-1]
        if util > KPIS["utilization"][2]:
            flags.append({"region": region, "metric": "utilization", "recent": util, "baseline": d.utilization.mean(),
                          "z": None, "message": f"{region} region: utilization is {util:.1%} this month, above the 90% limit"})
    return pd.DataFrame(flags, columns=["region", "metric", "recent", "baseline", "z", "message"])


THEMES = {
    "Double-stacked pallets": ("double-stack", "Review stacking rules for TVs and audit rack space before peak."),
    "Re-slotting errors": ("slot", "Audit locations changed in the re-slot project and retrain on putaway scans."),
    "Forklift handling": ("forklift", "Refresh forklift handling training in the affected aisles."),
    "Carrier and transit": ("carrier|bol|trailer", "Raise freight claims and review carrier load bracing."),
    "Dock door leak": ("dock door|leak", "Open a facilities ticket for the leaking door or roof."),
    "Similar-looking SKUs": ("adjacent slot|look alike", "Separate look-alike SKUs and add label checks."),
    "Replenishment timing": ("replenish|pick face", "Tighten replenishment triggers at pick faces."),
    "Picking and packing": ("picking|outbound qc|wrap", "Add a pack-out check on high-value items."),
    "Unrecorded write-offs": ("write-off", "Reconcile damage write-offs to inventory weekly."),
}


def note_themes(notes):
    """Count incidents by theme with simple keyword rules (the non-AI baseline)."""
    counts = {t: int(notes.str.contains(rx, case=False, regex=True).sum()) for t, (rx, _) in THEMES.items()}
    return pd.Series(counts).loc[lambda s: s > 0].sort_values(ascending=False)


METRIC_EXCEPTION = {"damage_rate": "Damage", "inventory_accuracy": "Count variance"}


def rule_briefing(flags, exceptions):
    """Rule-based fallback used when there is no API key."""
    if flags.empty:
        return "No KPI is outside its normal range right now."
    lines = []
    recent_months = sorted(exceptions.month.unique())[-3:]
    for f in flags.itertuples():
        line = f"- **{f.message}.**"
        etype = METRIC_EXCEPTION.get(f.metric)
        if etype:
            notes = exceptions[(exceptions.region == f.region) & (exceptions.exception_type == etype)
                               & exceptions.month.isin(recent_months)].note
            themes = note_themes(notes)
            if len(themes):
                top = themes.index[0]
                where = exceptions[(exceptions.region == f.region) & (exceptions.exception_type == etype)
                                   & exceptions.month.isin(recent_months) & exceptions.note.str.contains(THEMES[top][0], case=False)]
                ids = where.warehouse_id.value_counts()
                line += (f" The most common note theme is {top.lower()} ({themes.iloc[0]} of {len(notes)} incidents, "
                         f"mostly warehouses {', '.join(ids.index[:3])}). Next step: {THEMES[top][1]}")
        lines.append(line)
    return "\n".join(lines)


def ai_context(flags, kpi, exceptions):
    months = sorted(kpi.month.unique())[-3:]
    flagged = flags.region.unique() if not flags.empty else []
    notes = exceptions[exceptions.month.isin(months) & exceptions.region.isin(flagged)]
    sample = notes.groupby(["region", "exception_type"]).head(8)[["region", "warehouse_id", "exception_type", "category", "note"]]
    cols = ["region", "units_sold", "logistics_expense_rate", "cost_per_unit", "inventory_accuracy", "damage_rate", "utilization", "on_time_rate"]
    return ("Latest month KPIs by region:\n" + kpi[kpi.month == months[-1]][cols].round(5).to_csv(index=False)
            + "\nFlags from statistical checks:\n" + (flags.drop(columns="message").to_csv(index=False) if not flags.empty else "none\n")
            + "\nSample exception notes from flagged regions, last 3 months:\n" + sample.to_csv(index=False))


def ask_ai(question, context):
    """Call OpenAI. Returns None when no API key is configured."""
    if not os.getenv("OPENAI_API_KEY"):
        return None
    from openai import OpenAI

    reply = OpenAI().chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": (
                "You are a logistics analytics assistant for a consumer electronics distribution network. "
                "Use only the data provided. Be concise and practical: what is happening, the likely root cause, "
                "the recommended action, and what to verify before acting. Say so when the data does not support a "
                "conclusion. This is a demo: the exception notes, cycle counts, and damage counts are simulated.")},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
        temperature=0.2,
    )
    return reply.choices[0].message.content


if __name__ == "__main__":  # self-check against the built database
    import duckdb

    con = duckdb.connect("warehouse.duckdb", read_only=True)
    q = lambda sql: con.execute(sql).df()  # noqa: E731

    for t in q("SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'").table_name:
        assert q(f"SELECT COUNT(*) AS n FROM gold.{t}").n[0] > 0, f"gold.{t} is empty"
    src = q("SELECT tab, origin FROM gold.data_sources")
    assert {(t, o) for t in ("forecast", "kpi", "ai", "recon") for o in ("Kaggle", "Simulated")} <= set(zip(src.tab, src.origin))

    kpi = q("SELECT * FROM gold.kpi_monthly")
    now = rollup(kpi)
    for k, v in now.items():  # sane ranges
        assert v > 0, k
        if k in KPIS and k != "cost_per_unit":
            assert v < 1, f"{k}={v}"
    assert 0.9 < now["inventory_accuracy"] < 1 and now["damage_rate"] < 0.02
    last = sorted(kpi.month.unique())[-1]
    states = {r: {k: status(k, rollup(kpi[(kpi.month == last) & (kpi.region == r)])[k], *baseline(kpi[kpi.region == r], last)[k])
                  for k in KPIS} for r in kpi.region.unique()}
    assert states["East"]["damage_rate"] == "Off target" and states["South"]["inventory_accuracy"] == "Off target", states
    assert states["North"]["damage_rate"] != "Off target" and states["West"]["damage_rate"] != "Off target", states
    assert baseline(kpi, sorted(kpi.month.unique())[4]) is None
    calm = [status(k, rollup(kpi[(kpi.month == last) & (kpi.region == r)])[k], *baseline(kpi[kpi.region == r], last)[k])
            for r in ("North", "West") for k in ("damage_rate", "inventory_accuracy", "on_time_rate")]
    assert "Off target" not in calm, calm

    flags = find_anomalies(kpi)
    got = set(zip(flags.region, flags.metric))
    assert {("East", "damage_rate"), ("South", "inventory_accuracy")} <= got, got  # the planted anomalies
    assert not {("North", "damage_rate"), ("West", "damage_rate")} & got, got

    wk = q("SELECT SUM(forecast_abs_error) / SUM(outbound_units) AS wape FROM gold.forecast_weekly")
    assert 0 <= wk.wape[0] < 1
    weekly = q("SELECT * FROM gold.forecast_weekly WHERE warehouse = 'WH_1'")
    weekly["week_start"] = pd.to_datetime(weekly.week_start)
    fc = forecast(weekly.set_index("week_start").sort_index()[["outbound_units", "inbound_units"]])
    assert len(fc) == 12 and fc.notna().all().all()
    assert (forecast(weekly.set_index("week_start").sort_index()[["outbound_units"]], scenario=0.1) / fc[["outbound_units"]] - 1.1).abs().max().max() < 1e-9

    recon = q("SELECT * FROM gold.recon")
    types = set(recon.recon_status)
    planted = {"Ordered, not shipped", "Short shipped (ERP vs WMS)", "Missing from 3PL report", "Quantity mismatch (WMS vs 3PL)",
               "Ship date mismatch (WMS vs 3PL)", "Billed more than once", "Billed by 3PL, no WMS record"}
    assert planted <= types, planted - types
    assert {"rate card", "no rate card (median used)", "n/a"} <= set(recon.rate_source.dropna())
    assert recon.order_id.is_unique and 0.8 < (recon.recon_status == "Matched").mean() < 1

    print("Self-check passed.\n" + rule_briefing(flags, q("SELECT * FROM gold.exceptions")))
