# Warehouse Operations Analytics: Claude Code plan

## Context

- **Owner:** David No
- **Purpose:** portfolio app for the Hisense USA *Logistics Planning & Analytics Manager* interview with Brian Kelly (Director of Distribution, 20+ years in warehouse and 3PL operations, consumer electronics). Interview: Monday, Nov 2, 2026, 5:00 PM EST, video.
- **Audience:** an operations leader, not an engineer. The app should speak in outcomes (cost, space, accuracy, damage, service), not tooling.
- **Deliverable:** a Streamlit app on a free Hugging Face Space (Docker SDK), with code on GitHub.

## Project rules (also save this section as CLAUDE.md)

1. **Kaggle first.** Use real Kaggle data wherever it covers a need. Simulate data **only** for gaps, and never simulate something a Kaggle file already has.
2. **Label provenance.** Every tab shows which data is from Kaggle (with the dataset name) and which is simulated. Never present simulated data as real.
3. **One dataset per section.** Sections don't need to share warehouses, products, or dates. Simulated gap data within a section must use that section's real keys (warehouses, products, dates).
4. **Medallion in DuckDB.** `bronze` = raw files as-is, `silver` = cleaned and conformed, `gold` = app-ready tables. Silver and gold logic is plain SQL.
5. **Rates from summed inputs.** Never average a rate across warehouses or months.
6. **Keep it lean.** Few files, no unneeded dependencies, one self-check script. Fixed random seed so results are reproducible.
7. **Responsible AI.** AI output is labeled as a starting point and the app tells the user to verify it before acting. With no API key, fall back to a rule-based summary.

## Starting point

Reuse the existing project (`warehouse-app.zip`). It already works end to end on fully simulated data:

| File | Keep | Change |
|---|---|---|
| `build_data.py` | Medallion build, SQL style, CSV export | Replace simulated sources with Kaggle loaders; keep generators only for gaps |
| `analytics.py` | `rollup`, `status`, `forecast`, `find_anomalies`, `note_themes`, `rule_briefing`, `ask_ai` | Adjust thresholds and column names to the real data |
| `app.py` | Four-tab layout, theme, charts | Point tabs at the new gold tables; add provenance labels |
| `Dockerfile`, `README.md`, `requirements.txt`, `.gitignore`, `.streamlit/config.toml` | All | Add Kaggle credits and license notes to README |

## Data sources

