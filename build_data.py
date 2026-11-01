"""Build warehouse.duckdb: bronze (Kaggle + simulated gaps) -> silver -> gold.

Run: python build_data.py
Kaggle CSVs in data/raw/ are converted to data/bronze/*.parquet when present; otherwise the committed Parquet is used.
Simulated gap tables are written to data/bronze/sim_*.parquet. Fixed seed, so every build is identical.
"""
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
RAW, BRONZE = HERE / "data" / "raw", HERE / "data" / "bronze"
DB = HERE / "warehouse.duckdb"
SEED = 42
UNITS_PER_LABOR_HOUR = 22  # assumption, shown in the app

# bronze table -> raw CSV. Columns are kept exactly as the file has them.
KAGGLE = {
    "inv_supply_chain": "inventory/supply_chain_dataset1.csv",
    "retail_supply_chain": "retail/Retail_Supply_Chain_Analytics_Dataset.csv",
    **{f"bru_{n}": f"brunel/{f}.csv" for n, f in [
        ("order_list", "OrderList"), ("freight_rates", "FreightRates"), ("wh_capacities", "WhCapacities"),
        ("wh_costs", "WhCosts"), ("plant_ports", "PlantPorts"), ("products_per_plant", "ProductsPerPlant"),
        ("vmi_customers", "VmiCustomers")]},
}

