# CLAUDE.md — paragliding forecast

## What this is
A daily forecast of how good tomorrow (and the next 5 days) is for **Mystic launch, Bright, Victoria**: an XC % and an acro %, shown on a phone-friendly dashboard.
Built so more sites can be added later: Organyà (restitution), Flowerdale, Dandenongs, Manilla. A site = `sites/<id>.yaml` (location, timezone, flying window, BOM stations) + `sites/<id>/rules.yaml` (limits) + `sites/<id>/notes.md`.
Owner: Fabian. Plain, short explanations; he is technical but not a data scientist.

## Names
- **"The rules"** = Fabian's and his brother's flying limits in `sites/bright/rules.yaml` (each rule scores 0–100%; a day's score is its weakest rule).
- **Creo** = the planned machine-learning model. Not built yet.
- **AI second opinion** = a Claude panel on the dashboard (`src/pgforecast/ai_opinion.py`). It comments; it never changes the scores.

## How it runs
- **Model weighting**: the job blends the models per variable with `sites/bright/model_weights.json` (ACCESS-G dropped, GFS out of gusts) and then scores the blend. **Currently `model_weighting: equal` (the old method stays live until the one-month review, ~2026-12-26)**; switch with the GitHub variable `MODEL_WEIGHTING` (`weighted` or `equal`, Settings > Variables) or `model_weighting` in `sites/bright.yaml`. Both blends and the old average are logged in `predictions/weighting_log.csv`.
- **GitHub Actions** (`.github/workflows/daily.yml`, 07:45 and 19:45 Melbourne time): collect BOM observations, update bias corrections, valley-wind calibration check, wait for the newest ECMWF/GFS/ICON runs, fetch forecasts, score, rebuild the dashboard, commit the results back to `main` as "Daily update".
- **Laptop (by hand)**: everything that isn't the daily job runs here today: downloaders (`fetch_barra2.py`), importers (`import_xcontest.py`, and `import_chats.py` using Haiku via the API, key from `ANTHROPIC_API_KEY`, cost-capped). Weekly jobs are **planned, not built**.
- **Home desktop (later)**: heavy downloads and local AI (e.g. a local model for the chat importer). Not in use yet.
- **Where data lives**: the daily job commits only `observations/ forecast_archive/ predictions/ model_runs/ corrections/ docs/`. Everything else is local and git-ignored: `data/` (SQLite: forecasts, chat labels, XContest flights, BARRA-C2 in `data/barra2/`, the hashing salt `data/chat_salt`), `xcontest_pages/`, `chat_exports/`, `igc_files/<pilot>/` (pilots' IGC/GPX tracks, shared with permission; folder = pilot identity). Backed up by hand to Fabian's Pi.

## Layout
- `sites/` — one config per site (`bright.yaml`) and a folder with `rules.yaml` + `notes.md`.
- `src/pgforecast/` — the code. `rules.py`/`rules_config.py` scoring; `summary.py` plain-English text; `render.py` dashboard; `fronts.py` cold/warm fronts; `valley.py` valley wind; `corrections.py` bias correction; `ai_opinion.py`; `xcontest.py` saved-page parser; `forecast_error.py` error vs BARRA-C2 maths; `igc.py` IGC/GPX track parser/thermal finder; `tzlookup.py` timezone from coordinates; `chatlog/` chat importer.
- `scripts/` — entry points (below). `scripts/commit.sh` = pull --rebase then push.
- `tests/` — pytest-style tests, one file per module.
- `docs/` — the published dashboard (GitHub Pages). Written by the daily job only.
- `forecast_archive/`, `predictions/`, `observations/`, `model_runs/`, `corrections/` — daily job output (history Creo will learn from).
- `data/`, `xcontest_pages/`, `chat_exports/` — local only. `design/`, `pwa/` — mockups, app icons/manifest.
- Notes: `BRIGHT_MODEL_BRIEF.md` (original brief), `PHASE3_NOTES.md` (forecast archive sources), `CHAT_IMPORT.md`. Plans: `TODO.md`. Decisions: `DECISIONS.md`.

## Adding a site (any country)
1. `sites/<id>.yaml`: copy `bright.yaml` — location points, timezone, flying window. Put BOM stations in only for Australian sites.
2. `sites/<id>/rules.yaml` (limits) and `notes.md`.
3. `sites/launches.yaml`: one entry per launch, each with `site: <id>`, its own `id`, region, country (ISO), timezone, lat/lon and `radius_m`. Several launches can share one site. Never rename a launch `id` once flights are imported.
4. Re-run `python scripts/import_igc.py igc_files`: it re-tags every stored flight to the current launch list, so new or moved launches apply to flights already imported.
5. Weather for past days: **BARRA-C2 is Australia-only** (BOM reanalysis, `scripts/fetch_barra2.py`). Overseas sites (Organyà, Bir...) need **ERA5** (global, ~25 km, hourly, Copernicus CDS), or **CERRA** for Europe (~5.5 km, covers 1984 to about mid-2021; check the CDS page for its end date). Not built yet. Timestamps are UTC in all of them; the site's own timezone is only for display.
6. Track timezones need no setup: they come from each flight's coordinates (`tzlookup.py`; oddities in `sites/timezones.yaml`).

## Run the main scripts (from the project root, `.venv` active)
- `python scripts/daily_forecast.py bright` — fetch, score, rebuild `docs/` (normally run by GitHub).
- `python scripts/fetch_forecasts.py bright [--until YYYY-MM-DD]` — fetch/archive forecasts.
- `python scripts/collect_bom.py bright` — append BOM station observations.
- `python scripts/update_corrections.py bright` — bias corrections from forecasts vs observations.
- `python scripts/calibrate_valley_wind.py bright` — valley-wind check (reports only).
- `python scripts/fetch_barra2.py bright` — BARRA-C2 reanalysis boxes into `data/barra2/` (resumable).
- `python scripts/import_chats.py chat.txt --default-site bright` — chat labels (`--backend anthropic` for Haiku; `--dry-run` to count).
- `python scripts/import_xcontest.py --summary` — import saved XContest pages; warns about incomplete months.
- `python scripts/import_igc.py igc_files` — import `.igc` and `.gpx` tracks from `igc_files/<pilot>/` offline into `data/igc_flights.sqlite` (flights, thermal circles, launch site by coordinates, sample-rate flag). Folder name = pilot (hashed). Each flight's local time uses the timezone of its launch coordinates (`tzlookup.py` using the `timezonefinder` package, laptop only: `pip install -r requirements-igc.txt`; hand overrides in `sites/timezones.yaml`). Re-run safe; prints a summary and lists skipped files.
- `python scripts/thermal_zones.py --split ring` — Emily vs Marcus thermals at Mystic vs BARRA-C2 weather (read-only analysis, pilot1 only by default; writes `data/thermal_zones/`).
- `python scripts/forecast_error.py bright` then `python scripts/forecast_error_report.py` — forecast error vs BARRA-C2 (11:00–16:00 local; read-only). Writes `data/forecast_error/` (`error_summary.csv`, `pairs.sqlite`, `priors.json`, `report.md`). Dashboard unchanged.
- `python scripts/barra_terrain.py bright` — BARRA terrain height vs Open-Meteo elevation per point (`data/forecast_error/elevations.json`; needed by `forecast_error.py`).
- `python scripts/fetch_bom_dwo.py 202103` then `python scripts/forecast_error_stations.py` — BOM Daily Weather Observations (Hotham, Falls Creek, Wangaratta; ~13 months) and the forecast-vs-station check.
- `python scripts/model_weights.py [--publish]` — per-variable model weights (`data/forecast_error/model_weights.json`); `--publish` writes the committed `sites/bright/model_weights.json` that the daily job reads (approved 2026-11-26). `sites/bright/priors.json` = BARRA-based corrections kept for reference only: **do not apply** (they made real error worse; `scripts/validate_corrections.py`).
- `python scripts/weighting_review.py bright` — equal vs weighted blends scored against the BOM stations (runs in the daily job; opens one GitHub issue after 30 days). `python scripts/check_observations.py bright` — gaps in the saved BOM observations (also in the job). `python scripts/validate_corrections.py` — tests bias corrections against real station readings.
- `scripts/commit.sh "message"` — pull --rebase, add code paths only, commit, push.
- Tests: `.venv/bin/python -m pytest tests`.

## Standing rules
- **Git from the laptop**: pull first. Never commit `docs/`, `forecast_archive/`, `predictions/`, `observations/`, `model_runs/`, `corrections/` — the daily job owns them. `scripts/commit.sh` adds only code paths (never `git add -A`) and the pre-commit hook blocks those folders as a backup; use `commit.sh` or add paths explicitly.
- **Never commit** data, databases, credentials/API keys, `chat_exports/`, `xcontest_pages/`, or pilot names.
- **Never fetch anything from XContest.** Only read pages Fabian saved by hand into `xcontest_pages/`.
- **Pilot names are always hashed** (`store.pilot_hash`, salt in `data/chat_salt`); never store or print raw names.
- **Ask first** before anything that costs money (paid APIs, big downloads), deletes data, or changes the live site in a big way.
- Units: **km/h, metres, Victorian local time** (Australia/Melbourne, AEST/AEDT). Heights ASL unless stated.
- The AI second opinion **never changes scores**.
- The rules' **deal-breakers are hard limits**: no model (including Creo) may override them.
- Months where saved XContest flights < XContest's total are **incomplete**; days with no saved flights there are **unknown**, not "nobody flew" (`xc_day_status`). Tandem (class T) is stored but excluded from XC grading (`xc_flight_graded`).
- Keep this file, the README-style notes and the tests current when behaviour changes. Log decisions in `DECISIONS.md`; tick `TODO.md`.

## Status (2026-11-26)
- Daily forecast live twice a day: 6 days, XC and acro %, hourly windows, fronts, plain-English summary, AI second opinion.
- BOM observations and bias corrections run daily; valley-wind calibration is reporting only (rules unchanged).
- BARRA-C2 downloader and chat-label importer built; chat labels are weak evidence.
- XContest importer built; Mystic 2026 imported (Jan and Apr incomplete, flagged); older years to be saved by hand.
- IGC/GPX importer built; per-pilot folders (`pilot1` 509 unique flights, `pilot2` 620). Flights with a track sample gap over ~5 s are flagged `low_rate` (thermals unreliable). Emily vs Marcus analysis done on pilot1 (`scripts/thermal_zones.py`); not used by the dashboard.
- Forecast error vs BARRA-C2 and BOM stations done (2026-11-26); per-variable model weights are built and logged but the old method stays published until the review; BARRA-based corrections tested and rejected; correction layer waits for Mystic station readings (see `TODO.md`). Creo not started.
