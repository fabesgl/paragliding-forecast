# SPEC.md — technical description of the project as it is on 2026-11-28

For Claude Code. Describes what the code and data are **now** (baseline tag `baseline-2026-11-28`), not what is planned. Plain-English version: `PLANS.md`. Reproducible results: `BASELINE.md`. Working rules: `CLAUDE.md`.

Conventions in this file: `path:line` points into the repo at the baseline commit. "Committed" = in git (the daily job or a human commits it). "Local" = git-ignored, exists only on Fabian's laptop. Units are km/h, metres, degrees (wind direction = direction the wind comes FROM), °C, hPa, mm, W/m². "Window" = the flying window 11:00–16:00 Victorian local time unless said otherwise.

Contents: 1 Data stores · 2 Scripts and modules · 3 Data-flow table · 4 Hard-coded definitions · 5 Time handling and suspected leaks · 6 External services and secrets · 7 Known inconsistencies.

---

## 1. Data stores

### 1.1 Committed, written only by the GitHub daily job (`.github/workflows/daily.yml`)

The pre-commit hook (`.git/hooks/pre-commit`) blocks laptop commits that touch these folders. `scripts/commit.sh` adds only code paths.

| Path | Format | One row / file means | Time zone and moment | Range (2026-11-28) | Updated by |
|---|---|---|---|---|---|
| `observations/<station>.csv` (94884 Benalla, 94889 Wangaratta, 94903 Falls Creek, 94905 Hotham Airport, 94906 Mount Hotham, 94908 Omeo) | CSV. Columns: `obs_time_utc` (ISO `…Z`), `local_time` (BOM `YYYYMMDDHHMMSS`, local), `air_temp` °C, `apparent_t` °C, `dewpt` °C, `rel_hum` %, `wind_dir` (compass word), `wind_spd_kmh`, `gust_kmh`, `press_qnh`, `press_msl` hPa, `rain_trace` (BOM's value, stored as text), `cloud`, `vis_km` | One BOM half-hourly observation (Benalla: about one a day) | The instant of the observation, UTC key; `local_time` is Australian local as BOM prints it | 2026-11-22 → now (365–429 rows per station; BOM's feed only goes back ~72 h, so history starts at the first run on 2026-11-25) | `scripts/collect_bom.py` (2 s pause per station; de-duplicated on `obs_time_utc`, rewritten whole). **Also writes to `observations/` when run from the laptop** (no `data/local_out` redirect). |
| `forecast_archive/<UTC fetch time>.json.gz` (`YYYY-MM-DDTHHMMZ`) | gzip JSON: `fetched_utc`, `model_runs` (from `model_runs/latest.json`), `models_ok`, `models_failed`, `fronts`, `points` (order of the 7 points), `response[i].hourly` = `time` + `<variable>_<model>` lists for point *i* | One whole 7-day Open-Meteo forecast for all points and models, **raw (before bias correction)** | `time` strings are **local** (Open-Meteo asked with `timezone=Australia/Melbourne`), naive `YYYY-MM-DDTHH:MM`, hourly; `fetched_utc` is UTC | 42 files, 2026-11-25 → now. **Only 4 days of live archive exist.** | `scripts/daily_forecast.py` step 1 |
| `predictions/log.csv` | CSV: `made_at_utc, target_date, lead_days, xc_pct, acro_pct, method, models, front_status, front_kind, front_time` | The published XC % and acro % for one target day from one run | `made_at_utc` UTC; `target_date` local date; `lead_days` = target local date − local date of the run (0–5) | 239 rows, 2026-11-25 → now; `method` = `rules-v1`/`v2`/`v3` (code writes `rules-v3`, `daily_forecast.py:203`) | `daily_forecast.py` step 3 (rewritten whole each run) |
| `predictions/weighting_log.csv` | CSV: `made_at_utc, target_date, lead_days, setting, xc_avg_of_models, acro_avg_of_models, xc_blend_equal, acro_blend_equal, xc_blend_weighted, acro_blend_weighted, models` | The three ways of combining the models, side by side, per target day | as above | 42 rows (since 2026-11-26) | `daily_forecast.py` (append) |
| `predictions/creo_log.csv` | CSV: `made_at_utc, target_date, lead_days, rules_xc_pct, creo_km, creo_raw_km, no_go, n_models, model` (`model` = first 8 hex of the SHA-1 of `creo_model.txt`) | Creo's predicted best flight (km) next to the rules' XC % ("shadow log") | as above | 36 rows (since 2026-11-27); three model versions appear in `creo_model.json` | `daily_forecast.py` step 3b (append) |
| `predictions/ai_log.csv` | CSV (118 lines): `made_at_utc, target_date, lead_days, model, xc_call, acro_call, xc_rule_pct, acro_rule_pct, limit_forced, reasoning` | The AI second opinion (retired 2026-11-27) | as above | up to 2026-11-27 | no longer written |
| `model_runs/latest.json`, `model_runs/log.csv` | JSON / CSV: per model (`ecmwf_ifs025`, `ncep_gfs025`, `dwd_icon`) the newest run's `run_init_utc`, `available_utc`, `delay_h`, `waited_min`, `status` (`newest` / `OLDER-THAN-EXPECTED`) | What model run Open-Meteo had when the job looked | UTC | since 2026-11-25 | `scripts/wait_for_models.py` |
| `corrections/corrections.json` | JSON: `version`, `updated_utc`, `last_day`, `bins{"model\|point\|variable\|sector": {kind add/mul, corr, n_days, eval_days, w, mae_raw, mae_corr, acc_raw, acc_corr}}` | Rolling bias correction per model, point, variable, wind sector (8 sectors + "all") | `last_day` = last local date used | 154 bins; **max `n_days` = 2, so none is active** (needs 30 days + 10 checked days, `corrections.py:14`) | `scripts/update_corrections.py` |
| `corrections/valley_wind_calibration.json` | JSON: `n_days, k, r2, by_thermal, ready, station, current_setting, min_days, updated, notified` | Fit of afternoon north wind at Wangaratta vs forecast thermal strength | UTC `updated` | report only | `scripts/calibrate_valley_wind.py` |
| `corrections/weighting_review.json` | JSON: `updated, min_days, n_days, ready, variables, score_differences, notified` | Equal vs weighted model blend scored against station readings | UTC | report only | `scripts/weighting_review.py` |
| `docs/forecast.json` | JSON `{updated_local, days[]}`; each day: `date, xc, acro, xc_range, models, key, xc_rules, acro_rules, winds, hourly, front, creo{rating,lines,text}, summary` | The published forecast, 6 local days from today | `updated_local` is local text | latest run | `daily_forecast.py` step 4 |
| `docs/index.html`, `docs/manifest.webmanifest`, `docs/sw.js`, `docs/icons/` | static site (GitHub Pages) | Dashboard | — | latest run | `daily_forecast.py` (HTML via `src/pgforecast/render.py`; PWA files copied from `pwa/`) |

### 1.2 Committed, edited by hand (configuration and trained artefacts)

