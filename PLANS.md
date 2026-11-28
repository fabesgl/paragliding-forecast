# PLANS.md — what each part of the system does and how far along it is

Plain English, for Fabian and anyone following the build. State on **2026-11-28** (tag `baseline-2026-11-28`). The technical detail is in `SPEC.md`; the key numbers are in `BASELINE.md`.

The parts follow the system map: **raw data → source tables → Receta → Correction → Rules → Pattern hunter → Creo → Dashboard → Check.**

Status words: **Built** (works and is in use), **Partly built** (some of it works, some is missing), **Not built yet**.

| Part | Status |
|---|---|
| 1. Raw data | Partly built |
| 2. Source tables (time-stamped) | **PLANNED, NOT BUILT** (today: separate local databases, not time-stamped to this definition) |
| 3. Receta | **PLANNED, NOT BUILT** |
| 4. Correction | Built, but switched off for now |
| 5. Rules | Built |
| 6. Pattern hunter | **PLANNED, NOT BUILT** |
| 7. Creo | Beta, running beside the rules |
| 8. Dashboard | Built |
| 9. Check | Partly built |

Parts marked **PLANNED, NOT BUILT** do not exist in the code. Their definitions below are the agreed plan; `SPEC.md` describes only what exists today.

---

## 1. Raw data — Partly built

**What it does.** Collects everything the system learns from, exactly as the source gave it, and never edits it.

