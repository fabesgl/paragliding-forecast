# PLANS.md — what each part of the system does and how far along it is

Plain English, for Fabian and anyone following the build. State on **2026-11-28** (tag `baseline-2026-11-28`). The technical detail is in `SPEC.md`; the key numbers are in `BASELINE.md`.

The parts follow the system map: **raw data → source tables → Receta → Correction → Rules → Pattern hunter → Creo → Dashboard → Check.**

Status words: **Built** (works and is in use), **Partly built** (some of it works, some is missing), **Not built yet**.

| Part | Status |
|---|---|
| 1. Raw data | Partly built |
| 2. Source tables | Partly built |
| 3. Receta | Not built yet |
| 4. Correction | Built, but switched off for now |
| 5. Rules | Built |
| 6. Pattern hunter | Not built yet |
| 7. Creo | Beta, running beside the rules |
| 8. Dashboard | Built |
| 9. Check | Partly built |

Where this file says what a part is *meant* to be (Receta, Pattern hunter), it is my reading of your notes. Please correct it if it is wrong.

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

## 2. Source tables — Partly built

**What it does.** Turns the raw files into tidy tables the other parts can read without caring where the data came from: one row per hour per model, one row per flight, one row per day with "was it flown, how far".

**What exists now.** The tables are there, but they are separate databases made for particular jobs, not one agreed set:
- a forecast table (`fc`), a flights table for XContest, one for pilot tracks, one for chat labels;
- a "day table" that is rebuilt every time the scoreboard runs (`days.csv`) and is not stored anywhere permanent.

**What is missing.** ERA5 and BARRA-C2 are still raw files, not tables. The Mystic station is not joined to anything. There is no single table of "one row per day with the weather the forecast gave and what happened".

**Reads:** raw data. **Writes:** local databases in `data/`.

---

## 3. Receta — Not built yet

**What it is meant to be.** The one place where every *definition* lives: the flying window, heights, which days are competition days, which months are unknown, how a month counts as complete, the Poor/Fair/Good/Great cut points, and the rule limits.

**Where things are today.** These are written into the code and settings files in dozens of places (listed in `SPEC.md` section 4). Some are repeated: the flying window appears in at least six places with slightly different forms, the 35% / 65% cut points in two. A few are guesses (the competition-day weight of 0.5, the 2026 holidays).

**Plan.** Move them into Receta one at a time, with no change to any result, and check each move against `BASELINE.md`.

**Reads / writes:** nothing yet.

---

## 4. Correction — Built, but switched off for now

**What it does.** Learns, from station readings, whether a model is usually too windy or too warm at a place, and nudges the forecast to fix it. A correction only switches on after 30 days of data and 10 days of proven help.

**State.** Running every day. It has **2 days of data**, so no correction is active yet. A test with BARRA-C2 as the truth made real errors worse, so those "priors" are kept for reference and not used. The plan to fix this properly waits on readings from a station at Mystic itself (you are asking the station owner).

**Also built:** a per-variable model weighting (which model to trust for wind, which for temperature). It is calculated and logged every day but the old plain average is still what you see, until the one-month review around 2026-12-26.

**Reads:** the forecast archive and BOM readings. **Writes:** `corrections/` (on GitHub).

---

## 5. Rules — Built

**What it does.** Your and your brother's flying limits, written as numbers. Each rule gives 0–100%; a day's score is its weakest rule. There is an XC score and an acro score, with hard no-go limits (for example a strong southerly aloft) that nothing else may override. Front detection (cold/warm) is shown as information only.

**State.** Live and stable since the westerly-wind change was reverted on 2026-11-27. How well it does against real flying is in `BASELINE.md` (rank correlation about +0.64 to +0.70 depending on the days counted).

**Known weak spots.** Winter ranks worst (rank correlation +0.44 in winter against +0.64 to +0.73 in the other seasons). Valley-wind strength is a default (6 km/h per m/s of thermal), not yet checked against real readings (needs 30 days from a station near Bright).

**Reads:** `sites/bright/rules.yaml` and the forecast. **Writes:** nothing itself; the daily job publishes the scores.

---

## 6. Pattern hunter — Not built yet

**What it is meant to be.** A tool that goes looking for patterns in the data that nobody told it about: for example days that score high but nobody flew, or conditions that fly far when the rules say they should not.

**What exists that is close.** One-off analyses, run by hand: the scoreboard's "biggest misses", a tester for possible rule changes (`rule_tests.py`), and the east-vs-west thermal study at Mystic. None of them runs on a schedule or writes down what it found in a standard way.

**Reads / writes:** nothing yet.

---

## 7. Creo — Beta, running beside the rules

**What it does.** A machine-learning model (LightGBM) that predicts the best flight of the day from the forecast weather, the rules' scores and the time of year. On the dashboard it shows only a word (Poor / Fair / Good / Great) and a few lines of reasons; it never shows kilometres and never changes a score or overrides a no-go.

**State.**
- Trained on 1,510 days (2022 to 2026-11-22, complete months only). Model file `sites/bright/creo_model.txt`.
- In the daily job since 2026-11-27, logging every prediction next to the rules' % in `predictions/creo_log.csv` (36 rows so far, from 3 model versions; 24 from the current one). The review script needs about a month of flights to judge it (planned around 2026-12-28).
- Tests (numbers in `BASELINE.md`): Creo beats the rules on every year in leave-one-year-out (+0.083 overall). On honest day-ahead forecasts, by walk-forward test, the gain is smaller (+0.044 with full variables, +0.032 with fewer) and the lower end of the range touches zero in places.

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
- No single check that the same flying-window and cut-off definitions are used everywhere (this is what Receta is for).
- Several known ways a test can look better than it is (list of suspected leaks in `SPEC.md` section 5). None has been fixed or measured yet.
- The scoreboard counts days from months with incomplete XContest data and Creo's tests do not, so their "rules" numbers differ (+0.68 vs +0.64).

**Reads:** nearly everything. **Writes:** reports in `data/` and two JSON files in `corrections/`.

---

## What happens next

Fixed dates already in `TODO.md`: about 2026-12-26 read the model-weighting review and decide; about 2026-12-28 run the Creo review; ask the Mystic station owner for readings. Nothing else is added here.