NOTES = {
    "Damage": [
        "Forklift tine punctured carton during putaway, aisle {a}",
        "Carton dropped during picking, corner crushed",
        "Water damage near dock door {d} after rain, roof leak reported",
        "Damaged on arrival, carrier noted damage on BOL",
        "Screen cracked, found at outbound QC",
        "Pallet wrap failed, units fell during transfer",
    ],
    "Damage (stacking)": [  # planted pattern: double-stacked pallets
        "Bottom cartons crushed, pallets double-stacked in aisle {a} to free floor space",
        "Top pallet crushed bottom layer, TVs double-stacked during peak build",
        "Load shifted in trailer, double-stacked pallets not braced",
        "Box compression damage on bottom tier after double-stacking",
    ],
    "Count variance": [
        "Cycle count off by {n} units at location {loc}",
        "System qty higher than physical, likely unrecorded damage write-off at {loc}",
    ],
    "Count variance (slotting)": [  # planted pattern: re-slot project
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
LOT = 150  # average units per damage incident (a damaged lot, not a single carton)


# ---------------------------------------------------------------- simulated gaps (one function per gap)
def sim_headroom(con, rng):
    """Capacity headroom per inventory warehouse (assumption). WH_1 runs tight on purpose."""
    whs = con.execute("SELECT DISTINCT Warehouse_ID FROM bronze.inv_supply_chain ORDER BY 1").df().Warehouse_ID
    return pd.DataFrame({"warehouse": whs, "headroom": [1.05, 1.15, 1.30, 1.20, 1.10][:len(whs)]})


def sim_cycle_counts(con, rng):
    """Locations counted vs. accurate per retail region-month. South dips in the last 4 months."""
    rm = con.execute("""SELECT DISTINCT strftime(CAST("Date" AS DATE), '%Y-%m') AS month, Warehouse_Location AS region
                        FROM bronze.retail_supply_chain ORDER BY 1, 2""").df()
    months = sorted(rm.month.unique())
    dip = (rm.region == "South") & rm.month.isin(months[-4:])
    acc = np.where(dip, rng.normal(0.958, 0.004, len(rm)), rng.normal(0.985, 0.003, len(rm))).clip(0, 1)
    counted = rng.integers(1700, 2000, len(rm))
    return rm.assign(locations_counted=counted, locations_accurate=np.round(counted * acc).astype(int))


def sim_exceptions(con, rng):
    """Incidents with operator notes, drawn from real retail warehouses and products.
    Planted: East damage climbs for 6 months at 3 warehouses (double-stacking); South count variance for 4 (re-slotting)."""
    r = con.execute("""SELECT Product_ID AS product_id, Product_Category AS category, Warehouse_ID AS warehouse_id,
                              Warehouse_Location AS region, strftime(CAST("Date" AS DATE), '%Y-%m') AS month,
                              Units_Sold AS units, Product_Cost_USD AS unit_cost FROM bronze.retail_supply_chain""").df()
    months = sorted(r.month.unique())
    east_tv = r[(r.region == "East") & (r.category == "Electronics")]
    pool = east_tv[east_tv.warehouse_id.isin(east_tv.warehouse_id.value_counts().index[:3])]
    region_factor = dict(zip(sorted(r.region.unique()), [1.0, 0.9, 1.05, 1.1]))
    rows = []

    def add(g, picks, month, etype, pool_key, units):
        start = pd.Timestamp(month + "-01")
        for (_, p), u in zip(picks.iterrows(), units):
            loc = f"{rng.choice(list('ABCDEFG'))}-{rng.integers(1, 40):02d}-{rng.integers(1, 6)}"
            note = str(rng.choice(NOTES[pool_key])).format(a=rng.integers(1, 40), d=rng.integers(1, 24), n=int(u), loc=loc)
            rows.append({"incident_date": (start + pd.Timedelta(days=int(rng.integers(0, 28)))).date(), "month": month,
                         "region": p.region, "warehouse_id": p.warehouse_id, "product_id": p.product_id,
                         "category": p.category, "exception_type": etype, "units": int(u),
                         "cost": round(int(u) * p.unit_cost, 2) if etype == "Damage" else (95.0 if etype == "Mis-pick" else 0.0),
                         "note": note})

    for (month, region), g in r.groupby(["month", "region"]):
        handled, w = g.units.sum(), g.units.to_numpy() / g.units.sum()
        pick = lambda n: g.iloc[rng.choice(len(g), n, p=w)]  # noqa: E731
        n = rng.poisson(handled * 0.0022 * region_factor[region] / LOT)
        add(g, pick(n), month, "Damage", "Damage", np.maximum(1, rng.poisson(LOT, n)))
        if region == "East" and month in months[-6:]:
            k = months[-6:].index(month) + 1
            n = rng.poisson(handled * 0.0007 * k / LOT)
            add(g, pool.iloc[rng.choice(len(pool), n)], month, "Damage", "Damage (stacking)", np.maximum(1, rng.poisson(LOT, n)))
        slot = region == "South" and month in months[-4:]
        n = rng.poisson(28 if slot else 6)
        add(g, pick(n), month, "Count variance", "Count variance (slotting)" if slot else "Count variance", rng.integers(1, 15, n))
        for etype, lam in (("Mis-pick", 9), ("Short ship", 5)):
            n = rng.poisson(lam)
            add(g, pick(n), month, etype, etype, rng.integers(1, 6, n))
    return pd.DataFrame(rows).sort_values("incident_date").reset_index(drop=True)


def sim_wms_and_billing(con, rng):
    """WMS shipments and the 3PL billing report for the real Brunel orders, with planted mismatches.
    Billed amounts come from the real FreightRates card (rate x weight, minimum charge)."""
    o = con.execute("""SELECT "Order ID" AS oid, "Order Date" AS d, Carrier AS carrier, "Service Level" AS svc,
                              "Origin Port" AS port, "Unit quantity" AS qty, Weight AS w FROM bronze.bru_order_list""").df()
    fr = con.execute("SELECT * FROM bronze.bru_freight_rates").df()
    n = len(o)
    o["id"] = o.oid.map("{:.1f}".format)

    m = o.reset_index().merge(fr, left_on=["carrier", "port", "svc"], right_on=["Carrier", "orig_port_cd", "svc_cd"])
    m = m[(m.w >= m.minm_wgh_qty) & (m.w <= m.max_wgh_qty)].drop_duplicates("index").set_index("index")
    o["charge"] = (np.maximum(m["minimum cost"], m.rate * m.w).reindex(o.index)
                   .fillna(np.maximum(fr["minimum cost"].median(), fr.rate.median() * o.w)))  # unrated carrier/service: median rate

    noisy = np.where(o.carrier == "V44_3", 2.5, 1.0)  # planted: this carrier's reporting is messiest
    no_wms = rng.random(n) < 0.010
    short = rng.integers(1, 6, n) * (rng.random(n) < 0.008)
    wms = o.assign(qty_shipped=np.maximum(1, o.qty - short),
                   ship_date=o.d + pd.to_timedelta(rng.choice([0, 0, 0, 0, 0, 1, 2], n), unit="D"))[~no_wms]
    wms_out = wms[["id", "qty_shipped", "ship_date"]].rename(columns={"id": "order_id"}).assign(source="simulated")

    r = (rng.random(len(wms)) / noisy[~no_wms])
    lines = wms[r >= 0.020].copy()  # r < 0.020: never billed
    r = r[r >= 0.020]
    lines["ship_qty"] = np.where(r < 0.032, np.maximum(1, lines.qty_shipped + rng.choice([-3, -2, -1, 1, 2], len(lines))), lines.qty_shipped)
    lines["ship_dt"] = lines.ship_date + pd.to_timedelta(np.where((r >= 0.032) & (r < 0.047), rng.integers(1, 4, len(lines)), 0), unit="D")
    dup = lines[(r >= 0.047) & (r < 0.052)]  # billed twice
    lines = pd.concat([lines, dup])

    def messy_code(c):
        return str(rng.choice([c, c.lower() + " ", c.replace("_", "-"), " " + c]))

    bill = pd.DataFrame({
        "invoice_ref": [f"{rng.choice(['ORD-', 'ord-', '', ' '])}{i}{rng.choice(['', ' '])}" for i in lines.id],
        "carrier_code": [messy_code(c) for c in lines.carrier],
        "ship_qty": lines.ship_qty.to_numpy(),
        "ship_dt": [d.strftime("%m/%d/%Y") if rng.random() < 0.2 else d.strftime("%Y-%m-%d") for d in lines.ship_dt],
        "billed_amount": lines.charge.round(2).to_numpy()})
    known, orphans = set(o.id), []
    for _ in range(40):  # charges for shipments that do not exist
        oid = f"{rng.integers(1447000000, 1447500000)}.{rng.integers(0, 10)}"
        if oid not in known:
            c = str(rng.choice(["V444_0", "V444_1", "V44_3"], p=[.4, .3, .3]))
            orphans.append({"invoice_ref": "ORD-" + oid, "carrier_code": messy_code(c), "ship_qty": int(rng.integers(100, 3000)),
                            "ship_dt": o.d.iloc[0].strftime("%Y-%m-%d"), "billed_amount": round(float(rng.uniform(20, 400)), 2)})
    return wms_out, pd.concat([bill, pd.DataFrame(orphans)], ignore_index=True).assign(source="simulated")


# ---------------------------------------------------------------- silver and gold: plain SQL
SILVER_GOLD = [
    # silver: clean names, types, and codes
    """CREATE TABLE silver.inv_daily AS  -- last day (Dec 30) is a partial week, so it is dropped
       SELECT CAST("Date" AS DATE) AS date, SKU_ID AS sku, Warehouse_ID AS warehouse, Units_Sold AS units_sold,
              Inventory_Level AS inventory_level, Reorder_Point AS reorder_point, Order_Quantity AS order_qty
       FROM bronze.inv_supply_chain WHERE CAST("Date" AS DATE) <= DATE '2024-12-29'""",
    """CREATE TABLE silver.retail AS
       SELECT Product_ID AS product_id, Product_Category AS category, Warehouse_ID AS warehouse_id,
              Warehouse_Location AS region, CAST("Date" AS DATE) AS date, strftime(CAST("Date" AS DATE), '%Y-%m') AS month,
              Units_Sold AS units_sold, Storage_Capacity AS storage_capacity, Utilization_Rate AS utilization_rate,
              On_Time_Delivery_Rate AS on_time_rate, Shipping_Cost_USD AS shipping_cost, Revenue_USD AS revenue
       FROM bronze.retail_supply_chain""",
    "CREATE TABLE silver.cycle_counts AS SELECT month, TRIM(region) AS region, locations_counted, locations_accurate FROM bronze.sim_cycle_counts",
    "CREATE TABLE silver.exceptions AS SELECT * EXCLUDE (source) FROM bronze.sim_exceptions",
    """CREATE TABLE silver.erp_orders AS  -- Order ID arrives as a float; keep one decimal as the text key
       SELECT printf('%.1f', "Order ID") AS order_id, "Order Date" AS order_date, TRIM(Carrier) AS carrier,
              "Service Level" AS service_level, "Plant Code" AS plant, "Unit quantity" AS qty_ordered, Weight AS weight
       FROM bronze.bru_order_list""",
    "CREATE TABLE silver.wms_shipments AS SELECT order_id, qty_shipped, CAST(ship_date AS DATE) AS ship_date FROM bronze.sim_wms_shipments",
    # the 3PL report has messy references, carrier codes, and two date formats; roll its lines up to one row per order
    """CREATE TABLE silver.tpl_billing AS
       SELECT regexp_replace(UPPER(TRIM(invoice_ref)), '^ORD-?', '') AS order_id,
              UPPER(TRIM(REPLACE(carrier_code, '-', '_'))) AS carrier, SUM(ship_qty) AS qty_reported,
              MIN(CAST(COALESCE(TRY_STRPTIME(ship_dt, '%Y-%m-%d'), TRY_STRPTIME(ship_dt, '%m/%d/%Y')) AS DATE)) AS ship_date,
              COUNT(*) AS line_count, SUM(billed_amount) AS billed_amount
       FROM bronze.sim_3pl_billing GROUP BY 1, 2""",
    # gold: one table per tab
    f"""CREATE TABLE gold.forecast_weekly AS
       SELECT warehouse, CAST(DATE_TRUNC('week', date) AS DATE) AS week_start,
              SUM(units_sold) AS outbound_units, SUM(order_qty) AS inbound_units,
              SUM(CASE WHEN date = CAST(DATE_TRUNC('week', date) AS DATE) + 6 THEN inventory_level END) AS inventory_units,
              SUM(CAST(inventory_level < reorder_point AS INT)) AS sku_days_below_reorder, COUNT(*) AS sku_days,
              (SUM(units_sold) + SUM(order_qty)) / {UNITS_PER_LABOR_HOUR}.0 AS labor_hours
       FROM silver.inv_daily GROUP BY 1, 2""",
    """CREATE TABLE gold.capacity AS  -- real peak weekly outbound x assumed headroom
       SELECT f.warehouse, MAX(outbound_units) / 7.0 AS peak_daily_units, h.headroom,
              ROUND(MAX(outbound_units) / 7.0 * h.headroom) AS daily_capacity
       FROM gold.forecast_weekly f JOIN bronze.sim_capacity_headroom h USING (warehouse) GROUP BY f.warehouse, h.headroom""",
    """CREATE TABLE gold.kpi_monthly AS
       WITH r AS (
           SELECT month, region, SUM(units_sold) AS units_sold, SUM(revenue) AS revenue, SUM(shipping_cost) AS shipping_cost,
                  SUM(storage_capacity) AS capacity, SUM(storage_capacity * utilization_rate / 100.0) AS occupied,
                  SUM(units_sold * on_time_rate / 100.0) AS units_on_time
           FROM silver.retail GROUP BY month, region),
       d AS (
           SELECT month, region, SUM(units) AS units_damaged, SUM(cost) AS damage_cost
           FROM silver.exceptions WHERE exception_type = 'Damage' GROUP BY month, region)
       SELECT r.month, r.region, r.units_sold, r.revenue, r.shipping_cost, r.capacity, r.occupied, r.units_on_time,
              COALESCE(d.units_damaged, 0) AS units_damaged, COALESCE(d.damage_cost, 0) AS damage_cost,
              c.locations_counted, c.locations_accurate,
              r.shipping_cost / r.revenue AS logistics_expense_rate, r.shipping_cost / r.units_sold AS cost_per_unit,
              c.locations_accurate * 1.0 / c.locations_counted AS inventory_accuracy,
              COALESCE(d.units_damaged, 0) * 1.0 / r.units_sold AS damage_rate,
              r.occupied / r.capacity AS utilization, r.units_on_time / r.units_sold AS on_time_rate
       FROM r JOIN silver.cycle_counts c USING (month, region) LEFT JOIN d USING (month, region)""",
    """CREATE TABLE gold.warehouse_top AS  -- 10 largest retail warehouses by units, all dates (Kaggle KPIs + simulated damage)
       WITH t AS (
           SELECT warehouse_id, SUM(units_sold) AS units_sold, SUM(revenue) AS revenue, SUM(shipping_cost) AS shipping_cost,
                  SUM(storage_capacity) AS capacity, SUM(storage_capacity * utilization_rate / 100.0) AS occupied,
                  SUM(units_sold * on_time_rate / 100.0) AS units_on_time
           FROM silver.retail GROUP BY 1 ORDER BY units_sold DESC LIMIT 10),
       d AS (SELECT warehouse_id, SUM(units) AS units_damaged FROM silver.exceptions WHERE exception_type = 'Damage' GROUP BY 1)
       SELECT t.*, COALESCE(d.units_damaged, 0) AS units_damaged FROM t LEFT JOIN d USING (warehouse_id)""",
    "CREATE TABLE gold.exceptions AS SELECT * FROM silver.exceptions",
    # three-way match: ERP (ordered) vs. WMS (shipped) vs. 3PL report (billed)
    """CREATE TABLE gold.recon AS
       SELECT COALESCE(e.order_id, m.order_id, t.order_id) AS order_id, COALESCE(e.carrier, t.carrier) AS carrier,
              e.service_level, e.plant, e.qty_ordered, m.qty_shipped, t.qty_reported,
              e.order_date, m.ship_date AS wms_ship_date, t.ship_date AS tpl_ship_date, t.line_count, t.billed_amount,
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
       FULL OUTER JOIN silver.tpl_billing t ON COALESCE(e.order_id, m.order_id) = t.order_id""",
    # provenance, shown at the top of each tab
    """CREATE TABLE gold.data_sources AS SELECT * FROM (VALUES
       ('forecast', 'Kaggle', 'Daily SKU sales, inventory, reorder points, and replenishment orders for 5 warehouses (2024)', 'High-Dimensional Supply Chain Inventory (ziya07)'),
       ('forecast', 'Simulated', 'Daily throughput capacity: real peak week x an assumed headroom (the Kaggle capacity file uses different plants)', 'Generated, seed 42'),
       ('forecast', 'Simulated', 'Labor hours: units handled / 22 units per labor hour (an assumption, not measured)', 'Assumption'),
       ('kpi', 'Kaggle', 'Units sold, revenue, shipping cost, storage capacity, utilization, on-time delivery rate', 'Retail Supply Chain Analytics (roshaaann30)'),
       ('kpi', 'Simulated', 'Cycle counts (inventory accuracy) and damaged units (summed from the exception incidents)', 'Generated, seed 42, keyed to the retail regions and warehouses'),
       ('ai', 'Kaggle', 'KPI trends that the anomaly checks run on', 'Retail Supply Chain Analytics (roshaaann30)'),
       ('ai', 'Simulated', 'Exception incidents and operator notes, on real retail warehouses and products', 'Generated, seed 42'),
       ('recon', 'Kaggle', 'ERP orders (9,215) and the freight rate card used to price billing', 'Supply Chain Logistics Data (omarstat2): OrderList, FreightRates'),
       ('recon', 'Simulated', 'WMS shipment records and the 3PL billing report, with planted mismatches', 'Generated, seed 42, keyed to the real order IDs')
       ) t(tab, origin, item, dataset)""",
]


def build(db_path=DB):
    import duckdb

    BRONZE.mkdir(parents=True, exist_ok=True)
    Path(db_path).unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    for schema in ("bronze", "silver", "gold"):
        con.execute(f"CREATE SCHEMA {schema}")

    def to_bronze(name, df=None, csv=None):
        pq = (BRONZE / f"{name}.parquet").as_posix()
        if csv is not None and csv.exists():  # raw files are only present on the dev machine
            con.execute(f"COPY (SELECT * FROM read_csv_auto('{csv.as_posix()}', sample_size=-1)) TO '{pq}' (FORMAT parquet, COMPRESSION zstd)")
        elif df is not None:
            con.register("sim_df", df.assign(source="simulated"))
            con.execute(f"COPY sim_df TO '{pq}' (FORMAT parquet, COMPRESSION zstd)")
            con.unregister("sim_df")
        con.execute(f"CREATE TABLE bronze.{name} AS SELECT * FROM read_parquet('{pq}')")

    for name, csv in KAGGLE.items():
        to_bronze(name, csv=RAW / csv)
    rng = np.random.default_rng(SEED)
    wms, billing = sim_wms_and_billing(con, rng)
    for name, df in {"sim_capacity_headroom": sim_headroom(con, rng), "sim_cycle_counts": sim_cycle_counts(con, rng),
                     "sim_exceptions": sim_exceptions(con, rng), "sim_wms_shipments": wms, "sim_3pl_billing": billing}.items():
        to_bronze(name, df=df)
    for sql in SILVER_GOLD:
        con.execute(sql)
    con.close()
    print(f"Built {db_path}")


if __name__ == "__main__":
    build()
