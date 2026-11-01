"""Generate simulated warehouse data and build warehouse.duckdb (bronze -> silver -> gold).

Run: python build_data.py
Writes the raw source extracts to data/*.csv (open them in Power BI or Excel too).
"""
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "data"
DB = HERE / "warehouse.duckdb"

WAREHOUSES = pd.DataFrame({
    "warehouse": ["Atlanta DC", "Cranbury DC", "Dallas DC", "Ontario DC"],
    "site_code": ["atl", "CRN ", "DAL", "ont"],  # 3PL report codes arrive messy on purpose
    "tpl": ["Apex Logistics", "Bayline Distribution", "Summit Fulfillment", "Meridian 3PL"],
    "size": [1.3, 1.0, 0.8, 1.1],
    "headroom": [1.03, 1.25, 1.22, 1.28],  # capacity vs. last peak; Atlanta runs tight
})
CATEGORIES = pd.DataFrame({
    "category": ["TVs", "Refrigerators", "Air Conditioners", "Freezers", "Dehumidifiers"],
    "base_units": [5200, 1400, 1800, 700, 900],
    "units_per_pallet": [12, 3, 16, 4, 20],
    "unit_value": [480, 950, 360, 320, 190],
    "season": ["holiday", "flat", "summer", "flat", "summer"],
    "sku_prefix": ["TV", "RF", "AC", "FZ", "DH"],
})
WEEKS = pd.date_range("2024-11-04", periods=104, freq="W-MON")
UNITS_PER_LABOR_HOUR = 22

NOTES = {
    "damage": [
        "Forklift tine punctured carton during putaway, aisle {a}",
        "Carton dropped during picking, corner crushed",
        "Water damage near dock door {d} after rain, roof leak reported",
        "Damaged on arrival, carrier noted damage on BOL",
        "Screen cracked, found at outbound QC",
        "Pallet wrap failed, units fell during transfer",
    ],
    "damage_stacking": [
        "Bottom cartons crushed, pallets double-stacked in aisle {a} to free floor space",
        "Top pallet crushed bottom layer, TVs double-stacked during peak build",
        "Load shifted in trailer, double-stacked pallets not braced",
        "Box compression damage on bottom tier after double-stacking",
    ],
    "Count variance": [
        "Cycle count off by {n} units at location {loc}",
        "System qty higher than physical, likely unrecorded damage write-off at {loc}",
    ],
    "variance_slotting": [
        "Units found in wrong location after slotting change, {loc}",
        "Putaway scanned to old slot after re-slot project, count off by {n}",
        "Physical count short {n} units, product located in adjacent re-slotted bay {loc}",
    ],
    "Mis-pick": [
        "Wrong model picked, similar SKU in adjacent slot {loc}",
        "Picked 55-inch instead of 65-inch, carton labels look alike",
    ],
    "Short ship": [
        "Short shipped {n} units, inventory not at pick face",
        "Order released before replenishment completed",
    ],
}


def seasonality(week_of_year, kind):
    w = np.asarray(week_of_year, dtype=float)
    if kind == "holiday":  # Black Friday through Christmas, plus a Super Bowl bump
        return 1 + 0.9 * np.exp(-((w - 48) / 3) ** 2) + 0.3 * np.exp(-((w - 5) / 1.5) ** 2)
    if kind == "summer":
        return 1 + 1.0 * np.exp(-((w - 25) / 6) ** 2)
    return 1 + 0.08 * np.sin(w / 52 * 2 * np.pi)


