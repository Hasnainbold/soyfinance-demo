# data_real/ — verified real data only

Assembled 2026-09-05. **Every file here was fetched from a named live source and passed an
integrity audit.** Nothing is hardcoded, synthesised, back-filled from a constant, or derived
from a formula and presented as an observation.

This directory exists because the project previously shipped fabricated data: 231 dates of
Indian mandi prices that were one day's snapshot copy-pasted forward, and USDA "production"
figures that were typed into a Python dict. Both silently fed the risk engine. See
`../quarantine_fabricated/README.md`.

Rule for anything added here: if you cannot name the URL it came from and show an audit,
it does not belong in this directory.

---

## China — Dalian Commodity Exchange (`china_dce/`)

**Source:** Sina Finance inner-futures endpoint, which republishes DCE/CZCE settlement data —
the same feed the `akshare` library wraps.

```
https://stock2.finance.sina.com.cn/futures/api/jsonp.php/
  var%20_{SYMBOL}=/InnerFuturesNewService.getDailyKLine?symbol={SYMBOL}
```

**Verify it yourself:** `finance.sina.com.cn/futures/quotes/M0.shtml` (豆粕连续), or the exchange
itself at `www.dce.com.cn` (product 豆粕 / soybean meal, contract code **M**). Prices are
CNY per tonne. Cross-check: soybean-meal front month closed **3,423 CNY/t on 2026-09-04**.

| File | Product | Rows | Coverage |
|---|---|---|---|
| `soybean_meal_continuous_adjusted.csv` | 豆粕 **soybean cake / DOC** | 1,641 | 2019-12-02 → 2026-09-04 |
| `soybean_oil_continuous_adjusted.csv` | 豆油 soy oil | 1,641 | same |
| `soybean_no1_continuous_adjusted.csv` | 黄大豆一号 food-grade bean | 1,641 | same |
| `soybean_no2_continuous_adjusted.csv` | 黄大豆二号 crush-grade bean | 1,555 | 2019-12-10 → 2026-09-04 |
| `palm_oil_continuous_adjusted.csv` | 棕榈油 palm oil | 1,641 | same |
| `rapeseed_meal_continuous_adjusted.csv` | 菜粕 rapeseed meal (CZCE) | 1,641 | same |
| `corn_continuous_adjusted.csv` | corn | 1,641 | same |
| `raw_contracts/` | 243 individual contract months, as downloaded | — | — |

### These are BACK-ADJUSTED — use `close_adj`, not `close_raw`

Sina's own "M0" continuous series splices front months **without adjusting**, so each rollover
appears as a fake price gap (2023-12-11 reads −15.2%, 2022-12-19 −14.7%). `fetch_china_contracts.py`
rebuilds the series from individual contracts: the dominant contract each day is the one with
the highest open interest, dominance is forced monotonic, and history is ratio-adjusted at each
roll using both contracts' prices on the overlap day.

| Product | \|ret\|>5% days raw → adj | Ann. vol raw → adj |
|---|---|---|
| soybean meal | 13 → **5** | 21.2% → **18.2%** |
| soybean oil | 11 → 7 | 21.8% → 20.6% |
| soybean no1 | 5 → 1 | 17.5% → 15.9% |
| soybean no2 | 16 → 7 | 23.4% → 20.7% |
| palm oil | 35 → 27 | 29.2% → 27.3% |
| rapeseed meal | 18 → 13 | 26.7% → 23.9% |
| corn | 2 → **0** | 11.7% → 10.8% |

`close_raw` is kept alongside so the adjustment is auditable.

**Integrity checks passed:** 975 distinct closes in 1,619 rows · 0 duplicate rows · 0 OHLC
violations in 11,333 rows · median volume 1.01M contracts/day, no zero-volume days · correct
Chinese holiday calendar, including Spring Festival 2024/2025/2026 and National Day golden weeks
· 242–243 trading days/year · **+0.879 level correlation against CBOT meal from an independent
source** · real events present (Dec-2024 glut low 2,567; Aug-2023 high 4,488).

---

## India — CEDA Ashoka (`india_ceda/`)

**Source:** `POST https://agmarknet.ceda.ashoka.edu.in/api/prices` with
`{state_id:0, commodity_id:13, district_id:0, calculation_type:'d', start_date, end_date}`.
CEDA (Centre for Economic Data & Analysis, Ashoka University) republishes cleaned Agmarknet data.

`soybean_ceda_daily_2020_2025.csv` — **2,116 daily rows, 2020-01-01 → 2025-10-30.**
`raw/` holds the 70 raw monthly JSON responses.

**Validation:** monthly means reproduce the project's independent `soybean_monthly_avg.csv`
to **0.0% on all 12 months of 2020**. Integrity audit clean — 353 distinct values in 365 days
for 2020, longest flat run 2 days.

**Coverage ends 2025-10-30** and this is not a bug: Agmarknet migrated to "Agmarknet 2.0"
around 2025-11-07 (the build date is in their JS bundle) and the old open feed died. CEDA's
upstream died with it.

---

## India — Agmarknet 2.0 price-trend API (`india_agmarknet/`)

**Source:** the open price-trend family — no token, no CAPTCHA, no account:

```
GET https://api.agmarknet.gov.in/v1/price-trend/wholesale-prices-weekly
    ?report_mode=Statewise&commodity=13&year=2026&month=3&week=1
```

`soybean_agmarknet_weekly_2020_2026.csv` — **321 weekly points, 2020-01-04 → 2026-09-04.**
`soybean_agmarknet_weekly_states_2020_2026.csv` — per-state rows, 13 states.
`raw/` holds 276 raw weekly JSON responses.