**What exists now**
- **Forecasts, past:** about 2.7 million hourly rows in a local database, from 2017 for ECMWF (the European model) and from 2021–2024 for the others. Two kinds: "short lead" (what the model said a few hours before; looks good but is a bit too good) and "1 and 2 days ahead" (the honest kind, but fewer variables). Up to 2026-11-23.
- **Forecasts, live:** since 2026-11-25 the daily job saves every raw forecast it fetches (42 files so far). This is the honest record Creo will learn from later. It is only four days old.
- **Flights:** 4,734 Mystic flights from saved XContest pages (2022 to Nov 2026; six months are incomplete) and 1,140 flights from pilots' tracks (two pilots). Chat messages gave 447 weak labels.
- **Weather "truth":** BARRA-C2 (BOM's 4.4 km re-analysis, 2008 to July 2026) is downloaded. ERA5 (global, 2008 on) is being downloaded right now and has just started (1 of 453 files verified when this was written).
- **Observations:** BOM station readings, saved every run since 2026-11-22. The Mystic weather station (FreeFlightWx) is saved daily since 2026-10-27.
- **Missing:** older XContest years (2020, 2021 folders are empty); any weather station at Mystic itself in the BOM feed; nothing yet for other sites.

**Reads:** Open-Meteo, BOM, NCI (BARRA-C2), Copernicus (ERA5), FreeFlightWx, your saved XContest pages, pilots' track files, chat exports.
**Writes:** the local `data/` folder (not on GitHub) and, from the daily job, `forecast_archive/` and `observations/` (on GitHub).

---

## 2. Source tables (time-stamped) — PLANNED, NOT BUILT

**Definition.** One raw table per data source: forecasts, ERA5, BARRA, stations, flights. Every row is stamped with when it happened. Forecast rows carry two stamps: **made-time** (when the forecast was issued) and **valid-time** (the moment it is about).

**Time gate (part of the plan).** Each script only sees rows stamped before the time it is predicting for. For a forecast made at time T, a script may read rows with made-time and event time before T and nothing later. This is what removes the leaks listed in `SPEC.md` section 5.2.

**What exists today instead.** Separate local databases made for particular jobs: a forecast table (`fc`, valid-time only, and "short lead" rows have no true made-time), flights from XContest and from pilots' tracks, chat labels, and raw ERA5/BARRA/station files. A per-day table is rebuilt each time the scoreboard runs. None of it has the stamps or the gate described above.

**Reads (planned):** raw data. **Writes (planned):** one table per source.

---

## 3. Receta — PLANNED, NOT BUILT

**Definition.** One shared file of definitions that every script reads: flying window, heights, comp days, unknown months, weather types. No script keeps its own copy.

**What exists today instead.** The same definitions are written into code and settings files in dozens of places, some repeated and slightly different (list in `SPEC.md` section 4). A few are guesses (the comp-day weight of 0.5, the 2026 holidays).

**Plan.** Move them into Receta one at a time with no change to any result, checking each move against `BASELINE.md`.

---

## 4. Correction — Built, but switched off for now

**What it does.** Learns, from station readings, whether a model is usually too windy or too warm at a place, and nudges the forecast to fix it. A correction only switches on after 30 days of data and 10 days of proven help.

**State.** Running every day. It has **2 days of data**, so no correction is active yet. A test with BARRA-C2 as the truth made real errors worse, so those "priors" are kept for reference and not used. The plan to fix this properly waits on readings from a station at Mystic itself (you are asking the station owner).

**Also built:** a per-variable model weighting (which model to trust for wind, which for temperature). It is calculated and logged every day but the old plain average is still what you see, until the one-month review around 2026-12-26.

**Reads:** the forecast archive and BOM readings. **Writes:** `corrections/` (on GitHub).

---

## 5. Rules — Built

**What it does.** Your and your brother's flying limits, written as numbers. Each rule gives 0–100%; a day's score is its weakest rule. There is an XC score and an acro score, with hard no-go limits (for example a strong southerly aloft) that nothing else may override. Front detection (cold/warm) is shown as information only.

**State.** Live and stable since the westerly-wind change was reverted on 2026-11-27. How well it does against real flying is in `BASELINE.md` (rank correlation +0.64 on complete months since 2022).

**Known weak spots.** Winter ranks worst (rank correlation +0.44 in winter against +0.51 to +0.70 in the other seasons). Valley-wind strength is a default (6 km/h per m/s of thermal), not yet checked against real readings (needs 30 days from a station near Bright).

**Reads:** `sites/bright/rules.yaml` and the forecast. **Writes:** nothing itself; the daily job publishes the scores.

---

## 6. Pattern hunter — PLANNED, NOT BUILT

**Definition.** A script that finds weather setups in the days before rule-good days, using ERA5, BARRA and station tables. Its rules:
- a pattern needs at least 30 days;
- the pattern must hold in other years;
- every clue must have a live stand-in from forecasts;
- it reruns whenever the rules change;
- it never uses flights (flights are left to Creo).

**What exists today instead.** One-off analyses run by hand, none of which meets the rules above: the scoreboard's "biggest misses", `rule_tests.py` (which does use flights), and the east-vs-west thermal study.

---

## 7. Creo — Beta, running beside the rules

**What it does.** A machine-learning model (LightGBM) that predicts the best flight of the day from the forecast weather, the rules' scores and the time of year. On the dashboard it shows only a word (Poor / Fair / Good / Great) and a few lines of reasons; it never shows kilometres and never changes a score or overrides a no-go.

**State.**
- Trained on 1,510 days (2022 to 2026-11-22, complete months only). Model file `sites/bright/creo_model.txt`.
- In the daily job since 2026-11-27, logging every prediction next to the rules' % in `predictions/creo_log.csv` (36 rows so far, from 3 model versions; 24 from the current one). The review script needs about a month of flights to judge it (planned around 2026-12-28).
- Tests (numbers in `BASELINE.md`; the headline is the walk-forward gain): Creo beats the rules on every year in leave-one-year-out (+0.083 overall). On honest day-ahead forecasts, by walk-forward test, the gain is smaller (+0.044 with full variables, +0.032 with fewer) and the lower end of the range touches zero in places.

**Cautions** (details in `SPEC.md` section 5): the training weather is "short lead", which is more accurate than a day-ahead forecast; Creo uses the rules' scores as inputs, and those rules were set after you had seen the flights.

**Reads:** forecast archive, flights, rules. **Writes:** `sites/bright/creo_model.*` (when retrained by hand), `predictions/creo_log.csv`, `data/creo/` reports.

---

## 8. Dashboard — Built

**What it does.** A phone-friendly page (GitHub Pages, installable as an app) showing the next six days: XC % and acro %, an hourly strip, wind at each height, fronts, a plain-English summary, the Creo (beta) panel and a track record of the last 14 days.

**State.** Updates twice a day (about 07:45 and 19:45 Melbourne time) after waiting for the newest ECMWF, GFS and ICON runs. Only Mystic. Adding another site means a new config file and rules (Organyà, Flowerdale, Dandenongs and Manilla are planned; launch positions exist for some).

**Reads:** the forecast, corrections, Creo. **Writes:** `docs/` (on GitHub).

---

## 9. Check — Partly built

**What it does.** Asks "is this actually any good?" and "did something break?"

**Built**
- **Scoreboard:** the rules against real flying, from archived forecasts and from BARRA-C2.
- **Creo tests:** leave-one-year-out, walk-forward, and a review of the live log.
- **Data checks:** gaps in BOM readings, XContest month completeness, forecast error against BARRA-C2 and station readings.
- **Automatic reviews:** model weighting and valley-wind strength each open one GitHub issue when they have 30 days of evidence.
- **Unit tests:** 145 tests, all passing.

**Not built / weak**
- Nothing watches the daily job for silent failure beyond GitHub's own red cross.
- No single check that the same flying-window and cut-off definitions are used everywhere (Receta is planned for this).
- Several known ways a test can look better than it is (list of suspected leaks in `SPEC.md` section 5). None has been fixed or measured yet.
- The scoreboard and Creo's tests now use the same days (complete months only), so their "rules" numbers agree (+0.64).

**Reads:** nearly everything. **Writes:** reports in `data/` and two JSON files in `corrections/`.

---

## What happens next

Fixed dates already in `TODO.md`: about 2026-12-26 read the model-weighting review and decide; about 2026-12-28 run the Creo review; ask the Mystic station owner for readings. Nothing else is added here.
