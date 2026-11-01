"""Analytics used by the app. Pure pandas, so it can be tested without Streamlit."""
import os

import pandas as pd

# key: (label, target, watch, higher_is_better, format)
KPIS = {
    "logistics_expense_rate": ("Logistics expense rate", 0.030, 0.032, False, "{:.2%}"),
    "inventory_accuracy": ("Inventory accuracy", 0.980, 0.970, True, "{:.1%}"),
    "damage_rate": ("Damage rate", 0.0025, 0.0035, False, "{:.2%}"),
    "utilization": ("Warehouse utilization", 0.85, 0.90, False, "{:.1%}"),
    "on_time_rate": ("On-time shipping", 0.95, 0.93, True, "{:.1%}"),
    "units_per_labor_hour": ("Units per labor hour", 22.0, 21.0, True, "{:.1f}"),
}


def rollup(df):
    """Network-level KPIs from summed inputs (never average the rates)."""
    s = df.sum(numeric_only=True)
    return {
        "logistics_expense_rate": s.logistics_cost / s.sales_revenue,
        "inventory_accuracy": s.locations_accurate / s.locations_counted,
        "damage_rate": s.units_damaged / s.units_handled,
        "utilization": s.avg_pallets / s.pallet_capacity,
        "on_time_rate": s.units_shipped_on_time / s.units_shipped,
        "units_per_labor_hour": s.units_handled / s.labor_hours,
    }


def status(key, value):
    _, target, watch, higher, _ = KPIS[key]
    good = value >= target if higher else value <= target
    ok = value >= watch if higher else value <= watch
    return "On target" if good else "Watch" if ok else "Off target"


def forecast(weekly, horizon=12, scenario=0.0):
    """Seasonal baseline: the same week last year, scaled by the last 12 weeks' year-over-year trend.

    weekly: DataFrame indexed by week_start (Timestamp), numeric columns.
    ponytail: seasonal-naive with trend, swap in Prophet or ETS if accuracy needs to improve.
    """
    last = weekly.index.max()
    week = pd.Timedelta(weeks=1)
    recent = weekly.loc[last - 11 * week:last].sum()
    prior = weekly.loc[last - 63 * week:last - 52 * week].sum()
    future = [last + k * week for k in range(1, horizon + 1)]
    base = weekly.reindex([f - 52 * week for f in future]).to_numpy()
    out = pd.DataFrame(base * (recent / prior).to_numpy() * (1 + scenario), index=future, columns=weekly.columns)
    out.index.name = weekly.index.name
    return out


# metric: (higher_is_bad, minimum change worth flagging)
WATCHED = {"damage_rate": (True, 0.0005), "inventory_accuracy": (False, 0.01), "on_time_rate": (False, 0.015)}


def find_anomalies(kpi, z_limit=3.0):
    """Compare each warehouse's last 3 months with the 6 months before the last 6 (a clean baseline)."""
    flags = []
    for wh, d in kpi.sort_values("month").groupby("warehouse"):
        recent, base = d.tail(3), d.iloc[-12:-6]
        for metric, (higher_bad, min_change) in WATCHED.items():
            r, b, sd = recent[metric].mean(), base[metric].mean(), base[metric].std() or 1e-9
            change = r - b if higher_bad else b - r
            z = change / sd
            if z >= z_limit and change >= min_change:
                flags.append({"warehouse": wh, "tpl": d.tpl.iloc[0], "metric": metric, "recent": r,
                              "baseline": b, "z": round(z, 1),
                              "message": f"{wh} ({d.tpl.iloc[0]}): {KPIS[metric][0].lower()} is "
                                         f"{KPIS[metric][4].format(r)} over the last 3 months vs. "
                                         f"{KPIS[metric][4].format(b)} baseline"})
        util = d.utilization.iloc[-1]
        if util > KPIS["utilization"][2]:
            flags.append({"warehouse": wh, "tpl": d.tpl.iloc[0], "metric": "utilization", "recent": util,
                          "baseline": d.utilization.mean(), "z": None,
                          "message": f"{wh}: utilization is {util:.1%} this month, above the 90% limit"})
    return pd.DataFrame(flags)


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
    if flags.empty:
        return "No KPI is outside its normal range right now."
    lines = []
    for f in flags.itertuples():
        line = f"- **{f.message}.**"
        etype = METRIC_EXCEPTION.get(f.metric)
        if etype:
            recent_months = sorted(exceptions.month.unique())[-3:]
            notes = exceptions[(exceptions.warehouse == f.warehouse) & (exceptions.exception_type == etype)
                               & exceptions.month.isin(recent_months)].note
            themes = note_themes(notes)
            if len(themes):
                top = themes.index[0]
                line += (f" The most common note theme is {top.lower()} ({themes.iloc[0]} of {len(notes)} "
                         f"incidents). Next step: {THEMES[top][1]}")
        lines.append(line)
    return "\n".join(lines)


def ai_context(flags, kpi, exceptions):
    months = sorted(kpi.month.unique())[-3:]
    flagged = flags.warehouse.unique() if not flags.empty else []
    notes = exceptions[exceptions.month.isin(months) & exceptions.warehouse.isin(flagged)]
    sample = notes.groupby(["warehouse", "exception_type"]).head(8)[["warehouse", "exception_type", "category", "note"]]
    latest = kpi[kpi.month == months[-1]].drop(columns=["month"]).round(4)
    return ("Latest month KPIs by warehouse:\n" + latest.to_csv(index=False)
            + "\nFlags from statistical checks:\n" + (flags.drop(columns="message").to_csv(index=False) if not flags.empty else "none\n")
            + "\nSample exception notes from flagged warehouses, last 3 months:\n" + sample.to_csv(index=False))


def ask_ai(question, context):
    """Call OpenAI. Returns None when no API key is configured."""
    if not os.getenv("OPENAI_API_KEY"):
        return None
    from openai import OpenAI

    reply = OpenAI().chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": (
                "You are a logistics analytics assistant for a consumer electronics distribution network run "
                "through 3PL partners. Use only the data provided. Be concise and practical: what is happening, "
                "the likely root cause, the recommended action, and what to verify before acting. "
                "Say so when the data does not support a conclusion.")},
            {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
        ],
        temperature=0.2,
    )
    return reply.choices[0].message.content


if __name__ == "__main__":  # self-check against the built database
    import duckdb

    con = duckdb.connect("warehouse.duckdb", read_only=True)
    kpi = con.execute("SELECT * FROM gold.kpi_monthly").df()
    flags = find_anomalies(kpi)
    assert {("Cranbury DC", "damage_rate"), ("Dallas DC", "inventory_accuracy")} <= set(zip(flags.warehouse, flags.metric))
    weekly = con.execute("SELECT week_start, SUM(outbound_units) AS outbound_units FROM gold.weekly_volume "
                         "WHERE warehouse = 'Atlanta DC' GROUP BY 1").df()
    weekly["week_start"] = pd.to_datetime(weekly.week_start)
    assert len(forecast(weekly.set_index("week_start").sort_index())) == 12
    print("Self-check passed.\n" + rule_briefing(flags, con.execute("SELECT * FROM gold.exceptions").df()))
