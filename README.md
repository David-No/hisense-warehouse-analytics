---
title: Warehouse Operations Analytics
emoji: 📦
colorFrom: blue
colorTo: yellow
sdk: docker
app_port: 8501
pinned: false
license: mit
---

# Warehouse operations analytics

A portfolio dashboard for a consumer electronics distribution network: four 3PL-run DCs shipping TVs and appliances. All data is simulated.

**Tabs**
- **KPI scorecard:** logistics expense rate, inventory accuracy, damage rate, utilization, on-time shipping, and labor productivity against targets.
- **Forecast and capacity:** 12-week volume and storage forecast with a what-if slider for volume changes.
- **AI insights:** statistical anomaly flags, root-cause themes from exception notes, and an AI briefing (OpenAI) with a rule-based fallback.
- **Data reconciliation:** three-way match of ERP orders, WMS shipments, and the 3PL billing report.

**Data flow (DuckDB medallion):** raw extracts land in `bronze`, are conformed in `silver` (for example, mapping the 3PL's site codes to warehouses), and become KPI-ready tables in `gold`. See `SILVER_GOLD` in `build_data.py`.

## Run locally
```
pip install -r requirements.txt
python build_data.py      # writes data/*.csv and warehouse.duckdb
python analytics.py       # self-check
streamlit run app.py
```
Set `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`, default `gpt-4o-mini`) to turn on AI briefings.
