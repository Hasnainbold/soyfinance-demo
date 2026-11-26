# Quarantined 2026-09-05 — fabricated / mislabelled data

Removed from the modelling pipeline. Kept only as evidence; **do not re-import**.

| File | Why |
|---|---|
| `add_production.py`, `add_global_production.py`, `add_brazil_production.py` | US/Brazil/India/China `*_Production_MMT` were **hardcoded into a Python dict** and commented "Official USDA NASS/WASDE", but were never fetched from USDA. The 2026 figures forecast a crop year that has not finished. |
| `fetch_brazil_proxy.py` | `Brazil_Export_Price_USD_MT` was **computed as CBOT x USD/BRL** by a function named `fetch_brazil_calculated_price`. Not an observed Brazilian quote — a transform of columns already in the dataset, so collinear by construction. |
| `commodities_with_*.csv`, `final_ml_dataset.csv` | Outputs of the above: real CBOT columns contaminated with the fabricated production and Brazil columns. |

The genuine CBOT data survives in `../global_commodities_5yr.csv` (yfinance ZS=F / ZM=F /
ZL=F / ZC=F) and that is what `build_ml_dataset.py` now reads.

**Kept, because they are real:** `fetch_brazil_usd.py` (Brazil Comex Stat / MDIC, NCM 1201),
`fetch_brazil_prices.py` + `brazil_cepea_soybean_prices.csv` (CEPEA/ESALQ Paranaguá via agrobr —
genuine, but only 15 rows), `fetch_commodities.py`, `global_commodities_5yr.csv`.

If you want real production data, fetch it from USDA PSD (apps.fas.usda.gov/psdonline/app/index.html
— has a public API) rather than retyping numbers.
