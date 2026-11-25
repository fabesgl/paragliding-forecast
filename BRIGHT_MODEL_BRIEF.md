# Project brief: Bright paragliding day forecaster

## Goal
Build a system that predicts, for each of the next 3–5 days, the chance that **Bright, Victoria (Mystic launch)** will be:
1. a **good XC day** (%)
2. a **good acro day** (%)

It must learn from real outcomes (who flew, how far, how it felt) and get better over time. It ends up as a simple web dashboard that updates itself every morning. Notifications may come later.

Owner: Fabian. He understands computers well but isn't a data scientist. Keep explanations short and plain. Use km/h, metres and local Victorian time (AEST/AEDT).

## Working rules
- Work in phases (below). At the end of each phase, give a short report: what was done, what was found, and any problems. Wait for Fabian's go-ahead before starting the next phase.
- **Ask before:** starting any scraping, creating the GitHub repo, storing any login or credential, anything that costs money, and deleting data.
- Python 3.11+, a virtual environment, and SQLite for storage. Small, clear commits.
- Never commit credentials, browser session files, scraped flight data or pilot names to any repo that could be public.
- Code needs to be resumable: a crash or stop should never mean starting a download again from scratch.
- **Build it so new sites can be added later.** All site details (points, launches, wind directions, BOM stations, XContest launch names, grading thresholds) go in one config file per site, so adding a site means adding a config and its data, not rewriting code. Only Bright is built now. Manilla, NSW (Mt Borah) is the likely second site.
- If something in this brief turns out to be wrong (an API limit, a site layout, data that doesn't exist), say so and suggest the fix. Don't work around it silently.

---

## Phase 1: Setup and locations
- Set up the project folder, venv and SQLite database.
- Confirm the coordinates and heights of these points (the values below are approximate, so check them):
  - Mystic launch, Bright (about -36.74, 146.96)
  - Bright valley floor and landing (about -36.73, 146.96)
  - Mount Hotham (about -36.98, 147.13)
  - Falls Creek (about -36.87, 147.28)
  - Mount Buffalo / The Horn (about -36.77, 146.79)
  - 1–2 points upwind of the Ovens Valley to the NW and SW
- Ask Fabian which wind directions work at Mystic, and whether any other nearby launches should count (e.g. Tawonga Gap, Buffalo).

**Done when:** the points are confirmed and the database schema exists.

## Phase 2: Scrape XContest flight history
Purpose: find out how good each past day was at Bright.

- First check XContest's robots.txt, terms and privacy policy. Report anything relevant to Fabian and **wait for his go-ahead.**
- Use Playwright in a visible browser. **Fabian logs in himself**, and the session is saved locally only.
- Collect flights launched from the Bright area across as many past seasons as are available (Australian national contest and world).
- For each flight, store: flight ID, date, launch time, pilot ID, launch site name and coordinates, distance, points, route type, glider model and glider class (EN-A/B/C/D/CCC), and duration if shown.
- For pilots who regularly fly Bright, also collect their flight history (distances only) so each pilot's normal distance can be worked out.
- Store pilot names in hashed form only.
- **Be polite:** at least 8–10 seconds between page loads. Cache the raw HTML locally so no page is fetched twice. Stop at once and report if there's a captcha, a block, an error spike or a login problem.
- Don't download IGC files in this phase.

**Done when:** the database holds the Bright-area flights, with a summary of the count per season and per month.

## Phase 3: Weather forecast archive
Purpose: what the weather models **predicted** for each past day.

- Use Open-Meteo. Read its docs for the Historical Forecast API, Previous Runs API and Single Runs API, and check what date ranges and models are available for Australia.
- **Leakage warning:** the model must train on what was forecast *before* the day, not on near-real-time analysis. Use 1-day and 2-day lead forecasts (the Previous Runs API or Single Runs API) wherever possible, and explain to Fabian which data has which lead time.
- Models: ECMWF IFS, GFS, BOM ACCESS-G and any higher-resolution model that covers SE Australia.
- Variables (hourly, for every point), where available: wind speed, direction and gusts at 10 m; wind at 850 and 700 hPa; temperature and dew point at 2 m; temperature at 850 and 700 hPa; low, mid and high cloud cover; CAPE; lifted index; convective inhibition; boundary layer height; shortwave radiation; precipitation; mean sea level pressure.

**Done when:** forecast data covers every day that has flight data, and Fabian has a short note on its coverage and lead times.

## Phase 4: Real observations
Purpose: what the weather actually did.

- Find the BOM station IDs for Bright, Mount Hotham, Falls Creek and any other useful stations nearby.
- Past years: get whatever BOM provides for free (likely daily summaries). Tell Fabian what detailed history would cost.
- From now on: save the free recent half-hourly observations every day so a detailed record builds up.

**Done when:** past daily observations are stored and daily collection works.

## Phase 5: Grade each day (labels)
- **Flyable:** at least one flight logged.
- **XC quality**, built to cope with pilots of different skill:
  - Use the **top 3 distances** of the day, not the average.
  - Score each flight against **that pilot's own normal distance**.
  - Adjust for glider class.
  - Ignore flights launched before about 11am when judging XC quality.
- **Days nobody flew:** in-season weekends and public holidays with zero flights count as probably bad. Weekdays with zero flights are unknown, so give them low weight or leave them out.
- **Confidence weight:** days with only a few flights, or only low-skill flights, count less in training.
- **Acro:** XContest can't show this. Make:
  - a simple CSV template for pilot notes: date, site, flew (y/n), rating 1–5, XC or acro, turbulence 1–5, wind, notes
  - an importer for pilots' IGC files (from XCTrack) that pulls out date, launch, duration, max height, climb rates and drift, and joins them to the notes.
- Show Fabian the thresholds you chose and let him adjust them. He knows Bright.

## Phase 6: Build the training table
- One row per day.
- Forecast values averaged over the flying window (about 11am–4pm local), plus:
  - the wind and temperature difference between Hotham/Falls Creek and the valley, which affects valley wind
  - pressure gradients across the points
  - lapse rate (2 m to 850 and 700 hPa)
  - estimated cloud base, (T − Td) × 125 m
  - how much the forecast changed between the 2-day and 1-day lead
  - day of year, and days since meaningful rain

## Phase 7: Train
- **Baseline first:** simple hand-written rules (wind, gusts, cloud base, thermal strength, rain).
- Then two LightGBM models: XC and acro. The acro model only gets trained once there are enough labels; until then it's rule-based and marked "experimental".
- Calibrate the outputs (e.g. isotonic) so that "70%" really means good about 7 days in 10.

## Phase 8: Test honestly
- Leave-one-season-out testing: train on the other seasons, test on the one held out.
- Report the Brier score, ROC-AUC and a reliability chart, **compared with the baseline**.
- If the model doesn't clearly beat the baseline, say so plainly and suggest what to change. Don't tune on the test seasons.
- Show which inputs matter most (feature importance or SHAP), so Fabian can check they make sense for Bright.

## Phase 9: Dashboard and automation
- A GitHub repo (ask Fabian first: public or private, and how GitHub Pages works with each).
- A GitHub Actions job runs every morning (about 5am local). It fetches the latest forecasts, scores the next 3–5 days and publishes a static page on GitHub Pages.
- The page shows: the XC % and acro % for each day, the 2–3 main reasons for each score, key forecast numbers (wind at launch and at height, gusts, cloud base, thermal strength), the last-updated time, and a track record of past predictions against what happened. It has to work well on a phone.
- The scraped flight data and pilot data stay on Fabian's machine, not in the repo. Only the code, the trained model file and the predictions get pushed.

## Phase 10: Keep learning
- The daily Actions job also saves BOM observations and logs each prediction.
- **Weekly, on Fabian's machine:** scrape the past week's Bright flights (small and polite), import any new pilot notes or IGC files, retrain, run the Phase 8 checks again, and push the new model only if it isn't worse.
- Give Fabian one simple command to run the weekly update.

---

## Final deliverables
- Working repo with a README written in plain language: what it does, how to run each part, and how to add pilot notes or IGC files.
- A live dashboard URL.
- A one-page summary: how well it performs against the baseline, its known weak spots, and what more data would help most.
