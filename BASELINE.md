# BASELINE.md — key results on 2026-11-28 and how to reproduce them

Tag: `baseline-2026-11-28`. Code state before this documentation commit: `fda30df` ("ERA5 collector: one request at a time…"). All commands run from the project root. Numbers are rank correlation (Spearman) between a score and the day's best flown distance (nobody flew = 0 km; tandems excluded) unless stated. Higher is better; +1 is perfect.

Everything below was rerun on 2026-11-28 from local data; nothing was copied from older reports. Each result was run twice where noted and reproduced exactly.

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
| Live record | forecast archive since 2026-11-25 (42 files); `creo_log.csv` 36 rows (24 from the current model version) |
| Unit tests | 145 tests in 16 modules, all pass |

The data files are local, so these numbers can only be reproduced on the laptop with the same files. If a flight page is added or a month's completeness changes, the numbers change.

---

## 1. Scoreboard: how well the rules' XC % predicted real flying

Command (default start = 2024-01-01, about 3 s):

```
.venv/bin/python scripts/scoreboard.py
```

and from 2022 (this is the version that was on disk before the baseline, and `rule_tests.py` reads its `days.csv`):

```
.venv/bin/python scripts/scoreboard.py --from 2022-01-01
```

Outputs: `data/scoreboard/report.md`, `data/scoreboard/days.csv` (both overwrite the previous run; run the 2022 one last if you want the older state). Reproducibility: the 2022 report matched the saved one line for line (apart from the "Made" time); the "held back by" columns of `days.csv` change from run to run (tie-break, see SPEC §7 item 4); scores do not.

### 1.1 Overall

| Version | 2022 on: days | all days | flown days only | 2024 on: days | all days | flown days only |
|---|---|---|---|---|---|---|
| A: forecast (hist archive, each model scored then averaged) | 1,634 | **+0.68** | +0.58 | 911 | **+0.70** | +0.63 |
| B: BARRA-C2 (weather that actually happened) | 1,520 | +0.69 | +0.61 | 797 | +0.69 | +0.67 |
| A on the same days as B | 1,520 | +0.67 | +0.57 | 797 | +0.70 | +0.63 |

Days with a label: 1,637 (2022 on; 830 flown, 807 not; 95 unknown skipped; 7 flown days known only from a track) and 914 (2024 on; 508 flown, 406 not; 88 unknown).

**Caveat found during this run:** the scoreboard keeps 119 days from months with incomplete XContest data, and every one of them is "flown" (days with no saved flights in such a month are "unknown"). Taken out, version A is **+0.644** (2022 on, 1,518 days) and **+0.652** (2024 on, 816 days). This second number is what the Creo test compares against. (Computed from `days.csv` with the same rank-correlation method; not a line of the script's own report.)

### 1.2 Bands, version A (from the forecast)

| XC % band | 2022 on: days | % flown | median best km (days flown) | 2024 on: days | % flown | median best km |
|---|---|---|---|---|---|---|
| 0–20 | 679 | 20% | 3 | 373 | 25% | 3 |
| 20–40 | 232 | 50% | 6 | 136 | 56% | 5 |
| 40–60 | 276 | 67% | 15 | 155 | 71% | 13 |
| 60–80 | 234 | 85% | 22 | 134 | 89% | 22 |
| 80–100 | 213 | 91% | 51 | 113 | 95% | 53 |

### 1.3 Bands, version B (BARRA-C2)

| XC % band | 2022 on: days | % flown | median best km | 2024 on: days | % flown | median best km |
|---|---|---|---|---|---|---|
| 0–20 | 906 | 28% | 5 | 456 | 33% | 4 |
| 20–40 | 173 | 64% | 13 | 91 | 68% | 8 |
| 40–60 | 162 | 82% | 28 | 85 | 82% | 20 |
| 60–80 | 145 | 90% | 34 | 75 | 89% | 36 |
| 80–100 | 134 | 96% | 69 | 90 | 97% | 65 |

### 1.4 Splits, version A (2022 on / 2024 on rank correlation)

| Group | 2022 on | 2024 on |
|---|---|---|
| all days | +0.68 | +0.70 |
| summer | +0.54 | +0.64 |
| autumn | +0.73 | +0.72 |
| winter | +0.44 | +0.44 |
| spring | +0.69 | +0.73 |
| weekends / holidays | +0.73 | +0.77 |
| weekdays | +0.66 | +0.67 |
| Bright Open weeks only | +0.76 (31 days) | +0.80 (18 days) |

The full tables, including the biggest misses, are in `data/scoreboard/report.md`.

---

## 2. Creo, leave-one-year-out (the first test)

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

## 3. Creo, walk-forward (honest order in time, day-ahead inputs)

Local data only; the whole run took **18 seconds**, so it was rerun (not copied). Command:

```
.venv/bin/python scripts/creo_walkforward.py --out baseline_2026-11-28
```

Output: `data/creo/baseline_2026-11-28.md` (the default name `walkforward.md` still holds the earlier run, made before the westerly revert and unknown-months change). Reproduced exactly on a second run. Every week from 2025-12-01 the model is retrained on days before that week only; the week is predicted from forecasts issued 1 and 2 days ahead. The 95 % ranges resample whole days.

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
| Live Creo review | Not yet meaningful: 24 log rows from the current model; flights for those days not imported | `.venv/bin/python scripts/creo_review.py` (planned ~2026-12-28) |
| Bias corrections | 154 bins, 2 days of data each, 0 active | read `corrections/corrections.json` |
| Model weighting | `equal` published; `weighted` and the old average logged in `predictions/weighting_log.csv` (42 rows) | review ~2026-12-26 |
| Valley-wind calibration | not ready (fewer than 30 usable days) | `corrections/valley_wind_calibration.json` |

## 5. What was not rerun

- `scripts/creo_train.py` (would overwrite the deployed model), `scripts/creo_review.py` (nothing to score yet), `scripts/rule_tests.py`, `scripts/forecast_error*.py`, `scripts/model_weights.py` (they write into the committed `sites/bright/priors.json` / `model_weights.json` or are outside the request), and anything that downloads data.
- The pre-existing `data/creo/report.md` was replaced by the new run; the old one differed only because it predated the westerly revert (2026-11-27 14:23) and the unknown-months change (14:47).
