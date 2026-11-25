# Phase 3 notes: forecast archive (Open-Meteo)

## Which data has which lead time
| Source | Lead time | What it is | Use for training? |
|---|---|---|---|
| Previous Runs API (`prev`, day1/day2) | true 1 and 2 days ahead | What the model said 1 or 2 days before | Yes - the honest one, but only a few variables |
| Historical Forecast API (`hist`) | ~0-12 h | First hours of each model run, stitched together. Close to "what the model thought at dawn" | Use with care: near-analysis, slightly optimistic |
| Single Runs API | any run | Only ~4 months back | No (can't reach past seasons) |

## Earliest dates available (Mystic, probed 2026-11-25)
| Model | hist from | prev from |
|---|---|---|
| ECMWF IFS 9 km (`ecmwf_ifs`) | 2017-01-03 | - |
| GFS | 2021-03-23 | 2021-03-27 |
| ICON | 2022-11-16 | - (2024-01-19 only) |
| BOM ACCESS-G | 2024-01-03 | 2024-01-23 |
| ECMWF IFS 0.25 deg | 2024-02-03 | 2024-02-07 |

## Gaps found
- Previous Runs has NO pressure-level data (850/700 hPa), no cloud cover, no lifted index, CIN or boundary layer height. Only 10 m wind, 2 m temp/dew, CAPE (GFS only), radiation, rain, pressure. Gusts: GFS only.
- Historical Forecast: ECMWF 9 km has no 850/700 hPa, CAPE, LI, CIN or BLH. BOM ACCESS-G has no pressure-level, CAPE, LI, CIN or BLH. GFS has everything. ECMWF 0.25 lacks LI, CIN, BLH.
- No higher-resolution model for SE Australia is available in Open-Meteo (ECMWF 9 km is the finest).
- Model grid height differs from the launch (ECMWF 9 km grid ~709 m vs launch 797 m).
- Flight history before 2021 can only be matched with ECMWF 9 km (basic variables).
- To build a true lead-time archive going forward, the daily job should save each morning's forecast (planned for the dashboard step).

## Download
`python scripts/fetch_forecasts.py bright` - resumable (table `fetch_log`), stops cleanly at the free daily limit (10,000 weighted calls); just re-run. Data goes to `data/forecast.sqlite` (git-ignored).