Download with the Kaggle CLI (needs `kaggle.json` from kaggle.com > Settings > API, saved to `%USERPROFILE%\.kaggle\`):

```powershell
pip install kaggle
kaggle datasets download -d ziya07/high-dimensional-supply-chain-inventory-dataset -p data/raw/inventory --unzip
kaggle datasets download -d roshaaann30/retail-supply-chain-analytics-dataset -p data/raw/retail --unzip
kaggle datasets download -d omarstat2/supply-chain-logistics-data -p data/raw/brunel --unzip
```

If a slug fails, search Kaggle for the dataset name and update the command. Check each dataset's license on its Kaggle page and credit it in the README.

## Section design

Columns listed below are expectations from the dataset descriptions. **Verify every one in Phase 0** before building.

### 1. Forecasting and modeling
- **Kaggle:** High-Dimensional Supply Chain Inventory (daily SKU demand/sales, inventory levels, reorder points and quantities, lead times, warehouse and region, stockouts). Brunel `WhCapacities` (daily throughput limit per warehouse).
- **Simulate only if missing:** labor hours, derived from units handled with a stated productivity assumption (for example, 22 units per labor hour), shown in the app as an assumption.
- **Gold:** weekly volume by warehouse (inbound = replenishment receipts, outbound = demand/sales), inventory, stockouts; capacity table.
- **App:** 12-week forecast, scenario slider (volume change %), capacity and labor needed, weeks at risk.

### 2. AI and advanced analytics
- **Kaggle:** reuses the real data from sections 1 and 3 for anomaly detection (z-score of recent vs. baseline).
- **Simulate:** exception incidents with free-text operator notes (damage, mis-pick, short ship, count variance), keyed to section 3's real warehouses and products. Plant one or two clear patterns (for example, rising damage at one warehouse tied to double-stacked pallets) so the AI has something real to find.
- **App:** flags, root-cause themes from notes, AI briefing and "ask a question" (OpenAI, model from `OPENAI_MODEL`, default `gpt-4o-mini`), rule-based fallback.

### 3. KPI management
- **Kaggle:** Retail Supply Chain Analytics (inventory, warehouse utilization, throughput, logistics and financial fields). Brunel `WhCosts` (cost per unit by warehouse) and `FreightRates` (carrier rates) for cost.
- **Simulate only if missing:** cycle counts (locations counted vs. accurate) for **inventory accuracy**; damaged units for **damage rate** (aggregate from section 2's simulated exceptions so the two tabs agree).
- **KPIs:** logistics expense rate (logistics cost / sales), inventory accuracy, damage rate (damaged / handled), warehouse utilization (occupied / capacity), inbound and outbound throughput, cost per unit, on-time / service rate.
- **App:** scorecard with targets and on-target / watch / off-target status, trend by warehouse, table by warehouse.

### 4. Data reconciliation
- **Kaggle:** Brunel `OrderList` (about 9,200 orders: order ID, order date, carrier, service level, ship-ahead and ship-late days, plant, ports, unit quantity, weight). Treat it as the **ERP** source of truth.
- **Simulate:** the **WMS** shipment record and the **3PL billing report** for those same orders, with planted mismatches: missing lines, quantity differences, date differences, duplicate billing, and charges with no shipment. Give the 3PL report messy codes that silver must clean.
- **App:** three-way match rate, exceptions by type, match rate by carrier or 3PL, billing at risk, filterable exception table.

## Phases

### Phase 0: Profile the data (stop and confirm before Phase 1)
- Load each file with pandas and write `docs/data_profile.md`: row counts, columns, types, date ranges, distinct warehouses and products, nulls.
- Write a **KPI-to-source map**: for every KPI and chart, the exact source column(s), or `SIMULATE` with a one-line reason.
- Show David the map and wait for a go-ahead.

### Phase 1: Bronze
- Convert raw CSVs to Parquet in `data/bronze/` (smaller files for GitHub and Hugging Face).
- Load each into `bronze.<name>` in `warehouse.duckdb`.

### Phase 2: Simulate the gaps
- One function per gap in `build_data.py`, seeded, keyed to the real dimensions from bronze.
- Write each to `data/bronze/sim_<name>.parquet` and load to bronze with a `source = 'simulated'` column.

### Phase 3: Silver and gold
- Silver: clean names, types, and codes; conform keys.
- Gold: one table per tab (`gold.forecast_weekly`, `gold.kpi_monthly`, `gold.exceptions`, `gold.recon`) plus `gold.data_sources` (table, origin Kaggle/simulated, dataset name).

### Phase 4: App
- Update the four tabs to the new gold tables.
- Add a provenance line at the top of each tab from `gold.data_sources`.
- Keep the existing theme and layout.

### Phase 5: Check
- `python analytics.py` asserts: every gold table has rows; each KPI is within a sane range; the planted anomaly is flagged; the forecast returns 12 weeks; reconciliation finds each planted mismatch type.
- Run `streamlit run app.py` and click every tab and control.

### Phase 6: Deploy
- GitHub: create empty repo `warehouse-ops-analytics`; add MIT `LICENSE` (copyright 2026 David No); commit and push.
- Hugging Face: new Space `DavidNo7/warehouse-ops-analytics`, SDK Docker, free CPU. `git remote add space https://huggingface.co/spaces/DavidNo7/warehouse-ops-analytics` then `git push --force space main` (first push only; use an HF write token as the password).
- Files over 10 MB need Git LFS on Hugging Face: `git lfs install` and `git lfs track "*.parquet"` before committing.
- Add secret `OPENAI_API_KEY` in Space settings. Confirm the build log, then test every tab in a private window.
- Write files without a BOM (README front matter must start with `---`).

## Acceptance checklist

- [ ] KPI-to-source map approved, and nothing simulated that Kaggle covers
- [ ] Every tab shows its data provenance
- [ ] All eight KPIs calculate from summed inputs
- [ ] Forecast, scenario slider, and capacity warning work
- [ ] AI briefing works with a key and falls back without one
- [ ] Reconciliation finds every planted mismatch type
- [ ] Self-check passes; app runs locally and on Hugging Face
- [ ] README credits each Kaggle dataset and explains what is simulated

## Timeline

- **Saturday night:** Phases 0–2.
- **Sunday 1:00–5:00 PM:** Phases 3–5.
- **Sunday evening:** Phase 6, then interview prep. Stop by 9:30 PM.
- **Fallback:** if Kaggle work runs long, deploy the existing all-simulated app for Monday and finish this version afterwards.

## First prompt for Claude Code

> Read PLAN.md. Start with Phase 0: profile the three Kaggle datasets in data/raw and write docs/data_profile.md with a KPI-to-source map. Follow the project rules. Stop after Phase 0 and show me the map before building anything.