**Three gotchas, documented so nobody re-derives them:**
1. `report_mode` is **case-sensitive**: `Statewise` / `Districtwise` / `Marketwise`. Lower-case
   returns `400 Invalid report_mode`.
2. The **monthly** sibling `wholesale-prices-monthly` is **broken server-side** — 500 on every
   input, all years. Only weekly returns data.
3. Send `state` only with Districtwise/Marketwise; including it with Statewise breaks the call.

Each response carries four dated price columns (target week, previous week, same week last
month, same week last year), so harvesting all of them reached back to 2020 for free.

### Do not use Agmarknet's own `average` field

It equal-weights ~13 states, while the Indian daily series is ~65 mandis that are 43% Madhya
Pradesh and 29% Maharashtra by row count. Equal-weighting small high-price states biases it
**+7.7%**. Re-weighting the per-state rows by our own state composition fixes it:

| | mean bias | mean abs diff | correlation |
|---|---|---|---|
| Agmarknet `average` | +7.7% | 8.1% | +0.375 |
| **Composition-weighted** | **+0.1%** | **2.3%** | **+0.615** |

(measured over 51 overlapping weeks against known-good daily data.)
Agmarknet spells it "Chattisgarh"; the mandi CSVs use "Chhattisgarh" — normalise before joining.

---

## FX (`fx/`)

`fx_rates.csv` — USD/INR and USD/CNY, **1,761 rows, 2019-12-02 → 2026-09-05**, Yahoo Finance
(`USDINR=X`, `USDCNY=X`), forward-filled across non-trading days.

Needed because the three price series live in three currencies. A correlation between CNY/t and
INR/qtl computed without converting is partly measuring the USD/CNY vs USD/INR cross rate.

---

## CBOT (`cbot/`)

`global_commodities_5yr.csv` — **1,255 rows, 2021-08-27 → 2026-08-26**, Yahoo Finance tickers
`ZS=F` (soybean), `ZM=F` (meal), `ZL=F` (oil), `ZC=F` (corn).

Note the units differ per contract: soybean in **cents/bushel**, meal in **USD/short ton**, oil
in **cents/lb**. `build_ml_dataset.py` converts all three to USD/tonne when computing the crush
margin. This file is the survivor of `final_ml_dataset.csv` — the real CBOT columns, without the
fabricated production and Brazil columns that were quarantined.

---

## Processed (`processed/`)

### `soybean_price_master.csv` — 2,429 days, 2020-01-01 → 2026-09-08

| Segment | Source | Days |
|---|---|---|
| 2020-01-01 → 2025-10-30 | CEDA daily | 2,116 |
| 2025-10-31 → 2026-09-08 | Agmarknet weekly, composition-weighted | 313 |

Weekly values are carried across their days and flagged `is_interpolated`. Rebuild with
`python build_price_master.py` — deterministic from the raw caches.

### `soybean_ml_dataset.csv` — 2,429 rows × 22 columns

The modelling table. Rebuild with `python build_ml_dataset.py`.

| Group | Columns | From |
|---|---|---|
| target | `IN_price_inr_qtl`, `is_observed`, `source` | 2020-01-01 |
| India context | `IN_price_usd_t`, `IN_msp_inr_qtl`, `IN_price_to_msp` | 2020-01-01 |
| FX | `USDINR`, `USDCNY` | 2020-01-01 |
| China (back-adjusted) | `CN_soybean_meal_usd_t` and 6 more, `CN_meal_to_bean` | 2020-01-02 |
| CBOT | `Soybean/Soy_Meal_DOC/Soy_Oil/Corn_CBOT_USD`, `crush_margin_usd_t` | 2021-08-27 |

**Filter `is_observed` before training.** 271 rows (11.2%) are weekly values carried across
days; they create 8-day flat runs. Observed-only, the longest flat run is 2 days. Training on
the carried rows teaches the model that price is constant — the exact pathology that made the
previous model's R²≈0.95 meaningless.

**CBOT starts 2021-08-27**, so choose: 22 columns from 2021-08, or 18 columns from 2020.

The only hardcoded value in the pipeline is the MSP schedule, which is a published government
constant — not yet checked against the gazette.

---

## Known gaps

- **Post-2025-10-30 India is weekly, not daily.** That is genuinely all that exists publicly.
  Daily would require an official Agmarknet 2.0 account (`/user-login_report`), which has no
  public registration.
- **No production data.** The old figures were fabricated and were removed. Fetch real ones from
  USDA PSD (`apps.fas.usda.gov/psdonline` — it has a public API) rather than retyping numbers.
- **No weather data.** `agri_data_cache/rainfall_all.json` covers only 2018-07 → 2019-12 and is
  truncated alphabetically at "Manipur" — zero overlap with the price window. Unusable.
- **No Brazil series.** `fetch_brazil_usd.py` (Comex Stat/MDIC, NCM 1201) and
  `fetch_brazil_prices.py` (CEPEA/ESALQ via `agrobr`) are both real and working, but neither
  has been run to produce a full series.

## Rebuilding everything

```bash
python fetch_china_contracts.py --product all   # DCE, back-adjusted
python fetch_ceda_history.py                    # India daily 2020 -> 2025-10
python fetch_agmarknet_trend.py --from 2020-01  # India weekly -> today
python fetch_fx.py                              # USD/INR, USD/CNY
python build_price_master.py                    # splice the India series
python build_ml_dataset.py                      # the modelling table
```

All fetchers cache their raw responses, so re-runs are cheap and offline-reproducible.
