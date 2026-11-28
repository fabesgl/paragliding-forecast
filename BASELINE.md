# BASELINE.md — key results on 2026-11-28 (v2) and how to reproduce them

Tag: `baseline-2026-11-28-v2` (the first version, `baseline-2026-11-28`, is kept). v2 = the first baseline plus the fixes of the same day: the scoreboard now uses complete months only, `creo_review.py` and the dashboard track record ignore lead-0 rows, and three scripts no longer write into committed paths. Neither Creo's model nor the rules changed, so every Creo number below is identical to v1. All commands run from the project root. Numbers are rank correlation (Spearman) between a score and the day's best flown distance (nobody flew = 0 km; tandems excluded) unless stated. Higher is better; +1 is perfect.

## Headline number

**Creo's walk-forward gain over the rules: +0.044 (95% range +0.006 to +0.085)**, Part A (day-ahead forecasts, full variables, 207 days, 2026-05-01 → 2026-11-25). The reduced-variable Part B gives +0.032 (−0.002 to +0.065) over 296 days. Walk-forward is the headline because it trains only on earlier days and tests on forecasts issued 1 and 2 days ahead, which is how Creo is used. The leave-one-year-out gain (+0.083) and the scoreboard (+0.64) are secondary: they use near-analysis "short lead" weather (leak L1 in `SPEC.md` §5.2, still open).

Everything below was rerun on 2026-11-28 from local data. Creo results were run twice and reproduce exactly; the scoreboard is now deterministic (same file under a different hash seed).

## 0. State of the inputs (what the numbers depend on)

| Input | State |
|---|---|
| Rules | `sites/bright/rules.yaml` SHA-1 `06c65c1bd76b…` (westerly-launch rule off) |
| Calendar | `sites/bright/calendar.yaml` SHA-1 `92c99d8c8ded…`; unknown months 2025-09, 2025-10, 2025-11; comp weight 0.5 |
| Site config | `sites/bright.yaml` SHA-1 `12ca88b2780f…`; window 11:00–16:00; `model_weighting: equal` |
| Creo model | `sites/bright/creo_model.txt` SHA-1 `ba547fb9…`; trained through 2026-11-22 on 1,510 days from 2022-01-01; rating cuts 5.838 / 13.632 / 49.344 km |
| Forecast archive (`fc`) | `hist` to 2026-11-23, `prev` (1 and 2 days) to 2026-11-23, 2,674,538 rows |
| XContest flights | 4,734 flights, 2022-01-01 → 2026-11-25; incomplete months 2023-02, 2024-01, 2024-02, 2024-03, 2026-01, 2026-04 |
| IGC flights | 1,140 flights, 2 pilots, to 2026-04-05 |
| BARRA-C2 | 2008-01 → 2026-07 (so "B" scores stop at 2026-07) |
| Single Runs | 237 runs, 2026-04-02 → 2026-11-24 |
| Live record | forecast archive since 2026-11-25; `creo_log.csv` 30+ rows (current model version `ba547fb9`) |
| Unit tests | 145 tests in 16 modules, all pass |

The data files are local, so these numbers can only be reproduced on the laptop with the same files.

---

## 1. Scoreboard: how well the rules' XC % predicted real flying

