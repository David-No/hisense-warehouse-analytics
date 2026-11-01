# Project rules

1. **Kaggle first.** Use real Kaggle data wherever it covers a need. Simulate data **only** for gaps, and never simulate something a Kaggle file already has.
2. **Label provenance.** Every tab shows which data is from Kaggle (with the dataset name) and which is simulated. Never present simulated data as real.
3. **One dataset per section.** Sections don't need to share warehouses, products, or dates. Simulated gap data within a section must use that section's real keys (warehouses, products, dates).
4. **Medallion in DuckDB.** `bronze` = raw files as-is, `silver` = cleaned and conformed, `gold` = app-ready tables. Silver and gold logic is plain SQL.
5. **Rates from summed inputs.** Never average a rate across warehouses or months.
6. **Keep it lean.** Few files, no unneeded dependencies, one self-check script. Fixed random seed so results are reproducible.
7. **Responsible AI.** AI output is labeled as a starting point and the app tells the user to verify it before acting. With no API key, fall back to a rule-based summary.
