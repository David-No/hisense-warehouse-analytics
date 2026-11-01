# Data profile and KPI-to-source map (Phase 0)

Raw files are in `data/raw/` (already downloaded; gitignored). Profiled with pandas.
Short names used below: **INV** = High-Dimensional Supply Chain Inventory (`ziya07`), **RET** = Retail Supply Chain Analytics (`roshaaann30`), **BRU** = Supply Chain Logistics Data (`omarstat2`, Brunel).

## 1. What is actually in the files

### INV `supply_chain_dataset1.csv` — 91,250 rows, 15 cols, no nulls
- Grain: exactly **one row per date x SKU x warehouse** (365 days x 50 SKUs x 5 warehouses). 2024-01-01 to 2024-12-30 (one year only).
- Keys: `Warehouse_ID` WH_1..WH_5, `SKU_ID` SKU_1..SKU_50, `Region` (4 values, **not** tied to a warehouse: each WH has all 4), `Supplier_ID` (10).
- Measures: `Units_Sold` 0-59, `Inventory_Level` 168-990, `Reorder_Point`, `Order_Quantity` (0 on 94.5% of rows; 5,027 non-zero, sum 1.76M vs 1.83M units sold), `Supplier_Lead_Time_Days` 2-14, `Unit_Cost`, `Unit_Price`, `Promotion_Flag`, `Demand_Forecast` (the dataset's own forecast).
- **`Stockout_Flag` is 0 on every row** and `Inventory_Level` never reaches 0. There are no real stockouts. 5.2% of rows are below reorder point (usable as "at risk").
- ~367k units sold per warehouse per year (about 1,005 units/day each). Clear seasonality: monthly units go 177k (Jan), 231k (Mar), 76k (Sep), 132k (Dec).
- No labor, capacity, receipts, or damage columns. `Order_Quantity` is an order **placed**; a receipt would be order date + lead time (derived, not in the file).

### RET `Retail_Supply_Chain_Analytics_Dataset.csv` — 200,000 rows, 41 cols, no nulls
- Grain: **one row per product** (`Product_ID` is unique). Dates are spread across 2023-01-01 to 2025-12-30 (1,095 days). It is a product snapshot, not a warehouse time series.
- 499 `Warehouse_ID`s with 343-457 rows each, spread over 4 `Warehouse_Location`s. Each warehouse ID appears under **all 4 locations**, and `Storage_Capacity` varies by row within a warehouse (so capacity is not a warehouse attribute). Warehouse ID is effectively a random label; **Warehouse_Location (4 regions) is the only stable grouping** (>= 1,258 rows per region-month).
- 5 categories; **Electronics = 39,688 rows**. 4 brands.
- Columns that matter: `Units_Sold`, `Current_Stock`, `Storage_Capacity`, `Utilization_Rate` (0-100, row level), `Shipping_Cost_USD`, `Revenue_USD`, `Profit_USD`, `Product_Cost_USD`, `Selling_Price_USD`, `On_Time_Delivery_Rate` (0-100, row level), `Delivery_Time_Days`, `Transportation_Mode`, `Date`/`Month`/`Quarter`/`Year`.
- **Data-quality caveats, to be shown in the app:** nearly every numeric column is uniformly distributed (means about 50% of range), and `Revenue_USD` does not equal `Units_Sold x Selling_Price_USD` (median ratio 0.05). Shipping cost is 0.10% of revenue overall, so a logistics expense rate computed from it will look unrealistically low. The file looks synthetic even though it is on Kaggle. Labelled "Kaggle" for provenance, but I will not claim it is real operations data.
- No damage, cycle-count, or inbound/receipt columns. `Predicted_Reorder_Quantity` is a model output, not a receipt.

### BRU (Brunel) — 7 CSVs
| File | Rows | Notes |
|---|---|---|
| `OrderList` | 9,215 | **All orders have one order date (2013-05-26)**, so no trend is possible. 3 carriers (V444_0 6,264; V444_1 2,097; V44_3 854), 3 service levels (DTP, DTD, CRF), 7 plants (PLANT03 has 8,541 of them), 772 products, 46 customers. `Ship Late Day count` > 0 on 2.1% of orders. `Order ID` is a float (1447296446.7); must be cast to a string key carefully. |
| `FreightRates` | 1,540 | Carrier x origin port x weight band x service (DTD/DTP only) -> `rate`, `minimum cost`. Matches OrderList carriers V444_0 and V444_1 only; **V44_3 and service CRF have no rate**. |
| `WhCapacities` | 19 | `Daily Capacity` per PLANT (7-1,070). Plant keys, not INV's WH_1..5. |
| `WhCosts` | 19 | `Cost/unit` per PLANT (0.37-2.04). Static, no dates. |
| `PlantPorts`, `ProductsPerPlant`, `VmiCustomers` | 22 / 2,036 / 14 | Reference lookups; not needed for KPIs. |
- Capacity is not comparable to OrderList volumes: PLANT03 has capacity 1,013/day but 8,541 orders averaging 3,203 units in one day. Units of capacity are undefined in the dataset.

## 2. KPI-to-source map

`SIMULATE` = gap, no Kaggle column covers it. "Derived" = computed in SQL from Kaggle columns (still Kaggle-sourced).

### Section 1 — Forecasting and modeling (INV)
| Item | Source | Status |
|---|---|---|
| Weekly outbound volume by warehouse | INV `Units_Sold` summed by week x `Warehouse_ID` | Kaggle |
| Weekly inbound volume | INV `Order_Quantity`, shifted by `Supplier_Lead_Time_Days` to a receipt date | Kaggle (derived) |
| Inventory level | INV `Inventory_Level` (week-end value, not summed across days) | Kaggle |
| Stockouts | INV `Stockout_Flag` is all zero -> use "SKU-days below `Reorder_Point`" and label it **at risk**, not stockout | Kaggle (derived); no simulation |
| 12-week forecast + scenario slider | Model on INV weekly `Units_Sold`; INV `Demand_Forecast` shown as a benchmark | Kaggle |
| Daily capacity per warehouse | BRU `WhCapacities` keyed by PLANT, **no key link to WH_1..5** | **Needs your decision (A)** |
| Labor hours needed | units / assumed productivity (22 units per labor hour) | SIMULATE (assumption, shown in app) |
| Weeks at risk | weekly forecast vs capacity x 7 | Derived |

### Section 3 — KPI management (RET, plus BRU cost)
| KPI | Source | Status |
|---|---|---|
| Logistics expense rate | RET sum(`Shipping_Cost_USD`) / sum(`Revenue_USD`) | Kaggle (derived); about 0.1%, flagged |
| Warehouse utilization | RET sum(`Storage_Capacity` x `Utilization_Rate`/100) / sum(`Storage_Capacity`) | Kaggle (derived) |
| Outbound throughput | RET sum(`Units_Sold`) | Kaggle |
| Inbound throughput | none in RET | **SIMULATE** (or drop, see decision C) |
| Cost per unit | RET sum(`Shipping_Cost_USD`) / sum(`Units_Sold`); BRU `WhCosts` is static by plant, no key link | Kaggle (derived) |
| On-time / service rate | RET sum(`On_Time_Delivery_Rate`/100 x `Units_Sold`) / sum(`Units_Sold`) (unit-weighted, since the column is a row-level %) | Kaggle (derived approximation) |
| Inventory accuracy | no cycle-count data anywhere | **SIMULATE**: locations counted vs. accurate, keyed to RET `Warehouse_Location` x month |
| Damage rate | no damage data anywhere | **SIMULATE**: damaged units from Section 2 exceptions / RET `Units_Sold` |

All rates are computed as sum(numerator) / sum(denominator) at the requested grain (rule 5).

### Section 2 — AI and advanced analytics
| Item | Source | Status |
|---|---|---|
| Anomaly flags (z-score recent vs. baseline) | INV weekly units and RET monthly KPIs | Kaggle |
| Exception incidents + operator notes | none in any file | **SIMULATE**, keyed to RET `Warehouse_Location`/`Warehouse_ID` and real `Product_ID`s, with 1-2 planted patterns |
| Root-cause themes, AI briefing, ask-a-question | notes + flags | Rule-based fallback always; OpenAI when key set |

### Section 4 — Data reconciliation (BRU)
| Item | Source | Status |
|---|---|---|
| ERP record (order ID, date, carrier, service, plant, qty, weight) | BRU `OrderList` (9,215 orders) | Kaggle |
| Expected freight charge | BRU `FreightRates` (`rate` x `Weight`, min charge) for carriers V444_0/V444_1; **V44_3 and CRF have no rate** | Kaggle (derived); 854 orders unrated |
| WMS shipment record | none | **SIMULATE** from OrderList keys, with planted mismatches |
| 3PL billing report | none | **SIMULATE** from OrderList keys, charges seeded from real freight rates, messy codes, duplicates, orphan charges |
| Match rate by carrier | ERP vs WMS vs 3PL | Derived. Only one order date exists, so no date trend |

## 3. Decisions needed before Phase 1

- **A. Capacity for Section 1.** BRU `WhCapacities` is keyed by PLANT01-19, INV by WH_1..5. Mapping them would present Brunel numbers as capacity of warehouses they do not describe. Recommended: simulate capacity for WH_1..5 from INV's real peak daily outbound (for example peak x 1.15), shown as an assumption. The alternative is a separate "benchmark plants" table from BRU.
- **B. Section 3 grain.** RET warehouse IDs are random labels. Recommended: scorecard by the 4 `Warehouse_Location`s (all 3 years, monthly trend), with a drill-down to the 10 largest `Warehouse_ID`s. Optionally filter to Electronics (39,688 rows) for Hisense relevance.
- **C. Inbound throughput.** RET has no receipts. Recommended: drop inbound from the Section 3 scorecard and keep real inbound in Section 1 (INV), rather than simulate it. This changes "eight KPIs" in the checklist to seven.
- **D. Stockouts.** INV has none. Recommended: show "below reorder point" as at-risk, with a note that the source flags zero stockouts.
- **E. BRU `WhCosts`.** No usable key to RET or INV. Recommended: do not use it in KPIs; cost per unit comes from RET.

## 4. Decisions taken (approved by David) and how they were built

- **A. Capacity:** simulated per inventory warehouse as real peak weekly outbound / 7 x an assumed headroom (WH_1 1.05, WH_2 1.15, WH_3 1.30, WH_4 1.20, WH_5 1.10). Table `bronze.sim_capacity_headroom`, result in `gold.capacity`.
- **B. Section 3 grain:** 4 retail regions by month (`gold.kpi_monthly`), plus the 10 largest warehouses over all dates (`gold.warehouse_top`). All product categories (not Electronics only): per-warehouse electronics rows were too thin.
- **C. Inbound throughput:** dropped from Section 3; real inbound (replenishment orders placed) is in Section 1. Orders are used, not receipts shifted by lead time, because receipts would leave the first two weeks of 2024 incomplete.
- **D. Stockouts:** shown as "SKU-days below reorder point".
- **E. Brunel WhCosts / WhCapacities:** loaded to bronze, not used.
- **Not decided earlier, chosen while building:** the first 52 weeks (2024-01-01 to 2024-12-29) are used, because Dec 30 is a partial week. With one year of data the forecast has no growth trend.
- **Targets** on the scorecard are illustrative. The retail file looks synthetic, so on-time and utilization are about 50% in every region.