| Path | Content | Notes |
|---|---|---|
| `sites/bright.yaml` | Site config: id, name, `timezone: Australia/Melbourne`, `flying_window_local`, 7 points (lat/lon/role/status/`bom_station`), `launch_height_m` 797, `launch_above_landing_m` 477, `launch_facing_deg` 0, `bom_stations`, `valley_wind_station`, `chat_aliases`, `model_weighting: equal` | Read by almost every script |
| `sites/bright/rules.yaml` | Every rule threshold (validated by `src/pgforecast/rules_config.py`) | See §4.2 |
| `sites/bright/calendar.yaml` | `public_holidays`, `bright_open` comp weeks, `comp_distance_weight`, `unknown_months` | Used only by scoreboard / Creo training. Holidays "typed from memory" (file header, TODO #8) |
| `sites/bright/notes.md` | Local pilot knowledge | Not used by scoring |
| `sites/bright/model_weights.json` | Per-variable model weights (`models`, `weights`, `dropped`, `provisional`, `status`, `version`) | Written by `model_weights.py --publish`; read by the job (used only if `model_weighting: weighted`) |
| `sites/bright/priors.json` | BARRA-based corrections (150 KB) | Reference only; **do not apply**. `scripts/forecast_error.py:179` overwrites it when run. |
| `sites/bright/creo_model.txt` | LightGBM model text (SHA-1 prefix `ba547fb9`) | Written by `creo_train.py` |
| `sites/bright/creo_model.json` | `versions{<sha8>: trained_through, first_day, days, rating_cuts_km, rating_bounds_km, xc_curve, rating_counts, rating_basis}` | Current version `ba547fb9`: trained through 2026-11-22, first day 2022-01-01, 1510 days, cuts `[5.838, 13.632, 49.344]` km. Two older versions (`8a7f80f5`, `cef1315c`) are kept; the shadow log still has their rows. |
| `sites/launches.yaml` | Launch list for IGC matching (id, site, lat/lon, `radius_m`, kind, tz) | Never rename an `id` after import |
| `sites/timezones.yaml` | Timezone override boxes | Used by `tzlookup.py` |
| `pwa/` | App manifest, service worker, icons | Copied into `docs/` |

### 1.3 Local only (git-ignored: `data/`, `*.sqlite`, `chat_exports/`, `xcontest_pages/`, `igc_files/`)

**`data/forecast.sqlite`** (484 MB). Active table is `fc`; `forecast`, `flight`, `bom_obs`, `day_label`, `prediction_log` exist in `src/pgforecast/schema.sql` but are **empty and unused**; `site` (1 row) and `point` (7 rows, `elevation_m` from Open-Meteo, e.g. Mystic 786 m) are written by `scripts/init_db.py`.

| Table | Columns | Meaning |
|---|---|---|
| `fc` (2,674,538 rows) | PK `(source, lead_days, point_key, model, time_utc)`; `wind_speed_10m`, `wind_direction_10m`, `wind_gusts_10m` (km/h, deg), `wind_speed_850hPa`, `wind_direction_850hPa`, `wind_speed_700hPa`, `wind_direction_700hPa`, `temperature_2m`, `dew_point_2m`, `temperature_850hPa`, `temperature_700hPa` (°C), `cloud_cover_low/mid/high` (%), `cape` (J/kg), `lifted_index`, `convective_inhibition`, `boundary_layer_height` (m), `shortwave_radiation` (W/m²), `precipitation` (mm), `pressure_msl` (hPa) | One model's value for one point for one hour. Hourly, `time_utc` = `YYYY-MM-DDTHH:MM` UTC. |
| `fc` `source='hist'`, `lead_days=0` | 1,465,631 rows, 2017-01-03 → 2026-11-23 | Open-Meteo **Historical Forecast API**: first hours of each model run, stitched ("near-analysis, slightly optimistic", `PHASE3_NOTES.md`). Start dates differ per model: ECMWF IFS 9 km 2017-01-03, GFS 2021-03-23, ICON 2022-11-16, ACCESS-G 2024-01-03, ECMWF IFS 0.25° 2024-02-03 (`fetch_forecasts.py:18-23`). |
| `fc` `source='prev'`, `lead_days` 1 and 2 | 604,555 / 604,352 rows, 2021-03-27 → 2026-11-23 | **Previous Runs API**: the forecast as issued 1 or 2 days earlier. Only 10 m wind, gusts (GFS only), 2 m temperature/dew point, CAPE (GFS only), radiation, rain, pressure; **no 850/700 hPa, cloud, boundary-layer height** (`PREV_VARS`, `fetch_forecasts.py:15`). |
| `fc` models | `gfs_seamless` 1,035,720; `ecmwf_ifs` 606,648; `ecmwf_ifs025` 515,136; `bom_access_global` 270,466; `icon_seamless` 246,568 | |
| `fc` points | `mystic_launch, bright_valley, mount_hotham, falls_creek, mount_buffalo, upwind_nw, upwind_sw` (382,070 rows each; upwind 382,094) | |
| `fetch_log` (1400 rows) | `source, lead_days, point_key, model, start_date, end_date, fetched_at_utc, n_rows` | Makes `fetch_forecasts.py` resumable (90-day chunks). Last fetch 2026-11-26; data stops at `today − 3 days`. |

**`data/xcontest_flights.sqlite`** (4734 flights, site `mystic`, 2022-01-01 → 2026-11-25). Built from pages saved by hand in `xcontest_pages/mystic_<year>/` (2022: 24 files, 2023: 30, 2024: 30, 2025: 23, 2026: 22; the 2020 and 2021 folders are empty).
- `xc_flight`: `flight_id` (XContest FLID, PK), `site`, `date` (local), `start_time` (local `HH:MM`), `utc_offset` (`UTC+11:00`), `launch`, `pilot_hash` (salted hash, never the name), `route_type`, `distance_km`, `points`, `glider_model`, `glider_class` (letter; `T` = tandem), `src_file`, `airtime_min`/`speed_kmh` (always NULL). Unique on `(date, start_time, pilot_hash, launch)`.
- `xc_page_total` (`src_file, site, month, xc_total, rows`): the total XContest printed on each saved page.
- `xc_month_status` (`site, month, xc_total, saved, complete`): `complete` = saved ≥ total − `MONTH_TOLERANCE` (2) (`xcontest.py:47`). 58 months; **6 incomplete: 2023-02, 2024-01, 2024-02, 2024-03, 2026-01, 2026-04**.
- `xc_day_status` (`site, date, status, n_flights`): `flew` 836, `no_flights` 859, `unknown` 95 days. `no_flights` only if every UTC month the day touches is complete (using the site's most common UTC offset for all dates).
- `data/xcontest_flights.before_2022_23.sqlite`: an older copy (2761 flights); not read by any script.

**`data/igc_flights.sqlite`** (1140 flights, 2017-04-23 → 2026-04-05, 2 pilots; 481 at Mystic). From `igc_files/<pilot>/*.igc|*.gpx` via `import_igc.py`.
- `igc_flight` PK `src_file`: `pilot_hash, date` (local, from launch coordinates' timezone), `launch_time`, `landing_time` (local `HH:MM`), `utc_offset`, `launch_site` (id from `launches.yaml`, else `unknown@lat,lon`), launch/land lat-lon, `launch_alt_m`, `duration_min`, `track_km`, `max_dist_km`, `max_alt_m`, `max_gain_m`, `max_climb_ms`, `max_sink_ms`, `n_thermals`, `mean_thermal_climb_ms`, `alt_glitches`, `imported_utc`, `pilot_folder`, `source_fmt`, `n_fixes`, `median_dt_s`, `gap_frac`, `low_rate`, `file_sha1`, `tz`, `tz_method`. View `igc_flight_unique` (same flight as both IGC and GPX counts once).
- `igc_thermal` PK `(src_file, n)`: `local_time`, `duration_s`, `lat`, `lon`, `start_alt_m`, `gain_m`, `climb_ms`, `turns`, `direction`, `kind` (`climb` if mean ≥ 0.3 m/s else `circling`). 10,284 rows.

**`data/chat_labels.sqlite`** (447 labels, 2020-05-13 → 2026-11-16, all `evidence='chat_llm'`, weight 0.3). `chat_label` (`msg_key, source, chat_hash, sender_hash, site_id, date, date_source` (`stated`/`message`), `flown, wind_speed, wind_dir, turbulence, thermals, restitution, rating, llm_confidence, evidence, confidence_tier, weight, model, prompt_version, imported_utc, kind` (`past`/`live`)); `chat_seen` (8670 messages processed); `chat_day_activity` (`chat_hash, date, n_messages, n_senders`). No text or names stored. Weak evidence; **not used by the scoreboard or Creo** (the scoreboard only checks whether an acro field exists).

**Reanalysis / reference weather (local):**
| Path | Content | Range | Writer |
|---|---|---|---|
| `data/barra2/` (1.1 GB, 13,380 files) | `<box>__<var>__YYYYMM.nc`, boxes `bright_area` (-37.05..-36.65 N, 146.70..147.35 E), `upwind_nw`, `upwind_sw`; 20 variables (`uas vas wsgs ua850 va850 ua700 va700 tas ta850 ta700 hurs cll clm clh CAPE CIN zmla rsds pr psl`). BARRA-C2, 4.4 km, hourly **means stamped HH:30 UTC**. | 2008-01 → 2026-07 | `fetch_barra2.py` |
| `data/raw/era5/` | ERA5 raw NetCDF, 0.25°, every 3 h UTC, area N-20 W110 S-50 E160. `era5_single_YYYY_MM.nc`, `era5_pressure_YYYY_MM.nc`, `manifest.csv` (`file, dataset, month, size_bytes, time_steps, verified, downloaded_at, download_seconds, preliminary_expver5, note`), `jobs.json`, `progress.log`, `fetch.log` | In progress (1 file at baseline; 453 planned from 2008-01) | `fetch_era5.py` via `era5.service` |
| `data/single_runs/<run date>.json` (237 files) | `{run, models, missing, hourly}`: 00 UTC run of each day, 3 days, GFS / ECMWF 0.25 / ICON, Mystic, UTC hours | 2026-04-02 → 2026-11-24 | `fetch_single_runs.py` |
| `data/bom_dwo/IDCJDW<id>.<yyyymm>.csv` | BOM Daily Weather Observations (9 am, 3 pm, daily max gust); stations 3027, 3055, 3056, 3081 | 2025-10 → 2026-11 | `fetch_bom_dwo.py` |
| `data/ffwx/mystic.csv` (4,487 rows) | Mystic station, 10-minute averages: `time_utc, time_local, resolution (full/thinned), n_readings, wind_min_kmh, wind_avg_kmh, wind_max_kmh, wind_dir_deg, temp_c, dew_point_c, pressure_hpa` | 2026-10-27 → now | `fetch_ffwx.py` (systemd user timer 12:00). **Wind max/min of `thinned` rows understate gusts.** Not used by any model yet. |

**Analysis outputs (local, regenerated by their scripts):** `data/forecast_error/` (`error_summary.csv`, `pairs.sqlite`, `barra_points.pkl`, `elevations.json`, `orog.nc`, `priors.json`, `model_weights.json`, `station_pairs.pkl`, `station_summary.csv`, `station_vs_barra.csv`, `barra_vs_station.csv`, `correction_validation.csv`, `report.md`); `data/scoreboard/` (`report.md`, `days.csv`, `report_d1.md`, `days_d1.csv`, `barra_mystic.pkl` + `.stamp`, older reports, `rule_tests*.md`, `misses.md`); `data/creo/` (`report.md`, `walkforward.md`, `wf_*.md`, `baseline_2026-11-28.md`, `review.md` when run); `data/thermal_zones/{2d,ew,ring}/`; `data/local_out/` (laptop runs of the daily job: `docs`, `forecast_archive`, `predictions`, `corrections`, `model_runs`); `data/*.log`, `igc_failed.txt`, `unknown_launches.txt`. `data/chat_salt` is the hashing salt (secret; never print or commit).

`data/scoreboard/days.csv` columns: `date, flown, n_flights, best_km, median_km, source (xcontest/igc/none), flyable, month_complete, comp, count_weight, distance_weight, xc_a, n_models, limit_a, xc_b, limit_b, season, free, bright_open, weekday`. One row = one local day that could be judged; `xc_a` = rules' XC % from the archived (hist) forecast, `xc_b` = from BARRA-C2.

---

## 2. Scripts and modules

"Job" = GitHub daily job. "Manual" = run by hand on the laptop. Every script is run from the project root with `.venv/bin/python`.

### 2.1 Daily job, in the order of `daily.yml`

| # | Script | Purpose | Reads | Writes | Notes |
|---|---|---|---|---|---|
| 1 | `scripts/collect_bom.py bright` | Save BOM half-hourly observations | BOM feed `IDV60801.<id>.json`; `sites/bright.yaml` `bom_stations` | `observations/<id>.csv` | Fails the job if a station fails (step `bom`) |
| 2 | `scripts/check_observations.py bright` | Report gaps > 3 h, stale (> 12 h) and sparse (median step ≥ 20 h) stations | `observations/` | stdout only | Never fails the run |
| 3 | `scripts/update_corrections.py bright` | Update bias bins from finished days: forecast made the evening before vs BOM obs, hours 09–17 local | `forecast_archive/`, `observations/`, `corrections/corrections.json`, `sites/bright.yaml` | `corrections/corrections.json` | Needs ≥ 6 paired hours per point/model/day |
| 4 | `scripts/weighting_review.py bright` | Equal vs weighted blend scored on station readings (leads 1–5, hours 11–16), bootstrap verdict; opens one GitHub issue at 30 days | `forecast_archive/`, `observations/`, `sites/bright/model_weights.json` | `corrections/weighting_review.json`; GitHub issue | stdlib only (no numpy in the job) |
| 5 | `scripts/calibrate_valley_wind.py bright` | Fit north wind at Wangaratta (12:00–15:59 local, dry days) = k × forecast thermal strength; opens one issue at 30 days | `forecast_archive/`, `observations/94889.csv`, rules | `corrections/valley_wind_calibration.json`; GitHub issue | Reports only; never changes `rules.yaml` |
| 6 | `scripts/wait_for_models.py` | Wait up to 75 min for the newest ECMWF/GFS/ICON runs; record which runs | Open-Meteo `data/<model>/static/meta.json` | `model_runs/latest.json`, `model_runs/log.csv` | Waits only when `GITHUB_EVENT_NAME=schedule`; never fails |
| 7 | `scripts/daily_forecast.py bright` | Fetch 4 models × 7 points (7 days), archive raw, apply corrections, score each model and day with the rules, blend (equal/weighted), detect fronts, run Creo, log, render dashboard | Open-Meteo forecast API; `sites/`, `corrections/`, `model_runs/latest.json`, `creo_model.txt/json`, `model_weights.json` | `forecast_archive/`, `predictions/{log,weighting_log,creo_log}.csv`, `docs/` | Step `fc` is `continue-on-error`, then the job fails at the end if `bom` or `fc` failed. Output root = repo when `GITHUB_ACTIONS` is set, else `data/local_out/`. |
| 8 | commit step | `git add observations forecast_archive predictions model_runs corrections docs`; commit "Daily update <date>"; pull --rebase; push | — | git | Cron `45 20 * * *` and `45 8 * * *` UTC (07:45 and 19:45 AEDT; one hour earlier local in AEST) plus manual dispatch |

`daily_forecast.py` internals worth knowing: scoring is done **per model** on the window hours, then averaged ("old way") and, in parallel, on a per-variable blend of the models ("equal" and "weighted"); the published numbers come from the blend named by `MODEL_WEIGHTING` env var → `sites/bright.yaml` `model_weighting` → fallback `"weighted"` (`daily_forecast.py:31`). Gaps in one model (e.g. ECMWF has no boundary-layer height) are filled with the mean of the other models (`fill()`, line 85). Corrections modify the hourly lists **in place after the raw archive is written** (lines 69–77), so scoring and Creo see corrected values, the archive keeps raw ones. Creo step is wrapped in try/except: any failure drops the panel and the job carries on.

### 2.2 Source modules (`src/pgforecast/`)

| Module | Purpose | Used by |
|---|---|---|
| `rules.py` | `score_day(h, rules, launch_elev)` → XC %, acro %, per-rule factors, key numbers; `acro_window_score`; smoothing functions `below`/`above`; rough thermal and cloud-base estimates (`_window_features`) | job, scoreboard, Creo scripts |
| `rules_config.py` | Load and validate `rules.yaml`; `limits()` for the (retired) AI opinion | job, scripts |
| `blend.py` | Per-variable weighted blend of models (circular mean for directions) | job, weighting_review |
| `corrections.py` | Rolling bias bins (add/multiply), status learning → validating → active/off, `apply_hourly` | job |
| `fronts.py` | Cold/warm front detection from model-average pressure, 850 hPa temperature and wind direction; day labels pre/post-frontal | job (info only) |
| `valley.py` | Valley-wind calibration maths (`MIN_DAYS = 30`) | calibrate script |
| `summary.py` | Plain-English day text, `NOGO_BELOW = 15`, best window, deal-breaker | job, render |
| `render.py` | Builds `docs/index.html` (`OPEN_DAYS = 3`) | job |
| `creo.py` | Creo helpers: `model_features`, `day_features`, `predict_day`, `hard_limit`, rating words and calibration, panel text, bootstrap and Spearman helpers | job (if numpy+lightgbm), creo scripts |
| `scoreboard.py` | Pure scoreboard maths: bands, Spearman, `build_day_labels`, comp weights, misses | scoreboard, Creo scripts |
| `forecast_error.py` | Forecast-vs-BARRA maths, bilinear interpolation, height/lapse adjustment, `LOCAL_HOURS` | forecast_error scripts, scoreboard |
| `xcontest.py` | Parse saved XContest pages, month/day status | `import_xcontest.py` |
| `igc.py` | IGC/GPX parser, thermal finder, schema, retime | `import_igc.py` |
| `tzlookup.py` | Timezone from coordinates (`timezonefinder`, Australian border rules, `sites/timezones.yaml`) | `igc.py` |
| `ffwx.py` | FreeFlightWx CSV → 10-minute averages | `fetch_ffwx.py` |
| `chatlog/` (`parsers, extract, llm, store, batch`) | WhatsApp/Telegram parsing, message → label via a local or Anthropic model, hashing | `import_chats.py` |
| `ai_opinion.py` | AI second opinion (Anthropic Messages API). **Retired from the job; kept unused.** | tests only |
| `schema.sql` | Table definitions for `forecast.sqlite` | `fetch_forecasts.py`, `init_db.py` |

### 2.3 Manual scripts, grouped

**Forecast history and weather reference**
| Script | Purpose / inputs → outputs |
|---|---|
| `init_db.py` | Open-Meteo elevation API + `sites/<id>.yaml` → `site` and `point` tables |
| `fetch_forecasts.py [site] [--until D]` | Open-Meteo Historical Forecast and Previous Runs APIs → `fc`, `fetch_log`. Resumable; stops cleanly at the daily limit. |
| `fetch_single_runs.py [--workers 3]` | Open-Meteo Single Runs API → `data/single_runs/`. ~30 s per request; stops on any error. Generic user-agent. |
| `fetch_barra2.py [site]` | NCI THREDDS OPeNDAP → `data/barra2/`. Resumable. |
| `fetch_era5.py` | Copernicus CDS → `data/raw/era5/` (see CLAUDE.md for the request rules). **Running as `era5.service`; do not touch.** |
| `fetch_bom_dwo.py [yyyymm] [ids]` | BOM climate pages → `data/bom_dwo/` |
| `fetch_ffwx.py` | FreeFlightWx table → `data/ffwx/mystic.csv`. systemd user timer `ffwx.timer` 12:00, `Persistent=true`. |

**Labels (what pilots did)**
| Script | Purpose |
|---|---|
| `import_xcontest.py [--summary]` | Saved pages in `xcontest_pages/*/` → `xcontest_flights.sqlite` (rebuilds month/day status; warns on incomplete months). Never fetches from XContest. |
| `import_igc.py <folder> [--summary-only]` | `igc_files/<pilot>/` → `igc_flights.sqlite`; re-tags all flights against `launches.yaml`. |
| `import_chats.py <files> [--default-site] [--backend] [--dry-run]` | Chat exports → `chat_labels.sqlite` via a model (local by default; `--backend anthropic` sends scrubbed text to the Claude API; cost-capped). |

**Analysis (read-only unless noted)**
| Script | Purpose / outputs |
|---|---|
| `barra_terrain.py` | BARRA orography vs Open-Meteo elevation → `data/forecast_error/elevations.json` (downloads `orog.nc` once) |
| `forecast_error.py [site]` | Archive vs BARRA-C2 at 11–16 local → `data/forecast_error/{error_summary.csv,pairs.sqlite,priors.json,barra_points.pkl}` **and overwrites committed `sites/<site>/priors.json`** |
| `forecast_error_report.py` | → `data/forecast_error/report.md` |
| `forecast_error_stations.py [--hour 15]` | Archive vs BOM DWO readings → `station_*.csv`, `station_pairs.pkl` |
| `model_weights.py [--publish]` | Weights = 1/(error after best bias fix)² per variable → `data/forecast_error/model_weights.json`; `--publish` writes committed `sites/<site>/model_weights.json` |
| `validate_corrections.py` | Tests BARRA priors and station-learned corrections against station readings → `correction_validation.csv` |
| `scoreboard.py [--from D] [--lead hist|d1|d2] [--launch id] [--rebuild-barra]` | Rules' XC % (A: archive, B: BARRA-C2) vs flown/best distance → `data/scoreboard/{report,days}[_d1].md/csv`. **Default `--from 2024-01-01`.** |
| `rule_tests.py [--variants …]` | Leave-one-year-out test of candidate XC rule changes using `days.csv` → `data/scoreboard/rule_tests.md` |
| `thermal_zones.py [--split ring|ew|2d]` | East/west thermals vs BARRA weather (pilot1) → `data/thermal_zones/` |

**Creo**
| Script | Purpose / outputs |
|---|---|
| `creo_test.py` | Leave-one-year-out LightGBM test → `data/creo/report.md`. Defines `PARAMS`, `FEATURES`, `dataset()`, `loyo()` reused by the others. Not in the job. |
| `creo_train.py` | Trains on every usable day, **overwrites `sites/bright/creo_model.txt`**, appends a version to `creo_model.json` (cuts, curve, counts). Not run for this baseline. |
| `creo_review.py [--log]` | Scores `predictions/creo_log.csv` against imported flights (days the model trained on excluded) → `data/creo/review.md` |
| `creo_walkforward.py [--embargo-days N] [--old-westerly] [--out NAME]` | Weekly retrain from 2025-12-01, test on forecasts issued 1 and 2 days ahead (A: Single Runs; B: Previous Runs) → `data/creo/<NAME>.md` |

**Other:** `make_icons.py` (Pillow → `pwa/icons/`), `commit.sh` (add code paths, commit with the Claude co-author trailer, pull --rebase --autostash, push), `tests/` (16 `unittest` files; see §7).

---

## 3. Data-flow table

Which script reads and writes what. `Committed` stores are marked (c); everything else is local. Store names are as in §1.

| Script | Reads | Writes |
|---|---|---|
| `collect_bom.py` | BOM feed; `sites/bright.yaml` | `observations/` (c) |
| `check_observations.py` | `observations/` | — |
| `update_corrections.py` | `forecast_archive/`, `observations/`, `corrections/corrections.json`, `sites/bright.yaml` | `corrections/corrections.json` (c) |
| `weighting_review.py` | `forecast_archive/`, `observations/`, `sites/bright/model_weights.json` | `corrections/weighting_review.json` (c); GitHub issue |
| `calibrate_valley_wind.py` | `forecast_archive/`, `observations/94889.csv`, `rules.yaml` | `corrections/valley_wind_calibration.json` (c); GitHub issue |
| `wait_for_models.py` | Open-Meteo run meta | `model_runs/` (c) |
| `daily_forecast.py` | Open-Meteo forecast API; `sites/`; `corrections/corrections.json`; `corrections/valley_wind_calibration.json`; `model_runs/latest.json`; `creo_model.txt/json`; `model_weights.json`; `predictions/log.csv` | `forecast_archive/`, `predictions/log.csv`, `weighting_log.csv`, `creo_log.csv`, `docs/` (all c) |
| `fetch_forecasts.py` | Open-Meteo Historical Forecast and Previous Runs APIs; `sites/bright.yaml`; `schema.sql` | `data/forecast.sqlite` (`fc`, `fetch_log`) |
| `init_db.py` | Open-Meteo elevation API; `sites/<id>.yaml` | `data/forecast.sqlite` (`site`, `point`) |
| `fetch_single_runs.py` | Open-Meteo Single Runs API | `data/single_runs/` |
| `fetch_barra2.py` | NCI THREDDS | `data/barra2/` |
| `barra_terrain.py` | NCI orography file; Open-Meteo elevation | `data/forecast_error/{orog.nc,elevations.json}` |
| `forecast_error.py` | `forecast.sqlite`, `barra2/`, `elevations.json`, `sites/bright.yaml` | `data/forecast_error/{barra_points.pkl,error_summary.csv,pairs.sqlite,priors.json}`; **`sites/bright/priors.json` (c)** |
| `forecast_error_report.py` | `error_summary.csv`, `pairs.sqlite` | `data/forecast_error/report.md` |
| `fetch_bom_dwo.py` | BOM climate pages | `data/bom_dwo/` |
| `forecast_error_stations.py` | `forecast.sqlite`, `barra_points.pkl`, `data/bom_dwo/` | `data/forecast_error/{station_pairs.pkl,station_summary.csv,station_vs_barra.csv,barra_vs_station.csv}` |
| `model_weights.py` | `station_pairs.pkl`, `pairs.sqlite` | `data/forecast_error/model_weights.json`; with `--publish` **`sites/bright/model_weights.json` (c)** |
| `validate_corrections.py` | `pairs.sqlite`, `station_pairs.pkl` | `data/forecast_error/correction_validation.csv` |
| `import_xcontest.py` | `xcontest_pages/*/`; `data/chat_salt` | `data/xcontest_flights.sqlite` |
| `import_igc.py` | `igc_files/<pilot>/`; `sites/launches.yaml`, `timezones.yaml`; `data/chat_salt` | `data/igc_flights.sqlite` |
| `import_chats.py` | chat exports; `sites/bright.yaml`; local or Anthropic model; `data/chat_salt` | `data/chat_labels.sqlite` |
| `scoreboard.py` | `forecast.sqlite` (`fc`), `xcontest_flights.sqlite`, `igc_flights.sqlite`, `chat_labels.sqlite` (columns only), `barra2/`, `elevations.json`, `sites/bright.yaml`, `rules.yaml`, `calendar.yaml` | `data/scoreboard/{report,days}[_d1].md/csv`, `barra_mystic.pkl/.stamp` |
| `rule_tests.py` | `data/scoreboard/days.csv`, `forecast.sqlite`, `rules.yaml` | `data/scoreboard/rule_tests*.md` |
| `creo_test.py` | as `scoreboard.py` (runs its code with `--from 2022-01-01`) | `data/creo/report.md` |
| `creo_train.py` | as `creo_test.py` | `sites/bright/creo_model.txt`, `creo_model.json` (c) |
| `creo_review.py` | `predictions/creo_log.csv`, `creo_model.json`, labels (as scoreboard) | `data/creo/review.md` |
| `creo_walkforward.py` | as `creo_test.py` + `data/single_runs/` + `fc` Previous Runs rows | `data/creo/<name>.md` |
| `thermal_zones.py` | `igc_flights.sqlite`, `barra2/` | `data/thermal_zones/` |
| `fetch_ffwx.py` | FreeFlightWx | `data/ffwx/mystic.csv` |
| `fetch_era5.py` | Copernicus CDS | `data/raw/era5/` |
| `make_icons.py` | — | `pwa/icons/` (c) |

Chain for the live product: `collect_bom` → `update_corrections` → `daily_forecast` (reads corrections, writes archive/predictions/docs) → GitHub Pages. Chain for Creo: `import_xcontest` + `import_igc` + `fetch_forecasts` → `creo_test` (check) → `creo_train` (writes the model the job loads) → `daily_forecast` (logs `creo_log.csv`) → `creo_review`.

---

## 4. Hard-coded definitions (candidates to move into Receta)

### 4.1 Flying window, hours and heights

| Definition | Value | Where |
|---|---|---|
| Flying window (config) | `["11:00", "16:00"]` inclusive = 6 hourly values | `sites/bright.yaml:5` |
| Same, as code fallback | `["11:00","16:00"]` | `scripts/daily_forecast.py:81`, `scripts/scoreboard.py:27` |
| Same, as integer hours | `range(11, 17)` | `src/pgforecast/forecast_error.py:14`, `scripts/weighting_review.py:17` |
| Valley-wind window | forecast side 12:00–16:00 inclusive (`calibrate_valley_wind.py:22`); observation side `12 <= hour < 16` (`:49`) | `scripts/calibrate_valley_wind.py:22, 49` |
| Bias-correction hours | 09:00–17:00 local, `range(9, 18)` | `src/pgforecast/corrections.py:17` |
| Dashboard hourly strip | 09:00–17:00, `range(9, 18)` | `scripts/daily_forecast.py:161, 188` |
| Hours shown in the wind table | 11:00, 13:00, 15:00 | `scripts/daily_forecast.py:107` |
| Minimum hours with data for a day to be scored | 4 of 6 | `scripts/scoreboard.py:33` (used by creo_test, rule_tests) |
| Days published | next 6 (`[:6]` from today); 7 requested from Open-Meteo | `daily_forecast.py:137, 44` |
| Days shown open on the page | 3 | `src/pgforecast/render.py:151` |
| Launch height | 797 m ASL (config); Open-Meteo elevation of the point in the DB is 786 m; ECMWF 9 km grid ≈ 709 m (`PHASE3_NOTES.md`) | `sites/bright.yaml:20`; `forecast.sqlite point` |
| Launch above landing | 477 m | `sites/bright.yaml:21` |
| Nominal height of pressure levels | 900 hPa ≈ 1,000 m, 850 ≈ 1,500 m, 800 ≈ 2,000 m, 700 ≈ 3,000 m (labels only) | `daily_forecast.py:23` |
| "Acro height" levels | 900, 850, 800 hPa | `rules.yaml:60` |
| Height words in Creo text | "1,500 m" for 850 hPa, "3,000 m" for 700 hPa | `src/pgforecast/creo.py` (`_phrase`) |
| Models used and their order | `gfs_seamless, ecmwf_ifs025, icon_seamless, bom_access_global`; GFS is the "best model" for wording | `daily_forecast.py:18, 149`; `scoreboard.py:30`; `model_weights.py:20`; `calibrate_valley_wind.py:16`; `wait_for_models.py:9` |
| Forecast points | 7 (`sites/bright.yaml:9-15`) | |
| Day label time-zone | `Australia/Melbourne` | `sites/bright.yaml:4` |

### 4.2 XC and acro rules (`sites/bright/rules.yaml`, line numbers in that file)

Score of a rule: 100 % comfortably inside the limit, ≈ 90 % at the limit, 0 % at `zero_at`; day score = weakest rule (`rules.py:35-45`, `rules.py:129`). Smoothing margin `full_score_margin: 0.25` (`rules.yaml:105`, and default argument `rules.py:35, 41`).

| Rule | Values | Line |
|---|---|---|
| Direction: launch arc 315→45 (NW–N–NE); calm below 6 km/h; unflyable arc (850 hPa) 135→247 above 25 km/h; southerly arc 135→247, valley wind 6 km/h per m/s thermal, fade 3 km/h | | 12–19 |
| Rain in window: limit 0.2 mm, zero at 2.0 mm | | 23–24 |
| CAPE: limit 800, zero at 1800 J/kg | | 26–27 |
| XC launch wind: 20 / 32 km/h | | 31–32 |
| XC gusts: 25 / 40 km/h | | 34–35 |
| XC 850 hPa wind: 25 / 45 km/h | | 37–38 |
| XC ceiling: ≥ 2000 m, zero at 1300 m ASL | | 40–41 |
| XC thermals: ≥ 1.5 m/s, zero at 0.5 | | 43–44 |
| Acro launch wind 15 / 25; gusts 25 / 40; 850 hPa 20 / 38; shear 15 / 35 km/h | | 48–58 |
| Acro northerly aloft: 5 → 20 km/h, max penalty 0.30, climb 600 → 1800 m above launch, share 0.33 | | 62–67 |
| Acro westerly aloft: weights WSW 0.5, W 1.0, WNW 1.0; limit 8, zero 25 km/h, floor 0.30 | | 69–72 |
| Acro strong thermals: 2.5 → 4.0 m/s, floor 0.60 | | 74–76 |
| Acro height: ≥ 1400 m above launch, zero at 400 | | 78–79 |
| Acro window strip: thermals ≥ 1.5 m/s (zero at 1.0); climb ≥ 1000 m above launch (zero at 600) | | 84–88 |
| Fronts: 6 h window, cold drop 4 °C, warm rise 4 °C, pressure fall 1.0 / rise 1.5 hPa, wind arcs, pre-frontal 36 h, post-frontal 48 h | | 92–102 |
| Estimates: cloud base 125 m per °C of (T − Td); heat fraction 0.3; climb fraction 0.7; depth cap 2500 m | | 109–112 |
| Ratings: Good ≥ 65 %, Marginal ≥ 35 %, "pulling down" below 0.9 | | 115–117 |
| Physics constants in code: sunshine/1200 → heat flux; `9.81/300`; cube root (Deardorff w*) | `rules.py:109-110` | |
| Southerly "valley wind beats it" logic; strong 850 hPa southerly aloft is a hard no-go | `rules.py:137-152` | |

### 4.3 Calendar, comp days, labels

| Definition | Value | Where |
|---|---|---|
| Bright Open weeks (comp days) | 2022-02-05..11, 2023-02-11..18, 2024-02-10..16, 2025-02-09..15, 2026-01-16..23 | `sites/bright/calendar.yaml:37-43` |
| Comp-day distance weight | 0.5 (a guess); count weight 0; `flyable` kept | `calendar.yaml:45`; default `src/pgforecast/scoreboard.py:9`; applied `src/pgforecast/scoreboard.py:59` |
| Public holidays (weekday ones, 2024–2026) | typed from memory | `calendar.yaml:5-34` |
| Free day | Saturday, Sunday or listed holiday | `src/pgforecast/scoreboard.py:63-65` |
| Unknown months (left out of scoreboard and Creo entirely) | 2025-09, 2025-10, 2025-11 | `calendar.yaml:49-52` |
| Month tolerance | a month short by ≤ 2 flights counts as complete | `src/pgforecast/xcontest.py:47` |
| Tandems | class `T` excluded from distances | `src/pgforecast/scoreboard.py:38` |
| XC "went XC" | best flight ≥ 20 km | `scripts/creo_train.py:28`; `creo.py` `_xc_times` |
| Short flight for the miss list | < 20 km | `src/pgforecast/scoreboard.py:10` |
| Low / high % for splits | < 40 / ≥ 60 | `src/pgforecast/scoreboard.py:11` |
| Bands | 0–20, 20–40, 40–60, 60–80, 80–100 | `src/pgforecast/scoreboard.py:8` |
| Scoreboard start | `--from 2024-01-01` default | `scripts/scoreboard.py:23` |
| Creo training start | 2022-01-01 (`creo_test.py` sets `--from 2022-01-01`) | `scripts/creo_test.py:13` |
| Chat label weight | 0.3 | `chatlog/store.py:26` |
| Pilot hash | SHA-256 of salt + name, 16 hex chars | `chatlog/store.py` |

### 4.4 Creo

| Definition | Value | Where |
|---|---|---|
| Rating words | Poor, Fair, Good, Great | `creo.py:124` |
| Rating bounds (median actual best flight) | < 5 km Poor, 5–20 Fair, 20–50 Good, ≥ 50 Great; `calibrate_cuts(bounds=(5,20,50), k=101)` | `creo.py:143` |
| Current cut points (estimate, km) | 5.838 / 13.632 / 49.344 | `sites/bright/creo_model.json` (version `ba547fb9`) |
| "Went XC" curve | `calibrate_share(k=101, points=25)` | `creo.py:152` |
| Minimum effect to mention a factor | 0.08 on the log scale (≈ 8 % in distance) | `creo.py:174` |
| "Strong thermals" wording | ≥ 2.5 m/s | `creo.py:199` |
| Panel "rules low / high" | < 35 / ≥ 65 % (duplicates `rules.yaml:115-116`) | `creo.py:286` |
| Model | LightGBM, 250 rounds, lr 0.03, 4 leaves, depth 3, `min_data_in_leaf` 25, L2 20, L1 1, bagging 0.7, feature fraction 0.7, seed 0, 2 threads | `creo_test.py:22-24` |
| Features | 9 weather speeds/sin/cos (10 m, 850, 700), gust, temp, dew point, cloud-base ASL, CAPE, rain, cloud low/mid/high, 8 rule factors, `xc_pct`, `doy`, `free` | `creo_test.py:25-26`, `creo.py:7-8` |
| Target | `log1p(best_km)`; weights = comp distance weight | `creo_test.py:70, 79` |
| Hard limit | XC % ≤ 0 → 0 km | `creo.py` `hard_limit` |
| Free-day flag at prediction | always 1 | `creo.py` `feature_row`; `creo_test.py:81` |
| Walk-forward | start 2025-12-01, weekly, min 300 training days | `creo_walkforward.py:25` |
| Review thresholds | 30 days for a range, 50 for bands | `creo_review.py:21` |
| Single Runs / Previous Runs missing variables for Part B | `BLANK_B` | `creo_walkforward.py:29` |

### 4.5 Other thresholds

| Definition | Value | Where |
|---|---|---|
| No-go day (summary wording) | score < 15 | `src/pgforecast/summary.py:33` |
| Bias correction | start after 30 days, check 10 days, gain ≥ 2 %, learning rate floor 0.05, decay 0.97, multiplier clamp 0.5–2.0, "too calm" mean forecast < 3 km/h, ≥ 4 (all) or ≥ 2 (sector) hours a day | `corrections.py:14-15, 28, 47, 49` |
| Model weighting review | 30 days, 300 bootstraps, 2 % margin, 0.9 probability | `weighting_review.py:17, 44-45` |
| Valley-wind calibration | 30 days; thermal bins 0–1, 1–1.5, 1.5–2, 2+ | `valley.py:5, 18` |
| Observation gaps | 3 h; sparse station 20 h; stale 12 h | `check_observations.py:8, 9, 33` |
| Wait for model runs | 75 min; typical delays ECMWF 7.5 h, GFS 8.5 h, ICON 3.75 h | `wait_for_models.py:9, 10` |
| Forecast-error cells | ≥ 150 paired hours on ≥ 25 days; direction ignored under 8 km/h; lapse 6.5 °C/km; no height fix for `ridge_ref` points | `scripts/forecast_error.py:22`, `forecast_error.py:34, 90, 99` |
| Seasons | Dec–Feb summer, Mar–May autumn, … | `forecast_error.py:12` |
| IGC | take-off 3 m/s, min flight 120 s, vehicle 15 m/s, circling 4 °/s, thermal ≥ 360°, low rate > 5 s median gap, climb ≥ 0.3 m/s | `igc.py:11-17` |
| FFWX | a 10-min row is "full" when readings ≤ 30 s apart | `ffwx.py:10` |
| Download chunk | 90 days; fetch stops at today − 3 days | `fetch_forecasts.py:8, 26` |
| Start of Previous Runs / Historical per model | see §1.3 | `fetch_forecasts.py:18-23` |
| `prev` variables | `PREV_VARS` | `fetch_forecasts.py:15` |
| Single Runs | from 2026-04-01, 00:00 UTC run, 3 days | `fetch_single_runs.py:10, 27` |
| BARRA boxes, variables, 2008-01 → 2026-07 | | `fetch_barra2.py:15-35` |
| ERA5 | area, 0.25°, 3-hourly, single request at a time | `fetch_era5.py` |
| Chat prompt version | `chat-v3` | `chatlog/extract.py:5` |
| AI opinion model | `claude-haiku-5-5` (retired) | `ai_opinion.py:6` |
| Chat importer price table | USD per M tokens for `claude-haiku-5-5` | `chatlog/llm.py:30` |
| Sites to be added | Organyà, Flowerdale, Dandenongs, Manilla (launch list only) | `sites/launches.yaml` |

---

## 5. Time handling and suspected leaks

### 5.1 Where time zones are converted

| Place | What happens |
|---|---|
| `daily_forecast.py:17, 44` | Open-Meteo is asked for `timezone=Australia/Melbourne`; `hourly.time` are **naive local strings**. The window is selected by comparing strings (`"11:00" <= t[11:16] <= "16:00"`, line 140). `now` is UTC; `local_now` is local; `lead_days` = target local date − local date of the run (line 202). Archive file name is UTC. |
| `fetch_forecasts.py:33`, `fetch_single_runs.py:27` | `timezone=UTC`; `fc.time_utc` and `data/single_runs` hours are UTC. Values stamped at the hour; Open-Meteo's accumulated or averaged variables (precipitation, radiation) cover the **preceding** hour. |
| `scoreboard.py:44-45` (`window`) | `time_utc` → `tz_localize("UTC")` → `tz_convert(Australia/Melbourne)`; keep local hours 11–16; local date. Used by creo_test, creo_walkforward, rule_tests. |
| `scoreboard.py:99` | BARRA hourly means are stamped at HH:30 UTC; shifted by +30 min so the label is the hour they end (same convention as the forecasts). |
| `forecast_error.py` (script) `:81`, `forecast_error_stations.py:40` | UTC → local for the 11–16 window / station hour. |
| `update_corrections.py:20-21, 45, 49, 60` | BOM UTC half-hours are bucketed to the nearest hour (obs at 10:30 and 11:00 → hour 11:00); archive local strings get the local zone via `replace(tzinfo=…)` and are converted to UTC for the observation lookup. A forecast file "made on day X covers day X+1"; the **last file of local day D−1** is the forecast for day D. |
| `calibrate_valley_wind.py:48-49, 56`, `weighting_review.py:54, 62-67` | Same pattern; lead = local target date − local run date. |
| `xcontest.py:41-45, 62-80` | Page dates/times are local with a printed `UTC±hh:mm` offset (stored). Month completeness is judged on UTC months; the day-status check uses the site's single most common offset for all dates (daylight saving ignored). |
| `igc.py:180-190` | Fixes are UTC epochs; local date/time come from the launch coordinates' zone (`tzlookup.py`); `date` is the **local date of the first fix**. |
| `ffwx.py`, `fetch_ffwx.py` | `time_utc` and `time_local` both stored; `thinned` rows have unreliable gusts. |
| `chatlog` | Message timestamps are as exported (no zone conversion); `date_source` says whether the date was stated or is the message day. |
| `wait_for_models.py` | Open-Meteo run times as UTC epochs; typical availability delays are hard-coded (§4.5). |
| `.github/workflows/daily.yml` | Cron in UTC: 20:45 and 08:45 (07:45/19:45 AEDT, 06:45/18:45 AEST). |
| `creo.py` `feature_row` | `doy` from the local target date string. |
| `render.py` / `forecast.json` | `updated_local` is a local-time string. |

### 5.2 Suspected leaks (listed, **not fixed**)

"Leak" here = a place where prediction or training could see information from after the moment the forecast would really have been issued, or from the target day itself. Severity is a judgement; none is verified by experiment unless stated.

| # | Suspected leak | Where | Why it matters |
|---|---|---|---|
| L1 | **Training inputs are near-analysis, serving inputs are day-ahead.** Creo, the scoreboard (A) and the model weights are built from the Historical Forecast API (`source='hist'`: the first hours of each model run, stitched). For the 11:00–16:00 window those hours are 0–12 h after the run starts, so they contain the day's actual weather almost as analysed. The live job feeds Creo forecasts 0–5 days ahead. `PHASE3_NOTES.md` and `forecast_error_report.py` already call this "near-analysis, slightly optimistic". | `creo_test.py:29-33`, `creo_train.py`, `scoreboard.py:56-78`, `creo_walkforward.py` training pool | Leave-one-year-out numbers (diff +0.083) are probably optimistic about real day-ahead use; the walk-forward test (day-ahead test inputs, same training) gives +0.044 (A) and +0.032 (B). |
| L2 | **Leave-one-year-out trains on later years to predict earlier ones** (e.g. test 2022 with a model trained on 2023–2026). | `creo_test.py:74-84` | Not a real forecasting order; the walk-forward test is the honest one. |
| L3 | **Rules were adjusted after seeing the flights they are scored on.** The XC thresholds, the westerly rule (tried 2026-11-26/27 and reverted), `unknown_months`, the comp weight and the model weights were chosen with 2024–2026 flights and BARRA/BOM errors in view; `xc_pct` and the eight rule factors are also Creo's top features (24 % + ~20 % of gain). | `rules.yaml`, `rule_tests.py`, `creo_test.py:26` | "Rules vs flights" and "Creo vs rules" are in-sample for the rules. `creo_walkforward.py --old-westerly` was a partial check. |
| L4 | **Lead-0 log rows can be made after the weather happened.** The log has runs at 01–06 UTC (12:00–17:00 local) and 09–10 UTC; `daily_forecast.py` scores every day from today on and writes `lead_days=0` for today even when the window is over (Open-Meteo returns analysis-like values for past hours of today). `creo_review.py` keeps "the LATEST row per (date, lead)" and the dashboard's last-14-days table keeps "the last prediction made for each past day". | `predictions/log.csv` (lead 0 at 12–17 h local), `creo_review.py:30`, `daily_forecast.py:250-254` | Lead-0 scores and the dashboard's track record can include forecasts made during or after the flying window. Lead ≥ 1 rows are safe. |
| L5 | **Label-quality selection uses later information.** Which months/days enter scoreboard and training depends on XContest monthly totals read after the fact (`month_complete`, `xc_day_status`) and on hand-picked `unknown_months`. | `scoreboard.py:141`, `xcontest.py:62-80`, `calendar.yaml:49` | Selects days with hindsight; modest, but also changes results between reruns when pages are added. |
| L6 | **Walk-forward has no label delay.** Week W is trained on every day before W (embargo 0 by default), but XContest flights are uploaded days later, so the labels of the last days before W would not exist yet in real use. `--embargo-days 14` exists; results in `data/creo/wf_embargo14.md` (older rules). | `creo_walkforward.py:25, 67` | Slightly optimistic walk-forward. |
| L7 | **Scoreboard includes days from incomplete months.** 119 days (all "flown" by construction, because days with no saved flights are `unknown`) from 2023-02, 2024-01..03, 2026-01, 2026-04 stay in `days.csv`; Creo training drops them. Not a time leak but a selection bias that raises the scoreboard correlation (2022-on: +0.677 with them, +0.644 without; 2024-on: +0.701 vs +0.652). | `scoreboard.py:141`, `src/pgforecast/scoreboard.py:57` | The scoreboard headline and the Creo report's "rules" column are not computed on the same days. |
| L8 | **Bias corrections applied live but not in training.** Not a leak (the corrections use only past days), but a train/serve mismatch: the live job corrects forecasts in place before scoring and before Creo; training uses uncorrected `hist` data. No bin is active yet, so no effect today. | `daily_forecast.py:71-77`, `corrections.json` | Matters once any bin becomes active. |
| L9 | **Reanalysis as truth.** BARRA-C2 (B scores) and ERA5 include observations of the day itself; they must never be inputs to anything that is used before the day. Currently they are only used for B scores, error statistics and weights. | `scoreboard.py:84-127`, `forecast_error.py`, `model_weights.py` | Guard for the planned ERA5 work. |
| L10 | **Rating cut points and the "went XC" curve** are fitted on leave-one-year-out estimates of all past days, including the days later used on the dashboard's track record. `creo_review.py` excludes days ≤ `trained_through`, so the review itself is clean. | `creo_train.py:19-29` | Descriptive only. |

Checked and found clean: `update_corrections.py` and `weighting_review.py` only use observations of days before today and the last forecast issued the evening before; `daily_forecast.py` writes the raw archive before applying corrections; `creo_walkforward.py` asserts that training days all precede the test week (`:68`).

---

## 6. External services and secrets (names only)

| Service | Used for | Secret / identifier |
|---|---|---|
| Open-Meteo forecast API (`api.open-meteo.com`), model meta files, elevation API | Daily forecast, run status, point elevation | none (free tier; 10,000 weighted calls/day for archives) |
| Open-Meteo Historical Forecast, Previous Runs, Single Runs APIs | `fc` table, `data/single_runs/` | none |
| BOM (`bom.gov.au/fwo/IDV60801`, `bom.gov.au/climate/dwo`) | Station observations, daily observations | none (browser-like user-agent string) |
| NCI THREDDS (`thredds.nci.org.au`) | BARRA-C2, orography | none (CC BY 4.0) |
| Copernicus Climate Data Store | ERA5 | CDS key in `~/.cdsapirc`; licence accepted on the CDS site |
| FreeFlightWx (`freeflightwx.com/mystic`) | Mystic station | `FFWX_CONTACT` env var (set in `~/.profile`) for the user-agent |
| Anthropic API | `import_chats.py --backend anthropic`; retired `ai_opinion.py` | `ANTHROPIC_API_KEY` env var (never in the repo) |
| Local model server (Ollama / llama.cpp on localhost) | `import_chats.py` default backend | none (`LocalLLM` refuses non-local hosts) |
| GitHub Actions / Pages / Issues | Daily job, dashboard hosting, notification issues | `GITHUB_TOKEN` / `GH_TOKEN` (provided by Actions); repo variable `MODEL_WEIGHTING` |
| XContest | **Never fetched.** Only pages saved by hand | personal login (not stored in the repo) |
| `data/chat_salt` | Hashing pilots, chats and senders | local secret file, mode 600 |
| systemd user units | `ffwx.timer`/`ffwx.service`, `era5.service` | none |

---

## 7. Known inconsistencies found while writing this file

(Also reported in the hand-over message. Not fixed.)

1. `CLAUDE.md` says tests run with `.venv/bin/python -m pytest tests`; pytest is **not installed** and the tests are `unittest` files. They pass with `PYTHONPATH=src .venv/bin/python -m unittest tests.<module>` (145 tests in 16 modules; `test_fronts` needs `PYTHONPATH=src`).
2. `CLAUDE.md` Status block still says "Creo not started" although Creo (beta) is live.
3. `CLAUDE.md` calls the scoreboard "2024 on", but `data/scoreboard/report.md` and `days.csv` on disk were made with `--from 2022-01-01` (and `rule_tests.py` reads that `days.csv`). The script default is 2024-01-01.
4. `scoreboard.py` `weakest()` breaks ties by set order, so `limit_a`/`limit_b` ("held back by") change between runs (51 and 84 of 1637 rows differed in the baseline rerun; scores did not).
5. `data/creo/report.md` and `walkforward.md` on disk before the baseline predated the westerly revert and the unknown-months change; they are superseded by the baseline run.
6. `daily_forecast.py:31` falls back to `"weighted"` when neither the env var nor the YAML sets a mode; the project's stated mode is `equal` (set in YAML, so no effect today).
7. `collect_bom.py` and `check_observations.py` always use the repo's `observations/`, unlike other job scripts (`data/local_out/` off GitHub), so running them on the laptop dirties a committed folder.
8. `scripts/forecast_error.py` overwrites the committed `sites/bright/priors.json`, which CLAUDE.md says is "reference only".
9. `launch_height_m` 797 m vs 786 m in the `point` table vs ECMWF grid ~709 m; the rules use 797.
10. The duplicated 35 / 65 % cut-offs: `rules.yaml:115-116` and `creo.py:286`.
11. `predictions/log.csv` holds many more runs per day than the two scheduled ones (manual dispatches and delayed cron); see L4.
12. `data/xcontest_flights.before_2022_23.sqlite` and `creo_model.json` versions `8a7f80f5`, `cef1315c` are stale but still present.