def generate(seed=42):
    rng = np.random.default_rng(seed)
    horizon = pd.date_range(WEEKS[0], periods=len(WEEKS) + 4, freq="W-MON")  # demand looks 4 weeks ahead
    growth = 1.08 ** (np.arange(len(horizon)) / 52)

    weekly = []
    for w in WAREHOUSES.itertuples():
        for c in CATEGORIES.itertuples():
            demand = (c.base_units * w.size * seasonality(horizon.isocalendar().week, c.season)
                      * growth * rng.lognormal(0, 0.07, len(horizon)))
            outbound = demand[: len(WEEKS)]
            forward = np.array([demand[i + 1:i + 5].mean() for i in range(len(WEEKS))])
            on_hand = 5 * forward * rng.lognormal(0, 0.04, len(WEEKS))  # ~5 weeks of cover
            inbound = np.clip(outbound + np.diff(on_hand, prepend=on_hand[0]), 0, None)
            weekly.append(pd.DataFrame({
                "week_start": WEEKS.date, "warehouse": w.warehouse, "category": c.category,
                "inbound_units": inbound.round().astype(int), "outbound_units": outbound.round().astype(int),
                "on_hand_units": on_hand.round().astype(int),
                "pallets_stored": (on_hand / c.units_per_pallet).round().astype(int),
                "sales_revenue": (outbound * c.unit_value).round(2),
            }))
    weekly = pd.concat(weekly, ignore_index=True)
    weekly["labor_hours"] = ((weekly.inbound_units + weekly.outbound_units) / UNITS_PER_LABOR_HOUR
                             * rng.lognormal(0, 0.05, len(weekly))).round(1)
    weekly["month"] = pd.to_datetime(weekly.week_start).dt.strftime("%Y-%m")

    peak = weekly.groupby(["warehouse", "week_start"]).pallets_stored.sum().groupby("warehouse").max()
    warehouses = WAREHOUSES.assign(pallet_capacity=lambda d: (d.warehouse.map(peak) * d.headroom).round(-2).astype(int))

    monthly = (weekly.groupby(["month", "warehouse"])
               .agg(units_received=("inbound_units", "sum"), units_shipped=("outbound_units", "sum"),
                    pallet_weeks=("pallets_stored", "sum"), weeks=("week_start", "nunique"))
               .reset_index())
    months = sorted(monthly.month.unique())
    m_idx = monthly.month.map({m: i for i, m in enumerate(months)})
    recent = lambda n: m_idx >= len(months) - n  # noqa: E731
    holiday = monthly.month.str[-2:].isin(["11", "12"])
    n = len(monthly)

    costs = monthly[["month", "warehouse"]].assign(
        storage_cost=(monthly.pallet_weeks / monthly.weeks * 16.5 * rng.normal(1, .03, n)).round(2),
        handling_cost=((monthly.units_received + monthly.units_shipped) * 1.85 * rng.normal(1, .03, n)).round(2),
        transport_cost=(monthly.units_shipped * 8.4 * rng.normal(1, .04, n)).round(2),
    )

    accuracy = rng.normal(0.985, 0.003, n)
    accuracy[(monthly.warehouse == "Dallas DC") & recent(4)] = rng.normal(0.958, 0.004, (( monthly.warehouse == "Dallas DC") & recent(4)).sum())
    on_time = rng.normal(0.965, 0.006, n) - np.where(holiday & monthly.warehouse.eq("Atlanta DC"), 0.035, 0)
    counted = rng.integers(1700, 2000, n)
    ops = monthly[["month", "warehouse"]].assign(
        locations_counted=counted,
        locations_accurate=(counted * accuracy.clip(0, 1)).round().astype(int),
        units_shipped_on_time=(monthly.units_shipped * on_time.clip(0, 1)).round().astype(int),
    )

    # Exceptions: one row per incident, with the kind of free-text note an operator writes.
    cat_mix = weekly.groupby("category").outbound_units.sum() / weekly.outbound_units.sum()
    sku_value = dict(zip(CATEGORIES.category, CATEGORIES.unit_value))
    rows = []
    for m in monthly.itertuples():
        i = months.index(m.month)
        handled = m.units_received + m.units_shipped
        stacking_spike = m.warehouse == "Cranbury DC" and i >= len(months) - 6
        slotting_spike = m.warehouse == "Dallas DC" and i >= len(months) - 4
        damage_rate = 0.0022 + (0.0007 * (i - (len(months) - 7)) if stacking_spike else 0)
        plan = {"Damage": int(handled * damage_rate / 4),  # ~4 units per damage incident
                "Count variance": rng.poisson(28 if slotting_spike else 6),
                "Mis-pick": rng.poisson(9), "Short ship": rng.poisson(5)}
        start = pd.Timestamp(m.month + "-01")
        for etype, count in plan.items():
            for _ in range(count):
                if etype == "Damage":
                    pool = NOTES["damage_stacking"] if stacking_spike and rng.random() < 0.65 else NOTES["damage"]
                    cat = "TVs" if pool is NOTES["damage_stacking"] else rng.choice(cat_mix.index, p=cat_mix.values)
                    units = int(rng.integers(1, 8))
                    cost = units * sku_value[cat]
                elif etype == "Count variance":
                    pool = NOTES["variance_slotting"] if slotting_spike and rng.random() < 0.7 else NOTES["Count variance"]
                    cat, units, cost = rng.choice(cat_mix.index, p=cat_mix.values), int(rng.integers(1, 15)), 0.0
                else:
                    pool = NOTES[etype]
                    cat, units = rng.choice(cat_mix.index, p=cat_mix.values), int(rng.integers(1, 6))
                    cost = 95.0 if etype == "Mis-pick" else 0.0
                note = rng.choice(pool).format(a=rng.integers(1, 40), d=rng.integers(1, 24), n=units,
                                               loc=f"{rng.choice(list('ABCDEFG'))}-{rng.integers(1, 40):02d}-{rng.integers(1, 6)}")
                rows.append({"incident_date": (start + pd.Timedelta(days=int(rng.integers(0, 28)))).date(),
                             "month": m.month, "warehouse": m.warehouse, "exception_type": etype,
                             "category": cat, "units": units, "cost": round(cost, 2), "note": note})
    exceptions = pd.DataFrame(rows).sort_values("incident_date").reset_index(drop=True)

    # Reconciliation: the same last 8 weeks of orders as ERP, WMS, and the 3PL each see them.
    skus = [f"{p}-{s}" for p in CATEGORIES.sku_prefix for s in ("U6", "U7", "U8", "Q6", "A4")]
    end = pd.Timestamp(WEEKS[-1]) + pd.Timedelta(days=4)
    erp, wms, tpl = [], [], []
    order_no = 100000
    for w in warehouses.itertuples():
        noisy = 2.5 if w.warehouse == "Cranbury DC" else 1.0  # Bayline's reporting is the messiest
        for _ in range(int(1200 * w.size)):
            order_no += 1
            oid = f"SO-{order_no}"
            promised = end - pd.Timedelta(days=int(rng.integers(3, 56)))
            qty = int(rng.integers(8, 120))
            erp.append({"order_id": oid, "warehouse": w.warehouse, "sku": rng.choice(skus),
                        "qty_ordered": qty, "promised_ship_date": promised.date()})
            if rng.random() < 0.010:
                continue  # ordered, never shipped
            shipped = qty - int(rng.integers(1, 6)) if rng.random() < 0.008 else qty
            ship_date = promised + pd.Timedelta(days=int(rng.choice([0, 0, 0, 0, 0, 1, 2])))
            wms.append({"order_id": oid, "warehouse": w.warehouse, "qty_shipped": shipped, "ship_date": ship_date.date()})
            r = rng.random() / noisy
            if r < 0.020:
                continue  # missing from 3PL report
            reported = shipped + int(rng.choice([-3, -2, -1, 1, 2])) if r < 0.032 else shipped
            reported_date = ship_date + pd.Timedelta(days=int(rng.integers(1, 4))) if 0.032 <= r < 0.047 else ship_date
            line = {"ref_no": oid, "site": w.site_code, "ship_qty": reported, "ship_dt": reported_date.date(),
                    "billed_amount": round(reported * 4.25, 2)}
            tpl.append(line)
            if 0.047 <= r < 0.052:
                tpl.append(dict(line))  # billed twice
        for _ in range(int(6 * noisy)):  # billed, but no such shipment exists
            qty = int(rng.integers(10, 90))
            tpl.append({"ref_no": f"SO-9{rng.integers(10000, 99999)}", "site": w.site_code, "ship_qty": qty,
                        "ship_dt": (end - pd.Timedelta(days=int(rng.integers(3, 56)))).date(),
                        "billed_amount": round(qty * 4.25, 2)})

    return {
        "warehouses": warehouses.drop(columns=["size", "headroom"]),
        "weekly_volume": weekly,
        "logistics_costs": costs,
        "ops_monthly": ops,
        "exceptions": exceptions,
        "erp_orders": pd.DataFrame(erp),
        "wms_shipments": pd.DataFrame(wms),
        "tpl_report": pd.DataFrame(tpl),
    }


