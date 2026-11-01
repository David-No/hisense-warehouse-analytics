---
title: Warehouse Operations Analytics
emoji: 📦
colorFrom: blue
colorTo: yellow
sdk: docker
app_port: 8501
pinned: false
---

# Warehouse operations analytics

**Live demo:** https://davidno7-hisense-warehouse-analytics.hf.space  |  **Code:** https://github.com/David-No/hisense-warehouse-analytics

A portfolio dashboard for warehouse and 3PL operations in consumer electronics. It uses **real Kaggle data wherever a dataset covers a need** and simulates data **only for gaps**. Every tab says which is which.

| Tab | Real data (Kaggle) | Simulated (labeled in the app) |
|---|---|---|
| **Forecast and capacity** | Daily SKU sales, inventory, reorder points, replenishment orders for 5 warehouses | Daily capacity (real peak week x assumed headroom); labor hours (22 units/hour assumption) |
| **KPI scorecard** | Units, revenue, shipping cost, storage capacity, utilization, on-time rate (4 regions, 2023-2025) | Cycle counts (inventory accuracy); damaged units |
| **AI insights** | Anomaly checks on the KPI trends | Exception incidents and operator notes, on real warehouses and products, with two planted patterns |
| **Data reconciliation** | 9,215 ERP orders and the freight rate card | WMS shipments and the 3PL billing report, with planted mismatches |

Known limits, also shown in the app: the retail file looks synthetic (evenly spread values), so utilization and on-time sit near 50%; the inventory file has one year and no real stockouts; all Brunel orders share one date, so reconciliation has no trend.

**Data flow (DuckDB medallion):** raw Kaggle files become Parquet in `bronze` (`data/bronze/`), simulated gaps are generated with a fixed seed and added to bronze with `source = 'simulated'`, `silver` cleans and conforms (for example, the 3PL's messy carrier codes), and `gold` holds one table per tab plus `gold.data_sources`. Silver and gold are plain SQL in `SILVER_GOLD` in `build_data.py`. Rates are always computed from summed inputs, never averaged.

## Run locally
```
pip install -r requirements.txt
python build_data.py      # builds warehouse.duckdb from data/bronze/*.parquet
python analytics.py       # self-check
streamlit run app.py
```
To rebuild bronze from the original files, download them into `data/raw/` first:
```
kaggle datasets download -d ziya07/high-dimensional-supply-chain-inventory-dataset -p data/raw/inventory --unzip
kaggle datasets download -d roshaaann30/retail-supply-chain-analytics-dataset -p data/raw/retail --unzip
kaggle datasets download -d omarstat2/supply-chain-logistics-data -p data/raw/brunel --unzip
```
Set `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`, default `gpt-4o-mini`) to turn on AI briefings. Without a key the app writes a rule-based briefing. AI output is a starting point: verify it before acting.

## Data credits
- [High-Dimensional Supply Chain Inventory Dataset](https://www.kaggle.com/datasets/ziya07/high-dimensional-supply-chain-inventory-dataset) by ziya07
- [Retail Supply Chain Analytics Dataset](https://www.kaggle.com/datasets/roshaaann30/retail-supply-chain-analytics-dataset) by roshaaann30
- [Supply Chain Logistics Data](https://www.kaggle.com/datasets/omarstat2/supply-chain-logistics-data) by omarstat2 (Brunel University supply chain case)

Licenses (read from the Kaggle API on 2026-10-31):

| Dataset | License | What it means here |
|---|---|---|
| High-Dimensional Supply Chain Inventory | CC0 1.0 | Public domain; copies and derived tables are fine. |
| Supply Chain Logistics Data (Brunel) | CC0 1.0 | Public domain; copies and derived tables are fine. |
| Retail Supply Chain Analytics | **CC BY-NC-SA 4.0** | Credit the author, non-commercial use only, and share adaptations under the same license. This covers `data/bronze/retail_supply_chain.parquet` and the KPI tables derived from it. |

Code: MIT, see `LICENSE`. The MIT license does not cover the retail data or anything derived from it, which stay under CC BY-NC-SA 4.0. This is a non-commercial portfolio project.