Complete XContest months only (119 days from incomplete months, all "flown" by construction, are left out; this makes the scoreboard use the same days as Creo's tests). Command (about 3 s; writes `data/scoreboard/latest/report.md` and `days.csv`, never the saved reports in `data/scoreboard/`):

```
.venv/bin/python scripts/scoreboard.py                    # 2022-01-01 on (default)
.venv/bin/python scripts/scoreboard.py --from 2024-01-01 --out data/scoreboard/latest_2024
```

### 1.1 Overall (2022 on, 1,518 days: 711 flown, 807 not flown, 95 unknown skipped, 119 incomplete-month days left out)

| Version | days | all days | flown days only |
|---|---|---|---|
| A: forecast (hist archive, each model scored then averaged) | 1,515 | **+0.64** | +0.57 |
| B: BARRA-C2 (weather that actually happened) | 1,401 | +0.65 | +0.59 |
| A on the same days as B | 1,401 | +0.64 | +0.56 |

From 2024-04 (2024 on, complete months only, 816 days): A **+0.65** (+0.62 flown only, 813 days); B +0.63 (+0.64, 699 days). Before the fix these were +0.68 and +0.70 (A, 2022 on and 2024 on).

### 1.2 Bands, 2022 on

| XC % band | A: days | % flown | median best km | B: days | % flown | median best km |
|---|---|---|---|---|---|---|
| 0–20 | 675 | 20% | 3 | 891 | 27% | 5 |
| 20–40 | 225 | 48% | 5 | 159 | 61% | 11 |
| 40–60 | 255 | 64% | 15 | 140 | 79% | 28 |
| 60–80 | 195 | 82% | 22 | 120 | 88% | 32 |
| 80–100 | 165 | 88% | 49 | 91 | 95% | 66 |

### 1.3 Splits, A, 2022 on (rank correlation)

| Group | days | rank correlation |
|---|---|---|
| all days | 1,515 | +0.64 |
| summer | 300 | +0.51 |
| autumn | 399 | +0.70 |
| winter | 460 | +0.44 |
| spring | 356 | +0.69 |
| weekends / holidays | 452 | +0.70 |
| weekdays | 1,063 | +0.63 |
| Bright Open weeks only | 14 | +0.84 |

Full tables, including the biggest misses, are in `data/scoreboard/latest/report.md`.

---

## 2. Creo, leave-one-year-out (secondary)

Command (about 7 s; deterministic: LightGBM `seed=0`, `deterministic=True`; reproduced exactly on a second run):

```
.venv/bin/python scripts/creo_test.py
```

Output: `data/creo/report.md`. Uses the short-lead (`hist`) archive, labelled days 2022–2026 in complete months, comp-day weights, no unknown months. Each year is predicted by a model trained on the other four.

### 2.1 A. All years 2022–2026: 1,510 days; the hard limit zeroes 409 days (38 of them flown)

| Period | Days | Rules | Creo | Creo − rules | 95% range |
|---|---|---|---|---|---|
| 2022 | 363 | +0.603 | +0.716 | +0.113 | +0.072 to +0.157 |
| 2023 | 334 | +0.690 | +0.775 | +0.085 | +0.047 to +0.130 |
| 2024 | 275 | +0.599 | +0.674 | +0.074 | +0.026 to +0.124 |
| 2025 | 274 | +0.696 | +0.766 | +0.070 | +0.030 to +0.116 |
| 2026 | 264 | +0.721 | +0.804 | +0.082 | +0.051 to +0.117 |
| **All years pooled** | **1,510** | **+0.644** | **+0.727** | **+0.083** | **+0.064 to +0.101** |

Bands (same number of days per band for both, by rank). Rules: 0–20 % → 674 days, 19 % flown, 3 km; 20–40 → 225, 48 %, 5 km; 40–60 → 254, 64 %, 15 km; 60–80 → 192, 81 %, 22 km; 80–100 → 165, 88 %, 49 km. Creo: 15 % / 3 km; 51 % / 5 km; 63 % / 13 km; 88 % / 28 km; 98 % / 61 km.

What Creo relies on (share of tree gain): `xc_pct` 24.1 %, `rule_thermals` 18.8 %, `wind700_speed` 15.9 %, `wind850_speed` 13.2 %, `temp` 6.8 %, `cloud_base_asl` 4.3 %, then under 2 % each.

### 2.2 B. Without 2022 (2023–2026): 1,147 days

Pooled: rules **+0.664**, Creo **+0.732**, difference **+0.067** (+0.049 to +0.086). By year: 2023 +0.082, 2024 +0.065, 2025 +0.077, 2026 +0.075.

Note: these inputs are "short lead" forecasts; see leak L1 in `SPEC.md` §5. The walk-forward test below uses day-ahead forecasts for the test days and gives smaller gains.

---

## 3. Creo, walk-forward (HEADLINE: honest order in time, day-ahead inputs)

Local data only; the whole run took **18 seconds**, so it was rerun (not copied). Command:

```
.venv/bin/python scripts/creo_walkforward.py --out baseline_2026-11-28_v2
```

Output: `data/creo/baseline_2026-11-28_v2.md` (identical to `baseline_2026-11-28.md` from v1; the default name `walkforward.md` still holds the earlier run, made before the westerly revert and unknown-months change). Reproduced exactly on a second run. Every week from 2025-12-01 the model is retrained on days before that week only; the week is predicted from forecasts issued 1 and 2 days ahead. The 95 % ranges resample whole days.

### 3.1 Part A: Single Runs API, full variables, 207 days (2026-05-01 → 2026-11-25), 414 predictions, 31 retrainings

| Lead | Rules | Creo | Difference | 95% range |
|---|---|---|---|---|
| 1 day ahead | +0.706 | +0.743 | +0.037 | −0.002 to +0.080 |
| 2 days ahead | +0.681 | +0.733 | +0.051 | +0.012 to +0.097 |
| **1 and 2 together** | **+0.693** | **+0.738** | **+0.044** | **+0.006 to +0.085** |

By month (both leads): May +0.060, Jun −0.029, Jul +0.041, Aug +0.103, Sep −0.076, Oct −0.070, Nov +0.059 (every monthly range includes zero except August).

### 3.2 Part B: Previous Runs API, reduced variables (no 850/700 hPa, cloud, boundary layer), 296 days (2025-12-01 → 2026-11-23), 592 predictions, 45 retrainings

| Lead | Rules | Creo | Difference | 95% range |
|---|---|---|---|---|
| 1 day ahead | +0.706 | +0.738 | +0.032 | −0.002 to +0.070 |
| 2 days ahead | +0.675 | +0.705 | +0.031 | −0.005 to +0.069 |
| **1 and 2 together** | **+0.690** | **+0.722** | **+0.032** | **−0.002 to +0.065** |

Same months as Part A: rules +0.694, Creo +0.720, difference +0.026 (−0.007 to +0.052).

Incomplete months skipped: 2026-04 (A); 2026-01, 2026-04 (B).

---

## 4. Other checks

| Check | Result | Command |
|---|---|---|
| Unit tests | 145 tests, all pass | `for t in tests/test_*.py; do PYTHONPATH=src .venv/bin/python -m unittest tests.$(basename $t .py); done` |
| Live Creo review | Not yet meaningful: flights for the logged days are not imported; lead 0 rows are now ignored | `.venv/bin/python scripts/creo_review.py` (planned ~2026-12-28) |
| Bias corrections | 154 bins, 2 days of data each, 0 active | read `corrections/corrections.json` |
| Model weighting | `equal` published; `weighted` and the old average logged in `predictions/weighting_log.csv` (42 rows) | review ~2026-12-26 |
| Valley-wind calibration | not ready (fewer than 30 usable days) | `corrections/valley_wind_calibration.json` |

## 5. What was not rerun

- `scripts/creo_train.py` (would overwrite the deployed model), `scripts/rule_tests.py`, `scripts/forecast_error*.py`, `scripts/model_weights.py`, and anything that downloads data.
- `scripts/creo_review.py` was run and has nothing to score yet.