# Silver and gold are plain SQL so the logic is easy to read and reuse.
SILVER_GOLD = [
    """CREATE TABLE silver.warehouses AS
       SELECT TRIM(warehouse) AS warehouse, UPPER(TRIM(site_code)) AS site_code, TRIM(tpl) AS tpl, pallet_capacity
       FROM bronze.warehouses""",
    "CREATE TABLE silver.weekly_volume AS SELECT * FROM bronze.weekly_volume",
    "CREATE TABLE silver.logistics_costs AS SELECT * FROM bronze.logistics_costs",
    "CREATE TABLE silver.ops_monthly AS SELECT * FROM bronze.ops_monthly",
    "CREATE TABLE silver.exceptions AS SELECT * FROM bronze.exceptions",
    "CREATE TABLE silver.erp_orders AS SELECT * FROM bronze.erp_orders",
    "CREATE TABLE silver.wms_shipments AS SELECT * FROM bronze.wms_shipments",
    # Conform the 3PL report: map its site codes to our warehouses and roll lines up to one row per order.
    """CREATE TABLE silver.tpl_shipments AS
       SELECT r.ref_no AS order_id, w.warehouse, SUM(r.ship_qty) AS qty_reported, MIN(r.ship_dt) AS ship_date,
              COUNT(*) AS line_count, SUM(r.billed_amount) AS billed_amount
       FROM bronze.tpl_report r JOIN silver.warehouses w ON UPPER(TRIM(r.site)) = w.site_code
       GROUP BY r.ref_no, w.warehouse""",
    """CREATE TABLE gold.weekly_volume AS
       SELECT v.*, w.tpl, w.pallet_capacity FROM silver.weekly_volume v JOIN silver.warehouses w USING (warehouse)""",
    """CREATE TABLE gold.exceptions AS
       SELECT e.*, w.tpl FROM silver.exceptions e JOIN silver.warehouses w USING (warehouse)""",
    """CREATE TABLE gold.kpi_monthly AS
       WITH wk AS (
           SELECT week_start, month, warehouse, SUM(inbound_units) AS inbound_units, SUM(outbound_units) AS outbound_units,
                  SUM(pallets_stored) AS pallets, SUM(sales_revenue) AS sales_revenue, SUM(labor_hours) AS labor_hours
           FROM silver.weekly_volume GROUP BY week_start, month, warehouse),
       vol AS (
           SELECT month, warehouse, SUM(inbound_units) AS units_received, SUM(outbound_units) AS units_shipped,
                  SUM(sales_revenue) AS sales_revenue, SUM(labor_hours) AS labor_hours, AVG(pallets) AS avg_pallets
           FROM wk GROUP BY month, warehouse),
       dmg AS (
           SELECT month, warehouse, SUM(units) AS units_damaged, SUM(cost) AS damage_cost
           FROM silver.exceptions WHERE exception_type = 'Damage' GROUP BY month, warehouse)
       SELECT v.month, v.warehouse, w.tpl, v.units_received, v.units_shipped,
              v.units_received + v.units_shipped AS units_handled, v.sales_revenue, v.labor_hours,
              c.storage_cost + c.handling_cost + c.transport_cost AS logistics_cost,
              COALESCE(d.units_damaged, 0) AS units_damaged, COALESCE(d.damage_cost, 0) AS damage_cost,
              o.locations_counted, o.locations_accurate, o.units_shipped_on_time,
              v.avg_pallets, w.pallet_capacity,
              (c.storage_cost + c.handling_cost + c.transport_cost) * 1.0 / v.sales_revenue AS logistics_expense_rate,
              o.locations_accurate * 1.0 / o.locations_counted AS inventory_accuracy,
              COALESCE(d.units_damaged, 0) * 1.0 / (v.units_received + v.units_shipped) AS damage_rate,
              v.avg_pallets * 1.0 / w.pallet_capacity AS utilization,
              o.units_shipped_on_time * 1.0 / v.units_shipped AS on_time_rate,
              (v.units_received + v.units_shipped) * 1.0 / v.labor_hours AS units_per_labor_hour
       FROM vol v
       JOIN silver.warehouses w USING (warehouse)
       JOIN silver.logistics_costs c USING (month, warehouse)
       JOIN silver.ops_monthly o USING (month, warehouse)
       LEFT JOIN dmg d USING (month, warehouse)""",
    # Three-way match: ERP (what was ordered) vs. WMS (what we shipped) vs. 3PL report (what they billed).
    """CREATE TABLE gold.recon AS
       SELECT r.*, w.tpl FROM (
           SELECT COALESCE(e.order_id, m.order_id, t.order_id) AS order_id,
                  COALESCE(e.warehouse, m.warehouse, t.warehouse) AS warehouse,
                  e.sku, e.qty_ordered, m.qty_shipped, t.qty_reported,
                  e.promised_ship_date, m.ship_date AS wms_ship_date, t.ship_date AS tpl_ship_date,
                  t.line_count, t.billed_amount,
                  CASE
                      WHEN m.order_id IS NULL AND t.order_id IS NOT NULL THEN 'Billed by 3PL, no WMS record'
                      WHEN m.order_id IS NULL THEN 'Ordered, not shipped'
                      WHEN t.order_id IS NULL THEN 'Missing from 3PL report'
                      WHEN t.line_count > 1 THEN 'Billed more than once'
                      WHEN m.qty_shipped <> t.qty_reported THEN 'Quantity mismatch (WMS vs 3PL)'
                      WHEN m.qty_shipped < e.qty_ordered THEN 'Short shipped (ERP vs WMS)'
                      WHEN m.ship_date <> t.ship_date THEN 'Ship date mismatch (WMS vs 3PL)'
                      ELSE 'Matched'
                  END AS recon_status
           FROM silver.erp_orders e
           FULL OUTER JOIN silver.wms_shipments m ON e.order_id = m.order_id
           FULL OUTER JOIN silver.tpl_shipments t ON COALESCE(e.order_id, m.order_id) = t.order_id
       ) r JOIN silver.warehouses w USING (warehouse)""",
]


def build(db_path=DB):
    import duckdb

    DATA.mkdir(exist_ok=True)
    frames = generate()
    for name, df in frames.items():
        df.to_csv(DATA / f"{name}.csv", index=False)
    Path(db_path).unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    for schema in ("bronze", "silver", "gold"):
        con.execute(f"CREATE SCHEMA {schema}")
    for name in frames:  # bronze = raw extracts, loaded as-is
        con.execute(f"CREATE TABLE bronze.{name} AS SELECT * FROM read_csv_auto('{(DATA / f'{name}.csv').as_posix()}')")
    for sql in SILVER_GOLD:
        con.execute(sql)
    con.close()
    print(f"Built {db_path} from {len(frames)} source files in {DATA}")


if __name__ == "__main__":
    build()
